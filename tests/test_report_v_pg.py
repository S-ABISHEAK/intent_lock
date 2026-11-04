"""`make report` and the UI results page show Layer V and the Prompt Guard fine-tune when their
results exist, label a pilot as a pilot, and never invent numbers when they don't (D-059, D-046)."""

import json

from eval import report

V_SUMMARY = {
    "stopped": None, "cases_lock": "577f", "model": "muse-spark-1.3",
    "summary": {
        "cases_run": 54, "errors": [], "attacks": 36, "attacks_stopped": 35,
        "attacks_with_expected_code": 33, "faithful_reading_would_stop": 36, "twins": 18,
        "twin_false_alarms": 1, "twins_ordered": 17, "caught_only_by_dom_check": 2,
        "vision_seconds_p50": 4.8,
        "by_category": {"quantity": {"attacks": 4, "stopped": 4, "expected_code": 3, "twins": 2,
                                     "false_alarms": 0, "twins_ordered": 2}},
    },
}  # fmt: skip
PG_SUMMARY = {
    "splits": {
        "test": {"stock": {"injections": 200, "detected": 0, "benign": 200, "false_positives": 0},
                 "finetuned": {"injections": 200, "detected": 180, "benign": 200,
                               "false_positives": 0}},
        "catalog": {"stock": {"injections": 0, "detected": 0, "benign": 44, "false_positives": 0},
                    "finetuned": {"injections": 0, "detected": 0, "benign": 44,
                                  "false_positives": 0}},
    }
}  # fmt: skip


def write(tmp_path, name, summary):
    d = tmp_path / name
    d.mkdir()
    (d / "summary.json").write_text(json.dumps(summary))


def test_layer_v_table_reports_attacks_twins_and_the_dom_catches():
    text = report.layer_v_table(V_SUMMARY)
    assert "35/36" in text and "1/18" in text and "2" in text and "577f" in text
    assert "pilot" not in text.lower()
    assert "quantity" in text


def test_a_pilot_is_labelled_as_a_pilot():
    pilot = json.loads(json.dumps(V_SUMMARY))
    pilot["summary"].update(cases_run=4, attacks=2, attacks_stopped=2, twins=2)
    assert "PILOT (4 of 54 cases)" in report.layer_v_table(pilot)


def test_pg_table_compares_stock_and_finetuned_per_split():
    text = report.pg_table(PG_SUMMARY)
    assert "180/200" in text and "0/200" in text and "0/44" in text


def test_results_json_includes_them_only_when_they_exist(tmp_path):
    write(tmp_path, "20261031-130254-g", {"cases": 1, "configs": {}})
    out = report.results_json(tmp_path)
    assert out["layer_v"] is None and out["layer_pg"] is None
    write(tmp_path, "20261104-010101-v", V_SUMMARY)
    write(tmp_path, "20261102-160517-pg", PG_SUMMARY)
    out = report.results_json(tmp_path)
    assert out["layer_v"]["source"] == "20261104-010101-v"
    assert out["layer_v"]["summary"]["summary"]["attacks_stopped"] == 35
    assert out["layer_pg"]["summary"]["splits"]["test"]["finetuned"]["detected"] == 180
