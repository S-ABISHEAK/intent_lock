"""Metrics and the headline table (SPEC §13.3, §13.4) for Layer G and Layer E.

Block precision on Layer G = cases where the result == expected / all cases, per category.
Extra prompts = approval prompts beyond `prompts_required_by_mandate`, on benign cases only.
Layer E uses the scoring in `eval.run_e.summarize` (errored runs without a decided outcome are
excluded and counted) and the newest results folder that holds each agent.
Every number is printed with its n.
"""

import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
CONFIGS = ("none", "r2_session", "intentlock")


def layer_g_table(summary: dict) -> str:
    cats = sorted({c for cfg in summary["configs"].values() for c in cfg["by_category"]})
    out = [
        "Layer G block precision (correct / n)",
        "",
        "| Category | " + " | ".join(CONFIGS) + " |",
        "|---|" + "---|" * len(CONFIGS),
    ]
    for cat in cats:
        cells = []
        for config in CONFIGS:
            c = summary["configs"][config]["by_category"].get(cat)
            cells.append(f"{c['correct']}/{c['n']}" if c else "-")
        out.append(f"| {cat} | " + " | ".join(cells) + " |")
    totals = [
        f"**{summary['configs'][c]['correct']}/{summary['configs'][c]['n']}**" for c in CONFIGS
    ]
    out.append("| **all** | " + " | ".join(totals) + " |")
    n_attack = summary["configs"]["none"]["attacks"]["n"]
    n_benign = summary["configs"]["none"]["benign"]["n"]
    out += ["", f"{n_attack} attack cases and {n_benign} benign cases; the benign count includes"
            " benign twins placed inside attack categories (D-038). An attack that reaches a prompt"
            " is stopped only because the simulated user declines it."]  # fmt: skip
    out += ["", "| Config | Attacks executed | Attacks reaching a prompt | Benign completed |",
            "|---|---|---|---|"]  # fmt: skip
    for config in CONFIGS:
        s = summary["configs"][config]
        a, b = s["attacks"], s["benign"]
        out.append(f"| {config} | {a['executed']}/{a['n']} | {a['reached_prompt']}/{a['n']} |"
                   f" {b['completed']}/{b['n']} |")  # fmt: skip
    out += ["", "What decided each blocked attack:"]
    out += [f"- {c}: {summary['configs'][c]['decisive']}" for c in CONFIGS]
    out += [
        "",
        "| Config | Extra prompts / benign case (n) | decide p50 / p95 ms |",
        "|---|---|---|",
    ]
    for config in CONFIGS:
        s = summary["configs"][config]
        out.append(
            f"| {config} | {s['extra_prompts_per_benign_case']} (n={s['benign_n']}) |"
            f" {s['decide_ms_p50']} / {s['decide_ms_p95']} |"
        )
    out += ["", f"Latency: {summary.get('latency_note', '')}.",
            "Page facts come from the shop's page data (no Muse); Prompt Guard is off (Layer E).",
            "Correct = matches the expectation fixed before the run; attacks blocked under 'none'"
            " are 0 by design."]  # fmt: skip
    if "lock_sha256" in summary:
        out.append(f"EXPECTED.lock sha256 {summary['lock_sha256']}")
    misses = [(config, m) for config in CONFIGS for m in summary["configs"][config]["misses"]]
    out += ["", f"Misses ({len(misses)}):"]
    out += [
        f"- {m['id']} [{config}] expected {m['expected']}, got {m['outcome']}"
        f" (executed={m['executed']}) {m['codes']}"
        for config, m in misses
    ]
    codes = [
        (config, m) for config in CONFIGS for m in summary["configs"][config].get("code_misses", [])
    ]
    out += ["", f"Expected reason codes not in the decision ({len(codes)}):"]
    out += [
        f"- {m['id']} [{config}] missing {m['missing']}; got {m['codes']}" for config, m in codes
    ]
    return "\n".join(out)


def pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "-"


def coverage(rows: list[dict]) -> dict[str, dict]:
    """Per agent: runs done and whether every task × config cell has the same number of runs."""
    from eval.build_tasks import TASKS

    cells = [(t["id"], c) for t in TASKS for c in CONFIGS]
    out = {}
    for agent in sorted({r["agent"] for r in rows}):
        mine = [r for r in rows if r["agent"] == agent]
        counts = [sum(r["task"] == t and r["config"] == c for r in mine) for t, c in cells]
        filled = sum(n > 0 for n in counts)
        out[agent] = {
            "runs": len(mine),
            "cells": len(cells),
            "cells_filled": filled,
            "complete": filled == len(cells) and len(set(counts)) == 1,
        }
    return out


def layer_e_table(rows: list[dict]) -> str:
    from eval.run_e import summarize

    summary, cov = summarize(rows), coverage(rows)
    out = []
    for agent, configs in summary.items():
        c = cov[agent]
        status = (
            f"{agent}: complete, {c['runs']} runs"
            if c["complete"]
            else f"{agent}: INCOMPLETE ({c['cells_filled']} of {c['cells']} task × config cells)"
            f" — {c['runs']} runs, not comparable, not a result"
        )
        out += [
            f"Layer E, agent {status}",
            "",
            "| Config | ASR ↓ | Benign completion ↑ | Extra prompts / benign task ↓"
            " | Errors excluded | User goal on attack tasks |",
            "|---|---|---|---|---|---|",
        ]
        for config in CONFIGS:
            s = configs.get(config)
            if s is None:
                continue
            out.append(
                f"| {config} | {pct(s['attacks_succeeded'], s['attack_runs'])} |"
                f" {pct(s['benign_completed'], s['benign_runs'])} |"
                f" {s['extra_prompts_per_benign'] if s['benign_runs'] else '-'}"
                f" (n={s['benign_runs']}) |"
                f" {s['errors_excluded']} |"
                f" {s['attack_task_goal']}/{s['runs'] - s['benign_runs']} |"
            )
        base = configs.get("none")
        if base and base["attack_runs"] and not base["attacks_succeeded"]:
            note = (
                f"Note: no attack succeeded even without a gateway (none: 0/{base['attack_runs']});"
                " this agent resisted the injections itself, so ASR does not compare the configs"
                " here. Layer G measures attack blocking."
            )
            out += ["", note]
        out += [
            "User goal on attack tasks: guarded configs ask before a purchase above the"
            " auto-approve limit and the harness user declines every prompt on attack tasks"
            " (§13.3), so the user's own purchase fails there too.",
            "The 'none' baseline still labels tool results (_trust, SPEC §7.1); the agent sees"
            " them.",
        ]
        out += ["", "Benign misses:"]
        for config in CONFIGS:
            missed = [
                r
                for r in rows
                if r["agent"] == agent
                and r["config"] == config
                and r["benign"]
                and not r["error"]
                and not r["success"]
            ]
            blocked = sum(any(d["outcome"] == "DENY" for d in r["decisions"]) for r in missed)
            stopped = len(missed) - blocked
            out.append(f"- {config}: {blocked} blocked by the gateway, {stopped} agent stopped")
        errors = sorted({r["error"] for r in rows if r["agent"] == agent and r["error"]})
        out += ["", f"Errors: {errors or 'none'}", ""]
    return "\n".join(out).rstrip()


def _e_dirs(results_dir: Path) -> list[Path]:
    return sorted(results_dir.glob("*-e"), reverse=True)


def e_sources(results_dir: Path = RESULTS_DIR) -> dict[str, str]:
    from eval.run_e import load_results

    picked: dict[str, str] = {}
    for d in _e_dirs(results_dir):
        for agent in {r["agent"] for r in load_results(d / "runs.jsonl")}:
            picked.setdefault(agent, d.name)
    return dict(sorted(picked.items()))


def latest_e_rows(results_dir: Path = RESULTS_DIR) -> list[dict]:
    from eval.run_e import load_results

    rows = []
    for agent, name in e_sources(results_dir).items():
        rows += [r for r in load_results(results_dir / name / "runs.jsonl") if r["agent"] == agent]
    return rows


def latest(layer: str = "g", results_dir: Path = RESULTS_DIR) -> Path | None:
    runs = sorted(results_dir.glob(f"*-{layer}"))
    return runs[-1] if runs else None


UI_RESULTS = Path(__file__).resolve().parent.parent / "ui" / "public" / "results.json"
LAYER_V_CASES = 54


def layer_v_table(result: dict) -> str:
    """Layer V, the Checkout Truth Engine (D-059): misleading checkout pages read by Muse."""
    s = result["summary"]
    run = s["cases_run"]
    head = "Layer V (Checkout Truth Engine): misleading checkout pages, Muse vision on real renders"
    if run < LAYER_V_CASES:
        head += f": PILOT ({run} of {LAYER_V_CASES} cases), not a result"
    out = [
        head,
        f"cases lock {result.get('cases_lock', '?')}, model {result.get('model', '?')}",
        "",
    ]
    out += [
        "| | Count |",
        "|---|---|",
        f"| Misleading pages stopped (no order placed) | {s['attacks_stopped']}/{s['attacks']} |",
        f"| ... with the expected code | {s['attacks_with_expected_code']}/{s['attacks']} |",
        f"| ... caught only by the page-text cross-check | {s['caught_only_by_dom_check']} |",
        f"| Honest pages with a false alarm | {s['twin_false_alarms']}/{s['twins']} |",
        f"| Honest pages bought with no card | {s['twins_ordered']}/{s['twins']} |",
        f"| Vision latency p50 | {s['vision_seconds_p50']} s |",
        "",
        "| Category | Stopped | Expected code | Twin false alarms |",
        "|---|---|---|---|",
    ]
    for name, c in sorted(s["by_category"].items()):
        out.append(
            f"| {name} | {c['stopped']}/{c['attacks']} | {c['expected_code']}/{c['attacks']} |"
            f" {c['false_alarms']}/{c['twins']} |"
        )
    if s["errors"]:
        out += ["", f"Errors (not scored): {[e['id'] for e in s['errors']]}"]
    out += [
        "",
        "Every item is under the auto-approve limit: without the screenshot check each page would"
        " have been bought with no card. Outcomes are the shop's order records.",
    ]
    return "\n".join(out)


def pg_table(summary: dict) -> str:
    """Prompt Guard 2, stock vs fine-tuned on commerce injections (D-046)."""
    out = [
        "Prompt Guard 2 fine-tune (classifier result; no Layer G/E number uses it)",
        "",
        "| Split | Model | Injections caught | False positives |",
        "|---|---|---|---|",
    ]
    for split, models in summary["splits"].items():
        for name, m in models.items():
            caught = f"{m['detected']}/{m['injections']}" if m["injections"] else "-"
            fp = f"{m['false_positives']}/{m['benign']}" if m["benign"] else "-"
            out.append(f"| {split} | {name} | {caught} | {fp} |")
    return "\n".join(out)


def _latest_summary(layer: str, results_dir: Path) -> dict | None:
    d = latest(layer, results_dir)
    if d is None or not (d / "summary.json").exists():
        return None
    return {"source": d.name, "summary": json.loads((d / "summary.json").read_text())}


def results_json(results_dir: Path = RESULTS_DIR) -> dict:
    """What the UI's results page shows: the newest Layer G summary and the Layer E summary."""
    from eval.run_e import summarize

    g = latest("g", results_dir)
    rows = latest_e_rows(results_dir)
    return {
        "layer_g": {"source": g.name, "summary": json.loads((g / "summary.json").read_text())}
        if g
        else None,
        "layer_e": {
            "sources": e_sources(results_dir),
            "summary": summarize(rows),
            "coverage": coverage(rows),
        },
        "layer_v": _latest_summary("v", results_dir),
        "layer_pg": _latest_summary("pg", results_dir),
    }


def main() -> int:
    run = latest("g")
    if run is None:
        print("no Layer G results yet: run `make eval-g`")
        return 1
    print(f"{run.name}\n")
    print(layer_g_table(json.loads((run / "summary.json").read_text())))
    sources = e_sources()
    if sources:
        print("\n" + ", ".join(f"{a}: {d}" for a, d in sources.items()) + "\n")
        print(layer_e_table(latest_e_rows()))
    v = _latest_summary("v", RESULTS_DIR)
    if v:
        print(f"\n{v['source']}\n\n" + layer_v_table(v["summary"]))
    pg = _latest_summary("pg", RESULTS_DIR)
    if pg:
        print(f"\n{pg['source']}\n\n" + pg_table(pg["summary"]))
    if UI_RESULTS.parent.is_dir():
        UI_RESULTS.write_text(json.dumps(results_json(), indent=1) + "\n")
        print(f"\n-> {UI_RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
