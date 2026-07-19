from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import semantic_veto_shadow_health as health


NOW = datetime(2026, 7, 19, 23, 35, tzinfo=ZoneInfo("Europe/London"))
MANIFEST_HASH = "a" * 64


def valid_preflight(manifest: Path) -> dict:
    return {
        "valid": True,
        "status": "ready",
        "manifest_path": str(manifest),
        "manifest_sha256": MANIFEST_HASH,
        "active_enforcement": False,
        "network_calls": 0,
    }


def valid_status(manifest: Path) -> dict:
    return {
        "feature_enabled": True,
        "configured_mode": "shadow",
        "manifest_path": str(manifest),
        "manifest": {"valid": True, "sha256": MANIFEST_HASH},
        "manifest_sha256": MANIFEST_HASH,
        "active_enforcement": False,
        "network_calls": 0,
        "production_selection_change_failures": 0,
        "events": 12,
    }


def test_health_check_atomically_writes_latest_and_dated_history(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        health,
        "shadow_preflight",
        lambda project_dir, manifest_path: valid_preflight(manifest_path),
    )
    monkeypatch.setattr(
        health,
        "shadow_status",
        lambda project_dir: valid_status(manifest),
    )

    report, latest, history = health.run_health_check(
        project, manifest, tmp_path / "state", now=NOW
    )

    assert report["health"] == "healthy"
    assert report["errors"] == []
    assert report["network_calls"] == 0
    assert latest == tmp_path / "state/latest.json"
    assert history == tmp_path / "state/history/2026-07-19.json"
    assert json.loads(latest.read_text(encoding="utf-8")) == report
    assert json.loads(history.read_text(encoding="utf-8")) == report
    assert not list(tmp_path.rglob("*.tmp"))


def test_preflight_failure_is_persisted_and_fails_health(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    def fail_preflight(project_dir: Path, manifest_path: Path) -> dict:
        raise RuntimeError("source hash mismatch")

    monkeypatch.setattr(health, "shadow_preflight", fail_preflight)
    monkeypatch.setattr(
        health,
        "shadow_status",
        lambda project_dir: {
            **valid_status(manifest),
            "manifest": {"valid": False, "reason": "source hash mismatch"},
        },
    )

    report, latest, history = health.run_health_check(
        project, manifest, tmp_path / "state", now=NOW
    )

    assert report["health"] == "unhealthy"
    assert any("preflight failed" in error for error in report["errors"])
    assert any("manifest is not valid" in error for error in report["errors"])
    assert latest.is_file()
    assert history.is_file()


def test_selection_invariant_failure_makes_daily_health_unhealthy(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        health,
        "shadow_preflight",
        lambda project_dir, manifest_path: valid_preflight(manifest_path),
    )
    monkeypatch.setattr(
        health,
        "shadow_status",
        lambda project_dir: {
            **valid_status(manifest),
            "production_selection_change_failures": 1,
        },
    )

    report = health.collect_health(project, manifest, now=NOW)

    assert report["health"] == "unhealthy"
    assert report["errors"] == [
        "semantic-veto history contains a production-selection invariant failure"
    ]
