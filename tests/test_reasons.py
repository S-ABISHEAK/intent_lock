"""The reason registry must match docs/REASON_CODES.md and fail closed on unknown codes."""

import re
from pathlib import Path

import pytest

from intentlock.models import Reason
from intentlock.policy.reasons import REGISTRY, make_reason, outcome_of

DOC = Path(__file__).resolve().parent.parent / "docs" / "REASON_CODES.md"


def test_unknown_code_raises_so_the_pipeline_can_fail_closed():
    with pytest.raises(KeyError):
        make_reason("MADE_UP")
    with pytest.raises(KeyError):
        outcome_of(Reason(code="MADE_UP", source="rule"))


def test_only_the_informational_code_allows():
    assert [c for c, (o, _) in REGISTRY.items() if o == "ALLOW"] == ["ALLOWED_IN_MANDATE"]


def test_make_reason_uses_the_registered_source():
    assert make_reason("TAINTED_ARG", "x").source == "taint"
    assert make_reason("RULE_OF_TWO").source == "r2"


@pytest.mark.skipif(not DOC.exists(), reason="docs/ is not part of the public repo")
def test_registry_matches_the_documented_table():
    table = DOC.read_text().split("## Mandate compiler codes")[0]
    rows = re.findall(r"^\| `([A-Z_]+)` \| (ALLOW|REVIEW|DENY) \| [^|]+ \| (\w+) \|", table, re.M)
    documented = {code: (outcome, source) for code, outcome, source in rows}
    assert documented == REGISTRY
