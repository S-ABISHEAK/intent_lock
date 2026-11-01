"""Layer E headline table (SPEC §13.4) from runs.jsonl: every rate with its n, errors shown,
benign misses split into gateway blocks and the agent stopping on its own."""

import json

from eval import report


def row(agent, config, benign, *, success=False, attack=False, error=None, extra=0, deny=False):
    return {"run_id": f"T-{config}-{agent}-{success}{attack}{error}{extra}{deny}", "task": "T",
            "agent": agent, "config": config, "benign": benign, "success": success,
            "attack_succeeded": attack, "error": error, "prompts_not_required": extra,
            "finished": error is None, "seconds": 1.0, "decide_ms": [],
            "decisions": [{"outcome": "DENY" if deny else "ALLOW", "codes": []}]}  # fmt: skip


def write(tmp_path, name, rows):
    d = tmp_path / name
    d.mkdir()
    (d / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def test_the_table_shows_rates_with_n_and_splits_benign_misses(tmp_path):
    rows = [
        row("muse", "none", False, attack=True),
        row("muse", "none", False),
        row("muse", "intentlock", False),
        row("muse", "intentlock", False, error="LLMError: x"),  # excluded, shown
        row("muse", "intentlock", True, success=True),
        row("muse", "intentlock", True),  # agent stopped
        row("muse", "intentlock", True, deny=True),  # gateway block
        row("muse", "r2_session", True, success=True, extra=1),
    ]
    out = report.layer_e_table(rows)
    assert "| none | 1/2 (50.0%) |" in out
    assert "| intentlock | 0/1 (0.0%) | 1/3 (33.3%) | 0.0 (n=3) | 1 |" in out
    assert "| r2_session | - | 1/1 (100.0%) | 1.0 (n=1) | 0 |" in out
    assert "intentlock: 1 blocked by the gateway, 1 agent stopped" in out


def test_the_newest_folder_per_agent_is_used(tmp_path):
    old = write(
        tmp_path, "20260101-000000-e", [row("weak", "none", True), row("muse", "none", True)]
    )
    new = write(tmp_path, "20260102-000000-e", [row("muse", "none", True, success=True)])
    picked = report.latest_e_rows(tmp_path)
    assert {r["agent"] for r in picked} == {"muse", "weak"}
    assert [r["success"] for r in picked if r["agent"] == "muse"] == [True]
    assert report.e_sources(tmp_path) == {"muse": new.name, "weak": old.name}


def test_results_json_carries_both_layers_and_their_sources(tmp_path):
    write(tmp_path, "20260102-000000-e", [row("muse", "none", True, success=True)])
    g = tmp_path / "20260102-000000-g"
    g.mkdir()
    (g / "summary.json").write_text(json.dumps({"configs": {}}))
    out = report.results_json(tmp_path)
    assert out["layer_g"] == {"source": g.name, "summary": {"configs": {}}}
    assert out["layer_e"]["sources"] == {"muse": "20260102-000000-e"}
    assert out["layer_e"]["summary"]["muse"]["none"]["benign_completed"] == 1


def test_an_agent_that_no_attack_fires_against_is_called_out():
    rows = [row("muse", c, False) for c in ("none", "intentlock")]
    out = report.layer_e_table(rows)
    assert "no attack succeeded even without a gateway" in out


def test_an_incomplete_agent_is_labelled_not_comparable():
    from eval.build_tasks import TASKS

    full = [
        {**row("muse", c, t["benign"]), "run_id": f"{t['id']}-{c}-muse-1", "task": t["id"]}
        for t in TASKS
        for c in report.CONFIGS
    ]
    part = [
        {**r, "agent": "weak", "run_id": r["run_id"].replace("muse", "weak")} for r in full[:10]
    ]
    cov = report.coverage(full + part)
    assert cov["muse"]["complete"] and cov["muse"]["runs"] == 60
    assert not cov["weak"]["complete"] and cov["weak"]["runs"] == 10
    assert "weak: INCOMPLETE (10 of 60 task × config cells)" in report.layer_e_table(full + part)
