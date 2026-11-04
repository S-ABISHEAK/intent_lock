import { useMemo, useState } from "react";

import type { Receipt } from "../api";
import { approvalNote, executionCodes, executionStatus, type Grouped, policyStatus, type Story, storySummary } from "../logic/stories";
import { rupees } from "../money";
import { CheckoutTruth, ExecutionBadge, plain, PolicyBadge, ReasonList, ReceiptIntegrity } from "./security";
import { EmptyState, Icon } from "./ui";

const VERB: Record<string, string> = {
  checkout: "Buy the cart",
  share_cart: "Share the cart",
  send_email: "Send an email",
  delete_saved_address: "Delete a saved address",
  cancel_order: "Cancel an order",
};

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="grid grid-cols-[1.5rem_1fr] gap-2">
      <span className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-soft text-[11px] font-semibold text-muted">{n}</span>
      <div className="min-w-0">
        <div className="label">{title}</div>
        <div className="mt-1 text-sm">{children}</div>
      </div>
    </li>
  );
}

function reasonsOf(r: Receipt | null | undefined): { code: string; detail?: string }[] {
  const rs = r?.payload?.reasons;
  return Array.isArray(rs) ? rs.map((x: any) => (typeof x === "string" ? { code: x } : x)) : [];
}

function ActionStory({ s }: { s: Story }) {
  const [open, setOpen] = useState(false);
  const policy = policyStatus(s);
  const exec = executionStatus(s);
  const p = s.proposal.payload;
  const reasons = reasonsOf(s.decision);
  const primary = reasons.find((r) => r.code !== "ALLOWED_IN_MANDATE") ?? reasons[0];
  const summary = storySummary(s);
  const finished = [...s.executions].reverse().find((e) => e.payload?.event === "finished" || e.payload?.event === "refused");
  const dropped: string[] = Array.isArray(p.dropped_args) ? p.dropped_args : [];
  const last = [s.decision, ...s.approvals, ...s.executions].filter(Boolean).at(-1) as Receipt | undefined;
  return (
    <li className={`card overflow-hidden ${policy === "DENY" ? "border-l-4 border-l-deny" : exec === "UNKNOWN" ? "border-l-4 border-l-review" : ""}`} data-testid="story">
      <button type="button" className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 p-3 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="chevron" className={`h-4 w-4 text-muted ${open ? "rotate-90" : ""}`} />
        <span className="font-semibold">{VERB[p.tool] ?? p.tool}</span>
        {typeof summary?.total_paise === "number" && <span className="text-sm text-muted tabular-nums">{rupees(summary.total_paise)}</span>}
        <span className="flex flex-wrap items-center gap-1.5" data-testid="story-status">
          <PolicyBadge outcome={policy} />
          <span className="text-muted" aria-hidden="true">·</span>
          <ExecutionBadge status={exec} tool={p.tool} />
        </span>
        <span className="ml-auto text-xs text-muted tabular-nums">{time(s.proposal.ts)}</span>
        {primary && <span className="w-full pl-7 text-sm text-muted">{plain(primary.code)}</span>}
        {policy === "DENY" && <span className="w-full pl-7 text-sm font-semibold text-deny">Blocked by IntentLock: this action cannot be approved</span>}
      </button>
      {open && (
        <ol className="space-y-4 border-t border-line bg-sunken/50 p-4">
          <Step n={1} title="Agent proposal">
            <code className="break-all">{p.tool}({JSON.stringify(p.args ?? {})})</code>
            {dropped.length > 0 && <div className="mt-1 text-xs text-review">Also sent {dropped.join(", ")}: ignored by the gateway (it never trusts agent-supplied money)</div>}
          </Step>
          <Step n={2} title="Gateway evaluation">
            {reasons.length ? <ReasonList reasons={reasons} /> : <span className="text-muted">No decision receipt yet.</span>}
          </Step>
          {summary?.tool === "checkout" && (
            <Step n={3} title="Merchant evidence">
              <CheckoutTruth summary={summary} execution={exec} codes={executionCodes(s)} reconciled={finished?.payload?.reconciled} />
            </Step>
          )}
          <Step n={summary?.tool === "checkout" ? 4 : 3} title="Policy decision and execution">
            <div className="flex flex-wrap items-center gap-2">
              <PolicyBadge outcome={policy} />
              <ExecutionBadge status={exec} tool={p.tool} />
            </div>
            {approvalNote(s) && <div className="mt-1 text-muted">{approvalNote(s)}</div>}
            {exec === "EXECUTED" && p.tool === "checkout" && <div className="mt-1 text-xs text-muted">Payment settlement not independently verified.</div>}
            {exec === "UNKNOWN" && <div className="mt-1 text-xs text-review">The merchant's answer was lost. IntentLock reports it as unknown, never as done{finished?.payload?.reconciled ? `; reconciliation: ${finished.payload.reconciled}` : ""}.</div>}
            {executionCodes(s).length > 0 && <div className="mt-2"><ReasonList reasons={reasonsOf(finished)} /></div>}
            {s.errors.map((e) => <div key={e.seq} className="mt-1 text-deny">Error: {String(e.payload?.error ?? "")}</div>)}
          </Step>
          <Step n={summary?.tool === "checkout" ? 5 : 4} title="Tamper-evident receipts">
            <span className="font-mono text-xs text-muted">
              seq {s.proposal.seq}{last ? `–${last.seq}` : ""} · last hash {(last ?? s.proposal).row_hash.slice(0, 12)}…
            </span>
          </Step>
        </ol>
      )}
    </li>
  );
}

function RawLine({ r }: { r: Receipt }) {
  const p = r.payload ?? {};
  const text = r.kind === "tool_result" ? `${p.tool ?? "read"}${p.error ? ` (error: ${p.error})` : ""}` : `${p.event ?? p.tool ?? ""} ${p.error ?? ""}`;
  return (
    <li className="flex gap-3 px-3 py-1.5 text-xs">
      <span className="w-10 shrink-0 text-right font-mono text-muted">#{r.seq}</span>
      <span className="w-20 shrink-0 text-muted uppercase">{r.kind.replace("_", " ")}</span>
      <span className="min-w-0 flex-1 break-words">{text}</span>
    </li>
  );
}

type Filter = "all" | "DENY" | "REVIEW" | "ALLOW" | "UNKNOWN";

export function ActivityFeed({ grouped, hasSession, stale }: { grouped: Grouped; hasSession: boolean; stale: boolean }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [reads, setReads] = useState(false);
  const stories = useMemo(() => {
    const all = [...grouped.stories].reverse(); // newest first
    if (filter === "all") return all;
    if (filter === "UNKNOWN") return all.filter((s) => executionStatus(s) === "UNKNOWN");
    return all.filter((s) => policyStatus(s) === filter);
  }, [grouped.stories, filter]);
  const shown = stories.slice(0, 200);

  return (
    <section className="space-y-3" aria-labelledby="activity-title">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="activity-title" className="text-lg font-semibold">Agent activity</h2>
        {stale && <span className="rounded bg-review-soft px-2 py-0.5 text-xs font-semibold text-review">stale: not live</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-sm">
            <span className="text-muted">Show</span>
            <select className="rounded-md border border-line bg-surface px-2 py-1 text-sm" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
              <option value="all">all actions</option>
              <option value="DENY">denied</option>
              <option value="REVIEW">reviewed</option>
              <option value="ALLOW">allowed</option>
              <option value="UNKNOWN">outcome unknown</option>
            </select>
          </label>
          <ReceiptIntegrity />
        </div>
      </div>

      {!hasSession && grouped.stories.length === 0 ? (
        <EmptyState title="No session yet">Write and confirm a mandate first; the agent's actions will appear here.</EmptyState>
      ) : grouped.stories.length === 0 ? (
        <EmptyState title="Waiting for the agent">Its first proposed action will appear here.</EmptyState>
      ) : (
        <ol className="space-y-2" aria-live="polite">
          {shown.map((s) => <ActionStory key={s.id} s={s} />)}
          {stories.length > shown.length && <li className="text-center text-xs text-muted">Showing the latest 200 of {stories.length} actions.</li>}
          {shown.length === 0 && <li className="text-sm text-muted">No actions match this filter.</li>}
        </ol>
      )}

      {grouped.unlinked.length > 0 && (
        <details className="card">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Unlinked events ({grouped.unlinked.length}): receipts with no matching action id</summary>
          <ol className="divide-y divide-line border-t border-line">{grouped.unlinked.map((r) => <RawLine key={r.seq} r={r} />)}</ol>
        </details>
      )}

      <div className="card">
        <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium" aria-expanded={reads} onClick={() => setReads(!reads)}>
          <Icon name="eye" /> {reads ? "Hide" : "Show"} what the agent read ({grouped.reads.length})
        </button>
        {reads && <ol className="max-h-80 divide-y divide-line overflow-y-auto border-t border-line">{grouped.reads.map((r) => <RawLine key={r.seq} r={r} />)}</ol>}
      </div>
    </section>
  );
}
