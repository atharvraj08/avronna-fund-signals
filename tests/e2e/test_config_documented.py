"""Every env var the code reads must be documented in .env.example and the README table."""

import re
from pathlib import Path

import pytest

from fundsignals.config import ENV_VARS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
# os.environ["X"], os.environ.get("X"), os.getenv("X"), env.get("X"), env["X"], my_env.get("X")
ENV_READ_RE = re.compile(
    r"\b(?:environ|getenv|\w*env)\s*(?:\.get\s*\(|\[|\()\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
)


def find_env_reads(text: str) -> set[str]:
    return set(ENV_READ_RE.findall(text))


def env_vars_in_code() -> set[str]:
    names = set(ENV_VARS)
    for path in SRC.rglob("*.py"):
        names |= find_env_reads(path.read_text())
    return names


def test_scanner_recognises_read_patterns():
    sample = """
    a = os.environ["A_KEY"]
    b = os.environ.get("B_KEY", "")
    c = os.getenv('C_KEY')
    d = env.get("D_KEY")
    e = env["E_KEY"]
    f = run_env.get("F_KEY")
    g = settings.get("not_env")
    h = data["H_NOT_ENV"]
    """
    assert find_env_reads(sample) == {"A_KEY", "B_KEY", "C_KEY", "D_KEY", "E_KEY", "F_KEY"}


def test_code_env_vars_found():
    names = env_vars_in_code()
    assert set(ENV_VARS) <= names
    assert "LLM_API_KEY" in names


def test_env_vars_listed_in_config_env_vars():
    missing = env_vars_in_code() - set(ENV_VARS)
    assert not missing, f"read in src/ but not declared in config.ENV_VARS: {sorted(missing)}"


def test_env_vars_in_env_example():
    declared = set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=", (ROOT / ".env.example").read_text(),
                              re.MULTILINE))
    missing = env_vars_in_code() - declared
    assert not missing, f"missing from .env.example: {sorted(missing)}"


def test_env_example_has_no_real_secrets():
    for line in (ROOT / ".env.example").read_text().splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m and any(k in m.group(1) for k in ("KEY", "PASSWORD", "WEBHOOK", "TOKEN")):
            assert m.group(2).strip() == "", f"{m.group(1)} must be blank in .env.example"


def test_env_vars_in_readme_placeholder_table():
    readme = ROOT / "README.md"
    if not readme.exists():
        pytest.skip("README.md not written yet (Orchestrator writes it last)")
    table_rows = [ln for ln in readme.read_text().splitlines() if ln.lstrip().startswith("|")]
    documented = {n for n in env_vars_in_code() if any(n in row for row in table_rows)}
    missing = env_vars_in_code() - documented
    assert not missing, f"missing from README placeholder table: {sorted(missing)}"
