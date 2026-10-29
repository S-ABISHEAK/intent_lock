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


def test_gateway_never_touches_the_approver_private_key():
    """Invariant 7: only approver/ may sign or load the private key."""
    forbidden = ("SigningKey", "approver.signing", "approver.keys", "APPROVER_KEY_PATH")
    offenders = [
        (rel(p), word)
        for p in py_files()
        if rel(p).startswith("intentlock/")
        for word in forbidden
        if word in p.read_text()
    ]
    assert offenders == []


def test_approver_never_touches_the_shop_key():
    """Invariant 7: the approver never holds the shop API key."""
    offenders = [
        rel(p)
        for p in py_files()
        if rel(p).startswith("approver/") and "SHOP_API_KEY" in p.read_text()
    ]
    assert offenders == []


def test_agent_knows_only_the_gateway():
    """Invariant 12: nothing in agent/ can reach the shop directly."""
    forbidden = ("SHOP_URL", "SHOP_API_KEY", "8001", "from shop", "import shop", "shop_url")
    offenders = [
        (rel(p), word)
        for p in py_files()
        if rel(p).startswith("agent/")
        for word in forbidden
        if word in p.read_text()
    ]
    assert offenders == []


def test_agent_prompt_has_no_security_instructions():
    """SPEC §15: we test the gateway, not prompt hardening."""
    from agent.prompts import SYSTEM_PROMPT

    words = ("inject", "untrusted", "security", "ignore instructions", "malicious", "verify")
    assert not [w for w in words if w in SYSTEM_PROMPT.lower()]


def test_only_the_ui_stand_in_knows_the_approver_ui_token():
    """MED 2: the gateway must not be able to obtain approvals without a user click."""
    allowed = {"intentlock/cli.py", "intentlock/config.py"}
    offenders = [
        rel(p)
        for p in py_files()
        if rel(p).startswith(("intentlock/", "agent/"))
        and rel(p) not in allowed
        and ("approver_ui_token" in p.read_text() or "APPROVER_UI_TOKEN" in p.read_text())
    ]
    assert offenders == []


def test_agent_never_knows_the_operator_token():
    offenders = [
        rel(p)
        for p in py_files()
        if rel(p).startswith("agent/")
        and ("operator_token" in p.read_text() or "GATEWAY_OPERATOR_TOKEN" in p.read_text())
    ]
    assert offenders == []


def test_make_dev_uses_an_allow_list_per_process():
    makefile = (ROOT / "Makefile").read_text()
    dev = makefile.split("\ndev:")[1].split("\n\n")[0]
    assert dev.count("env -i") == 3
    approver_line = [part for part in dev.split("env -i") if "approver.app" in part][0]
    assert "SHOP_API_KEY" not in approver_line and "MODEL_API_KEY" not in approver_line
    gateway_line = [part for part in dev.split("env -i") if "intentlock.api" in part][0]
    assert "APPROVER_KEY_PATH" not in gateway_line and "APPROVER_UI_TOKEN" not in gateway_line


def test_make_agent_never_hands_the_agent_shop_or_operator_secrets():
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\nagent:")[1].split("\n\n")[0]
    assert "env -i" in recipe
    for secret in ("SHOP_API_KEY", "GATEWAY_OPERATOR_TOKEN", "APPROVER_UI_TOKEN", "APPROVER_KEY"):
        assert secret not in recipe
