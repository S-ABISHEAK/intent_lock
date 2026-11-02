"""The Kaggle notebook (D-046) is generated from eval/pg/finetune.py and actually runs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eval.pg import notebook

pytest.importorskip("torch")
pytest.importorskip("transformers")


def test_committed_notebook_matches_the_generator():
    assert notebook.NOTEBOOK.read_text("utf-8") == notebook.render(), (
        "run `python -m eval.pg.notebook` to regenerate the notebook"
    )


def test_notebook_is_valid_nbformat_4():
    nb = json.loads(notebook.render())
    assert nb["nbformat"] == 4 and nb["cells"][0]["cell_type"] == "markdown"
    for c in nb["cells"]:
        assert isinstance(c["source"], list) and c["metadata"] == {}
        if c["cell_type"] == "code":
            assert c["outputs"] == [] and c["execution_count"] is None


def test_notebook_never_prints_the_token():
    code = "".join("".join(c["source"]) for c in notebook.notebook()["cells"])
    assert "print(token" not in code and "{token" not in code


def test_notebook_runs_end_to_end_with_a_tiny_model(tmp_path):
    """Every code cell, in order, in a fresh interpreter: data check, train, eval, zip."""
    from tests.test_pg_finetune import make_tiny

    base = make_tiny(tmp_path / "tiny")
    code = "\n\n".join(
        "".join(c["source"]) for c in notebook.notebook()["cells"] if c["cell_type"] == "code"
    )
    script = tmp_path / "nb.py"
    script.write_text(code, "utf-8")
    work = tmp_path / "work"
    env = {
        **os.environ,
        "PG_BASE": str(base),
        "PG_WORK": str(work),
        "PG_EPOCHS": "1",
        "PG_LIMIT": "32",
        "HF_HUB_OFFLINE": "1",
    }
    env.pop("HF_TOKEN", None)
    run = subprocess.run(
        [sys.executable, str(script)], env=env, cwd=tmp_path, capture_output=True, text=True,
        timeout=300,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    assert "OK" in run.stdout and "local base model, no login needed" in run.stdout
    [summary] = list((work / "results").glob("*-pg/summary.json"))
    s = json.loads(summary.read_text())
    assert set(s["splits"]) == {"test", "fixtures", "jailbreaks", "catalog"}
    assert set(s["splits"]["test"]) == {"stock", "finetuned"}
    assert s["splits"]["catalog"]["finetuned"]["benign"] == 44
    assert s["training"]["steps"] == 2 and s["training"]["device"] in ("cpu", "cuda")
    assert (work / "pg-finetuned.zip").stat().st_size > 0
    assert Path(work / "pg-finetuned" / "config.json").exists()
