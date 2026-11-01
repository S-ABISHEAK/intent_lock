import { useEffect, useState } from "react";

import { api } from "./api";
import { useEvents } from "./events";
import Approvals from "./views/Approvals";
import Mandate from "./views/Mandate";
import Results from "./views/Results";
import Timeline from "./views/Timeline";

const TABS = ["Mandate", "Timeline", "Results"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Mandate");
  const [config, setConfig] = useState("…");
  const [session, setSession] = useState("");
  const { receipts, pending, live } = useEvents();

  useEffect(() => {
    api.health().then((h) => setConfig(h.config), () => setConfig("gateway down"));
  }, []);

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <header className="mb-6 flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-bold">IntentLock</h1>
        <span className="rounded bg-slate-200 px-2 py-0.5 text-sm">config: {config}</span>
        {session && <span className="text-sm text-slate-500">session {session.slice(0, 8)}…</span>}
        <nav className="ml-auto flex gap-1">
          {TABS.map((t) => (
            <button key={t} className={tab === t ? "tab tab-on" : "tab"} onClick={() => setTab(t)}>
              {t}
              {t === "Timeline" && pending.length > 0 && (
                <span className="ml-1 rounded-full bg-review px-1.5 text-xs text-white">{pending.length}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {pending.length > 0 && tab !== "Timeline" && (
        <button className="mb-4 w-full rounded bg-amber-100 p-2 text-left text-review" onClick={() => setTab("Timeline")}>
          {pending.length} approval{pending.length > 1 ? "s" : ""} waiting for you →
        </button>
      )}

      {tab === "Mandate" && (
        <Mandate
          onStarted={(id) => {
            setSession(id);
            setTab("Timeline");
          }}
        />
      )}
      {tab === "Timeline" && (
        <div className="grid gap-6 lg:grid-cols-[1fr_24rem]">
          <Timeline receipts={receipts} live={live} />
          <aside className="space-y-2 lg:sticky lg:top-4 lg:self-start">
            <h2 className="font-semibold">Approvals</h2>
            <Approvals pending={pending} />
          </aside>
        </div>
      )}
      {tab === "Results" && <Results />}
    </div>
  );
}
