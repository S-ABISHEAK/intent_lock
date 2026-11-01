import { useEffect, useState } from "react";

// Written by `make report` into ui/public/results.json (SPEC §13.4). Every rate shows its n.
const CONFIGS = ["none", "r2_session", "intentlock"] as const;

function frac(k: number, n: number) {
  return n ? `${k}/${n} (${((100 * k) / n).toFixed(1)}%)` : "–";
}

export default function Results() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/results.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch(() => setError("No results yet: run `make report`."));
  }, []);

  if (error) return <p className="text-slate-500">{error}</p>;
  if (!data) return <p className="text-slate-500">Loading…</p>;
  const g = data.layer_g?.summary?.configs;
  const e = data.layer_e?.summary ?? {};

  return (
    <div className="space-y-8">
      {g && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">
            Gateway cases (Layer G) <span className="text-sm font-normal text-slate-500">({data.layer_g.source})</span>
          </h2>
          <p className="text-sm text-slate-600">
            {g.none.attacks.n} attack and {g.none.benign.n} benign cases, expectations hash-locked before the
            run. An attack that reaches a prompt is stopped only because the simulated user declines it.
          </p>
          <table className="results">
            <thead>
              <tr>
                <th>Config</th>
                <th>Attacks executed ↓</th>
                <th>Reached a prompt</th>
                <th>Benign completed ↑</th>
                <th>Extra prompts / benign case ↓</th>
                <th>Decide p50 / p95 ms</th>
              </tr>
            </thead>
            <tbody>
              {CONFIGS.filter((c) => g[c]).map((c) => {
                const s = g[c];
                return (
                  <tr key={c} className={c === "intentlock" ? "font-semibold" : ""}>
                    <td>{c}</td>
                    <td>{frac(s.attacks.executed, s.attacks.n)}</td>
                    <td>{frac(s.attacks.reached_prompt, s.attacks.n)}</td>
                    <td>{frac(s.benign.completed, s.benign.n)}</td>
                    <td>
                      {s.extra_prompts_per_benign_case} (n={s.benign_n})
                    </td>
                    <td>
                      {s.decide_ms_p50} / {s.decide_ms_p95}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
      {Object.entries(e).map(([agent, configs]: [string, any]) => {
        const cov = data.layer_e.coverage?.[agent];
        const resisted = configs.none?.attack_runs > 0 && configs.none.attacks_succeeded === 0;
        return (
        <section key={agent} className="space-y-2">
          <h2 className="text-lg font-semibold">
            Live agent runs — {agent}{" "}
            <span className="text-sm font-normal text-slate-500">
              ({data.layer_e.sources[agent]}, {cov?.runs ?? "?"} runs)
            </span>
          </h2>
          {cov && !cov.complete && (
            <p className="rounded bg-red-50 p-2 text-sm text-deny">
              Incomplete: {cov.cells_filled} of {cov.cells} task × config cells. Not comparable and not a result.
            </p>
          )}
          {resisted && (
            <p className="rounded bg-amber-50 p-2 text-sm">
              No attack succeeded even without a gateway, so this agent resisted the injections itself and
              attack success does not compare the configs here. Attack blocking is measured on the gateway
              cases above.
            </p>
          )}
          <table className="results">
            <thead>
              <tr>
                <th>Config</th>
                <th>Attack success ↓</th>
                <th>Benign completion ↑</th>
                <th>Extra prompts / benign task ↓</th>
                <th>Errors excluded</th>
                <th>User goal on attack tasks</th>
              </tr>
            </thead>
            <tbody>
              {CONFIGS.filter((c) => configs[c]).map((c) => {
                const s = configs[c];
                return (
                  <tr key={c} className={c === "intentlock" ? "font-semibold" : ""}>
                    <td>{c}</td>
                    <td>{frac(s.attacks_succeeded, s.attack_runs)}</td>
                    <td>{frac(s.benign_completed, s.benign_runs)}</td>
                    <td>{s.benign_runs ? `${s.extra_prompts_per_benign} (n=${s.benign_runs})` : "–"}</td>
                    <td>{s.errors_excluded}</td>
                    <td>{frac(s.attack_task_goal, s.runs - s.benign_runs)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-xs text-slate-500">
            Guarded configs ask before a purchase above the auto-approve limit, and the simulated user declines
            every prompt on attack tasks, so the user's own purchase fails there too. The none baseline still
            shows the agent trust labels on tool results.
          </p>
        </section>
        );
      })}

    </div>
  );
}
