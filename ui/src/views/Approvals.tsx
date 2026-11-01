import { useEffect, useState } from "react";

import { api, errorText, type Card } from "../api";
import { rupees } from "../money";
import { ReasonList } from "./Badge";

function useNow() {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

// SPEC §10: titles, qty, seller, every line including add-ons, fees, tax, total, recurring
// terms, shipping address and the reasons for REVIEW. Everything comes from the gateway's
// authoritative quote, never from the agent.
function CardView({ card }: { card: Card }) {
  const now = useNow();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState("");
  const s = card.summary ?? {};
  const left = Math.max(0, Math.floor(card.request.expires_at - now));

  const act = async (f: () => Promise<unknown>) => {
    setState("busy");
    setError("");
    try {
      await f();
      setState("done");
    } catch (e) {
      setError(errorText(e));
      setState("idle");
    }
  };

  return (
    <div className="space-y-3 rounded-lg border-2 border-review bg-white p-4 shadow">
      <div className="flex items-baseline justify-between">
        <h3 className="text-lg font-semibold">
          Approve <code>{card.request.action_tool}</code>?
        </h3>
        <span className={`text-sm ${left < 20 ? "text-deny" : "text-slate-500"}`}>expires in {left}s</span>
      </div>

      {Array.isArray(s.lines) && (
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th>Item</th>
              <th>Seller</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Price</th>
            </tr>
          </thead>
          <tbody>
            {s.lines.map((l: any) => (
              <tr key={l.sku} className={l.is_addon ? "bg-amber-50" : ""}>
                <td>
                  {l.title} <span className="text-slate-400">{l.sku}</span>
                  {l.is_addon && <span className="ml-2 rounded bg-amber-200 px-1 text-xs">add-on</span>}
                </td>
                <td>
                  {l.seller_id} ({l.seller_country})
                </td>
                <td className="text-right">{l.qty}</td>
                <td className="text-right">{rupees(l.unit_paise * l.qty)}</td>
              </tr>
            ))}
            <tr className="text-slate-600">
              <td colSpan={3}>Fees</td>
              <td className="text-right">{rupees(s.fees_paise)}</td>
            </tr>
            <tr className="text-slate-600">
              <td colSpan={3}>Tax</td>
              <td className="text-right">{rupees(s.tax_paise)}</td>
            </tr>
            <tr className="border-t font-bold">
              <td colSpan={3}>Total</td>
              <td className="text-right text-lg">{rupees(s.total_paise)}</td>
            </tr>
          </tbody>
        </table>
      )}

      {Array.isArray(s.recurring) && s.recurring.length > 0 && (
        <div className="rounded bg-amber-50 p-2 text-sm">
          Recurring:{" "}
          {s.recurring.map((r: any) => `${r.sku} ${rupees(r.amount_paise)} / ${r.interval}`).join(", ")}
        </div>
      )}

      {s.tool === "checkout" && (
        <div className="text-sm">
          Ship to:{" "}
          {s.shipping_address ? (
            <span>
              {s.shipping_address.label} — {s.shipping_address.line}
            </span>
          ) : (
            <span className="font-semibold text-deny">
              {s.shipping_address_id} is not one of your saved addresses
            </span>
          )}
        </div>
      )}

      {s.tool !== "checkout" && s.args && (
        <pre className="overflow-x-auto rounded bg-slate-100 p-2 text-xs">{JSON.stringify(s.args, null, 2)}</pre>
      )}

      <div>
        <div className="text-sm font-medium">Why you are being asked:</div>
        <ReasonList reasons={(s.reasons ?? []).map((code: string) => ({ code }))} />
      </div>

      {state === "done" ? (
        <p className="text-sm text-slate-500">Sent. The gateway re-checks the quote before it executes.</p>
      ) : (
        <div className="flex gap-2">
          <button className="btn bg-allow" disabled={state === "busy" || left === 0} onClick={() => act(() => api.approve(card.request))}>
            Approve
          </button>
          <button className="btn bg-deny" disabled={state === "busy"} onClick={() => act(() => api.reject(card.request.approval_id))}>
            Reject
          </button>
        </div>
      )}
      {error && <p className="text-sm text-deny">{error}</p>}
    </div>
  );
}

export default function Approvals({ pending }: { pending: Card[] }) {
  if (!pending.length) return <p className="text-slate-500">Nothing waiting for you.</p>;
  return (
    <div className="space-y-4">
      {pending.map((c) => (
        <CardView key={c.request.approval_id} card={c} />
      ))}
    </div>
  );
}
