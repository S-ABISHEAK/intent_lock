"""Every number in the README's results tables comes from the latest eval results (SUBMISSION
checklist: "Every README number traceable to a results file, with n stated")."""

import json
from pathlib import Path

import pytest

from eval.report import CONFIGS, latest, results_json

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def section(title: str) -> str:
    return README.split(f"### {title}")[1].split("\n### ")[0].split("\n## ")[0]


def row(text: str, config: str) -> list[str]:
    [line] = [ln for ln in text.splitlines() if ln.startswith("|") and f"`{config}`" in ln]
    return [c.strip().strip("*") for c in line.strip("|").split("|")][1:]


@pytest.fixture(scope="module")
def layer_g():
    folder = latest("g")
    if folder is None:
        pytest.skip("no Layer G results")
    return folder.name, json.loads((folder / "summary.json").read_text())


@pytest.fixture(scope="module")
def layer_e():
    e = results_json()["layer_e"]
    if "muse" not in e["sources"]:
        pytest.skip("no Layer E results")
    return e


@pytest.mark.parametrize("config", CONFIGS)
def test_layer_g_row_matches_the_summary(layer_g, config):
    _, summary = layer_g
    s = summary["configs"][config]
    a, b = s["attacks"], s["benign"]
    assert row(section("Layer G"), config) == [
        f"{a['executed']}/{a['n']}",
        f"{a['reached_prompt']}/{a['n']}",
        f"{b['completed']}/{b['n']}",
        str(s["extra_prompts_per_benign_case"]),
        f"{s['decide_ms_p50']} / {s['decide_ms_p95']} ms",
    ]


def test_layer_g_source_and_counts(layer_g):
    name, summary = layer_g
    text = section("Layer G")
    assert f"eval/results/{name}/summary.json" in text
    attacks = summary["configs"]["intentlock"]["attacks"]["n"]
    benign = summary["configs"]["intentlock"]["benign"]["n"]
    assert f"{summary['cases']} hand-written cases ({attacks} attacks, {benign} benign)" in text
    il = summary["configs"]["intentlock"]["attacks"]
    assert f"in {attacks} gateway cases" in README.split("\n")[2]
    assert il["executed"] == 0  # the headline line claims no attack executed


@pytest.mark.parametrize("config", CONFIGS)
def test_layer_e_muse_row_matches_the_runs(layer_e, config):
    s = layer_e["summary"]["muse"][config]
    assert row(section("Layer E"), config) == [
        f"{s['attacks_succeeded']}/{s['attack_runs']}",
        f"{s['benign_completed']}/{s['benign_runs']}",
        str(s["extra_prompts_per_benign"]),
        f"{s['attack_task_goal']}/{s['attack_runs']}",
    ]


def test_layer_e_source_counts_and_weak_status(layer_e):
    text = section("Layer E")
    src = layer_e["sources"]["muse"]
    runs = sum(layer_e["summary"]["muse"][c]["runs"] for c in CONFIGS)
    errors = sum(layer_e["summary"]["muse"][c]["errors"] for c in CONFIGS)
    assert f"eval/results/{src}/runs.jsonl` ({runs} runs, {errors} errors)" in text
    weak = layer_e["coverage"].get("weak")
    if weak and not weak["complete"]:
        assert f"{weak['cells_filled']} of {weak['cells']} task × config cells" in text
        assert "**incomplete**" in text
    else:
        pytest.fail("the weak agent is complete now: add its table to the README")
