import { useEffect, useReducer, useState } from "react";

import { api, errorText, type Card } from "../api";
import { cardFacts } from "../cardFacts";
import { cardReducer, cardView, initialCard } from "../logic/approvals";
import type { Story } from "../logic/stories";
import { rupees } from "../money";
import { CheckoutTruth, ReasonList } from "./security";
import { EmptyState, Icon } from "./ui";

export function useNow(ms = 500) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

const TITLE: Record<string, string> = {
  checkout: "Approve this purchase?",
  share_cart: "Approve sharing your cart?",
  send_email: "Approve sending this email?",
  delete_saved_address: "Approve deleting a saved address?",
  cancel_order: "Approve cancelling an order?",
};

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 text-sm">
      <div className="text-muted">{k}</div>
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}

function Flag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-review-soft px-1.5 py-0.5 text-xs font-semibold text-review ring-1 ring-review/40">
      <Icon name="alert" className="h-3 w-3" />
      {children}
    </span>
  );
}

function CheckoutBody({ s }: { s: Record<string, any> }) {
  const facts = cardFacts(s);
  const lines: any[] = Array.isArray(s.lines) ? s.lines : [];
  const recurring: any[] = Array.isArray(s.recurring) ? s.recurring : [];
  return (
    <>
      <div className="flex items-baseline justify-between gap-3 rounded-md bg-sunken px-3 py-2">
        <span className="text-sm text-muted">Total (the merchant's quote, fetched by the gateway)</span>
        <span className="text-2xl font-bold tabular-nums" data-testid="card-total">{rupees(s.total_paise)}</span>
      </div>
      <ul className="divide-y divide-line rounded-md border border-line text-sm">
        {lines.map((l) => (
          <li key={l.sku} className={`flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2 ${l.is_addon ? "bg-review-soft/60" : ""}`}>
            <div className="min-w-0 flex-1">
              <div className="font-medium break-words">{l.title}</div>
              <div className="text-xs text-muted">
                <code>{l.sku}</code> · seller {l.seller_id} ({l.seller_country})
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {l.is_addon && <Flag>add-on</Flag>}
                {facts.refurbished.includes(l.sku) && <Flag>refurbished, not new</Flag>}
              </div>
            </div>
            <div className="text-right tabular-nums">
              <div>{rupees(l.unit_paise * l.qty)}</div>
              <div className="text-xs text-muted">qty {l.qty}</div>
            </div>
          </li>
        ))}
        <li className="flex justify-between px-3 py-1.5 text-muted"><span>Fees</span><span className="tabular-nums">{rupees(s.fees_paise)}</span></li>
        <li className="flex justify-between px-3 py-1.5 text-muted"><span>Tax</span><span className="tabular-nums">{rupees(s.tax_paise)}</span></li>
      </ul>
      {recurring.length > 0 && (
        <div className="rounded-md border border-review/40 bg-review-soft p-2 text-sm">
          <span className="font-semibold">Recurring charges: </span>
          {recurring.map((r) => `${r.sku} ${rupees(r.amount_paise)} / ${r.interval}`).join(", ")}
        </div>
      )}
      <Row k="Ship to">
        {s.shipping_address ? (
          <span>{s.shipping_address.label}: {s.shipping_address.line}</span>
        ) : (
          <span className="font-semibold text-deny">{String(s.shipping_address_id)} is not one of your saved addresses</span>
        )}
      </Row>
    </>
  );
}

function OtherBody({ s }: { s: Record<string, any> }) {
  const a = s.args ?? {};
  const facts = cardFacts(s);
  if (s.tool === "share_cart")
    return (
      <>
        <Row k="Recipient"><strong>{String(a.to ?? "")}</strong></Row>
        <Row k={`Items (${facts.shareItems?.length ?? 0})`}>
          {facts.shareItems?.length ? (
            <ul className="list-disc pl-4">{facts.shareItems.map((x) => <li key={x}>{x}</li>)}</ul>
          ) : (
            <span className="text-muted">an empty cart</span>
          )}
        </Row>
      </>
    );
  if (s.tool === "send_email")
    return (
      <>
        <Row k="To"><strong>{String(a.to ?? "")}</strong></Row>
        <Row k="Subject">{String(a.subject ?? "")}</Row>
        <Row k="Body"><p className="max-h-48 overflow-y-auto rounded bg-sunken p-2 whitespace-pre-wrap">{String(a.body ?? "")}</p></Row>
      </>
    );
  if (s.tool === "delete_saved_address" || s.tool === "cancel_order")
    return <Row k="Target"><code className="font-semibold">{String(a.id ?? "")}</code></Row>;
  return (
    <Row k="Arguments">
      <pre className="overflow-x-auto rounded bg-sunken p-2 text-xs">{JSON.stringify(a, null, 2)}</pre>
    </Row>
  );
}

/** What the agent asked for next to what the gateway kept, linked by action_id only. */
function ProposedVsVerified({ story }: { story: Story | null }) {
  if (!story) return <p className="text-xs text-muted">The agent's proposal is not linked to this card (no matching action id).</p>;
  const p = story.proposal.payload;
  const dropped: string[] = Array.isArray(p.dropped_args) ? p.dropped_args : [];
  return (
    <div className="grid gap-2 text-xs sm:grid-cols-2">
      <div className="rounded border border-line p-2">
        <div className="label mb-1">The agent proposed</div>
        <code className="break-all">{p.tool}({JSON.stringify(p.args ?? {})})</code>
        {dropped.length > 0 && <div className="mt-1 text-review">Also sent {dropped.join(", ")}: ignored by the gateway</div>}
      </div>
      <div className="rounded border border-line p-2">
        <div className="label mb-1">The gateway verified</div>
        {p.tool === "checkout" ? "The merchant's own quote above: items, prices, fees, tax, total and address." : "The exact arguments above, bound to this approval."}
      </div>
    </div>
  );
}

export function ApprovalCard({ card, fresh, story }: { card: Card; fresh: boolean; story: Story | null }) {
  const now = useNow();
  const [st, dispatch] = useReducer(cardReducer, initialCard);
  const [more, setMore] = useState(false);
  const s = card.summary ?? {};
  const view = cardView({ now, expiresAt: card.request.expires_at, fresh, phase: st.phase });
  const tool = String(s.tool ?? card.request.action_tool);
  // the decision receipt has each reason's detail; the summary only its codes
  const detailed: { code: string; detail?: string }[] =
    story?.decision?.payload?.reasons ?? (Array.isArray(s.reasons) ? s.reasons.map((code: string) => ({ code })) : []);

  const approve = async () => {
    dispatch({ type: "approve" });
    try {
      await api.approve(card.request); // sign at the approver, then hand the signature to the gateway
      dispatch({ type: "done", result: "approved" });
    } catch (e) {
      dispatch({ type: "error", message: errorText(e) });
    }
  };
  const reject = async () => {
    dispatch({ type: "reject" });
    try {
      await api.reject(card.request.approval_id);
      dispatch({ type: "done", result: "rejected" });
    } catch (e) {
      dispatch({ type: "error", message: errorText(e) });
    }
  };

  const urgent = view.secondsLeft < 20;
  return (
    <article
      aria-label={TITLE[tool] ?? `Approve ${tool}?`}
      data-testid="approval-card"
      className={`card space-y-3 border-l-4 p-4 ${fresh ? "border-l-review" : "border-l-line opacity-70"}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="label text-review">Needs your approval</div>
          <h3 className="text-base font-semibold">{TITLE[tool] ?? `Approve ${tool}?`}</h3>
        </div>
        <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold tabular-nums ring-1 ${urgent ? "text-deny ring-deny/40" : "text-muted ring-line"}`}>
          <Icon name="clock" className="h-3.5 w-3.5" />
          {view.secondsLeft > 0 ? `${view.secondsLeft}s left` : "expiry reached"}
        </span>
      </header>

      {tool === "checkout" ? <CheckoutBody s={s} /> : <OtherBody s={s} />}

      {tool === "checkout" && <CheckoutTruth summary={s} stacked />}

      <div>
        <div className="label mb-1">Why you're asked</div>
        <ReasonList reasons={detailed} />
      </div>

      <div>
        <button type="button" className="flex items-center gap-1 text-xs font-medium text-primary" aria-expanded={more} onClick={() => setMore(!more)}>
          <Icon name="chevron" className={`h-3.5 w-3.5 ${more ? "rotate-90" : ""}`} /> What the agent proposed vs what the gateway verified
        </button>
        {more && <div className="mt-2"><ProposedVsVerified story={story} /></div>}
      </div>

      {view.notice && <p role="status" className="rounded bg-sunken px-2 py-1.5 text-sm font-medium text-muted">{view.notice}</p>}

      {st.phase === "approved" ? (
        <p role="status" className="text-sm font-medium">Approved: the gateway re-checks the quote before it executes. The result appears in the activity.</p>
      ) : st.phase === "rejected" ? (
        <p role="status" className="text-sm font-medium">Rejected: nothing will be executed.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-approve min-w-28" disabled={!view.canAct} onClick={approve}>
            <Icon name="check" /> {st.phase === "signing" ? "Signing…" : st.phase === "submitting" ? "Submitting…" : "Approve"}
          </button>
          <button type="button" className="btn-danger min-w-28" disabled={!view.canAct} onClick={reject}>
            <Icon name="x" /> Reject
          </button>
        </div>
      )}
      {st.error && <p role="alert" className="rounded bg-deny-soft px-2 py-1.5 text-sm text-deny">The gateway refused: {st.error}</p>}
    </article>
  );
}

export function ApprovalQueue({ pending, fresh, storyFor }: { pending: Card[]; fresh: boolean; storyFor: (id: unknown) => Story | null }) {
  if (!pending.length)
    return <EmptyState title="Nothing waiting for you">Actions that need your approval appear here.</EmptyState>;
  const oldest = [...pending].sort((a, b) => a.request.expires_at - b.request.expires_at);
  return (
    <div className="space-y-4" aria-live="assertive">
      {oldest.map((c) => (
        <ApprovalCard key={c.request.approval_id} card={c} fresh={fresh} story={storyFor(c.summary?.action_id)} />
      ))}
    </div>
  );
}
