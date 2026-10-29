"""Operator CLI: clear messages instead of tracebacks; never prints secrets."""

import httpx

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
