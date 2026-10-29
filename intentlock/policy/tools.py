"""Commit tools and their identifier args (SPEC §6, §14.2). Anything else the agent sends,
including price/total/amount, is dropped by `sanitize_args` (CLAUDE.md invariant 3)."""

from collections.abc import Mapping

TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "checkout": ("cart_id", "shipping_address_id"),
    "send_email": ("to", "subject", "body"),
    "share_cart": ("cart_id", "to"),
    "delete_saved_address": ("id",),
    "cancel_order": ("id",),
}
FREE_TEXT_ARGS = ("subject", "body")  # may be empty; all other args must be non-blank
COMMIT_TOOLS = tuple(TOOL_ARGS)


def sanitize_args(tool: str, args: Mapping[str, object]) -> dict[str, object]:
    return {k: args[k] for k in TOOL_ARGS[tool] if k in args}
