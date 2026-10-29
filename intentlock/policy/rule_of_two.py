"""Rule-of-Two session flags and the `r2_session` rule (SPEC §8, pipeline step 7).

A: an UNTRUSTED record was returned to the agent. B: a SENSITIVE tool was called (checkout implies
B, since it uses stored payment details). C: the current action is a commit tool. Tool-level labels
follow the SPEC §7.1 table. Step 7 runs only in `r2_session`; in `intentlock` the flags are recorded
for the UI but never decide anything.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from intentlock.models import Reason
from intentlock.policy.reasons import make_reason
from intentlock.policy.tools import COMMIT_TOOLS

if TYPE_CHECKING:
    from intentlock.policy.context import DecisionContext

UNTRUSTED_TOOLS = frozenset(
    {"get_product", "get_reviews", "read_inbox", "read_email", "fetch_page"}
)
SENSITIVE_TOOLS = frozenset({"get_saved_addresses", "get_order_history", "get_contacts"})


@dataclass(frozen=True)
class SessionFlags:
    untrusted_input: bool = False  # A
    sensitive_access: bool = False  # B

    def after_tool(self, tool: str) -> SessionFlags:
        """Flags after `tool` successfully returned a result to the agent."""
        return replace(
            self,
            untrusted_input=self.untrusted_input or tool in UNTRUSTED_TOOLS,
            sensitive_access=self.sensitive_access or tool in SENSITIVE_TOOLS,
        )

    def for_action(self, tool: str) -> tuple[bool, bool, bool]:
        a = self.untrusted_input
        b = self.sensitive_access or tool == "checkout"
        c = tool in COMMIT_TOOLS
        return a, b, c


def step_rule_of_two(ctx: DecisionContext) -> list[Reason]:
    if ctx.flags is None:
        raise ValueError("the Rule-of-Two step needs the session flags")
    a, b, c = ctx.flags.for_action(ctx.proposal.tool)
    return [make_reason("RULE_OF_TWO", "A,B,C")] if a and b and c else []
