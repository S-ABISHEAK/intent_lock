"""CLAUDE.md invariants that can be checked statically over the whole source tree."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", ".cache", "__pycache__", ".hypothesis", ".ruff_cache"}


def py_files():
    return [p for p in ROOT.rglob("*.py") if not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def test_shop_api_key_is_read_only_in_connector_and_shop():
    """Invariant 8: gateway code outside intentlock/connector/ must never mention the key."""
    allowed = ("intentlock/connector/", "shop/", "tests/")
    offenders = [
        rel(p)
        for p in py_files()
        if "SHOP_API_KEY" in p.read_text() and not rel(p).startswith(allowed)
    ]
    # config.py documents that it does not read it (docstring only)
    offenders = [o for o in offenders if o != "intentlock/config.py"]
    assert offenders == []


def test_config_module_does_not_read_the_shop_key():
    src = (ROOT / "intentlock/config.py").read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    assert "SHOP_API_KEY" not in code


def test_openai_is_imported_only_by_the_llm_client():
    pat = re.compile(r"^\s*(import openai|from openai)", re.M)
    offenders = [rel(p) for p in py_files() if pat.search(p.read_text())]
    assert offenders == ["intentlock/llm/client.py"]


def test_no_bare_except_pass_in_gateway_code():
    pat = re.compile(r"except(\s+Exception)?\s*:\s*\n\s*pass\b")
    offenders = [
        rel(p) for p in py_files() if rel(p).startswith("intentlock/") and pat.search(p.read_text())
    ]
    assert offenders == []


def test_no_float_typed_fields_in_models():
    tree = ast.parse((ROOT / "intentlock/models.py").read_text())
    floats = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.AnnAssign)
        and any(isinstance(x, ast.Name) and x.id == "float" for x in ast.walk(n.annotation))
    ]
    assert floats == []


def test_env_files_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and ".env.*" in ignore and "keys/" in ignore


def test_env_example_has_no_secret_values():
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.endswith(("_KEY", "_TOKEN")):
            assert value == "", f"{key} must be empty in .env.example"


def test_no_source_file_can_skip_required_pipeline_steps():
    offenders = [
        rel(p)
        for p in py_files()
        if not rel(p).startswith("tests/") and "allow_missing_steps" in p.read_text()
    ]
    assert offenders == []


def test_make_smoke_does_not_export_the_whole_env_file():
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\nsmoke:")[1]
    assert "set -a" not in recipe  # SHOP_API_KEY must not reach the smoke process
    assert "SHOP_API_KEY" not in recipe
