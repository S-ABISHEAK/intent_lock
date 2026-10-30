"""Operator CLI: clear messages instead of tracebacks; never prints secrets."""

import httpx
import pytest

from intentlock import cli
from intentlock.config import Settings


def test_missing_tokens_are_named_not_traced(monkeypatch, capsys):
    monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("APPROVER_UI_TOKEN", "ui-secret-value")
    assert cli.main(["session"]) == 2
    out = capsys.readouterr().out
    assert "GATEWAY_OPERATOR_TOKEN" in out and "APPROVER_UI_TOKEN" not in out
    assert "ui-secret-value" not in out


def test_unreachable_services_get_a_hint(monkeypatch, capsys):
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", "op-secret")
    monkeypatch.setenv("APPROVER_UI_TOKEN", "ui-secret")
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:9")  # nothing listens on port 9
    assert cli.main(["pending"]) == 1
    out = capsys.readouterr().out
    assert "make dev" in out and "op-secret" not in out


def test_401_explanations_point_at_the_right_token():
    s = Settings(gateway_url="http://gw", approver_url="http://ap")
    for url, name in (
        ("http://gw/session", "GATEWAY_OPERATOR_TOKEN"),
        ("http://ap/sign", "APPROVER_UI_TOKEN"),
    ):
        req = httpx.Request("POST", url)
        exc = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
        assert name in cli.explain(exc, s)


def test_timeline_lines_are_readable():
    rows = [
        {
            "seq": 0,
            "kind": "mandate",
            "payload": {"session_id": "abcdef123", "config": "r2_session"},
        },
        {"seq": 1, "kind": "tool_result", "payload": {"tool": "get_reviews"}},
        {
            "seq": 2,
            "kind": "proposal",
            "payload": {
                "tool": "checkout",
                "args": {"cart_id": "c1"},
                "dropped_args": ["total"],
                "flags": {"A": True, "B": True},
            },
        },
        {
            "seq": 3,
            "kind": "decision",
            "payload": {"outcome": "REVIEW", "reasons": [{"code": "RULE_OF_TWO"}]},
        },
        {"seq": 4, "kind": "approval", "payload": {"event": "signed"}},
        {
            "seq": 5,
            "kind": "execution",
            "payload": {"event": "finished", "executed": True, "reasons": []},
        },
    ]
    out = "\n".join(cli.format_receipt(r) for r in rows)
    assert "config=r2_session" in out and "dropped=['total']" in out
    assert "REVIEW ['RULE_OF_TWO']" in out and "signed" in out and "executed=True" in out


# -- checkpoint re-review MED: the card must show exactly what is signed --------------------------
HOSTILE = ["Mouse\x1b[8m hidden", "ADDR-9931\rADDR-0001", "SEL\u202eNI", "x\x9by", "a\u200bb"]


def card_with(title, address, seller="SEL-IN01"):
    return {
        "request": {"approval_id": "abcdef1234", "action_tool": "checkout"},
        "summary": {
            "tool": "checkout",
            "lines": [
                {
                    "qty": 1,
                    "title": title,
                    "sku": "MS-1001",
                    "unit_paise": 149900,
                    "seller_id": seller,
                    "seller_country": "IN",
                    "is_addon": False,
                }
            ],
            "fees_paise": 0,
            "tax_paise": 0,
            "total_paise": 149900,
            "recurring": [{"sku": "PP-1001", "amount_paise": 29900, "interval": "month\x1b[2K"}],
            "shipping_address_id": address,
            "shipping_address": None,
            "reasons": ["RULE_OF_TWO"],
        },
    }


@pytest.mark.parametrize("bad", HOSTILE)
def test_card_escapes_control_and_bidi_characters(bad):
    out = cli.describe(card_with(bad, bad, seller=bad))
    for ch in ("\x1b", "\r", "\u202e", "\x9b", "\u200b"):
        assert ch not in out
    assert "TOTAL" in out and "RECURRING" in out


def test_card_says_when_the_address_is_not_a_saved_one():
    out = cli.describe(card_with("Mouse", "ADDR-9931"))
    assert "ADDR-9931" in out and "NOT one of your saved addresses" in out
    known = card_with("Mouse", "ADDR-0001")
    known["summary"]["shipping_address"] = {"label": "Home", "line": "12 Lake View Road"}
    assert "Home: 12 Lake View Road" in cli.describe(known)


def test_timeline_escapes_hostile_text():
    r = {
        "seq": 1,
        "kind": "tool_result",
        "payload": {"tool": "get_product", "error": "no \x1b[31m sku"},
    }
    assert "\x1b" not in cli.format_receipt(r)


# -- approve_pending checks every response -------------------------------------------------------
def routed(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_approver_refusal_is_reported_and_nothing_is_approved(capsys):
    card = {"request": {"approval_id": "ap1"}, "summary": None}

    def handler(request):
        if request.url.host == "gw" and request.url.path == "/approvals/pending":
            return httpx.Response(200, json=[card])
        if request.url.host == "ap":
            return httpx.Response(401)
        raise AssertionError(f"unexpected call {request.url}")

    n = cli.approve_pending(
        "http://gw",
        "http://ap",
        routed(handler),
        operator_token="o",
        ui_token="u",
        confirm=lambda c: True,
    )
    assert n == 0 and "approver refused" in capsys.readouterr().out


def test_a_failed_reject_is_not_reported_as_rejected(capsys):
    card = {"request": {"approval_id": "ap1"}, "summary": None}

    def handler(request):
        if request.url.path == "/approvals/pending":
            return httpx.Response(200, json=[card])
        return httpx.Response(404)

    cli.approve_pending(
        "http://gw",
        "http://ap",
        routed(handler),
        operator_token="o",
        ui_token="u",
        confirm=lambda c: False,
    )
    out = capsys.readouterr().out
    assert "could not reject" in out and "rejected ap1" not in out


def test_yes_and_follow_cannot_be_combined(monkeypatch):
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", "o")
    monkeypatch.setenv("APPROVER_UI_TOKEN", "u")
    with pytest.raises(SystemExit):
        cli.main(["approve", "--yes", "--follow"])


def test_readback_lines_print_with_their_quoted_spans():
    lines = [
        {"kind": "grant", "text": "May checkout", "span": "you can buy accessories"},
        {"kind": "limit", "text": "No subscriptions or recurring charges", "span": None},
        {
            "kind": "not_granted",
            "text": "Not granted: allow_recurring (GRANT_NEGATED)",
            "span": "no subscriptions",
        },
        {"kind": "question", "text": "Which colour?", "span": None},
    ]
    out = cli.format_readback(lines)
    assert '"you can buy accessories"' in out and "Which colour?" in out and "Not granted" in out
