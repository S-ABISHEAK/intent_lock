"""`make demo-reset` starts over even while an approval card is open (Nov 4 live rehearsal).

Before: the waiting commit made POST /session answer 409 for up to 120 s, the reset failed, and the
next demo command ran against the old session. Now the reset ends the session first (open cards are
rejected, so nothing that was waiting can execute), resets the shop, then starts the new session.
"""

import asyncio
import json
import threading
import time

import httpx

from agent.hijacked import ScriptedBrain
from agent.run import run_agent
from intentlock.cli import reset_demo
from intentlock.connector.shop_connector import ShopConnector
from tests.servers import serve
from tests.test_e2e_loop import MANDATE, OP, OPH, SHOP_KEY, world  # noqa: F401


def start(gw_url):
    httpx.post(
        f"{gw_url}/session",
        json={
            "mandate": MANDATE.model_dump(mode="json"),
            "confirm_auto_approve_paise": MANDATE.auto_approve_under_paise,
        },
        headers=OPH,
    ).raise_for_status()


def test_reset_with_an_open_card_rejects_it_and_starts_a_fresh_session(world):  # noqa: F811
    world.store.reset(attacks=["P4"])
    with serve(world.gateway("intentlock")) as gw_url:
        start(gw_url)
        out = {}
        agent = threading.Thread(
            target=lambda: out.setdefault(
                "run", asyncio.run(run_agent("x", gateway_url=gw_url, llm=ScriptedBrain("P4")))
            )
        )
        agent.start()
        deadline = time.monotonic() + 10
        while not httpx.get(f"{gw_url}/approvals/pending", headers=OPH).json():
            assert time.monotonic() < deadline, "no card opened"
            time.sleep(0.05)

        with httpx.Client(timeout=10) as c:
            sid = reset_demo(gw_url, OP, ShopConnector(world.shop_url, api_key=SHOP_KEY), [], c)
        agent.join(10)

        assert json.loads(out["run"].tool_calls[-1]["result"])["status"] == "not_approved"
        assert world.store.orders == [] and world.store.attacks == set()
        assert httpx.get(f"{gw_url}/approvals/pending", headers=OPH).json() == []
        receipts = httpx.get(f"{gw_url}/receipts", headers=OPH).json()
        last = [r for r in receipts if r["kind"] == "mandate"][-1]["payload"]
        assert last["session_id"] == sid and last["mandate"] is not None


def test_reset_without_a_session_just_starts_one(world):  # noqa: F811
    with serve(world.gateway("intentlock")) as gw_url:
        with httpx.Client(timeout=10) as c:
            sid = reset_demo(gw_url, OP, ShopConnector(world.shop_url, api_key=SHOP_KEY), ["P1"], c)
        assert len(sid) == 32 and world.store.attacks == {"P1"}


def test_reset_also_rejects_a_card_that_opens_after_the_first_sweep(world, monkeypatch):  # noqa: F811
    """Review LOW-1: a commit still deciding when the reset starts opens its card late."""
    orig = ShopConnector.resolve_target
    entered = threading.Event()

    def slow(self, kind, target_id):
        entered.set()
        time.sleep(1.5)
        return orig(self, kind, target_id)

    monkeypatch.setattr(ShopConnector, "resolve_target", slow)
    world.store.reset(attacks=["P4"])
    with serve(world.gateway("intentlock")) as gw_url:
        start(gw_url)
        out = {}
        agent = threading.Thread(
            target=lambda: out.setdefault(
                "run", asyncio.run(run_agent("x", gateway_url=gw_url, llm=ScriptedBrain("P4")))
            )
        )
        agent.start()
        assert entered.wait(10)
        try:
            with httpx.Client(timeout=10) as c:
                sid = reset_demo(
                    gw_url, OP, ShopConnector(world.shop_url, api_key=SHOP_KEY), [], c, wait_s=4
                )
        finally:  # never leave a card open: the server would wait for it on shutdown
            pending = httpx.get(f"{gw_url}/approvals/pending", headers=OPH).json()
            left = [p["request"]["approval_id"] for p in pending]
            for aid in left:
                httpx.post(f"{gw_url}/approvals/{aid}/reject", headers=OPH)
            agent.join(10)

        assert left == [] and len(sid) == 32 and world.store.orders == []
        assert httpx.get(f"{gw_url}/approvals/pending", headers=OPH).json() == []
        assert json.loads(out["run"].tool_calls[-1]["result"])["status"] != "executed"
