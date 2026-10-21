"""Settings and environment loading (Orchestrator-owned, shared by all stages)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = REPO_ROOT / "config" / "settings.yaml"

# Every environment variable the code reads. Keep in sync with .env.example and README.
ENV_VARS = (
    "LLM_API_KEY",
    "LLM_MODEL",
    "NEWS_API_KEY",
    "JOBS_API_KEY",
    "EXTERNAL_SCRAPER_MODULE",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "REPORT_RECIPIENTS",
    "SLACK_WEBHOOK_URL",
    "FUND_UNIVERSE_PATH",
)


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    """Load config/settings.yaml (or the given path) as a plain dict."""
    with open(path or DEFAULT_SETTINGS) as fh:
        return yaml.safe_load(fh) or {}


def load_env(dotenv_path: str | Path | None = None) -> dict[str, str]:
    """Return known env vars, from .env overlaid by the process environment. Blank = unset."""
    file_values = dotenv_values(dotenv_path or REPO_ROOT / ".env")
    env: dict[str, str] = {}
    for name in ENV_VARS:
        value = os.environ.get(name, file_values.get(name) or "")
        if value and value.strip():
            env[name] = value.strip()
    return env


def resolve_path(path: str | Path) -> Path:
    """Resolve a repo-relative path from settings."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p
