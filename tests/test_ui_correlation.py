"""The UI links an action's receipts by `action_id` only (D-060).

Proposal, decision and execution receipts already carry it; approval receipts (`requested`,
`signed`, `not_approved`) and the checkout card summary gain it as an additive key. Nothing else in
those payloads changes, and the signed ApprovalRequest is untouched (its fields are what the
approver signs).
"""

import re

from intentlock.models import ApprovalRequest
from tests.test_proxy import CHECKOUT, call, make_gw, store  # noqa: F401, F811  (fixtures)

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _by_kind(gw, kind):
    return [r.payload for r in gw.receipts.all() if r.kind == kind]


def _approved_checkout(gw):
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    return call(gw, "checkout", **CHECKOUT)


def test_every_receipt_of_one_action_shares_its_action_id(make_gw):  # noqa: F811
    gw = make_gw()
    assert _approved_checkout(gw)["status"] == "executed"
    (prop,) = _by_kind(gw, "proposal")
    aid = prop["action_id"]
    assert HEX32.match(aid)
    assert [d["action_id"] for d in _by_kind(gw, "decision")] == [aid]
    approvals = _by_kind(gw, "approval")
    assert [a["event"] for a in approvals] == ["requested", "signed"]
    assert [a["action_id"] for a in approvals] == [aid, aid]
    assert approvals[0]["summary"]["action_id"] == aid
    assert {e["action_id"] for e in _by_kind(gw, "execution")} == {aid}


def test_a_not_approved_receipt_carries_the_action_id(make_gw):  # noqa: F811
    gw = make_gw(waiter=lambda req: None)
    assert _approved_checkout(gw)["status"] == "not_approved"
    (prop,) = _by_kind(gw, "proposal")
    last = _by_kind(gw, "approval")[-1]
    assert last["event"] == "not_approved" and last["action_id"] == prop["action_id"]


def test_the_pending_card_summary_carries_the_action_id(make_gw):  # noqa: F811
    seen = {}

    def waiter(req):
        seen["summary"] = dict(gw.summaries[req.approval_id])
        return None

    gw = make_gw(waiter=waiter)
    _approved_checkout(gw)
    (prop,) = _by_kind(gw, "proposal")
    assert seen["summary"]["action_id"] == prop["action_id"]


def test_two_actions_never_share_an_id_and_sessions_do_not_reuse_them(make_gw):  # noqa: F811
    gw = make_gw()
    _approved_checkout(gw)
    _approved_checkout(gw)
    first = [p["action_id"] for p in _by_kind(gw, "proposal")]
    gw2 = make_gw(config="intentlock")
    _approved_checkout(gw2)
    second = [p["action_id"] for p in _by_kind(gw2, "proposal")]
    assert len(set(first)) == 2 and not set(first) & set(second)


def test_the_change_is_additive_only(make_gw):  # noqa: F811
    gw = make_gw()
    _approved_checkout(gw)
    requested, signed = _by_kind(gw, "approval")
    assert set(requested) == {"event", "request", "summary", "action_id"}
    assert set(signed) == {"event", "approval", "action_id"}
    assert set(requested["summary"]) == {
        "tool", "args", "reasons", "lines", "fees_paise", "tax_paise", "total_paise",
        "recurring", "shipping_address_id", "shipping_address", "two_channel", "action_id",
    }  # fmt: skip
    # the signed request is unchanged: action_id is never part of what the approver signs
    assert "action_id" not in ApprovalRequest.model_fields
    assert "action_id" not in requested["request"]
    assert "action_id" not in signed["approval"]

    gw_no = make_gw(config="intentlock", waiter=lambda req: None)
    _approved_checkout(gw_no)
    assert set(_by_kind(gw_no, "approval")[-1]) == {"event", "approval_id", "action_id"}
