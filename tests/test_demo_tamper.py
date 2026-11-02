"""Demo tamper helper (SPEC §18 beat 6): change the cart while an approval card is open.

The cart change goes through the connector, the only holder of SHOP_API_KEY (invariant 8), and the
signed approval must then fail with QUOTE_CHANGED because the connector re-fetches the quote before
execution (invariant 6). Nothing the helper prints may contain the key.
"""

import pytest
from fastapi.testclient import TestClient

from intentlock import cli
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from shop.app import create_app
from shop.store import Store
from tests.test_e2e_loop import SHOP_KEY, buy, kinds, run_scenario, world  # noqa: F401

KEY = "test-shop-key-DO-NOT-LEAK"
BASE = "http://shop.test"


@pytest.fixture
def store():
    s = Store()
    s.add_to_cart("LAP-1201")
    return s


@pytest.fixture
def shop(store):
    return TestClient(create_app(store, KEY), base_url=BASE)


def test_tamper_cart_adds_the_sku_through_the_connector(store, shop):
    cart = ShopConnector(BASE, api_key=KEY, client=shop).tamper_cart("XC-4999")
    assert {i["sku"] for i in cart["items"]} == {"LAP-1201", "XC-4999"}
    assert {i["sku"] for i in store.state()["cart"]} == {"LAP-1201", "XC-4999"}


def test_tamper_cart_needs_the_shop_key(store, shop):
    with pytest.raises(ConnectorError, match="401"):
        ShopConnector(BASE, api_key="wrong", client=shop).tamper_cart("XC-4999")
    assert shop.post("/cart/items", json={"sku": "XC-4999"}).status_code == 401
    assert [i["sku"] for i in store.state()["cart"]] == ["LAP-1201"]


def test_tamper_cart_rejects_unknown_skus_and_bad_quantities(store, shop):
    c = ShopConnector(BASE, api_key=KEY, client=shop)
    with pytest.raises(ConnectorError, match="404"):
        c.tamper_cart("NOPE-1")
    with pytest.raises(ConnectorError):
        c.tamper_cart("XC-4999", qty=0)
    assert [i["sku"] for i in store.state()["cart"]] == ["LAP-1201"]


def use_shop(monkeypatch, shop):
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", "op")
    monkeypatch.setenv("APPROVER_UI_TOKEN", "ui")
    monkeypatch.setattr(
        cli, "ShopConnector", lambda url: ShopConnector(BASE, api_key=KEY, client=shop)
    )


def test_cli_tamper_prints_the_cart_and_never_the_key(monkeypatch, capsys, shop):
    use_shop(monkeypatch, shop)
    assert cli.main(["tamper", "--add", "XC-4999"]) == 0
    out = capsys.readouterr()
    assert "XC-4999" in out.out and "LAP-1201" in out.out
    assert KEY not in out.out + out.err


def test_cli_tamper_failure_is_explained_without_the_key(monkeypatch, capsys, shop):
    use_shop(monkeypatch, shop)
    assert cli.main(["tamper", "--add", "NOPE-1"]) == 1
    out = capsys.readouterr()
    assert "404" in out.err and KEY not in out.out + out.err


def test_tamper_while_the_card_is_open_is_quote_changed(world):  # noqa: F811
    """The on-camera path: approve a card, tamper via the helper, execution refuses."""
    tamper = ShopConnector(world.shop_url, api_key=SHOP_KEY)
    s = run_scenario(
        world, "r2_session", buy("LAP-1201"), before_approve=lambda: tamper.tamper_cart("XC-4999")
    )
    assert s.checkout == {"status": "denied", "reasons": ["QUOTE_CHANGED"]}
    assert world.store.orders == [] and kinds(s.receipts)[-1] == "execution"


def test_cli_tamper_needs_only_the_shop_key(monkeypatch, capsys, shop):
    """Checkpoint review LOW-3: the tamper process gets no operator or UI token."""
    monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("APPROVER_UI_TOKEN", raising=False)
    monkeypatch.setattr(
        cli, "ShopConnector", lambda url: ShopConnector(BASE, api_key=KEY, client=shop)
    )
    assert cli.main(["tamper", "--add", "XC-4999"]) == 0
    assert "XC-4999" in capsys.readouterr().out
