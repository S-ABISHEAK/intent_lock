import { type ReactNode, useState } from "react";

import { api, errorText, type Compiled, type Confirmed, type ReadbackLine } from "../api";

const DEMO =
  "Laptop under ₹60,000 all-in, Indian seller, no subscriptions, must be refundable; " +
  "you can buy accessories under ₹2,000 without asking.";

// Highlights the grant's span inside the user's own words (SPEC §9 readback).
function Highlighted({ text, spans }: { text: string; spans: string[] }) {
  const lower = text.toLowerCase();
  const marks: [number, number][] = [];
  for (const s of spans) {
    const at = lower.indexOf(s.toLowerCase());
    if (s && at >= 0) marks.push([at, at + s.length]);
  }
  marks.sort((a, b) => a[0] - b[0]);
  const out: ReactNode[] = [];
  let i = 0;
  for (const [a, b] of marks) {
    if (a < i) continue;
    out.push(text.slice(i, a), <mark key={a}>{text.slice(a, b)}</mark>);
    i = b;
  }
  out.push(text.slice(i));
  return <p className="rounded border border-slate-200 bg-white p-3 leading-relaxed">{out}</p>;
}

function Line({ line }: { line: ReadbackLine }) {
  const icon = { grant: "✚", limit: "■", question: "?" }[line.kind] ?? "•";
  return (
    <span>
      <span className="mr-2 text-slate-400">{icon}</span>
      {line.text}
      {line.span && <span className="ml-2 text-sm text-slate-500">“{line.span}”</span>}
    </span>
  );
}

export default function Mandate({ onStarted }: { onStarted: (sessionId: string) => void }) {
  const [text, setText] = useState(DEMO);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [compiled, setCompiled] = useState<Compiled | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [typed, setTyped] = useState("");
  const [confirmed, setConfirmed] = useState<Confirmed | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (f: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await f();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const compile = () =>
    run(async () => {
      const typedAnswers = (compiled?.clarifications ?? [])
        .filter((q) => answers[q]?.trim())
        .map((q) => ({ question: q, answer: answers[q].trim() }));
      setCompiled(await api.compile(text, typedAnswers));
      setAccepted(new Set());
      setTyped("");
      setConfirmed(null);
    });

  const confirm = () =>
    run(async () => {
      if (!compiled) return;
      setConfirmed(await api.confirm(compiled.mandate, [...accepted], typed));
    });

  const start = () =>
    run(async () => {
      if (!confirmed) return;
      onStarted((await api.startSession(confirmed)).session_id);
    });

  const grants = compiled?.readback.filter((l) => l.kind === "grant" && l.field) ?? [];
  const others = compiled?.readback.filter((l) => !(l.kind === "grant" && l.field)) ?? [];
  const open = (compiled?.clarifications.length ?? 0) > 0;

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">1. Tell the agent what it may do</h2>
        <textarea
          className="h-24 w-full rounded border border-slate-300 p-3"
          maxLength={2000}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setCompiled(null);
            setConfirmed(null);
            setAnswers({});
          }}
        />
        <button className="btn" disabled={busy || !text.trim()} onClick={compile}>
          {busy && !compiled ? "Reading…" : compiled ? "Read again" : "Read my request"}
        </button>
      </section>

      {compiled && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">2. Check what IntentLock understood</h2>
          <Highlighted text={compiled.mandate.user_text} spans={grants.map((g) => g.span ?? "")} />
          <ul className="space-y-1">
            {others.map((l, i) => (
              <li key={i}>
                <Line line={l} />
              </li>
            ))}
          </ul>
          {compiled.issues.length > 0 && (
            <div className="rounded border border-slate-200 bg-slate-100 p-3 text-sm">
              <div className="font-medium">Not granted (your words don't say so):</div>
              <ul className="list-disc pl-5">
                {compiled.issues.map((i, k) => (
                  <li key={k}>
                    <code>{i.field}</code> — {i.code}
                    {i.detail && `: ${i.detail}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {open && (
            <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-3">
              <div className="font-medium">Please answer before confirming:</div>
              {compiled.clarifications.map((q) => (
                <label key={q} className="block text-sm">
                  {q}
                  <input
                    className="mt-1 w-full rounded border border-slate-300 p-1"
                    maxLength={500}
                    value={answers[q] ?? ""}
                    onChange={(e) => setAnswers({ ...answers, [q]: e.target.value })}
                  />
                </label>
              ))}
              <button className="btn" disabled={busy} onClick={compile}>
                Send answers
              </button>
            </div>
          )}
          {!open && grants.length > 0 && (
            <div className="space-y-2 rounded border border-slate-300 bg-white p-3">
              <div className="font-medium">Permissions beyond searching and reading — allow each one:</div>
              {grants.map((g) =>
                g.field === "auto_approve_under_paise" ? (
                  <label key={g.field} className="block">
                    <Line line={g} />
                    <input
                      className="mt-1 block w-48 rounded border border-slate-300 p-1"
                      placeholder="type the amount to allow"
                      value={typed}
                      onChange={(e) => setTyped(e.target.value)}
                    />
                  </label>
                ) : (
                  <label key={g.field} className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={accepted.has(g.field!)}
                      onChange={(e) => {
                        const next = new Set(accepted);
                        e.target.checked ? next.add(g.field!) : next.delete(g.field!);
                        setAccepted(next);
                      }}
                    />
                    <Line line={g} />
                  </label>
                ),
              )}
            </div>
          )}
          {!open && (
            <button className="btn" disabled={busy} onClick={confirm}>
              Confirm
            </button>
          )}
        </section>
      )}

      {confirmed && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">3. The mandate the gateway will enforce</h2>
          <ul className="space-y-1 rounded border border-green-300 bg-green-50 p-3">
            {confirmed.readback.map((l, i) => (
              <li key={i}>
                <Line line={l} />
              </li>
            ))}
          </ul>
          <button className="btn" disabled={busy} onClick={start}>
            Start session
          </button>
        </section>
      )}

      {error && <p className="rounded bg-red-50 p-2 text-sm text-deny">{error}</p>}
    </div>
  );
}
