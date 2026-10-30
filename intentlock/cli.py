"""Operator CLI: the stand-in for the UI until Nov 3 (SPEC §10; D-023, D-026).

  python -m intentlock.cli approve [--follow] [--yes]   show each pending card and ask y/N
  python -m intentlock.cli reject <id>
  python -m intentlock.cli pending
  python -m intentlock.cli verify                      check the receipt hash chain
  python -m intentlock.cli session [mandate.json]      start a gateway session (default: demo)
  python -m intentlock.cli reset [--attacks P1,P6]     empty the shop (optionally with SPEC §14.3
                                                       attack fixtures), start a fresh demo session
  python -m intentlock.cli timeline                    the session's receipts, one line each
  python -m intentlock.cli mandate "<request>"         Muse drafts a mandate; read it back, y/N

`approve` shows what is being approved (items, total, recipient), and only a "y" sends the
request to the approver with the UI token, then hands the signature to the gateway. `--yes`
signs without asking and is meant for scripted runs only; it can't be combined with `--follow`.
The CLI never sees a private key or the shop key.
"""

import argparse
import json
import sys
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from intentlock.config import Settings
from intentlock.connector.shop_connector import ShopConnector

COMPILE_TIMEOUT_S = 180.0  # a Muse structured-output call (with retries) can take a while
DEMO_MANDATE = Path(__file__).resolve().parent.parent / "eval" / "tasks" / "demo_mandate.json"
Confirm = Callable[[dict[str, Any]], bool]


def safe_text(text: Any) -> str:
    """Shop- and agent-controlled text made inert for a terminal: control, format and bidi
    characters (ANSI escapes, \\r, U+202E, zero-width) are shown as escapes, never interpreted."""
    out = []
    for ch in str(text):
        if unicodedata.category(ch)[0] == "C":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def rupees(paise: int) -> str:
    return f"₹{paise // 100:,}.{paise % 100:02d}"


def describe(card: dict[str, Any]) -> str:
    s = card.get("summary") or {}
    req = card["request"]
    lines = [f"Approval {req['approval_id'][:8]}: {s.get('tool', req['action_tool'])}"]
    for line in s.get("lines", []):
        tag = " (add-on)" if line.get("is_addon") else ""
        lines.append(
            f"  {line['qty']} x {line['title']} [{line['sku']}] {rupees(line['unit_paise'])}"
            f"  seller {line['seller_id']} ({line['seller_country']}){tag}"
        )
    if "total_paise" in s:
        lines.append(
            f"  fees {rupees(s['fees_paise'])}  tax {rupees(s['tax_paise'])}"
            f"  TOTAL {rupees(s['total_paise'])}"
        )
        saved = s.get("shipping_address")
        if saved:
            lines.append(
                f"  ship to {s['shipping_address_id']} ({saved['label']}: {saved['line']})"
            )
        else:
            lines.append(
                f"  ship to {s['shipping_address_id']}  <-- NOT one of your saved addresses"
            )
        for r in s.get("recurring", []):
            lines.append(
                f"  RECURRING {r['sku']}: {rupees(r['amount_paise'])} every {r['interval']}"
            )
    elif s.get("args"):
        lines.append(f"  {json.dumps(s['args'], ensure_ascii=False)}")
    lines.append(f"  why you are asked: {', '.join(s.get('reasons', [])) or 'unknown'}")
    return "\n".join(safe_text(line) for line in lines)


def format_receipt(r: dict[str, Any]) -> str:
    p, kind = r["payload"], r["kind"]
    if kind == "mandate":
        what = f"session {p['session_id'][:8]} config={p.get('config')}"
        if "promptguard" in p:
            what += f" promptguard={p['promptguard']}"
    elif kind == "tool_result":
        what = f"{p['tool']}" + (f"  ERROR {p['error']}" if "error" in p else "")
    elif kind == "proposal":
        flags = p.get("flags", {})
        dropped = f"  dropped={p['dropped_args']}" if p.get("dropped_args") else ""
        args = json.dumps(p["args"], ensure_ascii=False)
        what = f"{p['tool']} {args}  A={flags.get('A')} B={flags.get('B')}{dropped}"
    elif kind == "decision":
        what = f"{p['outcome']} {[x['code'] for x in p['reasons']]}"
    elif kind == "approval":
        what = p["event"]
    elif kind == "execution":
        what = p["event"] + (f" executed={p['executed']} {p['reasons']}" if "executed" in p else "")
    else:
        what = json.dumps(p, ensure_ascii=False)[:120]
    return safe_text(f"{r['seq']:>4}  {kind:<11} {what}")


MARKS = {"grant": "+", "limit": "-", "not_granted": "!", "question": "?"}


def format_readback(lines: list[dict[str, Any]]) -> str:
    out = []
    for b in lines:
        quote = (
            f'  "{b["span"]}"' if b.get("span") and b["kind"] in ("grant", "not_granted") else ""
        )
        out.append(safe_text(f"{MARKS.get(b['kind'], '*')} {b['text']}{quote}"))
    return "\n".join(out)


def ask(card: dict[str, Any]) -> bool:
    print(describe(card))
    return input("Approve? [y/N] ").strip().lower() == "y"


def approve_pending(
    gateway_url: str,
    approver_url: str,
    client: httpx.Client,
    *,
    operator_token: str,
    ui_token: str,
    confirm: Confirm = ask,
) -> int:
    op = {"X-Operator-Token": operator_token}
    approved = 0
    cards = client.get(f"{gateway_url}/approvals/pending", headers=op)
    cards.raise_for_status()
    for card in cards.json():
        approval_id = card["request"]["approval_id"]
        if not confirm(card):
            r = client.post(f"{gateway_url}/approvals/{approval_id}/reject", headers=op)
            if r.is_success:
                print(f"rejected {approval_id[:8]}")
            else:
                print(f"could not reject {approval_id[:8]}: {r.status_code}")
            continue
        signed = client.post(
            f"{approver_url}/sign", json=card["request"], headers={"X-Approver-Token": ui_token}
        )
        if signed.status_code != 200:
            print(f"approver refused {approval_id[:8]}: {signed.status_code}")
            continue
        r = client.post(
            f"{gateway_url}/approvals/{approval_id}/signature", json=signed.json(), headers=op
        )
        if r.status_code == 200:
            approved += 1
            print(f"approved {approval_id[:8]}")
        else:
            print(f"gateway did not take the approval for {approval_id[:8]}: {r.status_code}")
    return approved


def start_session(
    gateway_url: str, operator_token: str, mandate_path: Path, client: httpx.Client
) -> str:
    mandate = json.loads(mandate_path.read_text(encoding="utf-8"))
    r = client.post(
        f"{gateway_url}/session",
        json={"mandate": mandate},
        headers={"X-Operator-Token": operator_token},
    )
    r.raise_for_status()
    return r.json()["session_id"]


def explain(exc: httpx.HTTPError, s: Settings) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"cannot reach the services ({exc.request.url}). Is `make dev` running?"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        url = str(exc.request.url)
        if url.startswith(s.approver_url):
            return (
                "the approver refused the UI token: APPROVER_UI_TOKEN in .env must match the one"
                " `make dev` started with (restart `make dev` after editing .env)."
            )
        return (
            "the gateway refused the operator token: GATEWAY_OPERATOR_TOKEN in .env must match"
            " the one `make dev` started with (restart `make dev` after editing .env)."
        )
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409:
        return "the gateway is busy with a pending approval; approve or reject it first."
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except ValueError:
            detail = exc.response.text
        return f"gateway returned {exc.response.status_code}: {safe_text(str(detail))[:500]}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out waiting for the gateway; the model may be slow (see `make dev` output)"
    return f"request failed: {exc}"


MAX_QUESTION_ROUNDS = 3


def compile_text(s: Settings, c: httpx.Client, text: str) -> dict[str, Any]:
    r = c.post(
        f"{s.gateway_url}/mandate/compile",
        json={"user_text": text},
        headers={"X-Operator-Token": s.operator_token},
        timeout=COMPILE_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()


def run_mandate(s: Settings, c: httpx.Client, text: str) -> int:
    """Compile, read back, ask the open questions (SPEC §9 rule 6), confirm. Answers are appended to
    the request as the user's own words and the whole thing is compiled and grounded again."""
    for _ in range(MAX_QUESTION_ROUNDS):
        body = compile_text(s, c, text)
        print(format_readback(body["readback"]))
        if not body["clarifications"]:
            break
        print("\nPlease answer (an empty answer stops without confirming):")
        answered = []
        for q in body["clarifications"]:
            answer = input(f"? {safe_text(q)}\n> ").strip()
            if not answer:
                print("not confirmed")
                return 1
            answered.append(f"{q} {answer}")
        text = f"{text} {' '.join(answered)}"
        print("\nRecompiling with your answers...\n")
    else:
        print("\nStill unclear after several rounds; please rewrite the request.")
        return 1
    if input("\nConfirm this mandate? [y/N] ").strip().lower() != "y":
        print("not confirmed")
        return 1
    mandate = {**body["mandate"], "user_confirmed": True}
    r = c.post(
        f"{s.gateway_url}/session",
        json={"mandate": mandate},
        headers={"X-Operator-Token": s.operator_token},
    )
    r.raise_for_status()
    print(f"session {r.json()['session_id']} started with your confirmed mandate")
    return 0


def main(argv: list[str] | None = None) -> int:
    s = Settings.from_env()
    missing = [
        name
        for name, value in (
            ("GATEWAY_OPERATOR_TOKEN", s.operator_token),
            ("APPROVER_UI_TOKEN", s.approver_ui_token),
        )
        if not value
    ]
    if missing:
        print(f"not set in .env: {', '.join(missing)} (then restart `make dev`)")
        return 2
    try:
        return _run(s, argv)
    except httpx.HTTPError as exc:
        print(f"error: {explain(exc, s)}")
        return 1


def _run(s: Settings, argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(prog="intentlock")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("approve")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--follow", action="store_true", help="keep watching for new cards")
    mode.add_argument("--yes", action="store_true", help="approve without asking (scripts only)")
    rj = sub.add_parser("reject")
    rj.add_argument("approval_id")
    sub.add_parser("pending")
    sub.add_parser("verify")
    se = sub.add_parser("session")
    se.add_argument("mandate", nargs="?", default=str(DEMO_MANDATE))
    rs = sub.add_parser("reset")
    rs.add_argument("--attacks", default="", help="comma-separated fixtures, e.g. P1,P2,P5,P6")
    sub.add_parser("timeline")
    md = sub.add_parser("mandate")
    md.add_argument("text", help="your request in your own words")
    args = parser.parse_args(argv)
    op = {"X-Operator-Token": s.operator_token}

    with httpx.Client(timeout=10) as c:
        if args.cmd == "approve":
            confirm: Confirm = (lambda card: True) if args.yes else ask
            while True:
                approve_pending(
                    s.gateway_url,
                    s.approver_url,
                    c,
                    operator_token=s.operator_token,
                    ui_token=s.approver_ui_token,
                    confirm=confirm,
                )
                if not args.follow:
                    return 0
                time.sleep(0.5)
        if args.cmd == "reject":
            r = c.post(f"{s.gateway_url}/approvals/{args.approval_id}/reject", headers=op)
            return 0 if r.is_success else 1
        if args.cmd == "session":
            print(start_session(s.gateway_url, s.operator_token, Path(args.mandate), c))
            return 0
        if args.cmd == "reset":
            attacks = [a.strip() for a in args.attacks.split(",") if a.strip()]
            ShopConnector(s.shop_url).reset_shop(attacks=attacks)
            print(start_session(s.gateway_url, s.operator_token, DEMO_MANDATE, c))
            return 0
        if args.cmd == "mandate":
            return run_mandate(s, c, args.text)
        if args.cmd == "timeline":
            receipts = c.get(f"{s.gateway_url}/receipts", headers=op)
            receipts.raise_for_status()
            for r in receipts.json():
                print(format_receipt(r))
            check = c.get(f"{s.gateway_url}/receipts/verify", headers=op).json()
            print(f"chain ok={check['ok']} head={check['head'][:16]}")
            return 0
        if args.cmd == "pending":
            for card in c.get(f"{s.gateway_url}/approvals/pending", headers=op).json():
                print(describe(card))
            return 0
        print(json.dumps(c.get(f"{s.gateway_url}/receipts/verify", headers=op).json(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
