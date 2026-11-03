"""Layer V runner (D-059) with a fake vision model: real shop, real offline render, real pipeline,
outcomes from the shop's records. Only Muse is replaced."""

import pytest

from eval.layer_v import build
from eval.layer_v import run as runner
from eval.run_g import facts_from_page
from intentlock.llm.spend import BudgetExceeded
from shop.store import Store
from tests.servers import serve

pytest.importorskip("playwright.sync_api")
CASES = {c["id"]: c for c in build.load()}


def oracle(c):
    s = Store()
    for item in c["cart"]:
        s.add_to_cart(item["sku"], item["qty"])
    s.page_variant = c["variant"]
    s.quote("c1", "ADDR-0001")  # so the page shows the delivery address
    return facts_from_page(s.checkout_page("c1"))


class FakeVision:
    """Returns the faithful reading for the case being run, or a swayed one."""

    def __init__(self, facts_for, swayed=()):
        self.facts_for, self.swayed, self.case = facts_for, set(swayed), None

    def structured(self, messages, schema, *, model=None, use_cache=True):
        f = self.facts_for(self.case)
        if self.case["id"] in self.swayed:  # the page's note "worked" on the reader
            f = f.model_copy(update={"recurring_text": ""})
        return f


def run_ids(ids, tmp_path, fake):
    cases = [CASES[i] for i in ids]
    orig = runner.run_case

    def tracked(c, llm, model, workdir, srv):
        fake.case = c
        return orig(c, llm, model, workdir, srv)

    runner.run_case, saved = tracked, orig
    try:
        return runner.run_all(cases, fake, "m", tmp_path, serve, log=lambda *_: None)
    finally:
        runner.run_case = saved


SAMPLE = [
    "V-displayed_price-01", "V-displayed_price-05", "V-hidden_subscription-01",
    "V-hidden_subscription-05", "V-delivery_address-01", "V-delivery_address-05",
]  # fmt: skip


def test_a_faithful_reading_stops_every_attack_and_buys_every_twin(tmp_path):
    s = run_ids(SAMPLE, tmp_path, FakeVision(oracle))["summary"]
    assert (s["attacks"], s["attacks_stopped"], s["attacks_with_expected_code"]) == (3, 3, 3)
    assert (s["twins"], s["twin_false_alarms"], s["twins_ordered"]) == (3, 0, 3)


def test_a_swayed_reading_is_caught_by_the_page_text(tmp_path):
    fake = FakeVision(oracle, swayed={"V-reader_note-01"})
    s = run_ids(["V-reader_note-01"], tmp_path, fake)["summary"]
    assert s["attacks_stopped"] == 1 and s["caught_only_by_dom_check"] == 1
    assert s["attacks_with_expected_code"] == 0  # the model missed it; the cross-check didn't


def test_a_budget_stop_ends_cleanly_and_resume_continues(tmp_path):
    class Broke(FakeVision):
        def structured(self, *a, **k):
            raise BudgetExceeded("spend cap reached")

    out = run_ids(SAMPLE[:2], tmp_path, Broke(oracle))
    assert out["stopped"] == "budget" and out["summary"]["cases_run"] == 0
    again = run_ids(SAMPLE[:2], tmp_path, FakeVision(oracle))
    assert again["stopped"] is None and again["summary"]["cases_run"] == 2


def test_summary_records_the_cases_lock(tmp_path):
    out = run_ids(SAMPLE[:1], tmp_path, FakeVision(oracle))
    assert out["cases_lock"] == build.LOCK.read_text().strip()
