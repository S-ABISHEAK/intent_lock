import { useState } from "react";

import { api, errorText, type Receipt } from "../api";
import { rupees } from "../money";
import { Outcome, ReasonList } from "./Badge";

const KIND_TONE: Record<string, string> = {
  mandate: "border-slate-400",
  tool_result: "border-sky-300",
  proposal: "border-indigo-400",
  decision: "border-slate-900",
  approval: "border-review",
  execution: "border-allow",
  error: "border-deny",
};

function short(v: unknown): string {
  const s = JSON.stringify(v);
  return s.length > 140 ? `${s.slice(0, 140)}…` : s;
}

function Body({ r }: { r: Receipt }) {
  const p = r.payload;
  switch (r.kind) {
    case "mandate":
      return p.event === "ended" ? <span>Session ended</span> : <span>Session started ({p.config ?? "config"})</span>;
    case "tool_result":
      return (
        <span>
          <code>{p.tool}</code> <span className="text-slate-500">{short(p.args)}</span>
          {p.error && <span className="ml-2 text-deny">{p.error}</span>}
        </span>
      );
    case "proposal":
      return (
        <span>
          Agent proposes <code className="font-semibold">{p.tool}</code> <span className="text-slate-500">{short(p.args)}</span>
          {p.dropped_args?.length > 0 && <span className="ml-2 text-xs text-review">dropped: {p.dropped_args.join(", ")}</span>}
        </span>
      );
    case "decision":
      return (
        <div>
          <Outcome outcome={p.outcome} /> <span className="ml-1 text-xs text-slate-500">config {p.config}</span>
          <ReasonList reasons={p.reasons ?? []} />
        </div>
      );
    case "approval":
      if (p.event === "requested")
        return (
          <span>
            Asked you to approve <code>{p.request?.action_tool}</code>
            {typeof p.summary?.total_paise === "number" && ` (${rupees(p.summary.total_paise)})`}
          </span>
        );
      if (p.event === "signed") return <span className="text-allow">You approved (signed by the approver)</span>;
      return <span className="text-slate-600">Not approved</span>;
    case "execution":
      if (p.event === "started") return <span>Executing…</span>;
      return (
        <div>
          {p.executed === true ? <Outcome outcome="ALLOW" /> : <Outcome outcome="DENY" />}{" "}
          <span>{p.executed === true ? "Executed at the shop" : p.executed === "unknown" ? "Outcome unknown" : "Not executed"}</span>
          <ReasonList reasons={(p.reasons ?? []).map((c: any) => (typeof c === "string" ? { code: c } : c))} />
        </div>
      );
    default:
      return <span className="text-deny">{p.error ?? short(p)}</span>;
  }
}

export default function Timeline({ receipts, live }: { receipts: Receipt[]; live: boolean }) {
  const [reads, setReads] = useState(true);
  const [chain, setChain] = useState("");
  const shown = reads ? receipts : receipts.filter((r) => r.kind !== "tool_result");

  const verify = async () => {
    try {
      const v = await api.verify();
      setChain(v.ok ? `chain OK, head ${v.head.slice(0, 12)}…` : `chain BROKEN at seq ${v.bad_seq}`);
    } catch (e) {
      setChain(errorText(e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className={live ? "text-allow" : "text-deny"}>{live ? "● live" : "○ reconnecting"}</span>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={reads} onChange={(e) => setReads(e.target.checked)} /> show reads
        </label>
        <button className="btn-quiet" onClick={verify}>
          Verify receipt chain
        </button>
        {chain && <span className={chain.startsWith("chain OK") ? "text-allow" : "text-deny"}>{chain}</span>}
      </div>
      {shown.length === 0 && <p className="text-slate-500">No receipts yet. Start a session and run the agent.</p>}
      <ol className="space-y-2">
        {shown.map((r) => (
          <li key={r.seq} className={`rounded border-l-4 bg-white p-2 shadow-sm ${KIND_TONE[r.kind] ?? ""}`}>
            <div className="flex gap-3">
              <span className="w-10 shrink-0 text-right font-mono text-xs text-slate-400">#{r.seq}</span>
              <span className="w-20 shrink-0 text-xs uppercase text-slate-500">{r.kind.replace("_", " ")}</span>
              <div className="min-w-0 flex-1 break-words text-sm">
                <Body r={r} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
