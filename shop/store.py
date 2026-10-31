"""ShopLite state and business logic (SPEC §14), shared by the REST app and the MCP server.

Prices are tax-inclusive integer paise, so a quote total is the sum of its lines plus fees and tax
(both 0 for now; the hidden-fee variants are added with the Layer G fixtures). `quote_id` is derived
from the quote contents, so re-fetching an unchanged cart gives an identical `quote_hash` (D-016).
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from intentlock.canonical import canonical_hash, compute_quote_hash
from intentlock.models import Quote, QuoteLine, RecurringTerm

DATA_DIR = Path(__file__).parent / "data"
ATTACKS = frozenset(p.stem for p in (DATA_DIR / "attacks").glob("P*.json"))
DEFAULT_CART_ID = "c1"
PUBLIC_FIELDS = ("sku", "title", "price_paise", "seller_id", "seller_country", "stock", "url")


class ShopError(Exception):
    status = 400


class NotFound(ShopError):
    status = 404


class StaleQuote(ShopError):
    status = 409


class UnknownAttack(ShopError, ValueError):
    status = 400


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class Store:
    def __init__(
        self, public_url: str = "http://localhost:8001", attacks: Iterable[str] = ()
    ) -> None:
        self.public_url = public_url.rstrip("/")
        self.reset(attacks=attacks)

    def reset(self, attacks: Iterable[str] | None = None) -> None:
        """Back to the initial state. `attacks` picks the SPEC §14.3 fixtures; None keeps them."""
        if attacks is not None:
            names = set(attacks)
            unknown = names - ATTACKS
            if unknown:
                raise UnknownAttack(f"unknown attack fixtures: {sorted(unknown)}")
            self.attacks = frozenset(names)
        catalog = _load("catalog.json")
        account = _load("account.json")
        self._products: dict[str, dict] = {p["sku"]: p for p in catalog["products"]}
        self._reviews: dict[str, list[dict]] = catalog["reviews"]
        self._addresses: list[dict] = account["addresses"]
        self._orders_history: list[dict] = account["orders"]
        self._inbox: list[dict] = account["inbox"]
        self._cart: dict[str, int] = {}
        self.fees_paise, self.tax_paise = 0, 0  # set by Layer G hidden-fee cases (SPEC §13.1)
        for name in sorted(self.attacks):
            self._apply_attack(_load(f"attacks/{name}.json"))
        self._quotes: dict[str, Quote] = {}
        self.orders: list[dict] = []
        self.messages: list[dict] = []
        self.deleted_addresses: list[str] = []
        self.cancelled_orders: list[str] = []

    def _apply_attack(self, fixture: dict) -> None:
        for sku, reviews in fixture.get("reviews", {}).items():
            self._reviews.setdefault(sku, []).extend(reviews)
        for sku, fields in fixture.get("products", {}).items():
            self._products[sku].update(fields)
        self._inbox.extend(fixture.get("inbox", []))

    # -- reads -------------------------------------------------------------------------------
    def search_products(self, query: str = "", max_price: int | None = None) -> list[dict]:
        words = query.lower().split()
        out = []
        for p in self._products.values():
            if not p["searchable"] or p["is_addon"]:
                continue
            text = f"{p['title']} {p['category']} {p['sku']}".lower()
            if all(w in text for w in words) and (
                max_price is None or p["price_paise"] <= max_price
            ):
                out.append({k: p[k] for k in PUBLIC_FIELDS})
        return out

    def get_product(self, sku: str) -> dict:
        p = self._products.get(sku)
        if p is None:
            raise NotFound(f"unknown sku {sku}")
        return {
            **{k: p[k] for k in PUBLIC_FIELDS},
            "description": p["description"],
            "seller_note": p["seller_note"],
            "recurring": p["recurring"],
        }

    def get_reviews(self, sku: str) -> list[dict]:
        if sku not in self._products:
            raise NotFound(f"unknown sku {sku}")
        return list(self._reviews.get(sku, []))

    def get_saved_addresses(self) -> list[dict]:
        return list(self._addresses)

    def get_order_history(self) -> list[dict]:
        return list(self._orders_history)

    def read_inbox(self) -> list[dict]:
        return [{"id": m["id"], "from": m["from"], "subject": m["subject"]} for m in self._inbox]

    def read_email(self, email_id: str) -> dict:
        for m in self._inbox:
            if m["id"] == email_id:
                return {k: m[k] for k in ("id", "from", "subject", "body") if k in m}
        raise NotFound(f"unknown email {email_id}")

    # -- cart --------------------------------------------------------------------------------
    def cart(self, cart_id: str = DEFAULT_CART_ID) -> dict:
        self._check_cart(cart_id)
        return {"cart_id": cart_id, "items": [{"sku": s, "qty": q} for s, q in self._cart.items()]}

    def add_to_cart(self, sku: str, qty: int = 1) -> dict:
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            raise ShopError("qty must be a positive integer")
        p = self._products.get(sku)
        if p is None:
            raise NotFound(f"unknown sku {sku}")
        if self._cart.get(sku, 0) + qty > p["stock"]:
            raise ShopError(f"insufficient stock for {sku}")
        self._cart[sku] = self._cart.get(sku, 0) + qty
        bundled = p.get("bundled_addon")  # P3: the shop pre-checks an add-on for you
        if bundled and bundled not in self._cart:
            self._cart[bundled] = 1
        return self.cart()

    def set_cart(self, items: list[dict]) -> None:
        """Eval only (Layer G): put exactly these items in the cart, no bundling."""
        self._cart = {}
        for item in items:
            if item["sku"] not in self._products:
                raise NotFound(f"unknown sku {item['sku']}")
            self._cart[item["sku"]] = item.get("qty", 1)

    def remove_from_cart(self, sku: str) -> dict:
        self._cart.pop(sku, None)
        return self.cart()

    @staticmethod
    def _check_cart(cart_id: str) -> None:
        if cart_id != DEFAULT_CART_ID:
            raise NotFound(f"unknown cart {cart_id}")

    # -- quote and order ---------------------------------------------------------------------
    def quote(self, cart_id: str, shipping_address_id: str) -> Quote:
        self._check_cart(cart_id)
        if not self._cart:
            raise ShopError("cart is empty")
        if not shipping_address_id:
            raise ShopError("shipping_address_id is required")
        lines, recurring = [], []
        for sku, qty in self._cart.items():
            p = self._products[sku]
            lines.append(
                QuoteLine(
                    sku=sku,
                    title=p["title"],
                    qty=qty,
                    unit_paise=p["price_paise"],
                    seller_id=p["seller_id"],
                    seller_country=p["seller_country"],
                    is_addon=p["is_addon"],
                )
            )
            if p["recurring"]:
                recurring.append(RecurringTerm(sku=sku, **p["recurring"]))
        fees, tax = self.fees_paise, self.tax_paise
        total = sum(line.unit_paise * line.qty for line in lines) + fees + tax
        draft = Quote(
            quote_id="",
            cart_id=cart_id,
            lines=lines,
            fees_paise=fees,
            tax_paise=tax,
            total_paise=total,
            recurring=recurring,
            shipping_address_id=shipping_address_id,
            checkout_url=f"{self.public_url}/checkout/{cart_id}",
            quote_hash="",
        )
        quote_id = "Q-" + canonical_hash(draft, exclude={"quote_id", "quote_hash"})[:12]
        draft = draft.model_copy(update={"quote_id": quote_id})
        quote = draft.model_copy(update={"quote_hash": compute_quote_hash(draft)})
        self._quotes[quote_id] = quote
        return quote

    def checkout_page(self, cart_id: str) -> dict:
        """What the checkout page at `checkout_url` shows (SPEC §11, §14.3 P3/P4)."""
        self._check_cart(cart_id)
        if not self._cart:
            raise ShopError("cart is empty")
        products = [(self._products[sku], qty) for sku, qty in self._cart.items()]
        return {
            "cart_id": cart_id,
            "lines": [
                {
                    "sku": p["sku"],
                    "title": p["title"],
                    "qty": qty,
                    "unit_paise": p["price_paise"],
                    "is_addon": p["is_addon"],
                    "seller_name": p["seller_name"],
                    "recurring": p["recurring"],
                }
                for p, qty in products
            ],  # fmt: skip
            "fees_paise": self.fees_paise + self.tax_paise,
            "total_paise": sum(p["price_paise"] * qty for p, qty in products)
            + self.fees_paise
            + self.tax_paise,
            "refund_policy": (
                "non_refundable"
                if any(p.get("refund_policy") == "non_refundable" for p, _ in products)
                else "refundable"
            ),
            "styles": sorted({p["page_style"] for p, _ in products if p.get("page_style")}),
        }

    def place_order(self, quote_id: str) -> dict:
        issued = self._quotes.get(quote_id)
        if issued is None:
            raise NotFound(f"unknown quote {quote_id}")
        if not self._cart:
            raise StaleQuote("cart changed since the quote was issued")
        current = self.quote(issued.cart_id, issued.shipping_address_id)
        if current.quote_hash != issued.quote_hash:
            raise StaleQuote("cart changed since the quote was issued")
        for line in current.lines:
            self._products[line.sku]["stock"] -= line.qty
        order = {
            "id": f"ORD-{6000 + len(self.orders)}",
            "quote_id": quote_id,
            "quote_hash": current.quote_hash,
            "total_paise": current.total_paise,
            "lines": [line.model_dump(mode="json") for line in current.lines],
            "shipping_address_id": current.shipping_address_id,
            "status": "placed",
        }
        self.orders.append(order)
        self._cart.clear()
        return order

    # -- other commit tools (executed only by the connector via REST) ------------------------
    def share_cart(self, cart_id: str, to: str) -> dict:
        self._check_cart(cart_id)
        msg = {"kind": "share_cart", "to": to, "cart": self.cart(cart_id)["items"]}
        self.messages.append(msg)
        return msg

    def send_email(self, to: str, subject: str, body: str) -> dict:
        msg = {"kind": "send_email", "to": to, "subject": subject, "body": body}
        self.messages.append(msg)
        return msg

    def delete_saved_address(self, address_id: str) -> dict:
        for a in self._addresses:
            if a["id"] == address_id:
                self._addresses.remove(a)
                self.deleted_addresses.append(address_id)
                return {"deleted": address_id}
        raise NotFound(f"unknown address {address_id}")

    def cancel_order(self, order_id: str) -> dict:
        for o in [*self.orders, *self._orders_history]:
            if o["id"] == order_id:
                o["status"] = "cancelled"
                self.cancelled_orders.append(order_id)
                return {"cancelled": order_id}
        raise NotFound(f"unknown order {order_id}")

    def resolve_target(self, kind: str, target_id: str) -> dict:
        """Existence check the connector uses for destructive tools (SPEC §6 step 3)."""
        if kind == "address":
            for a in self._addresses:
                if a["id"] == target_id:
                    return {"kind": kind, "id": target_id, "label": a["label"], "line": a["line"]}
            found = False
        elif kind == "order":
            found = any(o["id"] == target_id for o in [*self.orders, *self._orders_history])
        else:
            raise ShopError(f"unknown target kind {kind}")
        if not found:
            raise NotFound(f"unknown {kind} {target_id}")
        return {"kind": kind, "id": target_id}

    def state(self) -> dict:
        return {
            "orders": self.orders,
            "messages": self.messages,
            "deleted_addresses": self.deleted_addresses,
            "cancelled_orders": self.cancelled_orders,
            "cart": self.cart()["items"],
        }
