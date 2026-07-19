#!/usr/bin/env python3
"""Persist a network-free health snapshot for semantic-veto shadow operation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from zoneinfo import ZoneInfo

from semantic_alignment.io import atomic_write_json
from semantic_alignment.quote_image_semantic_veto import shadow_preflight, shadow_status


LOCAL_TIMEZONE = ZoneInfo("Europe/London")
SCHEMA_VERSION = 1


def _failure(kind: str, exc: Exception) -> dict[str, Any]:
    """Return a bounded diagnostic for a failed local health operation."""
    return {
        "valid": False,
        "failure_kind": kind,
        "reason": f"{type(exc).__name__}: {exc}",
    }


def collect_health(
    project_dir: Path,
    manifest_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect preflight and observation status without contacting a provider."""
    started = time.perf_counter()
    project_dir = project_dir.resolve()
    manifest_path = manifest_path.resolve()
    local_now = now or datetime.now(LOCAL_TIMEZONE)
    if local_now.tzinfo is None:
        raise ValueError("health-check time must be timezone-aware")

    errors: list[str] = []
    try:
        preflight = shadow_preflight(project_dir, manifest_path)
    except Exception as exc:
        preflight = _failure("shadow_preflight", exc)
        errors.append(f"shadow preflight failed: {preflight['reason']}")

    try:
        status = shadow_status(project_dir)
    except Exception as exc:
        status = _failure("shadow_status", exc)
        errors.append(f"shadow status failed: {status['reason']}")

    if preflight.get("valid") is not True:
        errors.append("semantic-veto manifest preflight is not valid")
    if preflight.get("active_enforcement") is not False:
        errors.append("semantic-veto preflight does not confirm inactive enforcement")
    if preflight.get("network_calls") != 0:
        errors.append("semantic-veto preflight did not report zero network calls")

    status_manifest = status.get("manifest") if isinstance(status, dict) else None
    if not isinstance(status_manifest, dict) or status_manifest.get("valid") is not True:
        errors.append("configured semantic-veto manifest is not valid")
    if status.get("feature_enabled") is not True:
        errors.append("semantic-veto shadow is not enabled")
    if status.get("configured_mode") != "shadow":
        errors.append("semantic-veto mode is not shadow")
    if status.get("active_enforcement") is not False:
        errors.append("semantic-veto active enforcement is not false")
    if status.get("network_calls") != 0:
        errors.append("semantic-veto status did not report zero network calls")
    if status.get("production_selection_change_failures") != 0:
        errors.append("semantic-veto history contains a production-selection invariant failure")

    configured_path = Path(str(status.get("manifest_path") or ""))
    if not configured_path.is_absolute():
        configured_path = project_dir / configured_path
    if configured_path.resolve() != manifest_path:
        errors.append("health-check manifest differs from the configured shadow manifest")
    if (
        preflight.get("manifest_sha256")
        and status.get("manifest_sha256")
        and preflight["manifest_sha256"] != status["manifest_sha256"]
    ):
        errors.append("preflight and status manifest hashes differ")

    errors = list(dict.fromkeys(errors))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": local_now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "local_date": local_now.astimezone(LOCAL_TIMEZONE).date().isoformat(),
        "timezone": str(LOCAL_TIMEZONE),
        "health": "healthy" if not errors else "unhealthy",
        "errors": errors,
        "project_dir": str(project_dir),
        "manifest_path": str(manifest_path),
        "preflight": preflight,
        "shadow_status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "network_calls": 0,
    }


def run_health_check(
    project_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Collect and atomically persist the latest and dated health snapshots."""
    report = collect_health(project_dir, manifest_path, now=now)
    history_path = output_dir / "history" / f"{report['local_date']}.json"
    latest_path = output_dir / "latest.json"
    atomic_write_json(history_path, report)
    atomic_write_json(latest_path, report)
    return report, latest_path, history_path


def main() -> int:
    """Run the semantic-veto shadow health-check command."""
    parser = argparse.ArgumentParser(
        description="Persist a local semantic-veto shadow health snapshot"
    )
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    report, latest_path, history_path = run_health_check(
        args.project_dir,
        args.manifest,
        args.output_dir,
    )
    output = {
        **report,
        "latest_path": str(latest_path),
        "history_path": str(history_path),
    }
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["health"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
