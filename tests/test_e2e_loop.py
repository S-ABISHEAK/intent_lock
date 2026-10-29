"""Oct 30 checkpoint (SPEC §21): agent -> proxy -> DENY/REVIEW -> approve -> execute -> receipt.

Real shop, approver and gateway servers on local ports; the agent talks only to the gateway's MCP
endpoint; a scripted LLM drives it; a "UI" thread approves through the approver service exactly as
`python -m intentlock.cli approve` does. Also §17 test 8 (partial): the shop key appears in no
receipt, LLM request, agent transcript or log line.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from nacl.signing import SigningKey

from agent.run import run_agent
from approver.app import create_app as create_approver
from intentlock.api import broker_waiter
from intentlock.api import create_app as create_gateway
from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore
from intentlock.cli import approve_pending
from intentlock.config import Settings
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.llm.client import LLMClient
from intentlock.provenance.ledger import Ledger
from intentlock.proxy.mcp_proxy import Gateway
from intentlock.receipts import ReceiptLog
from shop.app import create_app as create_shop
from shop.store import Store
from tests.factories import make_mandate
from tests.servers import serve

SHOP_KEY = "e2e-shop-key-must-never-leak-7f3a"
OP = "e2e-operator-token"
UI_TOKEN = "e2e-ui-token"
OPH = {"X-Operator-Token": OP}
APPROVER = SigningKey.generate()
MANDATE = make_mandate(expires_at=4_102_444_800, auto_approve_under_paise=200_000)


def turn(*calls, content=None):
    tc = [
        SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
        for i, (n, a) in enumerate(calls)
    ]
    msg = SimpleNamespace(content=content, tool_calls=tc or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class ScriptedLLM:
    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.turns.pop(0)


def buy(sku):
    return [
        turn(("search_products", {"query": "laptop"})),
        turn(("add_to_cart", {"sku": sku, "qty": 1})),
        turn(("checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001", "total": 1})),
        turn(content="done"),
    ]


@pytest.fixture
def world(tmp_path):
    """Start shop + approver; yields a function that starts a gateway in a given config."""
    store = Store()
    with (
        serve(create_shop(store, SHOP_KEY)) as shop_url,
        serve(create_approver(APPROVER, ui_token=UI_TOKEN)) as approver_url,
    ):

        def gateway(config):
            db = Database(tmp_path / f"{config}.sqlite")
            broker = ApprovalBroker()
            gw = Gateway(
                config=config,
                connector=ShopConnector(shop_url, api_key=SHOP_KEY),
                receipts=ReceiptLog(db),
                ledger=Ledger(db),
                nonces=NonceStore(db),
                verify_key=APPROVER.verify_key,
                waiter=broker_waiter(broker),
                extra_steps={},
                features=frozenset(),
            )
            return create_gateway(gw, broker, operator_token=OP)

        yield SimpleNamespace(
            store=store, shop_url=shop_url, approver_url=approver_url, gateway=gateway, tmp=tmp_path
        )


class UI(threading.Thread):
    """Plays the user: approves every pending card (optionally doing something first)."""

    def __init__(self, gateway_url, approver_url, before=None, decide=lambda card: True):
        super().__init__(daemon=True)
        self.gateway_url, self.approver_url, self.before = gateway_url, approver_url, before
        self.decide, self.cards = decide, []
        self.stop, self.approved = threading.Event(), 0

    def confirm(self, card):
        self.cards.append(card)
        return self.decide(card)

    def run(self):
        with httpx.Client(timeout=10) as c:
            while not self.stop.is_set():
                if (
                    self.before
                    and c.get(f"{self.gateway_url}/approvals/pending", headers=OPH).json()
                ):
                    self.before()
                    self.before = None
                self.approved += approve_pending(
                    self.gateway_url,
                    self.approver_url,
                    c,
                    operator_token=OP,
                    ui_token=UI_TOKEN,
                    confirm=self.confirm,
                )
                time.sleep(0.05)


def run_scenario(w, config, turns, before_approve=None, decide=lambda card: True):
    llm_fake = ScriptedLLM(turns)
    llm = LLMClient(Settings(llm_cache_dir=str(w.tmp / "llm")), client=llm_fake)
    w.store.reset()
    with serve(w.gateway(config)) as gw_url:
        httpx.post(
            f"{gw_url}/session", json={"mandate": MANDATE.model_dump(mode="json")}, headers=OPH
        ).raise_for_status()
        ui = UI(gw_url, w.approver_url, before_approve, decide)
        ui.start()
        try:
            run = asyncio.run(
                run_agent(
                    "Buy me a laptop",
                    gateway_url=gw_url,
                    llm=llm,
                    model="m",
                    transcript_path=w.tmp / f"{config}-transcript.json",
                )
            )
        finally:
            ui.stop.set()
            ui.join(5)
        receipts = httpx.get(f"{gw_url}/receipts", headers=OPH).json()
        verify = httpx.get(f"{gw_url}/receipts/verify", headers=OPH).json()
    checkout = json.loads(run.tool_calls[-1]["result"])
    return SimpleNamespace(
        run=run, receipts=receipts, verify=verify, checkout=checkout, ui=ui, llm=llm_fake
    )


def kinds(receipts):
    return [r["kind"] for r in receipts]


def test_review_approve_execute_receipt(world):
    s = run_scenario(world, "r2_session", buy("LAP-1201"))
    assert s.run.finished and [c["name"] for c in s.run.tool_calls] == [
        "search_products",
        "add_to_cart",
        "checkout",
    ]
    assert s.checkout["status"] == "executed", s.checkout
    assert s.ui.approved == 1
    assert len(world.store.orders) == 1 and world.store.orders[0]["total_paise"] == 5_499_900
    assert kinds(s.receipts) == [
        "mandate",
        "tool_result",
        "tool_result",
        "proposal",
        "decision",
        "approval",
        "approval",
        "execution",  # started
        "execution",  # finished
    ]
    assert s.verify["ok"] is True


def test_over_budget_is_denied_without_a_prompt(world):
    s = run_scenario(world, "r2_session", buy("LAP-1206"))  # ₹72,500 > ₹60,000 budget
    assert s.checkout == {"status": "denied", "reasons": ["BUDGET_EXCEEDED"]}
    assert s.ui.approved == 0 and world.store.orders == []
    assert kinds(s.receipts)[-2:] == ["proposal", "decision"] and s.verify["ok"]


def test_tampered_cart_after_approval_is_quote_changed(world):
    s = run_scenario(
        world,
        "r2_session",
        buy("LAP-1201"),
        before_approve=lambda: world.store.add_to_cart("XC-4999"),
    )
    assert s.checkout == {"status": "denied", "reasons": ["QUOTE_CHANGED"]}
    assert world.store.orders == [] and kinds(s.receipts)[-1] == "execution"


def test_config_none_executes_without_approval(world):
    s = run_scenario(world, "none", buy("LAP-1206"))  # even over budget: no guard
    assert s.checkout["status"] == "executed" and s.ui.approved == 0
    assert len(world.store.orders) == 1


def mouse_after_reading_reviews():
    return [
        turn(("search_products", {"query": "mouse"})),
        turn(("get_reviews", {"sku": "MS-1001"})),
        turn(("get_saved_addresses", {})),
        turn(("add_to_cart", {"sku": "MS-1001", "qty": 1})),
        turn(("checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"})),
        turn(content="done"),
    ]


def test_headline_intentlock_buys_without_a_prompt_where_r2_interrupts(world):
    """SPEC §18 beat 5, live: same agent, same reads, same ₹1,499 purchase under the limit."""
    il = run_scenario(world, "intentlock", mouse_after_reading_reviews())
    assert il.checkout["status"] == "executed" and il.ui.cards == []
    r2 = run_scenario(world, "r2_session", mouse_after_reading_reviews())
    assert r2.checkout["status"] == "executed" and len(r2.ui.cards) == 1
    assert r2.ui.cards[0]["summary"]["reasons"] == ["RULE_OF_TWO"]


def test_intentlock_asks_when_the_address_was_never_looked_up(world):
    s = run_scenario(world, "intentlock", buy("LAP-1201"))
    assert s.checkout["status"] == "executed" and len(s.ui.cards) == 1
    assert set(s.ui.cards[0]["summary"]["reasons"]) == {"APPROVAL_REQUIRED", "UNKNOWN_PROVENANCE"}


def test_shop_key_never_leaks_anywhere(world, caplog):
    s = run_scenario(world, "r2_session", buy("LAP-1201"))
    haystacks = [
        json.dumps(s.receipts),
        json.dumps(s.llm.requests, default=str),
        (world.tmp / "r2_session-transcript.json").read_text(),
        caplog.text,
        json.dumps([c for c in s.run.tool_calls]),
    ]
    assert not [h for h in haystacks if SHOP_KEY in h]


def test_user_sees_the_quote_and_declining_executes_nothing(world):
    s = run_scenario(world, "r2_session", buy("LAP-1201"), decide=lambda card: False)
    assert s.checkout["status"] == "not_approved" and world.store.orders == []
    card = s.ui.cards[0]["summary"]
    assert card["total_paise"] == 5_499_900 and card["lines"][0]["title"] == "Asus Vivobook 15"
