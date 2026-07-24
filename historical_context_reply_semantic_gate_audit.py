#!/usr/bin/env python3
"""Audit the reviewed historical-context-only semantic gate offline.

The programme processes every completed research packet, proves that regular
quotation eligibility is unchanged, and records the exact public replies that
the gate prevents.  It never imports the bot loop, contacts a network service,
or writes production state.  Only an explicitly supplied output path is
written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_reply_semantic_gate import (
    EXPECTED_DISPOSITION_COUNTS,
    POLICY_VERSION,
    SEMANTIC_REVIEW_PATH,
    load_historical_context_semantic_gate,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "historical_context_reply_semantic_gate_audit.json"
RUNTIME_ELIGIBLE_MANIFEST = (
    ROOT
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "runtime_eligible_quote_manifest.json"
)
AUDIT_KIND = "historical_context_reply_semantic_gate_audit"
SCHEMA_VERSION = 1
EXPECTED_COMPLETED = 626
EXPECTED_ELIGIBLE = 610
EXPECTED_INELIGIBLE = 16
EXPECTED_UNRESOLVED = 6
KNOWN_104653_QUOTE_ID = (
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
)


def _sha256(path: Path) -> str:
    """Hash one immutable input."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sequence_sha256(values: Iterable[str]) -> str:
    """Hash a sorted identity sequence with explicit lengths."""
    return hashlib.sha256(
        "".join(
            f"{len(value)}:{value}\n" for value in sorted(values)
        ).encode("utf-8")
    ).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    """Load one required JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def build_audit(
    *,
    root: Path = ROOT,
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    runtime_manifest_path: Path = RUNTIME_ELIGIBLE_MANIFEST,
) -> dict[str, Any]:
    """Return the deterministic all-packet semantic-gate audit."""
    root = root.resolve()
    research_dir = research_dir.resolve()
    runtime_manifest_path = runtime_manifest_path.resolve()
    packets, unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    eligible_ids = {
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    gate = load_historical_context_semantic_gate(
        root=root,
        eligible_quote_ids=eligible_ids,
    )
    runtime_manifest = _load_object(runtime_manifest_path)
    runtime_ids = runtime_manifest.get("runtime_eligible_quote_ids")
    resolved_runtime_ids = runtime_manifest.get("resolved_manifest_quote_ids")
    runtime_aliases = runtime_manifest.get("runtime_quote_aliases")
    runtime_source_hashes = runtime_manifest.get("source_file_hashes")
    if (
        not isinstance(runtime_ids, list)
        or not isinstance(resolved_runtime_ids, list)
        or not isinstance(runtime_aliases, dict)
        or not isinstance(runtime_source_hashes, dict)
        or any(not isinstance(value, str) for value in runtime_ids)
        or any(not isinstance(value, str) for value in resolved_runtime_ids)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in runtime_aliases.items()
        )
    ):
        raise RuntimeError("runtime eligible quote manifest is invalid")
    resolved_from_runtime_ids = [
        runtime_aliases.get(quote_id, quote_id) for quote_id in runtime_ids
    ]

    action_counts: Counter[str] = Counter()
    disposition_counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    blocked_render_failures: list[str] = []
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        eligible = quote_id in eligible_ids
        disposition = gate.disposition(quote_id) if gate.available else None
        if eligible and not gate.available:
            decision = "blocked_semantic_gate_unavailable"
            rendered = format_context_reply_public(packet)
            if rendered is None:
                blocked_render_failures.append(quote_id)
            suppressed_text = "" if rendered is None else rendered["text"]
        elif disposition is not None:
            decision = "blocked_open_semantic_review"
            rendered = format_context_reply_public(packet)
            if rendered is None:
                blocked_render_failures.append(quote_id)
            suppressed_text = "" if rendered is None else rendered["text"]
            disposition_counts[disposition] += 1
        elif eligible:
            decision = "eligible_allow"
            suppressed_text = ""
        else:
            decision = "ineligible_not_regular_post"
            suppressed_text = ""
        action_counts[decision] += 1
        records.append({
            "quote_id": quote_id,
            "quote_text": packet["quote_text"],
            "attribution_eligible": eligible,
            "public_reply_decision": decision,
            "open_review_disposition": disposition,
            "otherwise_rendered_public_reply_suppressed": suppressed_text,
        })

    reviewed_blocked_ids = set(gate.blocked_dispositions)
    effective_blocked_ids = (
        reviewed_blocked_ids if gate.available else set(eligible_ids)
    )
    invariants = {
        "gate_is_available": gate.available,
        "completed_packet_count_is_626": len(packets) == EXPECTED_COMPLETED,
        "attribution_eligible_count_is_610": len(eligible_ids) == EXPECTED_ELIGIBLE,
        "completed_ineligible_count_is_16": (
            len(packets) - len(eligible_ids) == EXPECTED_INELIGIBLE
        ),
        "unresolved_count_is_6": len(unresolved) == EXPECTED_UNRESOLVED,
        "runtime_cycle_membership_is_unchanged": (
            runtime_manifest.get("runtime_eligible_quote_count")
            == EXPECTED_ELIGIBLE
            and len(runtime_ids) == EXPECTED_ELIGIBLE
            and len(set(runtime_ids)) == EXPECTED_ELIGIBLE
            and set(runtime_aliases).issubset(set(runtime_ids))
            and len(resolved_from_runtime_ids) == EXPECTED_ELIGIBLE
            and len(set(resolved_from_runtime_ids)) == EXPECTED_ELIGIBLE
            and len(resolved_runtime_ids) == EXPECTED_ELIGIBLE
            and len(set(resolved_runtime_ids)) == EXPECTED_ELIGIBLE
            and sorted(resolved_from_runtime_ids) == resolved_runtime_ids
            and set(resolved_runtime_ids) == eligible_ids
            and runtime_source_hashes.get("active_source")
            == _sha256(root / "mrsMThatcher.txt")
            and runtime_source_hashes.get("completed_quote_research")
            == _sha256(research_dir / "research_packets.json")
        ),
        "all_blocked_quotes_remain_regular_post_eligible": (
            bool(effective_blocked_ids)
            and effective_blocked_ids.issubset(eligible_ids)
        ),
        "blocked_quotes_have_reviewed_dispositions": (
            disposition_counts == Counter(EXPECTED_DISPOSITION_COUNTS)
        ),
        "blocked_quotes_have_inspectable_pre_gate_renderings": (
            not blocked_render_failures
        ),
        "document_104653_is_not_blocked": (
            KNOWN_104653_QUOTE_ID in eligible_ids
            and KNOWN_104653_QUOTE_ID not in effective_blocked_ids
        ),
    }
    semantic_path = root / SEMANTIC_REVIEW_PATH.name
    source_role_path = research_dir / "historical_context_source_role_audit.json"
    effective_blocked_count = (
        action_counts["blocked_open_semantic_review"]
        + action_counts["blocked_semantic_gate_unavailable"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_kind": AUDIT_KIND,
        "policy_version": POLICY_VERSION,
        "input_hashes": {
            "historical_context_published_reply_semantic_review.json": (
                _sha256(semantic_path)
            ),
            "research_packets.json": _sha256(
                research_dir / "research_packets.json"
            ),
            "historical_context_source_role_audit.json": _sha256(
                source_role_path
            ),
            "runtime_eligible_quote_manifest.json": _sha256(
                runtime_manifest_path
            ),
            "mrsMThatcher.txt": _sha256(root / "mrsMThatcher.txt"),
        },
        "coverage": {
            "completed_packet_count": len(packets),
            "attribution_eligible_count": len(eligible_ids),
            "completed_attribution_ineligible_count": (
                len(packets) - len(eligible_ids)
            ),
            "unresolved_quote_count": len(unresolved),
            "regular_post_eligible_quote_ids_sha256": _sequence_sha256(
                eligible_ids
            ),
            "runtime_cycle_quote_ids_sha256": _sequence_sha256(runtime_ids),
            "runtime_cycle_resolved_quote_ids_sha256": _sequence_sha256(
                resolved_runtime_ids
            ),
        },
        "gate": {
            "available": gate.available,
            "failure_reason": gate.reason,
            "semantic_review_ledger_sha256": gate.ledger_sha256,
            "blocked_projection_sha256": gate.projection_sha256,
            "reviewed_blocked_quote_count": len(reviewed_blocked_ids),
            "blocked_quote_count": effective_blocked_count,
            "disposition_counts": dict(sorted(disposition_counts.items())),
        },
        "decision_counts": dict(sorted(action_counts.items())),
        "blocked_render_failures": blocked_render_failures,
        "records": records,
        "invariants": invariants,
        "invariant_failure_count": sum(not value for value in invariants.values()),
    }


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    """Write one deterministic audit atomically."""
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _validated_output_path(output: Path, *, overwrite: bool) -> Path:
    """Reject immutable, symlinked, or unrelated overwrite targets."""
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    resolved = output.resolve(strict=False)
    research = DEFAULT_RESEARCH_DIR.resolve()
    protected = {
        (ROOT / "historical_context_reply_history.json").resolve(),
        (ROOT / "mrsMThatcher.txt").resolve(),
        (ROOT / "quote_analysis.json").resolve(),
        SEMANTIC_REVIEW_PATH.resolve(),
        RUNTIME_ELIGIBLE_MANIFEST.resolve(),
        Path(__file__).resolve(),
    }
    if resolved in protected or resolved == research or research in resolved.parents:
        raise ValueError("refusing to overwrite immutable production input")
    if (
        not resolved.name.startswith(
            "historical_context_reply_semantic_gate_audit"
        )
        or resolved.suffix != ".json"
    ):
        raise ValueError("--output must use the semantic-gate audit filename")
    if resolved.exists():
        if not resolved.is_file():
            raise ValueError("--output must be a regular file")
        if not overwrite:
            raise FileExistsError("--output exists; pass --overwrite")
        try:
            prior = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("--overwrite requires a prior gate audit") from exc
        if not isinstance(prior, dict) or prior.get("audit_kind") != AUDIT_KIND:
            raise ValueError("--overwrite requires a prior gate audit")
    return resolved


def main(argv: list[str] | None = None) -> int:
    """Build the isolated audit at an explicit output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = _validated_output_path(args.output, overwrite=args.overwrite)
    audit = build_audit()
    if audit["invariant_failure_count"]:
        raise RuntimeError("semantic gate audit invariants failed")
    _atomic_write(output, audit)
    print(json.dumps({
        "blocked": audit["gate"]["blocked_quote_count"],
        "eligible": audit["coverage"]["attribution_eligible_count"],
        "invariant_failures": audit["invariant_failure_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
