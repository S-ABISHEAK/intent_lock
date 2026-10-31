"""IntentLock MCP proxy (SPEC §3, §6, §8, §10; CLAUDE.md invariants 2, 3, 9, 11, 12).

The agent sees only these tools. Read and cart tools are forwarded to the shop through the
connector (which holds the key); commit tools are never forwarded: they go through the decision
pipeline, an approval if REVIEW, and connector execution. Every proposal, decision, approval and
execution is receipted. Every result is labeled per field and written to the provenance ledger,
with §7.4.1 propagation, before the agent sees it (invariant 9); if that fails the agent gets an
error, never unlabeled content. The gateway installs taint step 6 from its own ledger.
"""

import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from nacl.signing import VerifyKey

from intentlock.approvals.requests import binding_for, new_request
from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.mandate.grounding import granted_addons
from intentlock.models import ActionProposal, Approval, ApprovalRequest, Decision, Mandate
from intentlock.policy.pipeline import StepFn, decide
from intentlock.policy.rule_of_two import SessionFlags
from intentlock.policy.tools import TOOL_ARGS, sanitize_args
from intentlock.provenance.labels import annotate
from intentlock.provenance.ledger import Ledger
from intentlock.provenance.taint import label_call, make_taint_step, normalize_value, user_records
from intentlock.receipts import ReceiptLog
from intentlock.verify.promptguard import Scorer, make_promptguard_step, score_records
from intentlock.verify.two_channel import PageReader, make_two_channel_step

log = logging.getLogger(__name__)

FORWARD_TOOLS = frozenset(
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
Waiter = Callable[[ApprovalRequest], Approval | None]


class GatewayBusy(Exception):
    """A commit is in flight; the session can't be replaced until it finishes or is rejected."""


class QuoteMoved(Exception):
    """The quote changed between the decision and showing the approval card."""


ShopTools = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def receipt_safe(value: Any) -> Any:
    """Agent-supplied values made safe for canonical receipts (floats and odd types to strings)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, dict):
        return {str(k): receipt_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [receipt_safe(v) for v in value]
    return repr(value)


@dataclass
class Session:
    session_id: str
    mandate: Mandate | None
    flags: SessionFlags = field(default_factory=SessionFlags)
    added_skus: list[str] = field(default_factory=list)  # every add_to_cart, for the record


class Gateway:
    def __init__(
        self,
        *,
        config: str,
        connector: ShopConnector | None,
        receipts: ReceiptLog,
        ledger: Ledger,
        nonces: NonceStore,
        verify_key: VerifyKey,
        waiter: Waiter,
        extra_steps: Mapping[int, StepFn],
        features: frozenset[int],
        clock: Callable[[], float] = time.time,
        shop_tools: ShopTools | None = None,
        scorer: Scorer | None = None,
        promptguard_status: str | None = None,
        page_reader: PageReader | None = None,
        two_channel_status: str | None = None,
    ) -> None:
        self.config = config
        self.connector = connector
        self.receipts = receipts
        self.nonces = nonces
        self.verify_key = verify_key
        self.waiter = waiter
        if 6 in extra_steps:
            raise ValueError("the gateway installs the taint step from its own ledger")
        self.ledger = ledger
        self.extra_steps = {6: make_taint_step(ledger), **extra_steps}
        if 9 in features:  # Prompt Guard (D-021, D-034): built from our own ledger and scorer
            if scorer is None or 9 in extra_steps:
                raise ValueError(
                    "step 9 needs a Prompt Guard scorer and is installed by the gateway"
                )
            self.extra_steps[9] = make_promptguard_step(ledger)
        if 8 in features:  # two-channel (SPEC §11): built from the connector's page reader
            if page_reader is None or 8 in extra_steps:
                raise ValueError("step 8 needs a page reader and is installed by the gateway")
            self.extra_steps[8] = make_two_channel_step(page_reader, {})
        self.two_channel_status = two_channel_status or ("on" if 8 in features else "off")
        self.scorer = scorer
        self.promptguard_status = promptguard_status or ("on" if 9 in features else "off")
        self.features = frozenset(features)
        self.clock = clock
        self.shop_tools = shop_tools or (connector.call_shop_tool if connector else None)
        self.session: Session | None = None
        self.summaries: dict[str, dict[str, Any]] = {}  # approval_id -> what the card shows
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._in_flight > 0

    def now(self) -> int:
        return int(self.clock())

    # -- sessions -------------------------------------------------------------------------------
    def start_session(self, mandate: Mandate | None) -> str:
        session_id = uuid.uuid4().hex
        with self._lock:
            if self._in_flight:
                raise GatewayBusy("a commit is in progress")
            self.session = Session(session_id=session_id, mandate=mandate)
        payload = {
            "session_id": session_id,
            "config": self.config,
            "promptguard": self.promptguard_status,
            "two_channel": self.two_channel_status,
        }
        payload["mandate"] = mandate.model_dump(mode="json") if mandate else None
        if mandate is not None:
            self.ledger.add(user_records(session_id, mandate.user_text, ts=self.now()))
        self.receipts.append("mandate", payload)
        return session_id

    def end_session(self) -> None:
        """Operator stop: no tools until a new session; waiting commits won't execute."""
        with self._lock:
            ended = self.session
            self.session = None
        if ended is not None:
            self.receipts.append("mandate", {"session_id": ended.session_id, "event": "ended"})

    # -- entry point from the MCP surface -----------------------------------------------------
    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            return {"error": "no active IntentLock session"}
        if name in FORWARD_TOOLS:
            with self._lock:
                self._in_flight += 1  # start_session waits for reads too (checkpoint review LOW)
            try:
                return await self._forward(self.session, name, args)
            finally:
                with self._lock:
                    self._in_flight -= 1
        return await anyio.to_thread.run_sync(self.commit, self.session, name, args)

    async def _forward(self, session: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
        sid = session.session_id
        record: dict[str, Any] = {"session_id": sid, "tool": name, "args": receipt_safe(args)}
        error = False
        try:
            result = await self.shop_tools(name, args)
        except ConnectorError as exc:
            result, error = {"error": str(exc)}, True
        except Exception as exc:  # anything unexpected is handled like a shop error, fail closed
            log.exception("forwarding %s failed", name)
            result, error = {"error": f"shop tool failed: {type(exc).__name__}"}, True
        if self.session is not session:  # replaced while we waited: nothing for a dead session
            self.receipts.append("tool_result", {**record, "error": "session replaced"})
            return {"error": "the session changed; result withheld"}
        # Label before delivery (invariant 9): nothing reaches the agent unless it is ledgered.
        try:
            seq = self.ledger.next_call_seq(sid)
            records = label_call(
                name, args, result, self.ledger.index(sid), session_id=sid, call_seq=seq,
                ts=self.now(), error=error,
            )  # fmt: skip
            if self.scorer is not None:  # scored before delivery, like the labels (SPEC §12)
                records = score_records(records, self.scorer)
            self.ledger.add(records)
        except Exception as exc:
            log.exception("could not record provenance; withholding the result")
            self.receipts.append("error", {**record, "error": type(exc).__name__})
            return {"error": "result withheld: provenance could not be recorded"}
        record["call_seq"] = seq
        if error:
            self.receipts.append("tool_result", {**record, "error": result["error"]})
            return {"error": result["error"]}
        session.flags = session.flags.after_tool(name)
        if name == "add_to_cart":
            session.added_skus.append(str(args.get("sku")))
        self.receipts.append("tool_result", {**record, "result": receipt_safe(result)})
        return annotate(name, result)

    def requested_skus(self, session: Session) -> frozenset[str]:
        """D-031: add-ons the agent added that the user explicitly asked for through a grounded
        `requested_addon` grant. Neither the agent adding it nor a mention in text is enough."""
        added = {normalize_value(sku) for sku in session.added_skus}
        return frozenset(added & granted_addons(session.mandate))

    # -- commit tools ---------------------------------------------------------------------------
    def _error(self, proposal: ActionProposal, exc: BaseException) -> None:
        self.receipts.append(
            "error",
            {"action_id": proposal.action_id, "tool": proposal.tool, "error": type(exc).__name__},
        )

    def commit(self, session: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
        proposal = ActionProposal(
            action_id=uuid.uuid4().hex,
            session_id=session.session_id,
            tool=name,
            args=args,
            ts=self.now(),
        )
        with self._lock:
            self._in_flight += 1
        try:
            return self._commit(session, proposal)
        except Exception as exc:  # fail closed: anything unexpected is a DENY plus an error receipt
            log.exception("commit path failed")
            try:
                self._error(proposal, exc)
            except Exception:
                log.exception("could not write the error receipt")
            return {"status": "denied", "reasons": ["PIPELINE_ERROR"]}
        finally:
            with self._lock:
                self._in_flight -= 1

    def _summary(self, proposal: ActionProposal, decision: Decision, kept: dict) -> dict[str, Any]:
        """What the approval card shows (SPEC §10). For checkout it is the authoritative quote,
        re-fetched and required to match the decided hash."""
        summary: dict[str, Any] = {
            "tool": proposal.tool,
            "args": receipt_safe(kept),
            "reasons": [r.code for r in decision.reasons],
        }
        if proposal.tool == "checkout":
            q = self.connector.get_quote(str(kept["cart_id"]), str(kept["shipping_address_id"]))
            if q.quote_hash != decision.quote_hash:
                raise QuoteMoved
            summary.update(
                lines=[line.model_dump(mode="json") for line in q.lines],
                fees_paise=q.fees_paise,
                tax_paise=q.tax_paise,
                total_paise=q.total_paise,
                recurring=[r.model_dump(mode="json") for r in q.recurring],
                shipping_address_id=q.shipping_address_id,
                shipping_address=self._saved_address(q.shipping_address_id),
            )
        return summary

    def _saved_address(self, address_id: str) -> dict[str, str] | None:
        """The saved address behind an id, or None if it is not one of the user's (P6)."""
        try:
            found = self.connector.resolve_target("address", address_id)
        except ConnectorError:
            return None
        return {"label": str(found.get("label", "")), "line": str(found.get("line", ""))}

    def _commit(self, session: Session, proposal: ActionProposal) -> dict[str, Any]:
        known = proposal.tool in TOOL_ARGS
        kept = sanitize_args(proposal.tool, proposal.args) if known else {}
        self.receipts.append(
            "proposal",
            {
                "action_id": proposal.action_id,
                "session_id": session.session_id,
                "tool": proposal.tool,
                "args": receipt_safe(kept),
                "dropped_args": sorted(k for k in proposal.args if k not in kept),
                "flags": {
                    "A": session.flags.untrusted_input,
                    "B": session.flags.sensitive_access,
                },
            },
        )
        decision = decide(
            proposal,
            session.mandate,
            self.config,
            connector=self.connector,
            now=self.now(),
            requested_skus=self.requested_skus(session),
            extra_steps=self.extra_steps,
            error_sink=self._error,
            flags=session.flags,
            features=self.features,
        )
        self.receipts.append("decision", decision.model_dump(mode="json"))
        codes = [r.code for r in decision.reasons]
        if decision.outcome == "DENY":
            return {"status": "denied", "reasons": codes}

        approval, expected = None, None
        if decision.outcome == "REVIEW":
            try:
                summary = self._summary(proposal, decision, kept)
            except QuoteMoved:
                return self._refuse_execution(
                    proposal, "QUOTE_CHANGED", "before the user was asked"
                )
            except ConnectorError as exc:
                return self._refuse_execution(proposal, "QUOTE_UNAVAILABLE", str(exc))
            req = new_request(
                session.mandate, session.session_id, proposal, decision, now=self.now()
            )
            self.summaries[req.approval_id] = summary
            self.receipts.append(
                "approval", {"event": "requested", "request": req.model_dump(), "summary": summary}
            )
            try:
                approval = self.waiter(req)
            finally:
                self.summaries.pop(req.approval_id, None)
            if approval is None:
                self.receipts.append(
                    "approval", {"event": "not_approved", "approval_id": req.approval_id}
                )
                return {"status": "not_approved", "reasons": codes}
            self.receipts.append("approval", {"event": "signed", "approval": approval.model_dump()})
            if (approval.approval_id, approval.nonce, approval.expires_at) != (
                req.approval_id,
                req.nonce,
                req.expires_at,
            ):
                return self._refuse_execution(
                    proposal, "APPROVAL_MISMATCH", "not the issued request"
                )
            expected = binding_for(session.mandate, session.session_id, proposal, decision)

        # Re-check what may have changed while waiting (review LOW: decision-time TOCTOU).
        if self.session is not session:
            return self._refuse_execution(proposal, "SESSION_REPLACED")
        if session.mandate is not None and self.config != "none":
            if not session.mandate.user_confirmed:
                return self._refuse_execution(proposal, "MANDATE_UNCONFIRMED")
            if self.now() >= session.mandate.expires_at:
                return self._refuse_execution(proposal, "MANDATE_EXPIRED")

        self.receipts.append("execution", {"action_id": proposal.action_id, "event": "started"})
        result = self.connector.execute(
            proposal,
            decision,
            approval=approval,
            expected=expected,
            verify_key=self.verify_key,
            nonces=self.nonces,
            clock=self.clock,
            mandate_expires_at=(
                session.mandate.expires_at
                if session.mandate is not None and self.config != "none"
                else None
            ),
        )
        exec_codes = [r.code for r in result.reasons]
        if result.executed:
            shown = self._label_execution(session, proposal, result.result or {})
            response: dict[str, Any] = {"status": "executed", "reasons": [], "result": shown}
        elif result.uncertain:
            response = {"status": "unknown", "reasons": exec_codes}
        else:
            response = {"status": "denied", "reasons": exec_codes}
        try:
            self.receipts.append(
                "execution",
                {
                    "action_id": proposal.action_id,
                    "event": "finished",
                    "executed": "unknown" if result.uncertain else result.executed,
                    "reasons": exec_codes,
                    "result": receipt_safe(result.result),
                },
            )
        except Exception:  # the merchant call already happened: report the truth, flag the gap
            log.exception("execution receipt failed after the merchant call")
            response["receipt_error"] = True
        return response

    def _label_execution(
        self, session: Session, proposal: ActionProposal, result: dict[str, Any]
    ) -> dict[str, Any] | str:
        """Merchant results are labeled and ledgered like any other result (invariant 9). They
        echo the agent's own choices, so they default to UNTRUSTED and never clean anything."""
        sid = session.session_id
        try:
            seq = self.ledger.next_call_seq(sid)
            records = label_call(
                proposal.tool, {}, result, self.ledger.index(sid), session_id=sid,
                call_seq=seq, ts=self.now(),
            )  # fmt: skip
            if self.scorer is not None:  # merchant echoes are untrusted text too
                records = score_records(records, self.scorer)
            self.ledger.add(records)
        except Exception:
            log.exception("could not record provenance for an execution result")
            return "withheld: provenance could not be recorded (the action did execute)"
        return annotate(proposal.tool, result)

    def _refuse_execution(self, proposal: ActionProposal, code: str, detail: str = "") -> dict:
        self.receipts.append(
            "execution",
            {
                "action_id": proposal.action_id,
                "event": "refused",
                "executed": False,
                "reasons": [code],
                "detail": detail,
            },
        )
        return {"status": "denied", "reasons": [code]}


def build_gateway_mcp(gateway: Gateway) -> FastMCP:
    """Agent-facing MCP server: same tool names and argument schemas as the shop (SPEC §14.2)."""
    mcp = FastMCP("IntentLock")

    @mcp.tool()
    async def search_products(query: str = "", max_price: int | None = None) -> dict:
        """Search the catalogue. max_price is in integer paise."""
        return await gateway.call_tool("search_products", {"query": query, "max_price": max_price})

    @mcp.tool()
    async def get_product(sku: str) -> dict:
        """Full product page for a SKU."""
        return await gateway.call_tool("get_product", {"sku": sku})

    @mcp.tool()
    async def get_reviews(sku: str) -> dict:
        """Customer reviews for a SKU."""
        return await gateway.call_tool("get_reviews", {"sku": sku})

    @mcp.tool()
    async def get_saved_addresses() -> dict:
        """The user's saved shipping addresses."""
        return await gateway.call_tool("get_saved_addresses", {})

    @mcp.tool()
    async def get_order_history() -> dict:
        """The user's past orders."""
        return await gateway.call_tool("get_order_history", {})

    @mcp.tool()
    async def read_inbox() -> dict:
        """List emails in the inbox."""
        return await gateway.call_tool("read_inbox", {})

    @mcp.tool()
    async def read_email(id: str) -> dict:
        """Read one email."""
        return await gateway.call_tool("read_email", {"id": id})

    @mcp.tool()
    async def add_to_cart(sku: str, qty: int = 1) -> dict:
        """Add a product to the cart."""
        return await gateway.call_tool("add_to_cart", {"sku": sku, "qty": qty})

    @mcp.tool()
    async def remove_from_cart(sku: str) -> dict:
        """Remove a product from the cart."""
        return await gateway.call_tool("remove_from_cart", {"sku": sku})

    @mcp.tool()
    async def checkout(cart_id: str, shipping_address_id: str) -> dict:
        """Buy everything in the cart."""
        return await gateway.call_tool(
            "checkout", {"cart_id": cart_id, "shipping_address_id": shipping_address_id}
        )

    @mcp.tool()
    async def share_cart(cart_id: str, to: str) -> dict:
        """Share the cart with someone by email."""
        return await gateway.call_tool("share_cart", {"cart_id": cart_id, "to": to})

    @mcp.tool()
    async def send_email(to: str, subject: str, body: str) -> dict:
        """Send an email."""
        return await gateway.call_tool("send_email", {"to": to, "subject": subject, "body": body})

    @mcp.tool()
    async def delete_saved_address(id: str) -> dict:
        """Delete a saved address."""
        return await gateway.call_tool("delete_saved_address", {"id": id})

    @mcp.tool()
    async def cancel_order(id: str) -> dict:
        """Cancel an order."""
        return await gateway.call_tool("cancel_order", {"id": id})

    return mcp
