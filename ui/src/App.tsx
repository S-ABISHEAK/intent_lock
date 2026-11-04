import { useCallback, useEffect, useMemo, useReducer, useState } from "react";

import { api, errorText } from "./api";
import { ActivityFeed } from "./components/activity";
import { ApprovalQueue } from "./components/approvals";
import { Dialog, Icon, Notice } from "./components/ui";
import { useEvents } from "./events";
import { freshReducer } from "./logic/approvals";
import { connection, modeLabel, sessionState } from "./logic/shell";
import { groupReceipts, type Story } from "./logic/stories";
import Mandate from "./views/Mandate";
import Results from "./views/Results";

const TABS = ["Session", "Mandate", "Results"] as const;
type Tab = (typeof TABS)[number];

const MODE_CLS = {
  protected: "bg-primary text-white",
  baseline: "bg-review-soft text-review ring-1 ring-review/50",
  unprotected: "bg-deny text-white",
  unknown: "bg-neutral-soft text-muted",
};

function useHealth() {
  const [health, setHealth] = useState<{ ok: boolean | null; config: string | null; error: string }>({ ok: null, config: null, error: "" });
  const check = useCallback(() => {
    api.health().then(
      (h) => setHealth({ ok: true, config: h.config, error: "" }),
      (e) => setHealth((s) => ({ ok: false, config: s.config, error: errorText(e) })),
    );
  }, []);
  useEffect(() => {
    check();
    const t = window.setInterval(check, 5000);
    return () => window.clearInterval(t);
  }, [check]);
  return { ...health, check };
}

function Status({ on, label }: { on: string | null; label: string }) {
  const known = on === "on" || on === "off";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      {label}: <span className={`font-semibold ${on === "on" ? "text-allow" : "text-ink"}`}>{known ? on : "unknown"}</span>
    </span>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Mandate");
  const [ending, setEnding] = useState(false);
  const [endError, setEndError] = useState("");
  const { receipts, pending, live } = useEvents();
  const health = useHealth();
  const [fresh, dispatch] = useReducer(freshReducer, { live: false, fresh: false });

  useEffect(() => dispatch({ type: "live", live }), [live]);
  useEffect(() => dispatch({ type: "pending" }), [pending]);

  const conn = connection(live, health.ok);
  const stale = conn !== "connected";
  const actionable = fresh.fresh && !stale;
  const session = useMemo(() => sessionState(receipts), [receipts]);
  const grouped = useMemo(() => groupReceipts(receipts), [receipts]);
  const byId = useMemo(() => new Map(grouped.stories.map((s) => [s.id, s])), [grouped]);
  const storyFor = useCallback((id: unknown): Story | null => (typeof id === "string" ? byId.get(id) ?? null : null), [byId]);
  const mode = modeLabel(health.config);
  const activeId = session.state === "active" ? session.sessionId : "";

  useEffect(() => {
    if (activeId) setTab("Session");
  }, [activeId]);

  const endSession = async () => {
    setEnding(false);
    setEndError("");
    try {
      await api.endSession();
    } catch (e) {
      setEndError(errorText(e));
    }
  };

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-white"><Icon name="lock" /></span>
            <div>
              <h1 className="text-base leading-tight font-bold">IntentLock</h1>
              <p className="text-xs text-muted">The agent proposes; IntentLock decides.</p>
            </div>
          </div>
          <span className={`rounded-md px-2 py-1 text-xs font-bold ${MODE_CLS[mode.tone]}`} data-testid="mode-badge" title="The gateway's protection mode">
            {mode.tone === "unprotected" && "⚠ "}
            {mode.label}
          </span>
          <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${conn === "connected" ? "text-allow" : conn === "reconnecting" ? "text-review" : "text-deny"}`} data-testid="connection">
            <span className={`h-2 w-2 rounded-full ${conn === "connected" ? "bg-allow" : conn === "reconnecting" ? "bg-review" : "bg-deny"}`} aria-hidden="true" />
            {conn === "connected" ? "Live" : conn === "reconnecting" ? "Reconnecting…" : "Gateway unavailable"}
          </span>
          {session.state === "active" && (
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-xs text-muted">
                session <code className="text-ink">{session.sessionId.slice(0, 8)}…</code> since {new Date(session.ts * 1000).toLocaleTimeString()}
              </span>
              <Status on={session.twoChannel} label="Screenshot check" />
              <Status on={session.promptGuard} label="Prompt Guard" />
            </span>
          )}
          {session.state === "ended" && <span className="text-xs text-muted">Session ended</span>}
          <nav className="ml-auto flex items-center gap-1" aria-label="Views">
            {TABS.map((t) => (
              <button type="button" key={t} className={tab === t ? "tab tab-on" : "tab"} aria-current={tab === t ? "page" : undefined} onClick={() => setTab(t)}>
                {t}
                {t === "Session" && pending.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-review px-1.5 text-xs text-white" aria-label={`${pending.length} waiting`}>{pending.length}</span>
                )}
              </button>
            ))}
            {session.state === "active" && (
              <button type="button" className="btn-secondary ml-2" disabled={stale} onClick={() => setEnding(true)}>
                End session
              </button>
            )}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] space-y-4 px-4 py-4">
        {conn === "unavailable" && (
          <Notice tone="deny" title="Gateway unavailable: live verification is off" action={<button type="button" className="btn-secondary" onClick={health.check}><Icon name="refresh" /> Retry</button>}>
            Start the services with <code>make dev</code>. What is shown below is stale and nothing can be approved until the gateway is back. ({health.error})
          </Notice>
        )}
        {conn === "reconnecting" && <Notice tone="review" title="Reconnecting to the gateway">Showing the last known state; approvals are paused until the live stream is back.</Notice>}
        {mode.tone === "unprotected" && <Notice tone="deny" title="No protection: this is a comparison baseline">Every agent action goes straight to the merchant.</Notice>}
        {endError && <Notice tone="deny" title="Could not end the session">{endError}</Notice>}
        {pending.length > 0 && tab !== "Session" && (
          <button type="button" className="flex w-full items-center gap-2 rounded-lg border border-review/40 bg-review-soft p-3 text-left text-sm font-semibold text-review" onClick={() => setTab("Session")}>
            <Icon name="hand" /> {pending.length} approval{pending.length > 1 ? "s" : ""} waiting for you <Icon name="arrow" />
          </button>
        )}

        {tab === "Mandate" && <Mandate onStarted={() => setTab("Session")} />}
        {tab === "Session" && (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_26rem] xl:grid-cols-[minmax(0,1fr)_30rem]">
            <aside className="space-y-3 lg:order-2 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:self-start lg:overflow-y-auto" aria-labelledby="queue-title">
              <h2 id="queue-title" className="text-lg font-semibold">
                Approvals {pending.length > 0 && <span className="text-review">({pending.length})</span>}
              </h2>
              <ApprovalQueue pending={pending} fresh={actionable} storyFor={storyFor} />
            </aside>
            <div className="min-w-0 lg:order-1">
              <ActivityFeed grouped={grouped} hasSession={session.state !== "none"} stale={stale} />
            </div>
          </div>
        )}
        {tab === "Results" && <Results />}
      </main>

      {ending && (
        <Dialog title="End this session?" confirm="End session" danger onConfirm={endSession} onCancel={() => setEnding(false)}>
          The agent will have no tools until a new mandate is confirmed, and anything waiting for approval will not execute.
        </Dialog>
      )}
    </div>
  );
}
