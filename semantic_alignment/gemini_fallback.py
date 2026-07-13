from __future__ import annotations

import os
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from google.genai import errors

from .bakeoff import (
    BAKEOFF_PROMPT_VERSION,
    BAKEOFF_SCHEMA_VERSION,
    MAX_OUTPUT_TOKENS,
    PRICES,
    common_prompt,
    validate_bakeoff_result,
)
from .io import atomic_write_json, read_json
from .large_bakeoff import _http_error_details, maximum_attempt_cost
from .vertex_recovery import GeminiVertexClient, VERTEX_MODEL, error_details

DAILY_QUOTA_THRESHOLD = 3
DEFAULT_VERTEX_FALLBACK_LIMIT = 3.0
RESET_HEADERS = ("Retry-After", "X-RateLimit-Reset", "RateLimit-Reset")


def utc_iso(value: float | datetime) -> str:
    moment = datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else value
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def quota_reset_metadata(
    headers: dict[str, str] | None,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/London",
    estimated_daily_reset_hour: int | None = None,
) -> dict[str, Any]:
    """Return provider-confirmed, explicitly estimated, or unknown reset metadata."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    values = {key.lower(): str(value) for key, value in (headers or {}).items()}
    reset = values.get("x-ratelimit-reset") or values.get("ratelimit-reset")
    retry = values.get("retry-after")
    expected = None
    source = None
    if reset:
        try:
            numeric = float(reset)
            expected = datetime.fromtimestamp(numeric, timezone.utc) if numeric > 1_000_000_000 else now + timedelta(seconds=numeric)
            source = "provider_reset_header"
        except (ValueError, OverflowError, OSError):
            try:
                expected = parsedate_to_datetime(reset).astimezone(timezone.utc)
                source = "provider_reset_header"
            except (TypeError, ValueError, OverflowError):
                pass
    if expected is None and retry:
        try:
            expected = now + timedelta(seconds=float(retry)); source = "provider_retry_after_header"
        except (ValueError, OverflowError):
            try:
                expected = parsedate_to_datetime(retry).astimezone(timezone.utc); source = "provider_retry_after_header"
            except (TypeError, ValueError, OverflowError):
                pass
    if expected is not None:
        return {"expected_reset_at": utc_iso(expected), "expected_reset_timezone": timezone_name,
                "reset_basis": source, "reset_time_confidence": "confirmed_provider_metadata"}
    if estimated_daily_reset_hour is not None:
        if type(estimated_daily_reset_hour) is not int or not 0 <= estimated_daily_reset_hour <= 23:
            raise ValueError("estimated daily reset hour must be an integer from 0 to 23")
        zone = ZoneInfo(timezone_name); local = now.astimezone(zone)
        candidate = local.replace(hour=estimated_daily_reset_hour, minute=0, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return {"expected_reset_at": utc_iso(candidate), "expected_reset_timezone": timezone_name,
                "reset_basis": "configured_provider_daily_quota_reset", "reset_time_confidence": "estimated"}
    return {"expected_reset_at": None, "expected_reset_timezone": timezone_name,
            "reset_basis": "unknown", "reset_time_confidence": "unknown"}


def verify_adc_access(env: dict[str, str], *, run: Callable[..., Any] = subprocess.run) -> dict[str, str]:
    from .vertex_recovery import validate_vertex_environment

    values = validate_vertex_environment(env)
    completed = run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("ADC could not mint an access token")
    return values


def classify_developer_failure(exc: BaseException) -> dict[str, Any]:
    """Classify only confirmed Developer API responses; transport errors stay ambiguous."""
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return {"classification": "ambiguous", "fallback_eligible": False}
    detail = _http_error_details(exc)
    headers = {key: exc.response.headers[key] for key in RESET_HEADERS if key in exc.response.headers}
    status = detail.get("http_status")
    text = " ".join(
        str(detail.get(key) or "") for key in ("provider_code", "provider_status", "message", "details")
    ).lower()
    daily = status == 429 and any(
        marker in text
        for marker in ("daily", "per_day", "per day", "quota exceeded", "daily_limit", "daily-limit")
    )
    rate = status == 429
    classification = "daily_quota_exhausted" if daily else "rate_limit" if rate else "ineligible_http_failure"
    return {**detail, "classification": classification, "fallback_eligible": rate,
            "daily_quota": daily, "reset_headers": headers}


def settings_signature(client: Any) -> dict[str, Any]:
    config = client.config() if hasattr(client, "config") else None
    if config is not None:
        return {
            "model": client.model,
            "max_output_tokens": config.max_output_tokens,
            "response_mime_type": config.response_mime_type,
            "response_schema": config.response_json_schema,
            "thinking_budget": config.thinking_config.thinking_budget,
            "temperature": config.temperature,
            "safety_settings": config.safety_settings,
            "tools": config.tools,
        }
    payload = client.payload("__PARITY_PROMPT__")
    generation = payload["generationConfig"]
    return {
        "model": client.model,
        "max_output_tokens": generation["maxOutputTokens"],
        "response_mime_type": generation["responseMimeType"],
        "response_schema": generation["responseJsonSchema"],
        "thinking_budget": generation["thinkingConfig"]["thinkingBudget"],
        "temperature": generation.get("temperature"),
        "safety_settings": payload.get("safetySettings"),
        "tools": payload.get("tools"),
    }


def require_transport_parity(developer: Any, vertex: Any) -> None:
    left, right = settings_signature(developer), settings_signature(vertex)
    if left != right:
        changed = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
        raise RuntimeError(f"Gemini Developer/Vertex settings parity failed: {', '.join(changed)}")


def _transport_metrics(ledger: dict[str, Any], results: dict[str, Any], transport: str) -> dict[str, Any]:
    attempts = ledger.get("attempts", [])
    calls = ledger.get("calls", [])
    completed = sum(item.get("transport", "developer_api") == transport for item in results.get("items", {}).values())
    attempted_cases = {row.get("case_id") for row in attempts}
    failed_cases = {row.get("case_id") for row in attempts if row.get("lifecycle_state") in {"confirmed_failure", "ambiguous_outcome"}}
    return {
        "completed": completed,
        "failed_cases": len(failed_cases - {key for key, item in results.get("items", {}).items() if item.get("transport", "developer_api") == transport}),
        "retries": sum(int(row.get("transport_attempt_number") or 1) > 1 for row in attempts),
        "attempted_cases": len(attempted_cases),
        "known_spend_usd": sum(float(row.get("cost_usd") or 0) for row in calls),
        "ambiguous_exposure_usd": float(ledger.get("uncertain_possible_exposure_usd") or 0),
    }


def fallback_status(run_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Read saved fallback state without credentials, environment loading, or provider calls."""
    now = now or datetime.now(timezone.utc)
    developer = read_json(run_dir / "gemini_ledger.json", {}) or {}
    vertex = read_json(run_dir / "gemini_vertex_fallback_ledger.json", {}) or {}
    results = read_json(run_dir / "gemini_results.json", {}) or {"items": {}}
    state = read_json(run_dir / "gemini_transport_state.json", {}) or {}
    manifest = read_json(run_dir / "cases.json", {}) or {}
    total = len(manifest.get("items", [])) or int(state.get("case_count") or len(results.get("items", {})))
    dev = _transport_metrics(developer, results, "developer_api")
    ver = _transport_metrics(vertex, results, "vertex_ai")
    paused = bool(state.get("developer_quota_exhausted"))
    expected = state.get("expected_reset_at")
    expired = False
    if expected:
        try:
            expired = datetime.fromisoformat(expected.replace("Z", "+00:00")) <= now
        except (TypeError, ValueError):
            pass
    enabled = bool(state.get("fallback_enabled"))
    available = bool(state.get("fallback_available"))
    vertex_used = ver["attempted_cases"] > 0
    fallback_state = "disabled" if not enabled else "unavailable" if not available else "active" if vertex_used or paused else "armed"
    completed = len(results.get("items", {}))
    unresolved = max(0, total - completed)
    developer_status = "paused" if paused else "active"
    vertex_status = "active fallback" if fallback_state == "active" else fallback_state
    remaining = None
    limit = state.get("vertex_cost_limit_usd")
    if type(limit) in {int, float}:
        remaining = max(0.0, float(limit) - ver["known_spend_usd"])
    resume = []
    if paused:
        resume.append("Developer API remains paused; an expired estimate does not re-enable or probe it.")
        if enabled and available:
            resume.append(f"{unresolved} unfinished cases will route directly to Vertex AI.")
        else:
            resume.append(f"{unresolved} unfinished cases remain unresolved because fallback is {fallback_state}.")
    else:
        resume.append(f"Developer API will handle {unresolved} unfinished cases first.")
    resume.append("No completed case will be repeated.")
    if remaining is not None:
        resume.append(f"Vertex fallback ceiling remaining: ${remaining:.2f}.")
    return {
        "schema_version": 1, "run_dir": str(run_dir), "active_transport": "vertex_ai" if paused and enabled and available else "developer_api",
        "developer_api": {**dev, "status": developer_status},
        "vertex_ai": {**ver, "status": vertex_status},
        "logical_gemini": {"completed": completed, "total": total, "still_missing": unresolved},
        "quota_pause": {"paused": paused, "pause_reason": state.get("pause_reason"),
            "paused_at": state.get("paused_at"), "expected_reset_at": expected,
            "expected_reset_timezone": state.get("expected_reset_timezone"),
            "reset_basis": state.get("reset_basis", "unknown"),
            "reset_time_confidence": state.get("reset_time_confidence", "unknown"),
            "estimate_expired": expired, "trigger_evidence": state.get("trigger_evidence", [])},
        "fallback": {"enabled": enabled, "available": available, "status": fallback_state,
            "direct_to_vertex_after_pause": paused and enabled and available,
            "preflight_status": state.get("fallback_preflight_status", "not_recorded")},
        "resume_plan": resume,
    }


def format_fallback_status(status: dict[str, Any]) -> str:
    dev, ver, logical, pause, fallback = (status[key] for key in ("developer_api", "vertex_ai", "logical_gemini", "quota_pause", "fallback"))
    reset = pause.get("expected_reset_at") or "unknown"
    if pause.get("expected_reset_at") and pause.get("expected_reset_timezone"):
        try:
            local_reset = datetime.fromisoformat(pause["expected_reset_at"].replace("Z", "+00:00")).astimezone(ZoneInfo(pause["expected_reset_timezone"]))
            reset = f"{local_reset.strftime('%Y-%m-%d %H:%M %Z')} ({pause['expected_reset_timezone']})"
        except (ValueError, TypeError, KeyError):
            pass
    if pause.get("estimate_expired"):
        reset += " (estimate passed; pause retained)"
    lines = [
        "Gemini",
        f"  active transport: {status['active_transport']}",
        f"  Developer API: completed={dev['completed']} failed={dev['failed_cases']} retries={dev['retries']} spend=${dev['known_spend_usd']:.4f} status={dev['status']}",
        f"    pause reason: {pause.get('pause_reason') or 'none'}; expected reset: {reset} ({pause.get('reset_time_confidence') or 'unknown'})",
        f"  Vertex AI: completed={ver['completed']} failed={ver['failed_cases']} retries={ver['retries']} spend=${ver['known_spend_usd']:.4f} status={ver['status']}",
        f"  Logical Gemini: completed={logical['completed']}/{logical['total']} still_missing={logical['still_missing']}",
        f"  fallback: {fallback['status']}; direct-to-Vertex={str(fallback['direct_to_vertex_after_pause']).lower()}",
        "Gemini resume plan:",
    ]
    lines.extend(f"- {row}" for row in status["resume_plan"])
    return "\n".join(lines)


def _pid_is_running(pid: Any) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def clear_expired_quota_pause(run_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    state_path = run_dir / "gemini_transport_state.json"
    state = read_json(state_path, None)
    if not isinstance(state, dict):
        raise RuntimeError("Gemini transport state is unavailable")
    if state.get("run_active") and _pid_is_running(state.get("active_pid")):
        raise RuntimeError("cannot clear quota pause while the run is active")
    if not state.get("developer_quota_exhausted"):
        raise RuntimeError("Developer API quota pause is not active")
    expected = state.get("expected_reset_at")
    if not expected:
        raise RuntimeError("quota reset is unknown; expired pause cannot be established")
    now = now or datetime.now(timezone.utc)
    reset = datetime.fromisoformat(expected.replace("Z", "+00:00"))
    if reset > now:
        raise RuntimeError("quota reset estimate has not expired")
    audit = {"action": "clear_expired_quota_pause", "cleared_at": utc_iso(now),
             "previous_pause": {key: state.get(key) for key in ("pause_reason", "paused_at", "expected_reset_at", "reset_basis", "reset_time_confidence")}}
    state.setdefault("pause_audit", []).append(audit)
    state.update({"developer_quota_exhausted": False, "consecutive_daily_quota_cases": 0,
                  "pause_reason": None, "paused_at": None, "pause_timestamp": None,
                  "expected_reset_at": None, "reset_basis": "cleared_by_operator",
                  "reset_time_confidence": "unknown", "trigger_evidence": []})
    atomic_write_json(state_path, state)
    return audit


def mark_interrupted_sending_ambiguous(run_dir: Path, *, transport: str = "vertex_ai") -> int:
    """Finalize interrupted local lifecycle state without sending or retrying a request."""
    path = run_dir / ("gemini_vertex_fallback_ledger.json" if transport == "vertex_ai" else "gemini_ledger.json")
    ledger = read_json(path, None)
    if not isinstance(ledger, dict):
        raise RuntimeError(f"missing Gemini {transport} ledger")
    completed = {(row.get("case_id"), row.get("transport_attempt_number")) for row in ledger.get("calls", [])}
    changed = 0
    for attempt in ledger.get("attempts", []):
        key = (attempt.get("case_id"), attempt.get("transport_attempt_number"))
        if attempt.get("lifecycle_state") == "sending" and key not in completed:
            attempt["lifecycle_state"] = "ambiguous_outcome"
            ledger.setdefault("ambiguous_outcomes", []).append({"case_id": key[0],
                "transport_attempt_number": key[1], "reason": "operator interrupted hung research request",
                "recorded_at": utc_iso(datetime.now(timezone.utc))})
            changed += 1
    if changed:
        atomic_write_json(path, ledger)
    return changed


class GeminiFallbackWorker:
    """One logical Gemini worker with explicit Developer and Vertex transports."""

    def __init__(
        self,
        *,
        cases: list[dict[str, Any]],
        quotes: dict[str, Any],
        images: dict[str, Any],
        run_dir: Path,
        developer_client: Any,
        vertex_client: GeminiVertexClient | Any | None,
        developer_limit: float,
        vertex_limit: float,
        combined_limit: float,
        fallback_enabled: bool = False,
        fallback_available: bool = False,
        quota_pause_threshold: int = DAILY_QUOTA_THRESHOLD,
        probe_after_pause: bool = False,
        reset_timezone: str = "Europe/London",
        estimated_daily_reset_hour: int | None = None,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
        prompt_builder: Callable[[dict[str, Any], dict[str, Any]], str] = common_prompt,
        result_validator: Callable[[Any], dict[str, Any]] = validate_bakeoff_result,
        prompt_version: str = BAKEOFF_PROMPT_VERSION,
        schema_version: int = BAKEOFF_SCHEMA_VERSION,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = "gemini"
        self.cases = cases
        self.quotes = quotes
        self.images = images
        self.run_dir = run_dir
        self.developer = developer_client
        self.vertex = vertex_client
        self.developer_limit = developer_limit
        self.vertex_limit = vertex_limit
        self.combined_limit = combined_limit
        self.fallback_enabled = fallback_enabled
        self.fallback_available = fallback_available
        self.quota_pause_threshold = quota_pause_threshold
        self.probe_after_pause = probe_after_pause
        self.reset_timezone = reset_timezone
        self.estimated_daily_reset_hour = estimated_daily_reset_hour
        self.status_callback = status_callback
        self.prompt_builder = prompt_builder
        self.result_validator = result_validator
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.sleep = sleep
        self.developer_ledger_path = run_dir / "gemini_ledger.json"
        self.vertex_ledger_path = run_dir / "gemini_vertex_fallback_ledger.json"
        self.results_path = run_dir / "gemini_results.json"
        self.state_path = run_dir / "gemini_transport_state.json"

    def _load(self):
        developer = read_json(self.developer_ledger_path, None) or {
            "schema_version": 2, "logical_provider": "gemini", "transport": "developer_api",
            "model": self.developer.model, "attempts": [], "calls": [], "confirmed_failures": [],
            "ambiguous_outcomes": [], "uncertain_possible_exposure_usd": 0.0,
        }
        vertex = read_json(self.vertex_ledger_path, None) or {
            "schema_version": 1, "logical_provider": "gemini", "transport": "vertex_ai",
            "model": getattr(self.vertex, "model", VERTEX_MODEL), "attempts": [], "calls": [],
            "confirmed_failures": [], "ambiguous_outcomes": [], "uncertain_possible_exposure_usd": 0.0,
        }
        results = read_json(self.results_path, None) or {
            "schema_version": 2, "provider": "gemini", "model": self.developer.model,
            "prompt_version": BAKEOFF_PROMPT_VERSION, "items": {}, "failures": {},
        }
        state = read_json(self.state_path, None) or {
            "schema_version": 1, "developer_quota_exhausted": False,
            "consecutive_daily_quota_cases": 0, "pause_reason": None, "pause_timestamp": None,
            "paused_at": None, "expected_reset_at": None, "expected_reset_timezone": self.reset_timezone,
            "reset_basis": "unknown", "reset_time_confidence": "unknown", "trigger_evidence": [],
            "pause_audit": [], "run_active": False, "active_pid": None,
        }
        state.update({"fallback_enabled": self.fallback_enabled, "fallback_available": self.fallback_available,
                      "fallback_preflight_status": "passed" if self.fallback_available else "unavailable",
                      "developer_cost_limit_usd": self.developer_limit,
                      "vertex_cost_limit_usd": self.vertex_limit, "combined_cost_limit_usd": self.combined_limit,
                      "case_count": len(self.cases), "probe_after_pause": self.probe_after_pause})
        # A sending record with no completed call is ambiguous and must never cross transports.
        for ledger in (developer, vertex):
            completed = {(x["case_id"], x["transport_attempt_number"]) for x in ledger["calls"]}
            for attempt in ledger["attempts"]:
                key = (attempt["case_id"], attempt["transport_attempt_number"])
                if attempt["lifecycle_state"] == "sending" and key not in completed:
                    attempt["lifecycle_state"] = "ambiguous_outcome"
                    ledger["ambiguous_outcomes"].append({"case_id": key[0], "transport_attempt_number": key[1], "recovered_on_resume": True})
        self._persist(developer, vertex, results, state)
        return developer, vertex, results, state

    def prepare_status(self) -> dict[str, Any]:
        self._load()
        return fallback_status(self.run_dir)

    def _persist(self, developer, vertex, results, state):
        atomic_write_json(self.developer_ledger_path, developer)
        atomic_write_json(self.vertex_ledger_path, vertex)
        atomic_write_json(self.results_path, results)
        atomic_write_json(self.state_path, state)

    @staticmethod
    def _spend(ledger):
        return sum(float(row.get("cost_usd") or 0) for row in ledger["calls"])

    def _guard(self, transport: str, maximum: float, developer, vertex):
        own = self._spend(developer if transport == "developer_api" else vertex)
        limit = self.developer_limit if transport == "developer_api" else self.vertex_limit
        if own + maximum > limit:
            raise RuntimeError(f"Gemini {transport} cost ceiling reached")
        other = 0.0
        for provider in ("grok", "openai", "anthropic"):
            ledger = read_json(self.run_dir / f"{provider}_ledger.json", {}) or {}
            other += sum(float(row.get("cost_usd") or 0) for row in ledger.get("calls", []))
        if other + self._spend(developer) + self._spend(vertex) + maximum > self.combined_limit:
            raise RuntimeError("combined Gemini cost ceiling reached")

    def _begin(self, ledger, case, transport, number, prompt, parent_ids=None):
        attempt = {
            "run_id": self.run_dir.name, "logical_provider": "gemini", "transport": transport,
            "case_id": case["case_id"], "quote_hash": case["quote_hash"],
            "image_basename": case["image_basename"], "model_slug": ledger["model"],
            "input_hash": case["normalised_input_hash"], "prompt_version": self.prompt_version,
            "schema_version": self.schema_version, "attempt_number": number,
            "transport_attempt_number": number, "timestamp": time.time(), "lifecycle_state": "prepared",
            "parent_request_ids": parent_ids or [], "fallback_used": transport == "vertex_ai",
        }
        ledger["attempts"].append(attempt)
        atomic_write_json(self.developer_ledger_path if transport == "developer_api" else self.vertex_ledger_path, ledger)
        attempt["lifecycle_state"] = "sending"
        atomic_write_json(self.developer_ledger_path if transport == "developer_api" else self.vertex_ledger_path, ledger)
        return attempt

    def _record_success(self, ledger, attempt, response, case, results, transport, fallback_reason=None):
        attempt["lifecycle_state"] = "response_received"
        attempt["request_id"] = response.get("request_id")
        call = {
            "case_id": case["case_id"], "transport_attempt_number": attempt["transport_attempt_number"],
            "request_id": response.get("request_id"), "timestamp": time.time(),
            "latency_seconds": response["latency_seconds"], "cost_usd": response["cost_usd"], **response["usage"],
        }
        ledger["calls"].append(call)
        value = self.result_validator(response["content"])
        attempt["lifecycle_state"] = "completed"
        results["items"][case["case_id"]] = {
            "case_id": case["case_id"], "quote_hash": case["quote_hash"],
            "image_basename": case["image_basename"], **value,
            "model": ledger["model"], "logical_provider": "gemini", "transport": transport,
            "fallback_used": transport == "vertex_ai", "fallback_reason": fallback_reason,
            "parent_request_id": attempt["parent_request_ids"][-1] if attempt["parent_request_ids"] else None,
            "input_hash": case["normalised_input_hash"], "prompt_version": self.prompt_version,
            "critic_schema_version": self.schema_version,
            "attempt_number": attempt["attempt_number"], "transport_attempt_number": attempt["transport_attempt_number"],
            "final_status": "completed_via_vertex_fallback" if transport == "vertex_ai" else "completed",
        }

    def _developer_attempts(self, case, prompt, developer, vertex, results, state):
        cid = case["case_id"]
        prior = [x for x in developer["attempts"] if x["case_id"] == cid]
        if any(x["lifecycle_state"] == "ambiguous_outcome" for x in prior):
            return "ambiguous"
        daily_case = False
        while len(prior) < 2 and cid not in results["items"]:
            number = len(prior) + 1
            maximum = maximum_attempt_cost("gemini", prompt)
            self._guard("developer_api", maximum, developer, vertex)
            attempt = self._begin(developer, case, "developer_api", number, prompt)
            try:
                response = self.developer.call(prompt)
            except requests.HTTPError as exc:
                classification = classify_developer_failure(exc)
                attempt["lifecycle_state"] = "confirmed_failure"
                attempt["failure"] = classification
                developer["confirmed_failures"].append({"case_id": cid, "transport_attempt_number": number, **classification})
                atomic_write_json(self.developer_ledger_path, developer)
                if not classification["fallback_eligible"]:
                    return "ineligible"
                daily_case = daily_case or classification.get("daily_quota", False)
                if classification.get("daily_quota"):
                    state.setdefault("trigger_evidence", []).append({
                        "case_id": cid, "transport_attempt_number": number,
                        "classification": classification["classification"],
                        "http_status": classification.get("http_status"),
                        "provider_status": classification.get("provider_status"),
                        "reset_headers": classification.get("reset_headers", {}),
                    })
                prior = [x for x in developer["attempts"] if x["case_id"] == cid]
                if len(prior) < 2:
                    self.sleep(min(2 ** number, 4))
                    continue
                if daily_case:
                    state["consecutive_daily_quota_cases"] += 1
                    if state["consecutive_daily_quota_cases"] >= self.quota_pause_threshold:
                        paused = datetime.now(timezone.utc)
                        reset = quota_reset_metadata(classification.get("reset_headers"), now=paused,
                            timezone_name=self.reset_timezone, estimated_daily_reset_hour=self.estimated_daily_reset_hour)
                        state.update({"developer_quota_exhausted": True, "pause_reason": "daily_quota_exhausted",
                            "pause_timestamp": paused.timestamp(), "paused_at": utc_iso(paused), **reset})
                        atomic_write_json(self.state_path, state)
                return "eligible_quota_exhausted"
            except BaseException as exc:
                attempt["lifecycle_state"] = "ambiguous_outcome"
                attempt["error"] = f"{type(exc).__name__}: {exc}"
                developer["ambiguous_outcomes"].append({"case_id": cid, "transport_attempt_number": number, "error": attempt["error"]})
                developer["uncertain_possible_exposure_usd"] += maximum
                atomic_write_json(self.developer_ledger_path, developer)
                return "ambiguous"
            try:
                self._record_success(developer, attempt, response, case, results, "developer_api")
            except ValueError as exc:
                attempt["lifecycle_state"] = "confirmed_failure"
                attempt["schema_error"] = str(exc)
                developer["confirmed_failures"].append({"case_id": cid, "transport_attempt_number": number, "schema_error": str(exc)})
                prior = [x for x in developer["attempts"] if x["case_id"] == cid]
                self._persist(developer, vertex, results, state)
                if len(prior) < 2:
                    continue
                return "ineligible"
            state["consecutive_daily_quota_cases"] = 0
            self._persist(developer, vertex, results, state)
            return "completed"
        return "eligible_quota_exhausted" if len(prior) >= 2 else "incomplete"

    def _vertex_attempts(self, case, prompt, reason, developer, vertex, results, state):
        cid = case["case_id"]
        prior = [x for x in vertex["attempts"] if x["case_id"] == cid]
        if any(x["lifecycle_state"] == "ambiguous_outcome" for x in prior):
            return "ambiguous"
        parent_ids = [x.get("request_id") for x in developer["attempts"] if x["case_id"] == cid and x.get("request_id")]
        while len(prior) < 2 and cid not in results["items"]:
            number = len(prior) + 1
            maximum = maximum_attempt_cost("gemini", prompt)
            self._guard("vertex_ai", maximum, developer, vertex)
            attempt = self._begin(vertex, case, "vertex_ai", number, prompt, parent_ids)
            try:
                response = self.vertex.call(prompt)
            except errors.APIError as exc:
                detail = error_details(exc)
                attempt["lifecycle_state"] = "confirmed_failure"
                attempt["failure"] = detail
                vertex["confirmed_failures"].append({"case_id": cid, "transport_attempt_number": number, **detail})
                atomic_write_json(self.vertex_ledger_path, vertex)
                prior = [x for x in vertex["attempts"] if x["case_id"] == cid]
                if detail.get("code") in {429, 500, 502, 503, 504} and len(prior) < 2:
                    self.sleep(min(2 ** number, 4))
                    continue
                return "failed"
            except BaseException as exc:
                attempt["lifecycle_state"] = "ambiguous_outcome"
                attempt["error"] = f"{type(exc).__name__}: {exc}"
                vertex["ambiguous_outcomes"].append({"case_id": cid, "transport_attempt_number": number, "error": attempt["error"]})
                vertex["uncertain_possible_exposure_usd"] += maximum
                atomic_write_json(self.vertex_ledger_path, vertex)
                return "ambiguous"
            try:
                self._record_success(vertex, attempt, response, case, results, "vertex_ai", reason)
            except ValueError as exc:
                attempt["lifecycle_state"] = "confirmed_failure"
                attempt["schema_error"] = str(exc)
                vertex["confirmed_failures"].append({"case_id": cid, "transport_attempt_number": number, "schema_error": str(exc)})
                prior = [x for x in vertex["attempts"] if x["case_id"] == cid]
                self._persist(developer, vertex, results, state)
                if len(prior) < 2:
                    continue
                return "failed"
            self._persist(developer, vertex, results, state)
            return "completed"
        return "failed"

    def run(self):
        try:
            return self._run()
        finally:
            state = read_json(self.state_path, {}) or {}
            if state.get("active_pid") == os.getpid():
                state.update({"run_active": False, "active_pid": None,
                              "run_finished_at": utc_iso(datetime.now(timezone.utc))})
                atomic_write_json(self.state_path, state)

    def _run(self):
        developer, vertex, results, state = self._load()
        state.update({"run_active": True, "active_pid": os.getpid(), "run_started_at": utc_iso(datetime.now(timezone.utc))})
        atomic_write_json(self.state_path, state)
        if self.fallback_enabled and self.fallback_available:
            if self.vertex is None:
                raise RuntimeError("Vertex fallback marked available without a client")
            require_transport_parity(self.developer, self.vertex)
        counters = Counter()
        for case in self.cases:
            cid = case["case_id"]
            if cid in results["items"]:
                continue
            prompt = self.prompt_builder(self.quotes[case["quote_hash"]], self.images[case["image_basename"]])
            direct_vertex = bool(state["developer_quota_exhausted"] and not self.probe_after_pause)
            if direct_vertex:
                outcome = "eligible_quota_exhausted"
                counters["routed_directly_after_pause"] += 1
            else:
                outcome = self._developer_attempts(case, prompt, developer, vertex, results, state)
            if outcome == "eligible_quota_exhausted":
                counters["eligible_fallback_triggers"] += 1
                if self.fallback_enabled and self.fallback_available:
                    self._vertex_attempts(case, prompt, "developer_quota_exhausted", developer, vertex, results, state)
                else:
                    results["failures"][cid] = {"reason": "developer_quota_exhausted", "fallback_available": self.fallback_available}
            elif outcome not in {"completed"}:
                results["failures"][cid] = {"reason": outcome}
            self._persist(developer, vertex, results, state)
            if self.status_callback is not None:
                self.status_callback(fallback_status(self.run_dir))
        summary = {
            "logical_provider": "gemini", "completed": len(results["items"]),
            "completed_via_developer_api": sum(x.get("transport") == "developer_api" for x in results["items"].values()),
            "completed_via_vertex_fallback": sum(x.get("transport") == "vertex_ai" for x in results["items"].values()),
            "still_missing": len(self.cases) - len(results["items"]),
            "developer_known_spend_usd": self._spend(developer), "vertex_known_spend_usd": self._spend(vertex),
            "developer_ambiguous_exposure_usd": developer["uncertain_possible_exposure_usd"],
            "vertex_ambiguous_exposure_usd": vertex["uncertain_possible_exposure_usd"],
            "provider_wide_quota_pause_activated": state["developer_quota_exhausted"], **counters,
        }
        summary.update({
            "provider": "gemini", "exhausted": summary["still_missing"],
            "known_cost_usd": summary["developer_known_spend_usd"] + summary["vertex_known_spend_usd"],
            "uncertain_possible_exposure_usd": summary["developer_ambiguous_exposure_usd"] + summary["vertex_ambiguous_exposure_usd"],
            "attempts": len(developer["attempts"]) + len(vertex["attempts"]),
            "max_active_requests": 1, "paused_reason": state["pause_reason"],
        })
        case_table = []
        for case in self.cases:
            cid = case["case_id"]
            dev = [x for x in developer["attempts"] if x["case_id"] == cid]
            ver = [x for x in vertex["attempts"] if x["case_id"] == cid]
            item = results["items"].get(cid)
            case_table.append({
                "case_id": cid,
                "developer_outcome": dev[-1]["lifecycle_state"] if dev else "skipped_after_quota_pause",
                "fallback_reason": item.get("fallback_reason") if item else None,
                "vertex_outcome": ver[-1]["lifecycle_state"] if ver else "not_used",
                "final_status": item.get("final_status") if item else results["failures"].get(cid, {}).get("reason", "incomplete"),
            })
        summary["cases"] = case_table
        state.update({"run_active": False, "active_pid": None, "run_finished_at": utc_iso(datetime.now(timezone.utc))})
        self._persist(developer, vertex, results, state)
        atomic_write_json(self.run_dir / "gemini_transport_summary.json", summary)
        atomic_write_json(self.run_dir / "gemini_worker_summary.json", summary)
        return summary
