"""Metrics and the headline table (SPEC §13.3, §13.4). Layer G today; Layer E joins on Nov 3.

Block precision on Layer G = cases where the result == expected / all cases, per category.
Extra prompts = approval prompts beyond `prompts_required_by_mandate`, on benign cases only.
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


def latest(layer: str = "g", results_dir: Path = RESULTS_DIR) -> Path | None:
    runs = sorted(results_dir.glob(f"*-{layer}"))
    return runs[-1] if runs else None


def main() -> int:
    run = latest("g")
    if run is None:
        print("no Layer G results yet: run `make eval-g`")
        return 1
    print(f"{run.name}\n")
    print(layer_g_table(json.loads((run / "summary.json").read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
