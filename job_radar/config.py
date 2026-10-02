"""Loads config.yaml and secrets from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def home_dir() -> Path:
    """Where your personal files live: .env, config.yaml, profile.md and data/.
    The repo folder by default; Docker points JOB_RADAR_HOME at the mounted folder."""
    return Path(os.environ.get("JOB_RADAR_HOME") or REPO_ROOT)


@dataclass
class Settings:
    raw: dict[str, Any]
    root: Path

    @property
    def min_score(self) -> int:
        return int(self.raw.get("min_score", 70))

    @property
    def require_location_fit(self) -> bool:
        return bool(self.raw.get("require_location_fit", True))

    @property
    def notify_window(self) -> dict[str, Any] | None:
        return self.raw.get("notify_window") or None

    @property
    def location(self) -> dict[str, Any]:
        return self.raw.get("location") or {}

    @property
    def credit(self) -> dict[str, Any]:
        return self.raw.get("credit") or {}

    @property
    def max_job_age_days(self) -> int | None:
        value = self.raw.get("max_job_age_days")
        return int(value) if value else None

    @property
    def db_path(self) -> Path:
        return self.root / self.raw.get("db_path", "data/jobs.db")

    @property
    def profile_text(self) -> str:
        path = self.root / self.raw.get("profile_path", "profile.md")
        if not path.exists():
            raise SystemExit(f"Falta {path.name}. Créalo desde la plantilla:  cp profile.example.md {path.name}")
        return path.read_text(encoding="utf-8")

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw.get("llm", {})

    @property
    def prefilter(self) -> dict[str, Any]:
        return self.raw.get("prefilter", {})

    @property
    def exclude_companies(self) -> list[str]:
        return self.raw.get("exclude_companies", [])

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("sources", {})


def load_settings(config_path: Path | None = None) -> Settings:
    home = home_dir()
    load_dotenv(home / ".env")
    path = config_path or home / "config.yaml"
    if not Path(path).exists():
        raise SystemExit("Falta config.yaml. Créalo desde el ejemplo:  cp config.example.yaml config.yaml")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings(raw=raw, root=home if config_path is None else Path(path).resolve().parent)


def secret(name: str) -> str | None:
    """Read a secret from the environment (.env is loaded by load_settings). Never log the value."""
    value = os.environ.get(name, "").strip()
    return value or None
