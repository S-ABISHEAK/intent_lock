"""ShopLite MCP server (SPEC §14.2, D-012).

Exposes the agent-visible tools. Read and cart tools run against the shared `Store`. Commit tools
are declared so the proxy can republish their schemas, but they never act here: they execute only
through the gateway connector's REST calls. `get_quote` and `place_order` are not MCP tools.
"""

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from shop.store import ShopError, Store

READ_TOOLS = (
    "search_products",
    "get_product",
    "get_reviews",
    "get_saved_addresses",
    "get_order_history",
    "read_inbox",
    "read_email",
)
CART_TOOLS = ("add_to_cart", "remove_from_cart")
COMMIT_TOOLS = ("checkout", "share_cart", "send_email", "delete_saved_address", "cancel_order")
AGENT_TOOLS = (*READ_TOOLS, *CART_TOOLS, *COMMIT_TOOLS)

_COMMIT_MSG = "{tool} executes only through the IntentLock gateway, not directly on the shop"


def _run(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except ShopError as exc:
        raise ValueError(str(exc)) from exc


def build_mcp(store: Store) -> FastMCP:
    mcp = FastMCP("ShopLite")

    @mcp.tool()
    def search_products(query: str = "", max_price: int | None = None) -> dict:
        """Search the catalogue. max_price is in integer paise."""
        return {"results": store.search_products(query, max_price)}

    @mcp.tool()
    def get_product(sku: str) -> dict:
        """Full product page for a SKU."""
        return _run(lambda: store.get_product(sku))

    @mcp.tool()
    def get_reviews(sku: str) -> dict:
        """Customer reviews for a SKU."""
        return {"reviews": _run(lambda: store.get_reviews(sku))}

    @mcp.tool()
    def get_saved_addresses() -> dict:
        """The user's saved shipping addresses."""
        return {"addresses": store.get_saved_addresses()}

    @mcp.tool()
    def get_order_history() -> dict:
        """The user's past orders."""
        return {"orders": store.get_order_history()}

    @mcp.tool()
    def read_inbox() -> dict:
        """List emails in the inbox."""
        return {"emails": store.read_inbox()}

    @mcp.tool()
    def read_email(id: str) -> dict:
        """Read one email."""
        return _run(lambda: store.read_email(id))

    @mcp.tool()
    def add_to_cart(sku: str, qty: int = 1) -> dict:
        """Add a product to the cart."""
        return _run(lambda: store.add_to_cart(sku, qty))

    @mcp.tool()
    def remove_from_cart(sku: str) -> dict:
        """Remove a product from the cart."""
        return store.remove_from_cart(sku)

    @mcp.tool()
    def checkout(cart_id: str, shipping_address_id: str) -> dict:
        """Buy everything in the cart."""
        raise ValueError(_COMMIT_MSG.format(tool="checkout"))

    @mcp.tool()
    def share_cart(cart_id: str, to: str) -> dict:
        """Share the cart with someone by email."""
        raise ValueError(_COMMIT_MSG.format(tool="share_cart"))

    @mcp.tool()
    def send_email(to: str, subject: str, body: str) -> dict:
        """Send an email."""
        raise ValueError(_COMMIT_MSG.format(tool="send_email"))

    @mcp.tool()
    def delete_saved_address(id: str) -> dict:
        """Delete a saved address."""
        raise ValueError(_COMMIT_MSG.format(tool="delete_saved_address"))

    @mcp.tool()
    def cancel_order(id: str) -> dict:
        """Cancel an order."""
        raise ValueError(_COMMIT_MSG.format(tool="cancel_order"))

    return mcp
