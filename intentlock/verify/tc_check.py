"""`make two-channel-check`: screenshot the clean, P3 and P4 checkout pages of an in-process shop
and let Muse read them (SPEC §11). Needs Playwright and MODEL_API_KEY. Prints what Muse saw and the
step-8 codes for the demo mandate (refundable required); exit 0 only if each page gets its codes."""

import json
import secrets
import socket
import sys
import threading
import time

from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.verify.two_channel import compare, extract_page_facts

# (fixtures, SKU, the codes this page must raise)
PAGES = (
    ("clean", "LAP-1201", set()),
    ("P3", "LAP-1204", {"PRICE_DISPLAY_MISMATCH", "PRESELECTED_ADDON"}),
    ("P4", "LAP-1209", {"NON_REFUNDABLE"}),
)


def _serve(app):
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    return server, f"http://127.0.0.1:{port}"


def main(llm=None, model: str | None = None) -> int:
    from intentlock.cli import DEMO_MANDATE
    from intentlock.config import Settings
    from intentlock.llm.client import LLMClient
    from intentlock.models import Mandate
    from shop.app import create_app
    from shop.store import Store

    settings = Settings.from_env()
    if llm is None:
        if not settings.model_api_key:
            print("MODEL_API_KEY is not set in .env")
            return 1
        llm = LLMClient(settings)
    model = model or settings.model_id
    mandate = Mandate.model_validate(json.loads(DEMO_MANDATE.read_text(encoding="utf-8")))
    key = secrets.token_hex(16)  # a throwaway key for this in-process shop only
    store = Store(attacks={"P3", "P4"})
    server, url = _serve(create_app(store, key))
    store.public_url = url
    connector = ShopConnector(url, api_key=key)
    ok = True
    try:
        for name, sku, want in PAGES:
            store.reset()
            store.add_to_cart(sku)
            quote = connector.get_quote("c1", "ADDR-0001")
            try:
                facts = extract_page_facts(connector.render_checkout(quote), llm, model=model)
            except ConnectorError as exc:
                print(f"{name}: page not rendered ({exc})")
                ok = False
                continue
            got = {r.code for r in compare(facts, quote, mandate)}
            passed = want <= got if want else not got
            ok &= passed
            print(f"{name} ({sku}, quote total {quote.total_paise}): {'OK' if passed else 'MISS'}")
            print(f"  Muse read: {facts.model_dump_json()}")
            print(f"  step 8 codes: {sorted(got) or 'none'} (expected {sorted(want) or 'none'})")
    finally:
        server.should_exit = True
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
