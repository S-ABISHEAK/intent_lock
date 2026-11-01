import { REASONS } from "../reasons";

const TONE: Record<string, string> = {
  ALLOW: "bg-green-100 text-allow ring-green-300",
  REVIEW: "bg-amber-100 text-review ring-amber-300",
  DENY: "bg-red-100 text-deny ring-red-300",
};

export function Outcome({ outcome }: { outcome: string }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-bold ring-1 ${TONE[outcome] ?? "bg-slate-100"}`}>
      {outcome}
    </span>
  );
}

export function ReasonList({ reasons }: { reasons: { code: string; detail?: string }[] }) {
  if (!reasons.length) return null;
  return (
    <ul className="mt-1 space-y-1">
      {reasons.map((r, i) => {
        const known = REASONS[r.code];
        return (
          <li key={i} className="text-sm">
            <code className={`mr-2 rounded px-1 text-xs ring-1 ${TONE[known?.outcome ?? ""] ?? ""}`}>{r.code}</code>
            <span className="text-slate-700">{r.detail || known?.text}</span>
          </li>
        );
      })}
    </ul>
  );
}
