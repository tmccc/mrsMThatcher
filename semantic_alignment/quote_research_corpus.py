from __future__ import annotations

import hashlib
import json
import os
import signal
import statistics
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

from .io import atomic_write_json, read_json
from .quote_research_gemini import (
    MAX_ATTEMPTS, MODEL, PROMPT_VERSION, SCHEMA_VERSION,
    bind_packet_to_grounding, classify_http_failure, maximum_next_cost,
    preflight as pilot_preflight, quote_hash, require_transport_parity,
    research_prompt, utc_now, validate_packet,
)

CORPUS_SCHEMA_VERSION = 1
DEFAULT_DEVELOPER_LIMIT = 40.0
DEFAULT_VERTEX_LIMIT = 65.0
DEFAULT_COMBINED_LIMIT = 75.0
TERMINAL = {"valid", "permanent_failure"}
RETRYABLE = {"pending", "transient_failure", "validation_failure", "interrupted"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _append_jsonl(path: Path, row: dict[str, Any], lock: threading.RLock | None = None) -> None:
    context = lock or threading.RLock()
    with context:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def build_corpus_manifest(source: dict[str, Any]) -> dict[str, Any]:
    records = source.get("records") or []
    occurrences = source.get("occurrences") or []
    if len(records) != 632 or len({row["quote_id"] for row in records}) != 632:
        raise RuntimeError("full corpus must contain exactly 632 distinct quote IDs")
    occurrence_map: dict[str, list[dict[str, Any]]] = {}
    for occurrence in occurrences:
        occurrence_map.setdefault(occurrence["quote_id"], []).append(dict(occurrence))
    frozen = []
    for row in records:
        if row.get("quote_hash") != quote_hash(row["quote_text"]):
            raise RuntimeError(f"quote hash mismatch: {row.get('quote_id')}")
        source_occurrences = list(row.get("source_occurrences") or occurrence_map.get(row["quote_id"], []))
        if not source_occurrences:
            raise RuntimeError(f"missing occurrence metadata: {row['quote_id']}")
        item = {**row, "source_occurrences": source_occurrences,
                "duplicate_occurrence_count": len(source_occurrences)}
        item["input_hash"] = hashlib.sha256(_canonical({
            "quote_id": item["quote_id"], "quote_hash": item["quote_hash"],
            "quote_text": item["quote_text"], "source_occurrences": source_occurrences,
            "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
        })).hexdigest()
        frozen.append(item)
    frozen.sort(key=lambda row: (min(x["line_number"] for x in row["source_occurrences"]), row["quote_id"]))
    payload = {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "record_kind": "quote_research_full_corpus_manifest",
        "source_manifest_version": source.get("manifest_version"),
        "source_builder_version": source.get("builder_version"),
        "record_count": len(frozen), "source_occurrence_count": len(occurrences),
        "records": frozen,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return payload


def verify_corpus_manifest(manifest: dict[str, Any]) -> None:
    expected = manifest.get("manifest_sha256")
    check = dict(manifest)
    check.pop("manifest_sha256", None)
    if hashlib.sha256(_canonical(check)).hexdigest() != expected:
        raise RuntimeError("immutable corpus manifest hash mismatch")
    expected_count = int(manifest.get("record_count") or 0)
    if expected_count <= 0 or len(manifest.get("records") or []) != expected_count:
        raise RuntimeError("immutable research manifest record count mismatch")
    if len({row["quote_id"] for row in manifest["records"]}) != expected_count:
        raise RuntimeError("duplicate quote IDs in corpus manifest")


def corpus_preflight(records: list[dict[str, Any]], completed: int = 0,
                     remaining_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    remaining = remaining_records if remaining_records is not None else records[completed:]
    base = pilot_preflight(remaining)
    expected = float(base["expected_cost_usd"])
    high = float(base["guarded_retry_cost_usd"])
    low = expected * 0.62
    return {
        **base, "records": len(records), "remaining_quotes": len(remaining),
        "projected_low_cost_usd": round(low, 6),
        "projected_expected_cost_usd": round(expected, 6),
        "projected_high_cost_usd": round(high, 6),
        "developer_ceiling_usd": DEFAULT_DEVELOPER_LIMIT,
        "vertex_ceiling_usd": DEFAULT_VERTEX_LIMIT,
        "combined_ceiling_usd": DEFAULT_COMBINED_LIMIT,
        "allowed": expected <= DEFAULT_COMBINED_LIMIT,
    }


def repair_prompt(record: dict[str, Any], raw_text: str, validation_error: str) -> str:
    bounded = raw_text[:24000]
    return f"""Prompt version: {PROMPT_VERSION}-schema-repair
Repair and revalidate the grounded research packet below. Return only one JSON object matching the same research-packet schema. You MUST invoke Google Search at least once during this repair. Re-check every material historical claim against the returned search evidence. Preserve supported content, but do not invent facts, sources, dates, locators, or missing evidence. Use "unknown" where a string is permitted and evidence is absent. Preserve quote_id and quote_text exactly. A response without grounding metadata linked to at least one supporting source is invalid.

quote_id: {record['quote_id']}
quote_text: {json.dumps(record['quote_text'], ensure_ascii=False)}
validation_error: {json.dumps(validation_error)}

RAW RESPONSE TO REPAIR:
{bounded}
"""


def _raw_text(raw: dict[str, Any]) -> str:
    return "".join(str(part.get("text") or "")
                   for candidate in raw.get("candidates") or []
                   for part in (candidate.get("content") or {}).get("parts") or [])


class CorpusRunner:
    def __init__(self, run_dir: Path, manifest: dict[str, Any], developer: Any, vertex: Any,
                 developer_limit: float, vertex_limit: float, combined_limit: float,
                 developer_concurrency: int = 2, vertex_concurrency: int = 2,
                 sleep: Callable[[float], None] = time.sleep,
                 prompt_builder: Callable[[dict[str, Any]], str] = research_prompt,
                 repair_builder: Callable[[dict[str, Any], str, str], str] = repair_prompt,
                 identity_binder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None):
        verify_corpus_manifest(manifest)
        self.run_dir = run_dir
        self.manifest = manifest
        self.records = {row["quote_id"]: row for row in manifest["records"]}
        self.order = [row["quote_id"] for row in manifest["records"]]
        self.developer = developer
        self.vertex = vertex
        self.limits = {"developer_api": developer_limit, "vertex_ai": vertex_limit}
        self.combined_limit = combined_limit
        self.concurrency = {"developer_api": max(1, developer_concurrency),
                            "vertex_ai": max(1, vertex_concurrency)}
        self.sleep = sleep
        self.prompt_builder = prompt_builder
        self.repair_builder = repair_builder
        self.identity_binder = identity_binder
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.paths = {name: run_dir / name for name in (
            "research_packets.json", "unresolved_quotes.json", "permanent_failures.json",
            "attempts.jsonl", "grounding_sources.json", "cost_ledger.json",
            "transport_status.json", "run_state.json", "status.json")}
        self._load()

    def _load(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.packets = read_json(self.paths["research_packets.json"], None) or {"schema_version": 1, "items": {}}
        self.unresolved = read_json(self.paths["unresolved_quotes.json"], None) or {"schema_version": 1, "items": {}}
        self.permanent = read_json(self.paths["permanent_failures.json"], None) or {"schema_version": 1, "items": {}, "reset_audit": []}
        self.grounding = read_json(self.paths["grounding_sources.json"], None) or {"schema_version": 1, "items": {}}
        self.costs = read_json(self.paths["cost_ledger.json"], None) or {
            "schema_version": 1, "calls": [], "reservations": {}, "uncertain_attempts": [],
            "developer_known_spend_usd": 0.0, "vertex_known_spend_usd": 0.0,
            "combined_known_spend_usd": 0.0, "ambiguous_possible_exposure_usd": 0.0}
        for key, default in (("calls", []), ("reservations", {}), ("uncertain_attempts", []),
                             ("developer_known_spend_usd", 0.0), ("vertex_known_spend_usd", 0.0),
                             ("combined_known_spend_usd", 0.0), ("ambiguous_possible_exposure_usd", 0.0)):
            self.costs.setdefault(key, default)
        self.transport = read_json(self.paths["transport_status.json"], None) or {
            "schema_version": 1, "developer_paused": False, "pause_reason": None,
            "paused_at": None, "trigger_attempts": [], "direct_to_vertex_count": 0,
            "fallback_enabled": True, "model": MODEL}
        for key, default in (("developer_paused", False), ("pause_reason", None), ("paused_at", None),
                             ("trigger_attempts", []), ("direct_to_vertex_count", 0),
                             ("fallback_enabled", True), ("model", MODEL)):
            self.transport.setdefault(key, default)
        self.state = read_json(self.paths["run_state.json"], None) or {
            "schema_version": 1, "run_id": self.run_dir.name,
            "manifest_sha256": self.manifest["manifest_sha256"], "created_at": utc_now(),
            "updated_at": utc_now(), "shutdown_requested": False, "items": {}}
        if self.state.get("manifest_sha256") != self.manifest["manifest_sha256"]:
            raise RuntimeError("run state belongs to a different immutable manifest")
        for quote_id in self.order:
            if quote_id in self.packets["items"]:
                status = "valid"
            elif quote_id in self.permanent["items"]:
                status = "permanent_failure"
            else:
                status = "pending"
            self.state["items"].setdefault(quote_id, {
                "status": status, "transport_attempts": {"developer_api": 0, "vertex_ai": 0},
                "attempt_epoch": 0, "last_error": None, "updated_at": utc_now()})
            self.state["items"][quote_id].setdefault("attempt_epoch", 0)
            if status in TERMINAL:
                self.state["items"][quote_id]["status"] = status
        self._reconcile_attempt_counts_and_crashes()
        self._persist_all()

    def _reconcile_attempt_counts_and_crashes(self) -> None:
        if not self.paths["attempts.jsonl"].exists():
            return
        rows = [json.loads(line) for line in self.paths["attempts.jsonl"].read_text().splitlines() if line.strip()]
        terminal_states = {"completed", "confirmed_failure", "transient_failure", "validation_failure",
                           "ambiguous_outcome", "permanent_failure", "cancelled_before_send"}
        grouped: dict[tuple[str, str, int, int], set[str]] = {}
        for row in rows:
            quote_id, transport = row.get("quote_id"), row.get("transport")
            if quote_id in self.state["items"] and transport in {"developer_api", "vertex_ai"}:
                number = int(row.get("transport_attempt_number", row.get("attempt_number", 0)) or 0)
                epoch = int(row.get("attempt_epoch", 0) or 0)
                if epoch == int(self.state["items"][quote_id].get("attempt_epoch", 0)):
                    self.state["items"][quote_id]["transport_attempts"][transport] = max(
                        self.state["items"][quote_id]["transport_attempts"].get(transport, 0), number)
                grouped.setdefault((quote_id, transport, epoch, number), set()).add(row.get("state"))
        for (quote_id, transport, epoch, number), states in grouped.items():
            if epoch != int(self.state["items"][quote_id].get("attempt_epoch", 0)):
                continue
            if "sending" in states and not (states & terminal_states):
                row = {"run_id": self.run_dir.name, "quote_id": quote_id, "transport": transport,
                       "transport_attempt_number": number, "attempt_epoch": epoch,
                       "state": "transient_failure",
                       "failure": {"kind": "interrupted", "message": "restart found unterminated sending attempt"},
                       "timestamp": utc_now()}
                _append_jsonl(self.paths["attempts.jsonl"], row, self.lock)
                item = self.state["items"][quote_id]
                if item["status"] not in TERMINAL:
                    item.update({"status": "interrupted", "last_error": row["failure"], "updated_at": utc_now()})
        self.costs["reservations"] = {}

    def _persist_all(self) -> None:
        with self.lock:
            self.state["updated_at"] = utc_now()
            for key, value in (("research_packets.json", self.packets),
                               ("unresolved_quotes.json", self.unresolved),
                               ("permanent_failures.json", self.permanent),
                               ("grounding_sources.json", self.grounding),
                               ("cost_ledger.json", self.costs),
                               ("transport_status.json", self.transport),
                               ("run_state.json", self.state)):
                atomic_write_json(self.run_dir / key, value)
            atomic_write_json(self.paths["status.json"], self.status_payload())

    def request_stop(self, reason: str = "operator_signal") -> None:
        with self.lock:
            self.stop_event.set()
            self.state["shutdown_requested"] = True
            self.state["shutdown_reason"] = reason
            self.state["updated_at"] = utc_now()
            atomic_write_json(self.paths["run_state.json"], self.state)

    def _spend(self, transport: str) -> float:
        return sum(float(row["cost_usd"]) for row in self.costs["calls"] if row["transport"] == transport)

    def _reserve(self, key: str, transport: str, maximum: float) -> None:
        with self.lock:
            own = self._spend(transport)
            own_reserved = sum(float(row["maximum_cost_usd"]) for row in self.costs["reservations"].values()
                               if row["transport"] == transport)
            combined = sum(float(row["cost_usd"]) for row in self.costs["calls"])
            reserved = sum(float(row["maximum_cost_usd"]) for row in self.costs["reservations"].values())
            exposure = float(self.costs.get("ambiguous_possible_exposure_usd") or 0)
            if own + own_reserved + maximum > self.limits[transport]:
                raise RuntimeError(f"{transport} cost ceiling reached")
            if combined + reserved + exposure + maximum > self.combined_limit:
                raise RuntimeError("combined cost ceiling reached")
            self.costs["reservations"][key] = {"transport": transport, "maximum_cost_usd": maximum}
            atomic_write_json(self.paths["cost_ledger.json"], self.costs)

    def _release(self, key: str) -> None:
        with self.lock:
            self.costs["reservations"].pop(key, None)

    def _record_attempt(self, row: dict[str, Any]) -> None:
        _append_jsonl(self.paths["attempts.jsonl"], row, self.lock)

    def _mark_permanent(self, record: dict[str, Any], failure: dict[str, Any], transport: str) -> None:
        with self.lock:
            quote_id = record["quote_id"]
            payload = {"quote_id": quote_id, "quote_hash": record["quote_hash"],
                       "input_hash": record["input_hash"], "transport": transport,
                       "failure": failure, "recorded_at": utc_now(),
                       "attempts": dict(self.state["items"][quote_id]["transport_attempts"])}
            self.permanent["items"][quote_id] = payload
            self.unresolved["items"].pop(quote_id, None)
            self.state["items"][quote_id].update({"status": "permanent_failure",
                "last_error": failure, "updated_at": utc_now()})
            self._persist_all()

    def _mark_retryable(self, record: dict[str, Any], status: str, failure: dict[str, Any]) -> None:
        with self.lock:
            quote_id = record["quote_id"]
            self.unresolved["items"][quote_id] = {"quote_id": quote_id, "status": status,
                "failure": failure, "attempts": dict(self.state["items"][quote_id]["transport_attempts"]),
                "updated_at": utc_now()}
            self.state["items"][quote_id].update({"status": status, "last_error": failure, "updated_at": utc_now()})
            self._persist_all()

    def _mark_valid(self, record: dict[str, Any], packet: dict[str, Any], response: dict[str, Any], transport: str) -> None:
        with self.lock:
            quote_id = record["quote_id"]
            self.packets["items"][quote_id] = {**packet, "transport": transport, "model": MODEL,
                "prompt_version": PROMPT_VERSION, "validation_status": "valid",
                "grounding_source_count": len(packet["sources"]), "completed_at": utc_now()}
            self.grounding["items"][quote_id] = {"transport": transport, **response["grounding"]}
            self.unresolved["items"].pop(quote_id, None)
            self.permanent["items"].pop(quote_id, None)
            self.state["items"][quote_id].update({"status": "valid", "last_error": None,
                "completed_transport": transport, "updated_at": utc_now()})
            self._persist_all()

    def _attempt(self, record: dict[str, Any], client: Any, prompt: str, repair: bool = False) -> dict[str, Any]:
        quote_id, transport = record["quote_id"], client.transport
        with self.lock:
            item = self.state["items"][quote_id]
            epoch = int(item.get("attempt_epoch", 0))
            number = int(item["transport_attempts"].get(transport, 0)) + 1
            item["transport_attempts"][transport] = number
            item.update({"status": "active", "active_transport": transport, "updated_at": utc_now()})
            self._persist_all()
        maximum = maximum_next_cost(prompt)
        key = f"{quote_id}:{transport}:{epoch}:{number}"
        try:
            self._reserve(key, transport, maximum)
        except RuntimeError:
            with self.lock:
                self.state["items"][quote_id].update({"status": "pending", "active_transport": None,
                                                       "updated_at": utc_now()})
                self._persist_all()
            raise
        base = {"run_id": self.run_dir.name, "quote_id": quote_id, "quote_hash": record["quote_hash"],
                "input_hash": hashlib.sha256(prompt.encode()).hexdigest(), "manifest_input_hash": record["input_hash"],
                "transport": transport, "model": client.model, "transport_attempt_number": number,
                "attempt_epoch": epoch,
                "repair_attempt": repair, "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION, "timestamp": utc_now()}
        self._record_attempt({**base, "state": "prepared"})
        if self.stop_event.is_set():
            self._release(key)
            self._record_attempt({**base, "state": "cancelled_before_send"})
            return {"outcome": "cancelled"}
        self._record_attempt({**base, "state": "sending"})
        started = time.monotonic()
        try:
            response = client.call(prompt)
        except Exception as exc:
            elapsed = time.monotonic() - started
            failure = classify_http_failure(exc)
            failure["elapsed_seconds"] = elapsed
            failure["timeout_type"] = ("read_timeout" if "ReadTimeout" in failure.get("message", "") else
                                       "connect_timeout" if "ConnectTimeout" in failure.get("message", "") else None)
            self._release(key)
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                error_dir = self.run_dir / "raw_responses" / quote_id
                error_dir.mkdir(parents=True, exist_ok=True)
                error_prefix = f"reset_{epoch}_" if epoch else ""
                error_path = error_dir / f"{error_prefix}{transport}_attempt_{number}_http_error.json"
                try:
                    error_payload = exc.response.json()
                except ValueError:
                    error_payload = {"body": exc.response.text[:20000]}
                atomic_write_json(error_path, error_payload)
                failure["raw_error_path"] = str(error_path.relative_to(self.run_dir))
            state = "transient_failure" if failure["kind"] in {"transient", "ambiguous", "quota_429"} else "confirmed_failure"
            self._record_attempt({**base, "state": state, "failure": failure})
            if failure["kind"] == "ambiguous":
                with self.lock:
                    self.costs["uncertain_attempts"].append({"quote_id": quote_id, "transport": transport,
                        "transport_attempt_number": number, "attempt_epoch": epoch,
                        "maximum_possible_cost_usd": maximum})
                    self.costs["ambiguous_possible_exposure_usd"] = sum(
                        float(row["maximum_possible_cost_usd"]) for row in self.costs["uncertain_attempts"])
                    atomic_write_json(self.paths["cost_ledger.json"], self.costs)
            return {"outcome": failure["kind"], "failure": failure}
        self._release(key)
        raw_dir = self.run_dir / "raw_responses" / quote_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"reset_{epoch}_" if epoch else ""
        raw_path = raw_dir / f"{prefix}{transport}_attempt_{number}.json"
        atomic_write_json(raw_path, response["raw"])
        extracted_path = raw_dir / f"{prefix}{transport}_attempt_{number}_extracted.json"
        atomic_write_json(extracted_path, {"content": response.get("content"),
            "parse_error": response.get("parse_error"), "response_text": _raw_text(response["raw"])})
        self._record_attempt({**base, "state": "response_received", "request_id": response.get("request_id"),
                              "raw_response_path": str(raw_path.relative_to(self.run_dir))})
        with self.lock:
            call = {"quote_id": quote_id, "transport": transport, "transport_attempt_number": number,
                    "request_id": response.get("request_id"), "timestamp": utc_now(),
                    "attempt_epoch": epoch,
                    "latency_seconds": response["latency_seconds"],
                    "search_query_count": len(response["grounding"]["queries"]), **response["usage"],
                    "token_cost_usd": response["token_cost_usd"],
                    "search_cost_usd_conservative": response["search_cost_usd_conservative"],
                    "cost_usd": response["cost_usd"]}
            self.costs["calls"].append(call)
            self.costs["developer_known_spend_usd"] = self._spend("developer_api")
            self.costs["vertex_known_spend_usd"] = self._spend("vertex_ai")
            self.costs["combined_known_spend_usd"] = self.costs["developer_known_spend_usd"] + self.costs["vertex_known_spend_usd"]
            atomic_write_json(self.paths["cost_ledger.json"], self.costs)
            self.grounding["items"][f"{quote_id}:{transport}:{number}"] = {"transport": transport, **response["grounding"]}
            atomic_write_json(self.paths["grounding_sources.json"], self.grounding)
        try:
            if not response["grounding"].get("sources"):
                raise LookupError("provider returned no linked grounding chunks/supports")
            if response.get("parse_error"):
                raise ValueError(response["parse_error"])
            content = response["content"]
            if self.identity_binder:
                content = self.identity_binder(content, record)
            bound = bind_packet_to_grounding(content, response["grounding"])
            packet = validate_packet(bound, record)
        except (ValueError, TypeError, KeyError, LookupError) as exc:
            kind = "missing_grounding" if isinstance(exc, LookupError) else "validation_failure"
            failure = {"kind": kind, "message": str(exc),
                       "raw_response_path": str(raw_path.relative_to(self.run_dir)),
                       "extracted_response_path": str(extracted_path.relative_to(self.run_dir))}
            self._record_attempt({**base, "state": "validation_failure", "failure": failure})
            return {"outcome": "validation_failure", "failure": failure,
                    "raw_text": _raw_text(response["raw"])}
        self._mark_valid(record, packet, response, transport)
        self._record_attempt({**base, "state": "completed", "request_id": response.get("request_id")})
        return {"outcome": "completed"}

    def _pause_developer(self, record: dict[str, Any], failures: list[dict[str, Any]]) -> None:
        with self.lock:
            if self.transport["developer_paused"]:
                return
            self.transport.update({"developer_paused": True,
                "pause_reason": "two_429s_for_first_provider_wide_quota_case", "paused_at": utc_now(),
                "trigger_quote_id": record["quote_id"], "trigger_attempts": failures})
            self._persist_all()

    def _process(self, record: dict[str, Any]) -> None:
        quote_id = record["quote_id"]
        if self.stop_event.is_set() or self.state["items"][quote_id]["status"] in TERMINAL:
            return
        with self.lock:
            use_vertex = bool(self.transport["developer_paused"])
            if use_vertex:
                self.transport["direct_to_vertex_count"] += 1
                self._persist_all()
        client = self.vertex if use_vertex else self.developer
        quota_failures: list[dict[str, Any]] = []
        while not self.stop_event.is_set():
            transport = client.transport
            used = self.state["items"][quote_id]["transport_attempts"].get(transport, 0)
            if used >= MAX_ATTEMPTS:
                self._mark_permanent(record, {"kind": "exhausted_after_retry",
                    "message": f"{transport} attempts exhausted"}, transport)
                return
            prompt = self.prompt_builder(record)
            result = self._attempt(record, client, prompt)
            outcome = result["outcome"]
            if outcome in {"completed", "cancelled"}:
                return
            if outcome == "validation_failure":
                if self.state["items"][quote_id]["transport_attempts"][transport] < MAX_ATTEMPTS:
                    missing_grounding = result["failure"].get("kind") == "missing_grounding"
                    next_prompt = self.prompt_builder(record) if missing_grounding else self.repair_builder(
                        record, result.get("raw_text", ""), result["failure"]["message"])
                    repaired = self._attempt(record, client, next_prompt, repair=not missing_grounding)
                    if repaired["outcome"] == "completed":
                        return
                    result, outcome = repaired, repaired["outcome"]
                self._mark_permanent(record, {"kind": "exhausted_validation_failure",
                    "message": (result.get("failure") or {}).get("message", "schema repair failed")}, transport)
                return
            if transport == "developer_api" and outcome == "quota_429":
                quota_failures.append({**result["failure"], "quote_id": quote_id,
                    "transport_attempt_number": self.state["items"][quote_id]["transport_attempts"][transport],
                    "recorded_at": utc_now()})
                if self.transport["developer_paused"]:
                    client = self.vertex
                    continue
                if len(quota_failures) < 2 and self.state["items"][quote_id]["transport_attempts"][transport] < MAX_ATTEMPTS:
                    self.sleep(2)
                    continue
                self._pause_developer(record, quota_failures)
                client = self.vertex
                continue
            if transport == "vertex_ai" and outcome == "quota_429":
                if self.state["items"][quote_id]["transport_attempts"][transport] < MAX_ATTEMPTS:
                    self._mark_retryable(record, "transient_failure", result["failure"])
                    self.sleep(2)
                    continue
                self._mark_permanent(record, {"kind": "exhausted_transient_failure",
                    "message": result["failure"].get("message", "Vertex quota retries exhausted")}, transport)
                return
            if outcome in {"transient", "ambiguous"}:
                if self.state["items"][quote_id]["transport_attempts"][transport] < MAX_ATTEMPTS:
                    self._mark_retryable(record, "transient_failure", result["failure"])
                    self.sleep(2)
                    continue
                self._mark_permanent(record, {"kind": "exhausted_transient_failure",
                    "message": result["failure"].get("message", "transient retries exhausted")}, transport)
                return
            self._mark_permanent(record, result.get("failure") or {"kind": "permanent", "message": outcome}, transport)
            return

    def eligible(self, only_quote_id: str | None = None,
                 statuses: set[str] | None = None) -> list[dict[str, Any]]:
        records = []
        for quote_id in self.order:
            if only_quote_id and quote_id != only_quote_id:
                continue
            item = self.state["items"][quote_id]
            if item["status"] in (statuses or RETRYABLE):
                records.append(self.records[quote_id])
        return records

    def run(self, max_items: int | None = None, only_quote_id: str | None = None,
            statuses: set[str] | None = None) -> dict[str, Any]:
        require_transport_parity(self.developer, self.vertex)
        self.state["shutdown_requested"] = False
        self.state.pop("shutdown_reason", None)
        work = self.eligible(only_quote_id, statuses)
        if max_items is not None:
            work = work[:max_items]
        queue = deque(work)
        maximum_workers = max(self.concurrency.values())
        with ThreadPoolExecutor(max_workers=maximum_workers, thread_name_prefix="quote_research") as pool:
            futures = set()
            while (queue or futures) and not self.stop_event.is_set():
                transport = "vertex_ai" if self.transport["developer_paused"] else "developer_api"
                limit = self.concurrency[transport]
                while queue and len(futures) < limit and not self.stop_event.is_set():
                    futures.add(pool.submit(self._process, queue.popleft()))
                if not futures:
                    break
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        future.result()
                    except RuntimeError as exc:
                        if "ceiling" in str(exc):
                            self.request_stop("cost_ceiling")
                        else:
                            raise
        self._persist_all()
        return self.status_payload()

    def reset_permanent(self, quote_id: str) -> None:
        with self.lock:
            if quote_id not in self.records or quote_id not in self.permanent["items"]:
                raise RuntimeError("quote is not a recorded permanent failure")
            previous = self.permanent["items"].pop(quote_id)
            self.permanent["reset_audit"].append({"quote_id": quote_id, "reset_at": utc_now(),
                                                   "previous_failure": previous})
            epoch = int(self.state["items"][quote_id].get("attempt_epoch", 0)) + 1
            self.state["items"][quote_id].update({"status": "pending", "last_error": None,
                                                   "attempt_epoch": epoch,
                                                   "transport_attempts": {"developer_api": 0, "vertex_ai": 0},
                                                   "updated_at": utc_now()})
            self._persist_all()

    def status_payload(self) -> dict[str, Any]:
        counts = Counter(row["status"] for row in self.state["items"].values())
        valid = counts["valid"]
        costs = self.costs
        known = float(costs.get("combined_known_spend_usd") or 0)
        latencies = [float(row["latency_seconds"]) for row in costs.get("calls", [])]
        completed_rows = [row for row in self.packets["items"].values()]
        recent = sorted((row.get("completed_at"), row) for row in completed_rows if row.get("completed_at"))[-20:]
        eta = None
        if len(recent) >= 2:
            first = datetime.fromisoformat(recent[0][0].replace("Z", "+00:00"))
            last = datetime.fromisoformat(recent[-1][0].replace("Z", "+00:00"))
            seconds_each = max((last-first).total_seconds() / (len(recent)-1), .001)
            eta = seconds_each * (len(self.order)-valid-counts["permanent_failure"])
        developer_completed = sum(row.get("transport") == "developer_api" for row in completed_rows)
        vertex_completed = sum(row.get("transport") == "vertex_ai" for row in completed_rows)
        total = len(self.order)
        terminal_count = valid + counts["permanent_failure"]
        remaining = total-valid-counts["permanent_failure"]
        return {"schema_version": 1, "run_id": self.run_dir.name, "total_quotes": total,
            "valid_packets": valid, "pending": counts["pending"], "active": counts["active"],
            "transient_failures": counts["transient_failure"] + counts["interrupted"],
            "validation_failures": counts["validation_failure"],
            "permanent_failures": counts["permanent_failure"], "remaining_nonterminal": remaining,
            "developer": {"completed": developer_completed, "known_spend_usd": float(costs.get("developer_known_spend_usd") or 0)},
            "vertex": {"completed": vertex_completed, "known_spend_usd": float(costs.get("vertex_known_spend_usd") or 0)},
            "combined_known_spend_usd": known,
            "ambiguous_possible_exposure_usd": float(costs.get("ambiguous_possible_exposure_usd") or 0),
            "developer_paused": bool(self.transport.get("developer_paused")),
            "developer_pause_reason": self.transport.get("pause_reason"),
            "direct_to_vertex_count": int(self.transport.get("direct_to_vertex_count") or 0),
            "average_cost_per_valid_quote": known/valid if valid else None,
            "projected_final_spend_usd": known/terminal_count*total if terminal_count else None,
            "mean_latency_seconds": statistics.mean(latencies) if latencies else None,
            "eta_seconds": eta, "resume_plan": {
                "completed_will_be_skipped": valid, "permanent_failures_will_be_skipped": counts["permanent_failure"],
                "unfinished_quotes": remaining,
                "next_transport": "vertex_ai" if self.transport.get("developer_paused") else "developer_api",
                "developer_will_be_probed": not bool(self.transport.get("developer_paused")),
            }, "updated_at": utc_now()}


def install_signal_handlers(runner: CorpusRunner) -> dict[int, Any]:
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda sig, _frame, r=runner: r.request_stop(signal.Signals(sig).name))
    return previous


def restore_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)
