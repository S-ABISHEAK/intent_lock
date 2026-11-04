import type { Receipt } from "../api";

// An action story is every receipt of one proposed action, linked by `action_id` only (D-060).
// Nothing is ever attached by position or time: a receipt whose id is missing, malformed, or
// names no proposal in the list is an unlinked event, shown on its own.
//
// Two separate dimensions, never merged:
//   policy    : the gateway's decision receipt (ALLOW / REVIEW / DENY)
//   execution : what the approval and execution receipts prove happened
// ALLOW never implies EXECUTED; only an `executed: true` receipt does.

export const ACTION_ID = /^[0-9a-f]{32}$/;

export type Story = {
  id: string;
  proposal: Receipt;
  decision: Receipt | null;
  approvals: Receipt[];
  executions: Receipt[];
  errors: Receipt[];
};

export type Grouped = {
  stories: Story[]; // in proposal order
  unlinked: Receipt[];
  reads: Receipt[];
  sessions: Receipt[];
};

export function validId(v: unknown): v is string {
  return typeof v === "string" && ACTION_ID.test(v);
}

export function groupReceipts(receipts: Receipt[]): Grouped {
  const byId = new Map<string, Story>();
  const out: Grouped = { stories: [], unlinked: [], reads: [], sessions: [] };
  for (const r of receipts) {
    if (r.kind === "tool_result") {
      out.reads.push(r);
      continue;
    }
    if (r.kind === "mandate") {
      out.sessions.push(r);
      continue;
    }
    const id = r.payload?.action_id;
    if (r.kind === "proposal") {
      if (!validId(id) || byId.has(id)) {
        out.unlinked.push(r); // never merge two proposals into one story
        continue;
      }
      const s: Story = { id, proposal: r, decision: null, approvals: [], executions: [], errors: [] };
      byId.set(id, s);
      out.stories.push(s);
      continue;
    }
    const story = validId(id) ? byId.get(id) : undefined;
    if (!story) {
      out.unlinked.push(r);
      continue;
    }
    if (r.kind === "decision") {
      if (story.decision) out.unlinked.push(r);
      else story.decision = r;
    } else if (r.kind === "approval") story.approvals.push(r);
    else if (r.kind === "execution") story.executions.push(r);
    else story.errors.push(r);
  }
  return out;
}

export type Policy = "ALLOW" | "REVIEW" | "DENY";
export type Execution = "PENDING" | "EXECUTED" | "REFUSED" | "FAILED" | "UNKNOWN" | "NOT_EXECUTED";

export function policyStatus(s: Story): Policy | null {
  const o = s.decision?.payload?.outcome;
  return o === "ALLOW" || o === "REVIEW" || o === "DENY" ? o : null;
}

function codesOf(r: Receipt): string[] {
  const rs = r.payload?.reasons;
  return Array.isArray(rs) ? rs.map((c: any) => (typeof c === "string" ? c : String(c?.code ?? ""))) : [];
}

export function executionStatus(s: Story): Execution {
  const done = [...s.executions].reverse().find((r) => r.payload?.event === "finished" || r.payload?.event === "refused");
  if (done) {
    const ex = done.payload.executed;
    if (ex === true) return "EXECUTED";
    if (ex === "unknown") return "UNKNOWN";
    if (ex === false) return codesOf(done).includes("EXECUTION_FAILED") ? "FAILED" : "REFUSED";
    return "UNKNOWN"; // a finished receipt with no readable result proves nothing either way
  }
  if (s.approvals.some((a) => a.payload?.event === "not_approved")) return "NOT_EXECUTED";
  if (policyStatus(s) === "DENY") return "NOT_EXECUTED";
  return "PENDING";
}

export function executionCodes(s: Story): string[] {
  const done = [...s.executions].reverse().find((r) => r.payload?.event === "finished" || r.payload?.event === "refused");
  return done ? codesOf(done) : [];
}

/** The quote evidence a story's approval card showed (the gateway's summary), if any. */
export function storySummary(s: Story): Record<string, any> | null {
  const req = s.approvals.find((a) => a.payload?.event === "requested");
  return (req?.payload?.summary as Record<string, any>) ?? null;
}

export function approvalNote(s: Story): string | null {
  const events = s.approvals.map((a) => a.payload?.event);
  if (events.includes("signed")) return "You approved (signed by the approver)";
  // the gateway writes the same receipt for a rejection and for an expiry
  if (events.includes("not_approved")) return "Not approved (rejected, or the card expired)";
  if (events.includes("requested")) return "Waiting for your approval";
  return null;
}
