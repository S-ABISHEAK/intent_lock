import { useState } from "react";

import { api, errorText } from "../api";
import { checkoutTruth, confirmationEvidence, type Evidence } from "../cardFacts";
import { parseChain } from "../logic/chain";
import type { Execution, Policy } from "../logic/stories";
import { REASONS } from "../reasons";
import { Icon } from "./ui";

// Policy (the gateway's decision) and execution (what the receipts prove) are two separate
// badges with distinct words, icons and shapes. ALLOW is never drawn as "done".
const POLICY: Record<Policy, { icon: string; cls: string; word: string }> = {
  ALLOW: { icon: "check", cls: "bg-allow-soft text-allow ring-1 ring-allow/40", word: "ALLOW" },
  REVIEW: { icon: "hand", cls: "bg-review-soft text-review ring-1 ring-review/50", word: "REVIEW" },
  DENY: { icon: "shieldX", cls: "bg-deny text-white", word: "DENY" },
};

export function PolicyBadge({ outcome }: { outcome: Policy | null }) {
  if (!outcome)
    return <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-bold text-muted ring-1 ring-line">NO DECISION</span>;
  const p = POLICY[outcome];
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-bold tracking-wide ${p.cls}`} title="Policy decision by IntentLock's deterministic rules">
      <Icon name={p.icon} className="h-3.5 w-3.5" />
      {p.word}
    </span>
  );
}

const EXEC: Record<Execution, { icon: string; cls: string; word: string }> = {
  EXECUTED: { icon: "check", cls: "text-allow ring-1 ring-allow/40 bg-surface", word: "Executed" },
  REFUSED: { icon: "x", cls: "text-deny ring-1 ring-deny/50 bg-surface", word: "Refused at execution" },
  FAILED: { icon: "x", cls: "text-deny ring-1 ring-deny/50 bg-surface", word: "Merchant call failed" },
  UNKNOWN: { icon: "question", cls: "text-review ring-2 ring-review/70 bg-surface border-dashed", word: "Outcome unknown" },
  PENDING: { icon: "clock", cls: "text-muted ring-1 ring-line bg-surface", word: "Not executed yet" },
  NOT_EXECUTED: { icon: "minus", cls: "text-muted ring-1 ring-line bg-sunken", word: "Not executed" },
};

export function ExecutionBadge({ status, tool }: { status: Execution; tool?: string }) {
  const e = EXEC[status];
  const word = status === "EXECUTED" && tool === "checkout" ? "Order confirmed by the merchant" : e.word;
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold ${e.cls}`} title="Execution status, from the execution receipts">
      <Icon name={e.icon} className="h-3.5 w-3.5" />
      {word}
    </span>
  );
}

export function plain(code: string): string {
  return (REASONS[code]?.text ?? code).replace(/`/g, "");
}

export function ReasonList({ reasons, primaryFirst = true }: { reasons: { code: string; detail?: string }[]; primaryFirst?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!reasons.length) return null;
  const rank = { DENY: 0, REVIEW: 1, ALLOW: 2 } as Record<string, number>;
  const list = primaryFirst
    ? [...reasons].sort((a, b) => (rank[REASONS[a.code]?.outcome ?? "REVIEW"] ?? 1) - (rank[REASONS[b.code]?.outcome ?? "REVIEW"] ?? 1))
    : reasons;
  return (
    <ul className="space-y-1.5">
      {list.map((r, i) => {
        const tone = REASONS[r.code]?.outcome;
        const chain = r.code === "TAINTED_ARG" ? parseChain(r.detail) : null;
        return (
          <li key={i} className="text-sm">
            <button type="button" className="flex w-full items-start gap-2 text-left" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone === "DENY" ? "bg-deny" : tone === "REVIEW" ? "bg-review" : "bg-allow"}`} aria-hidden="true" />
              <span className="min-w-0 flex-1">
                {plain(r.code)}
                <span className="ml-2 font-mono text-[11px] text-muted">{r.code}</span>
              </span>
              {(r.detail || chain) && <Icon name="chevron" className={`mt-0.5 h-4 w-4 text-muted ${open === i ? "rotate-90" : ""}`} />}
            </button>
            {open === i && (r.detail || chain) && (
              <div className="mt-1.5 ml-4">
                {chain ? <EvidenceChain detail={r.detail!} /> : <p className="rounded bg-sunken p-2 font-mono text-xs break-words whitespace-pre-wrap">{r.detail}</p>}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The provenance chain exactly as the backend recorded it; the original text when it can't be parsed. */
export function EvidenceChain({ detail }: { detail: string }) {
  const c = parseChain(detail);
  if (!c) return <p className="rounded bg-sunken p-2 font-mono text-xs break-words whitespace-pre-wrap">{detail}</p>;
  return (
    <div className="space-y-2 rounded-md border border-review/40 bg-review-soft/50 p-2.5 text-xs" data-testid="evidence-chain">
      <div className="label">
        {c.kind === "sources" ? "Where this value came from (the gateway's provenance ledger)" : "This value also appears in untrusted content"}
      </div>
      {c.chains.map((steps, k) => (
        <ol key={k} className="flex flex-wrap items-center gap-1.5">
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-1.5">
              <span className="rounded border border-line bg-surface px-1.5 py-1">
                <span className="text-muted">step {i + 1} · </span>
                <code>{s.tool}</code>
                <span className="text-muted"> call #{s.seq} · </span>
                <code className="break-all">{s.path}</code>
              </span>
              <Icon name="arrow" className="h-3.5 w-3.5 text-muted" />
            </li>
          ))}
          <li className="rounded border border-review bg-surface px-1.5 py-1 font-semibold">
            proposed by the agent: <code className="break-all">{c.field} = {c.value}</code>
          </li>
        </ol>
      ))}
      {c.sources.length > 0 && <div className="text-muted">Untrusted records: <code className="break-all">{c.sources.join(", ")}</code></div>}
    </div>
  );
}

const TONE_TEXT: Record<Evidence["tone"], string> = { allow: "text-allow", review: "text-review", deny: "text-deny", neutral: "text-muted" };

/** Checkout Truth: three channels, each labelled for what kind of evidence it is. */
export function CheckoutTruth({ summary, execution, codes = [], reconciled, stacked = false }: {
  summary: Record<string, any>;
  execution?: Execution;
  codes?: string[];
  reconciled?: string | null;
  stacked?: boolean; // one channel per row (the narrow approval card)
}) {
  const t = checkoutTruth(summary);
  if (!t) return null;
  const screen: Evidence =
    t.screen.state === "matches"
      ? { tone: "allow", text: "The page matches the quote" }
      : t.screen.state === "flagged"
        ? { tone: "review", text: `Disagrees: ${t.screen.codes.map(plain).join("; ")}` }
        : t.screen.state === "unavailable"
          ? { tone: "review", text: "The page could not be read: treated as needing review" }
          : { tone: "neutral", text: "Not checked: the screenshot check is off" };
  const conf = confirmationEvidence(execution ?? "PENDING", codes, reconciled);
  const cols: { title: string; kind: string; ev: Evidence; foot?: string }[] = [
    { title: "Merchant quote", kind: "Gateway-provided transaction evidence", ev: { tone: "allow", text: "Fetched by the gateway; every money check uses it" } },
    { title: "Checkout page", kind: "Vision-extracted evidence: not an authorisation", ev: screen, foot: "The screenshot itself is not shown in this console." },
    { title: "Order confirmation", kind: "Merchant execution evidence", ev: conf },
  ];
  return (
    <section aria-label="Checkout truth" className="rounded-md border border-line">
      <div className={`grid divide-y divide-line ${stacked ? "" : "sm:grid-cols-3 sm:divide-x sm:divide-y-0"}`}>
        {cols.map((c) => (
          <div key={c.title} className="space-y-1 p-2.5 text-xs">
            <div className="font-semibold text-ink">{c.title}</div>
            <div className="text-[11px] text-muted">{c.kind}</div>
            <div className={`font-medium ${TONE_TEXT[c.ev.tone]}`}>{c.ev.text}</div>
            {c.ev.note && <div className="text-muted">{c.ev.note}</div>}
            {c.foot && <div className="text-[11px] text-muted">{c.foot}</div>}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line bg-sunken px-2.5 py-1.5 text-[11px] text-muted">
        <Icon name="lock" className="h-3.5 w-3.5" />
        Decided by IntentLock's deterministic rules. The AI reading of the page is evidence only.
      </div>
    </section>
  );
}

export function ReceiptIntegrity() {
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const verify = async () => {
    setBusy(true);
    try {
      const v = await api.verify();
      setState(v.ok ? { ok: true, text: `Receipt chain intact · head ${v.head.slice(0, 12)}…` } : { ok: false, text: `Receipt chain BROKEN at seq ${v.bad_seq}` });
    } catch (e) {
      setState({ ok: false, text: `Could not verify: ${errorText(e)}` });
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className="btn-secondary" disabled={busy} onClick={verify}>
        <Icon name="receipt" /> Verify receipt chain
      </button>
      {state && <span role="status" className={`text-sm font-medium ${state.ok ? "text-allow" : "text-deny"}`}>{state.text}</span>}
    </span>
  );
}
