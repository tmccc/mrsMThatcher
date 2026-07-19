"""Validate and load the local lifecycle register for observational features."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_STATES = {"active_shadow", "offline_only", "suspended", "promoted", "retired"}
REQUIRED_FEATURE_FIELDS = {
    "feature_name",
    "purpose",
    "current_state",
    "start_date",
    "evidence_metric",
    "target_observation_count",
    "promotion_criteria",
    "retirement_criteria",
    "next_decision_date",
    "reason_for_current_state",
}


class ShadowLifecycleError(ValueError):
    """Raised when a lifecycle register is incomplete or internally unsafe."""


def _non_empty_text(value: Any, field: str) -> str:
    """Return a stripped required string or raise a validation error."""
    if not isinstance(value, str) or not value.strip():
        raise ShadowLifecycleError(f"{field} must be a non-empty string")
    return value.strip()


def _iso_date(value: Any, field: str) -> str:
    """Return a validated ISO calendar date."""
    text = _non_empty_text(value, field)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ShadowLifecycleError(f"{field} must be an ISO date") from exc
    return text


def validate_lifecycle_register(value: Any) -> dict[str, Any]:
    """Validate a lifecycle register and return it unchanged on success."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ShadowLifecycleError("unsupported lifecycle register schema")
    features = value.get("features")
    if not isinstance(features, list) or not features:
        raise ShadowLifecycleError("lifecycle register features must be a non-empty list")
    names: set[str] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or set(feature) != REQUIRED_FEATURE_FIELDS:
            raise ShadowLifecycleError(f"feature {index} fields mismatch")
        name = _non_empty_text(feature["feature_name"], f"feature {index} name")
        if name in names:
            raise ShadowLifecycleError(f"duplicate lifecycle feature: {name}")
        names.add(name)
        state = feature["current_state"]
        if state not in ALLOWED_STATES:
            raise ShadowLifecycleError(f"unsupported lifecycle state for {name}: {state!r}")
        for field in (
            "purpose",
            "evidence_metric",
            "promotion_criteria",
            "retirement_criteria",
            "reason_for_current_state",
        ):
            _non_empty_text(feature[field], f"{name}.{field}")
        _iso_date(feature["start_date"], f"{name}.start_date")
        _iso_date(feature["next_decision_date"], f"{name}.next_decision_date")
        target = feature["target_observation_count"]
        if type(target) is not int or target < 0:
            raise ShadowLifecycleError(
                f"{name}.target_observation_count must be a non-negative integer"
            )
    return value


def load_lifecycle_register(path: Path) -> dict[str, Any]:
    """Load and fail-closed validate one local lifecycle register."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowLifecycleError(f"cannot load lifecycle register {path}: {exc}") from exc
    return validate_lifecycle_register(value)


def lifecycle_decision_schedule(
    value: Any,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic due/overdue status for every validated feature."""
    register = validate_lifecycle_register(value)
    effective_date = as_of or date.today()
    rows: list[dict[str, Any]] = []
    for feature in register["features"]:
        decision_date = date.fromisoformat(feature["next_decision_date"])
        days_overdue = max((effective_date - decision_date).days, 0)
        rows.append(
            {
                "feature_name": feature["feature_name"],
                "next_decision_date": feature["next_decision_date"],
                "decision_overdue": days_overdue > 0,
                "days_overdue": days_overdue,
            }
        )
    return rows
