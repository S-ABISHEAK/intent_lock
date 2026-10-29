"""Small operator CLI (SPEC §10 stand-in until the UI exists on Nov 3).

  python -m intentlock.cli approve [--watch]   act as the user clicking Approve on pending cards
  python -m intentlock.cli reject <id>
  python -m intentlock.cli pending
  python -m intentlock.cli verify              check the receipt hash chain
  python -m intentlock.cli session [mandate.json]   start a gateway session (default: demo mandate)
  python -m intentlock.cli reset               empty the shop and start a fresh demo session

The CLI asks the approver to sign, then hands the signature to the gateway, exactly as the UI will.
It never sees a private key or the shop key.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from intentlock.config import Settings
from intentlock.connector.shop_connector import ShopConnector

DEMO_MANDATE = Path(__file__).resolve().parent.parent / "eval" / "tasks" / "demo_mandate.json"


def start_session(gateway_url: str, mandate_path: Path, client: httpx.Client) -> str:
    mandate = json.loads(mandate_path.read_text(encoding="utf-8"))
    r = client.post(f"{gateway_url}/session", json={"mandate": mandate})
    r.raise_for_status()
    return r.json()["session_id"]


def approve_pending(gateway_url: str, approver_url: str, client: httpx.Client | None = None) -> int:
    c = client or httpx.Client(timeout=10)
    approved = 0
    for req in c.get(f"{gateway_url}/approvals/pending").json():
        signed = c.post(f"{approver_url}/sign", json=req)
        if signed.status_code != 200:
            print(f"approver refused {req['approval_id']}: {signed.status_code}")
            continue
        r = c.post(f"{gateway_url}/approvals/{req['approval_id']}/signature", json=signed.json())
        if r.status_code == 200:
            approved += 1
            print(f"approved {req['approval_id']} ({req['action_tool']})")
    return approved


def main(argv: list[str] | None = None) -> int:
    s = Settings.from_env()
    parser = argparse.ArgumentParser(prog="intentlock")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("approve")
    ap.add_argument("--watch", action="store_true", help="keep approving until interrupted")
    rj = sub.add_parser("reject")
    rj.add_argument("approval_id")
    sub.add_parser("pending")
    sub.add_parser("verify")
    se = sub.add_parser("session")
    se.add_argument("mandate", nargs="?", default=str(DEMO_MANDATE))
    sub.add_parser("reset")
    args = parser.parse_args(argv)

    with httpx.Client(timeout=10) as c:
        if args.cmd == "approve":
            while True:
                approve_pending(s.gateway_url, s.approver_url, c)
                if not args.watch:
                    return 0
                time.sleep(0.5)
        if args.cmd == "reject":
            return (
                0
                if c.post(f"{s.gateway_url}/approvals/{args.approval_id}/reject").is_success
                else 1
            )
        if args.cmd == "session":
            print(start_session(s.gateway_url, Path(args.mandate), c))
            return 0
        if args.cmd == "reset":
            ShopConnector(s.shop_url).reset_shop()
            print(start_session(s.gateway_url, DEMO_MANDATE, c))
            return 0
        if args.cmd == "pending":
            print(json.dumps(c.get(f"{s.gateway_url}/approvals/pending").json(), indent=2))
            return 0
        print(json.dumps(c.get(f"{s.gateway_url}/receipts/verify").json(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
