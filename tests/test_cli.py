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
