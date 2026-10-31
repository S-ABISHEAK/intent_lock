"""Authoritative quote fetch and target resolution (SPEC §6 step 3, §14.2).

This is the only gateway module that reads SHOP_API_KEY (CLAUDE.md invariant 8). The key lives in a
private attribute and never reaches an error message or `repr`. Every failure raises
ConnectorError, which the pipeline maps to DENY `QUOTE_UNAVAILABLE` (invariant 2). Money comes only
from the shop's quote; callers pass identifiers, never prices (invariant 3). Execution (re-fetch,
approval checks, nonce consume, place_order) arrives with the approvals on Oct 30 (SPEC §6.2).
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote as urlquote
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from nacl.signing import VerifyKey
from pydantic import ValidationError

from intentlock.approvals.requests import Binding
from intentlock.approvals.verify import NonceStore, verify_approval
from intentlock.canonical import compute_quote_hash
from intentlock.models import ActionProposal, Approval, Decision, Quote, Reason
from intentlock.policy.reasons import make_reason
from intentlock.policy.tools import sanitize_args

TARGET_KINDS = ("address", "order")
CHECKOUT_VIEWPORT = {"width": 1280, "height": 1600}  # SPEC §11
OFFLINE_ARGS = ["--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>"]


def _launch_browser() -> tuple[Any, Any]:
    from playwright.sync_api import sync_playwright  # optional extra: .[browser]

    pw = sync_playwright().start()
    try:
        # every request, loopback included, goes to a closed port: the render is offline
        return pw, pw.chromium.launch(args=OFFLINE_ARGS)
    except Exception:
        pw.stop()
        raise


# Read and cart tools the proxy may forward to the shop MCP server. Commit tools never are (D-012).
FORWARDABLE_TOOLS = frozenset(
    {
        "search_products",
        "get_product",
        "get_reviews",
        "get_saved_addresses",
        "get_order_history",
        "read_inbox",
        "read_email",
        "add_to_cart",
        "remove_from_cart",
    }
)


class ConnectorError(Exception):
    """The authoritative shop state could not be obtained or did not check out."""

    def __init__(self, message: str, status: int | None = None, maybe_sent: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.maybe_sent = maybe_sent  # the request may have reached the shop (timeout after send)


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    reasons: list[Reason] = field(default_factory=list)
    result: dict[str, Any] | None = None
    uncertain: bool = False  # the merchant may have acted; reconcile before retrying


def _refused(code: str, detail: str = "") -> ExecutionResult:
    return ExecutionResult(executed=False, reasons=[make_reason(code, detail)])


class ShopConnector:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url
        self._api_key = os.environ.get("SHOP_API_KEY", "") if api_key is None else api_key
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def __repr__(self) -> str:
        return f"ShopConnector(base_url={self.base_url!r})"

    def _request(self, method: str, path: str, json: dict | None = None) -> dict[str, Any]:
        if not self._api_key:
            raise ConnectorError("shop credentials not configured")
        try:
            resp = self._client.request(
                method, path, json=json, headers={"X-API-Key": self._api_key}
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ConnectorError(f"shop unreachable: {type(exc).__name__}") from None
        except httpx.HTTPError as exc:  # sent, but no usable answer: the shop may have acted
            raise ConnectorError(
                f"shop did not answer: {type(exc).__name__}", maybe_sent=True
            ) from None
        if resp.status_code != 200:
            raise ConnectorError(f"shop returned {resp.status_code}", status=resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            raise ConnectorError("shop returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ConnectorError("shop returned an unexpected payload")
        return data

    def get_quote(self, cart_id: str, shipping_address_id: str) -> Quote:
        data = self._request(
            "POST", "/quote", {"cart_id": cart_id, "shipping_address_id": shipping_address_id}
        )
        try:
            quote = Quote.model_validate(data)
        except ValidationError:
            raise ConnectorError("shop returned an invalid quote") from None
        if not any(line.qty > 0 for line in quote.lines):
            raise ConnectorError("quote has no items")
        if compute_quote_hash(quote) != quote.quote_hash:
            raise ConnectorError("quote hash mismatch")
        parts = sum(line.unit_paise * line.qty for line in quote.lines)
        if parts + quote.fees_paise + quote.tax_paise != quote.total_paise:
            raise ConnectorError("quote total is not the sum of its lines, fees and tax")
        if quote.cart_id != cart_id:
            raise ConnectorError("quote is for a different cart")
        if quote.shipping_address_id != shipping_address_id:
            raise ConnectorError("quote is for a different address")
        return quote

    def render_checkout(self, quote: Quote, *, timeout_s: float = 20.0) -> bytes:
        """Full-page PNG of the quote's own checkout page on the shop (SPEC §11.1).

        The browser never holds the key and never touches the network (invariant 8): the
        connector fetches the HTML itself (no redirects followed), and Chromium renders that HTML
        offline with JavaScript off and every request aborted, so popups, redirects, refreshes and
        subresources go nowhere."""
        if not self._api_key:
            raise ConnectorError("shop credentials not configured")
        shop, page_url = urlsplit(self.base_url), urlsplit(quote.checkout_url)
        if (
            (page_url.scheme, page_url.netloc) != (shop.scheme, shop.netloc)
            or page_url.path != f"/checkout/{quote.cart_id}"
            or page_url.query
            or page_url.fragment
        ):
            raise ConnectorError("the checkout page is not the shop's page for this quote")
        try:
            resp = self._client.get(
                page_url.path,
                headers={"X-API-Key": self._api_key},
                timeout=timeout_s,
                follow_redirects=False,  # whatever client is injected
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"checkout page unreachable: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise ConnectorError(f"checkout page returned {resp.status_code}")
        if not resp.headers.get("content-type", "").startswith("text/html"):
            raise ConnectorError("checkout page is not HTML")
        try:
            pw, browser = _launch_browser()
        except Exception as exc:
            raise ConnectorError(f"browser unavailable: {type(exc).__name__}") from None
        try:
            context = browser.new_context(viewport=CHECKOUT_VIEWPORT, java_script_enabled=False)
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            context.on("page", lambda extra: extra is page or extra.close())  # no popups
            page.set_content(resp.text, timeout=timeout_s * 1000, wait_until="load")
            return page.screenshot(full_page=True, type="png", timeout=timeout_s * 1000)
        except Exception as exc:  # no browser detail
            raise ConnectorError(
                f"checkout page could not be rendered: {type(exc).__name__}"
            ) from None
        finally:
            browser.close()
            pw.stop()

    def reset_shop(self, attacks: list[str] | None = None) -> None:
        """Demo/eval only: initial state, optionally with SPEC §14.3 attack fixtures."""
        self._request("POST", "/reset", {"attacks": attacks} if attacks is not None else None)

    def resolve_target(self, kind: str, target_id: str) -> dict[str, Any]:
        """Confirm a destructive tool's target exists (SPEC §6 step 3)."""
        if kind not in TARGET_KINDS:
            raise ConnectorError(f"unknown target kind {kind!r}")
        return self._request("GET", f"/resolve/{kind}/{urlquote(target_id, safe='')}")

    # -- execution (SPEC §6.2) -----------------------------------------------------------------
    def execute(
        self,
        proposal: ActionProposal,
        decision: Decision,
        *,
        approval: Approval | None,
        expected: Binding | None,
        verify_key: VerifyKey,
        nonces: NonceStore,
        clock: Callable[[], float],
        mandate_expires_at: int | None = None,
    ) -> ExecutionResult:
        """Re-fetch, verify, consume, call. Never runs a DENY; a REVIEW needs a signed approval."""
        if decision.outcome == "DENY":
            raise ValueError("a DENY decision is never executed")
        tool = proposal.tool
        args = sanitize_args(tool, proposal.args)
        needs_approval = decision.outcome == "REVIEW"
        if needs_approval and approval is None:
            return _refused("APPROVAL_MISSING")
        if needs_approval and expected is None:
            raise ValueError("an approval can only be checked against the gateway's own binding")

        # 1. re-fetch the authoritative state and require it to be what was decided
        quote = None
        try:
            if tool == "checkout":
                quote = self.get_quote(str(args["cart_id"]), str(args["shipping_address_id"]))
                if quote.quote_hash != decision.quote_hash:
                    return _refused(
                        "QUOTE_CHANGED", "re-fetched quote differs from the decided one"
                    )
            elif tool == "delete_saved_address":
                self.resolve_target("address", str(args["id"]))
            elif tool == "cancel_order":
                self.resolve_target("order", str(args["id"]))
        except ConnectorError as exc:
            return _refused("QUOTE_UNAVAILABLE", str(exc))

        now = int(clock())  # read after the re-fetch, so a slow shop can't stretch an approval
        if mandate_expires_at is not None and now >= mandate_expires_at:
            return _refused("MANDATE_EXPIRED", "expired during the re-fetch")
        if needs_approval:
            # 2. signature, expiry and binding
            problems = verify_approval(approval, verify_key=verify_key, expected=expected, now=now)
            if problems:
                return ExecutionResult(executed=False, reasons=problems)
            # 3. single use: atomic UNIQUE insert
            if not nonces.consume(approval.nonce, now=now):
                return _refused("APPROVAL_REPLAYED")

        # 4. call the merchant with the connector-held key
        try:
            return ExecutionResult(executed=True, result=self._commit_call(tool, args, quote))
        except ConnectorError as exc:
            if exc.status == 409:
                return _refused("QUOTE_CHANGED", "shop refused a stale quote")
            if exc.maybe_sent:
                return ExecutionResult(
                    executed=False,
                    uncertain=True,
                    reasons=[make_reason("EXECUTION_FAILED", f"outcome unknown: {exc}")],
                )
            return _refused("EXECUTION_FAILED", str(exc))

    def _commit_call(self, tool: str, args: dict[str, Any], quote: Quote | None) -> dict[str, Any]:
        if tool == "checkout" and quote is not None:
            return self._request("POST", "/orders", {"quote_id": quote.quote_id})
        if tool == "share_cart":
            return self._request(
                "POST", "/share_cart", {"cart_id": args["cart_id"], "to": args["to"]}
            )
        if tool == "send_email":
            body = {"to": args["to"], "subject": args["subject"], "body": args["body"]}
            return self._request("POST", "/send_email", body)
        if tool == "delete_saved_address":
            return self._request("DELETE", f"/addresses/{urlquote(str(args['id']), safe='')}")
        if tool == "cancel_order":
            return self._request("POST", f"/orders/{urlquote(str(args['id']), safe='')}/cancel")
        raise ConnectorError(f"no merchant call for tool {tool!r}")

    # -- read/cart forwarding over the shop's MCP endpoint (key stays here, D-012) ----------------
    async def call_shop_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in FORWARDABLE_TOOLS:
            raise ConnectorError(f"tool {name!r} is not forwarded to the shop")
        if not self._api_key:
            raise ConnectorError("shop credentials not configured")
        clean = {k: v for k, v in args.items() if v is not None}
        try:
            async with (
                httpx.AsyncClient(headers={"X-API-Key": self._api_key}, timeout=10) as http,
                streamable_http_client(f"{self.base_url}/mcp", http_client=http) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(name, clean)
        except Exception as exc:  # transport or protocol failure: fail closed, no key in message
            raise ConnectorError(f"shop MCP unavailable: {type(exc).__name__}") from None
        text = result.content[0].text if result.content else ""
        if result.isError:
            raise ConnectorError(text[:300] or "shop tool error")
        try:
            data = json.loads(text)
        except ValueError:
            raise ConnectorError("shop tool returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ConnectorError("shop tool returned an unexpected payload")
        return data
