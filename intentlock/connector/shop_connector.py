"""Authoritative quote fetch and target resolution (SPEC §6 step 3, §14.2).

This is the only gateway module that reads SHOP_API_KEY (CLAUDE.md invariant 8). The key lives in a
private attribute and never reaches an error message or `repr`. Every failure raises
ConnectorError, which the pipeline maps to DENY `QUOTE_UNAVAILABLE` (invariant 2). Money comes only
from the shop's quote; callers pass identifiers, never prices (invariant 3). Execution (re-fetch,
approval checks, nonce consume, place_order) arrives with the approvals on Oct 30 (SPEC §6.2).
"""

import os
from typing import Any
from urllib.parse import quote as urlquote

import httpx
from pydantic import ValidationError

from intentlock.canonical import compute_quote_hash
from intentlock.models import Quote

TARGET_KINDS = ("address", "order")


class ConnectorError(Exception):
    """The authoritative shop state could not be obtained or did not check out."""


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
        except httpx.HTTPError as exc:
            raise ConnectorError(f"shop unreachable: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise ConnectorError(f"shop returned {resp.status_code}")
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

    def resolve_target(self, kind: str, target_id: str) -> dict[str, Any]:
        """Confirm a destructive tool's target exists (SPEC §6 step 3)."""
        if kind not in TARGET_KINDS:
            raise ConnectorError(f"unknown target kind {kind!r}")
        return self._request("GET", f"/resolve/{kind}/{urlquote(target_id, safe='')}")
