"""The UI labels every decision reason code (SPEC: the UI keys off REASON_CODES.md)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_every_decision_code_has_a_ui_label():
    path = ROOT / "docs" / "REASON_CODES.md"
    if not path.exists():  # the planning docs are kept out of the public repo (.gitignore)
        pytest.skip("docs/REASON_CODES.md is not in this checkout")
    doc = path.read_text(encoding="utf-8")
    codes = set(re.findall(r"^\| `([A-Z_]+)` \| (?:ALLOW|REVIEW|DENY) \|", doc, re.M))
    ui = set(re.findall(r"^  ([A-Z_]+): \{", (ROOT / "ui/src/reasons.ts").read_text(), re.M))
    assert len(codes) > 30 and codes == ui
