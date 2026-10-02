"""Tests run against config.example.yaml / profile.example.md, never against your personal config."""

from pathlib import Path

from job_radar.config import load_settings

REPO = Path(__file__).resolve().parent.parent


def example_settings():
    settings = load_settings(REPO / "config.example.yaml")
    settings.raw["profile_path"] = "profile.example.md"
    settings.raw["closed_check"] = {"enabled": False}  # tests never touch the network
    return settings
