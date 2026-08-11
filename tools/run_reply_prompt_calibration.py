#!/usr/bin/env python3
"""Prepare or execute a blinded current-versus-compact reply comparison.

Validate-only is the default and performs no HTTP request.  Paid execution is
available only behind three explicit gates and reuses the reviewed xAI pilot
transport and durable cost ledger.  This module never imports the bot or an X
client and has no posting, search, tool or media path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import reply_evidence
import reply_strategy
from reply_evidence import EvidenceRepository
from tools import pilot_ai_first_reply_strategy as pilot
from tools.reply_prompt_profiles import (
    COMPACT_WORD_CAPS,
    FROZEN_REPLY_STRATEGY_SHA256,
    activate_profile,
    profile_manifests,
    sha256_text,
    verify_current_production_objects,
)


RUNNER_VERSION = "reply-prompt-calibration-v4"
RUN_IDENTITY_SCHEMA_VERSION = 1
PACK_SCHEMA_VERSION = 2
PACK_TOOL_VERSION = "reply-replay-pack-v2"
CASE_PACK_VERSION = "mrs-reply-evaluation-48-v1"
FROZEN_GIT_COMMIT = "df7f53bfcb9ccd6b38e8b8631bd1d4a1eca1c5e6"
DEFAULT_MODEL = "grok-4.3"
DEFAULT_XAI_BASE = "https://api.x.ai/v1"
DEFAULT_BLIND_SEED = "mrs-reply-calibration-v1"
DEFAULT_MAXIMUM_RATE_LIMIT_RETRIES = 1
DEFAULT_MAXIMUM_SERVER_ERROR_RETRIES = 1
PAID_ACKNOWLEDGEMENT = "YES_I_UNDERSTAND"
MAXIMUM_REPLY_LENGTH = 270
VARIANTS = ("current", "compact")
CASE_SETS = ("calibration", "holdout")
HISTORY_SCHEMA_VERSION = 2
HISTORY_TOOL_VERSION = "reply-history-reconstruction-v2"
HISTORY_EXTRACTOR_GIT_COMMIT = "bdb6a5b18468bbda02f2c908f8a7699601ee5d18"
HISTORY_SELECTED_SNAPSHOT_COUNT = 32
HISTORY_CONTEXT_MESSAGE_PREFIX = "Context sent to AI reply pipeline:"
HISTORY_REQUIRED_FILES = {
    "run_manifest.json",
    "unique_log_records.jsonl",
}
HOLDOUT_CANDIDATE_IDS_SHA256 = (
    "753139b9eb097872d19597823e2b25cd321eecc2e77bace01c5ad6c0ba650a25"
)
STALE_HOLDOUT_CONTEXT_AUDIT_SHA256 = (
    "7f7f4392136d51707959681b26898b80143438aaf11632c45cff66c40fb547d8"
)
REAL_CONTEXT_RECOVERY_ASSERTIONS = {
    "candidate-675700344d79a2eda56653d1db5582563540743564a6bafe1f1e139827caa588": {
        "record_id": (
            "record-982b99698dc802c777b305a99af28da920cc18ef3da5818ac8b7f9b918d43158"
        ),
        "raw_record_sha256": (
            "8680f04fe14f164e27fa81e101dbd7077622a53a4fda6c299dd853dc3bc6012f"
        ),
        "source_identity": "zfs-auto-snap_daily-2026-07-30-0525",
        "source_stream_sequence": 53772,
    },
}
REQUIRED_STRATA = {
    "civil_challenge_or_disagreement",
    "factual_or_historical_question",
    "formulaic_substantive_posted",
    "genuine_social_courtesy",
    "justified_safety_no_reply",
    "safe_wit_opportunity",
}
REQUIRED_PACK_FILES = {
    "run_manifest.json",
    "model_inputs.jsonl",
    "historical_baselines.jsonl",
    "recent_account_replies.jsonl",
    "calibration_cases.jsonl",
    "frozen_cases.jsonl",
    "replay_plan.json",
    "leakage_audit.json",
}
FORBIDDEN_RUNTIME_MODULES = ("mrsMThatcher2", "tweepy")
SCORING_COLUMNS = (
    "safety_and_factual_integrity_pass",
    "mode_choice_1_to_5",
    "specificity_1_to_5",
    "added_value_1_to_5",
    "voice_1_to_5",
    "wit_appropriateness_1_to_5",
    "no_reply_correctness_1_to_5",
    "overall_rank",
    "reviewer_note",
)
SOURCE_PATHS = {
    "reply_strategy_sha256": PROJECT_ROOT / "reply_strategy.py",
    "reply_evidence_sha256": PROJECT_ROOT / "reply_evidence.py",
    "pilot_transport_source_sha256": PROJECT_ROOT / "tools/pilot_ai_first_reply_strategy.py",
    "prompt_profiles_source_sha256": PROJECT_ROOT / "tools/reply_prompt_profiles.py",
    "runner_source_sha256": Path(__file__).resolve(),
}
DISPOSABLE_DERIVED_FILES = (
    "blind_review.md",
    "blind_review.csv",
    "blind_key.json",
    "calibration_report.md",
    "run_manifest.json",
    "SHA256SUMS",
)
FINAL_OUTPUT_FILES = set(DISPOSABLE_DERIVED_FILES)
DURABLE_CORE_FILES = {
    "run_identity.json",
    "provider_phase_identity.json",
    "provider_model_metadata.json",
    "cost_ledger.json",
    "pack_verification.json",
    "profile_manifests.json",
    "execution_plan.json",
    "prompt_receipts.jsonl",
    "pipeline_results.jsonl",
    "pipeline_audits.jsonl",
}
HOLDOUT_CONTEXT_FILES = {
    "holdout_context_audit.json",
    "holdout_context_review.md",
    "holdout_context_clearance.csv",
    "holdout_context_recovery.jsonl",
}
PROVIDER_PHASE_FIELDS = {
    "schema_version",
    "runner_version",
    "run_identity_sha256",
    "provider_model_metadata_sha256",
    "model",
    "xai_endpoint",
    "usd_ticks_per_dollar",
    "prompt_text_token_price",
    "cached_prompt_text_token_price",
    "completion_text_token_price",
}
MODEL_AUDIT_STAGES = {
    "proposer",
    "revision_proposer",
    "no_reply_reviewer",
    "revision_no_reply_reviewer",
    "claim_auditor",
    "revision_claim_auditor",
    "evidence",
    "revision_evidence",
    "reviewer",
    "revision_reviewer",
}
VALID_PIPELINE_STATUSES = {"approved", "no_reply"}
CONTEXT_AUDIT_RULE_VERSION = "reply-holdout-context-audit-v1"
CONTEXT_DEPENDENCY_FLAG_ORDER = (
    "short_elliptical_question",
    "unresolved_third_person_pronoun",
    "demonstrative_reference",
    "what_did_mean_question",
    "source_or_attribution_question",
    "quote_or_above_reference",
    "missing_quoted_post_text",
    "missing_or_empty_bounded_parent_context",
)
CONTEXT_AUDIT_RULES = {
    "short_elliptical_question": {
        "question_mark_required": True,
        "maximum_word_count": 8,
        "word_pattern": r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?",
    },
    "unresolved_third_person_pronoun": {
        "pattern": (
            r"\b(?:he|him|his|himself|she|her|hers|herself|they|them|their|"
            r"theirs|themself|themselves|its|itself)\b"
        ),
    },
    "demonstrative_reference": {
        "pattern": r"\b(?:this|that|it|these|those)\b",
    },
    "what_did_mean_question": {
        "pattern": r"\bwhat\s+(?:did|do|does)\b[^?\n]{0,160}\bmean\b",
    },
    "source_or_attribution_question": {
        "pattern": (
            r"(?:\b(?:source|citation|reference|attribution|author|authorship|"
            r"speaker|origin|provenance)\b[^?\n]*\?|"
            r"\b(?:who|whose)\b[^?\n]{0,160}\?|"
            r"\bwhere\b[^?\n]{0,120}\b(?:from|published|printed|recorded)\b[^?\n]*\?|"
            r"\b(?:when|where)\b[^?\n]{0,120}\b(?:said|written|published|delivered|"
            r"spoken|recorded)\b[^?\n]*\?|"
            r"\bdid\b[^?\n]{0,120}\b(?:say|write|author|deliver)\b[^?\n]*\?)"
        ),
    },
    "quote_or_above_reference": {
        "pattern": r"\b(?:the\s+quote|these\s+words|the\s+above)\b",
    },
    "missing_quoted_post_text": {
        "lane": "quote_tweet",
        "requires_nonempty_quoted_post_text": True,
    },
    "missing_or_empty_bounded_parent_context": {
        "requires_dependency_warning": True,
        "requires_no_nonempty_parent_post": True,
        "applies_when_quoted_post_text_is_empty": True,
    },
}
FROZEN_PROFILE_IDENTITIES = {
    "current": {
        "profile_version": "current-production-profile-v1",
        "manifest_sha256": "edd2985d37c690c02556c518dd6e92ad39db8e379267a61b90ddb9d4650368f8",
        "prompt_versions": {
            "reviewer": "independent-reply-reviewer-v13",
        },
        "prompt_sha256": {
            "proposer": "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72",
            "reviewer": "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e",
            "no_reply_review": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
            "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
        },
    },
    "compact": {
        "profile_version": "compact-reply-profile-v4",
        "manifest_sha256": "be7784eb3ffb0e851fda598ca71326bdd6cf95cc001803f4b7ca1d26e822b30e",
        "prompt_versions": {
            "reviewer": "compact-reviewer-v4",
        },
        "prompt_sha256": {
            "proposer": "922aff370f775ff9c18e2e7a445600519f14bfba8610ee6db99f9daf99e0f8da",
            "reviewer": "36c0577d9b0ee8c536ea638591c9ce1a0e052a9e482f80aedc16b489b3cad089",
            "no_reply_review": "2b6677ed5766676acde2c9010ba04feda57b077e02daaabb103a319921643b67",
            "claim_auditor": "5a0ccdd28239b6eb5808870cfa6c6fe2cee0c32342f9243a9e1c8ec057ac05fe",
        },
    },
}


class CalibrationError(RuntimeError):
    """A calibration precondition or safety contract failed."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value for stable identity hashing."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def value_sha256(value: Any) -> str:
    """Hash one canonical JSON value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def candidate_ids_sha256(candidate_ids: Iterable[str]) -> str:
    """Hash a candidate set as one canonical, sorted list."""
    return value_sha256(sorted(candidate_ids))


def json_document_bytes(value: Any) -> bytes:
    """Encode one stable, human-readable JSON document."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )


def file_sha256(path: Path) -> str:
    """Hash a file without following any path supplied by its contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str, text: bool = True) -> str | bytes:
    """Run one local, read-only Git query against this worktree."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = exc.stderr
            detail = (stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace")).strip()
        raise CalibrationError(f"local Git provenance query failed: {detail or exc}") from exc
    return completed.stdout


def runner_source_hashes() -> dict[str, str]:
    """Hash every source whose exact bytes determine execute-mode behaviour."""
    return {name: file_sha256(path) for name, path in SOURCE_PATHS.items()}


def execution_provenance(
    expected_commit: str | None = None, *, require_clean_checkout: bool = False
) -> dict[str, Any]:
    """Describe the checkout, and strictly bind paid execution to clean Git blobs."""
    commit = str(_git("rev-parse", "HEAD")).strip()
    tracked_status = str(
        _git("status", "--porcelain=v1", "--untracked-files=no")
    )
    clean = not tracked_status.strip()
    hashes = runner_source_hashes()
    if require_clean_checkout:
        if expected_commit is None or re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
            raise CalibrationError(
                "execute requires --expected-runner-git-commit as one exact 40-character SHA"
            )
        if commit != expected_commit:
            raise CalibrationError(
                f"runner Git commit mismatch: expected {expected_commit}, got {commit}"
            )
        if not clean:
            raise CalibrationError("execute requires a clean tracked worktree and index")
        for name, path in SOURCE_PATHS.items():
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            checkout_bytes = _git("show", f"{commit}:{relative}", text=False)
            assert isinstance(checkout_bytes, bytes)
            if hashlib.sha256(checkout_bytes).hexdigest() != hashes[name]:
                raise CalibrationError(
                    f"running source does not match clean checkout: {relative}"
                )
    return {
        "runner_git_commit": commit,
        "runner_git_commit_expected": expected_commit,
        "worktree_clean": clean,
        **hashes,
    }


def evidence_repository_fingerprint(repository: EvidenceRepository) -> str:
    """Bind the loaded repository without exposing evidence or source text."""
    passages = []
    for evidence_id, passage in sorted(repository.passages.items()):
        actual_id = str(getattr(passage, "evidence_id"))
        if actual_id != evidence_id:
            raise CalibrationError("evidence repository passage identity mismatch")
        passages.append({
            "evidence_id": actual_id,
            "source_hash": str(getattr(passage, "source_hash")),
            "model_input_hash": str(passage.model_input_hash()),
        })
    identity = {
        "evidence_repository_version": reply_evidence.EVIDENCE_REPOSITORY_VERSION,
        "factual_evidence_schema_version": reply_evidence.FACTUAL_EVIDENCE_SCHEMA_VERSION,
        "factual_evidence_set_version": reply_evidence.FACTUAL_EVIDENCE_SET_VERSION,
        "completed_packet_count": repository.completed_packet_count,
        "unresolved_packet_count": repository.unresolved_packet_count,
        "attribution_eligible_packet_count": repository.attribution_eligible_packet_count,
        "factual_evidence_count": repository.factual_evidence_count,
        "passages": passages,
    }
    return value_sha256(identity)


def verify_frozen_profile_manifests(
    manifests: dict[str, dict[str, Any]],
) -> None:
    """Refuse any drift in either frozen profile identity or prompt hash."""
    if set(manifests) != set(FROZEN_PROFILE_IDENTITIES):
        raise CalibrationError("reply prompt profile inventory differs")
    for variant, expected in FROZEN_PROFILE_IDENTITIES.items():
        manifest = manifests.get(variant)
        if not isinstance(manifest, dict):
            raise CalibrationError(f"{variant} profile manifest is invalid")
        _require_equal(
            f"{variant} profile version",
            manifest.get("profile_version"),
            expected["profile_version"],
        )
        _require_equal(
            f"{variant} profile manifest SHA-256",
            manifest.get("manifest_sha256"),
            expected["manifest_sha256"],
        )
        versions = manifest.get("prompt_version_constants")
        prompts = manifest.get("prompts")
        if not isinstance(versions, dict) or not isinstance(prompts, dict):
            raise CalibrationError(f"{variant} prompt profile fields are invalid")
        _require_equal(
            f"{variant} reviewer prompt version",
            versions.get("REVIEWER_PROMPT_VERSION"),
            expected["prompt_versions"]["reviewer"],
        )
        for prompt_name, expected_sha256 in expected["prompt_sha256"].items():
            prompt = prompts.get(prompt_name)
            if not isinstance(prompt, dict):
                raise CalibrationError(
                    f"{variant} {prompt_name} prompt profile is invalid"
                )
            _require_equal(
                f"{variant} {prompt_name} prompt SHA-256",
                prompt.get("sha256"),
                expected_sha256,
            )
    _require_equal(
        "current production prompt functions",
        manifests["current"].get("uses_exact_production_prompt_functions"),
        True,
    )


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"invalid JSON file {path.name}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CalibrationError(f"cannot read JSONL file {path.name}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(
                f"invalid JSONL record {path.name}:{line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise CalibrationError(f"JSONL record must be an object: {path.name}:{line_number}")
        records.append(record)
    return records


def _require_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise CalibrationError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def _index_unique(records: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        candidate_id = record.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise CalibrationError(f"{label} has an invalid candidate_id")
        if candidate_id in result:
            raise CalibrationError(f"{label} repeats candidate_id {candidate_id}")
        result[candidate_id] = record
    return result


def parse_and_verify_checksums(pack: Path) -> tuple[dict[str, str], str]:
    """Parse a flat GNU checksum file without permitting path traversal."""
    checksum_path = pack / "SHA256SUMS"
    try:
        checksum_bytes = checksum_path.read_bytes()
        lines = checksum_bytes.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CalibrationError(f"cannot read pack SHA256SUMS: {exc}") from exc
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^\r\n]+)", line)
        if match is None:
            raise CalibrationError(f"malformed SHA256SUMS line {line_number}")
        expected, name = match.groups()
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or name in checksums
        ):
            raise CalibrationError(f"unsafe or duplicate SHA256SUMS path on line {line_number}")
        target = pack / relative
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise CalibrationError(f"missing checksum payload {name}: {exc}") from exc
        if resolved.parent != pack or target.is_symlink() or not resolved.is_file():
            raise CalibrationError(f"checksum payload path is not a flat regular file: {name}")
        actual = file_sha256(resolved)
        if actual != expected:
            raise CalibrationError(f"checksum mismatch for pack payload {name}")
        checksums[name] = actual
    missing = REQUIRED_PACK_FILES - set(checksums)
    if missing:
        raise CalibrationError(f"SHA256SUMS omits required payloads: {sorted(missing)}")
    return checksums, hashlib.sha256(checksum_bytes).hexdigest()


def _require_regular_input(path: Path, label: str) -> Path:
    """Require one existing, non-linked regular input file."""
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise CalibrationError(f"missing {label}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise CalibrationError(f"{label} must be a non-symlink regular file")
    return path


def verify_history_corpus(history_path: Path) -> dict[str, Any]:
    """Verify the immutable reconstruction corpus and its two required payloads."""
    if history_path.is_symlink():
        raise CalibrationError("history corpus path must not be a symbolic link")
    try:
        history = history_path.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError(f"history corpus does not exist: {history_path}") from exc
    if not history.is_dir():
        raise CalibrationError("history corpus path must be a directory")

    checksum_path = _require_regular_input(
        history / "SHA256SUMS", "history SHA256SUMS"
    )
    try:
        checksum_bytes = checksum_path.read_bytes()
        checksum_lines = checksum_bytes.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CalibrationError(f"cannot read history SHA256SUMS: {exc}") from exc

    checksums: dict[str, str] = {}
    for line_number, line in enumerate(checksum_lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^\r\n]+)", line)
        if match is None:
            raise CalibrationError(
                f"malformed history SHA256SUMS line {line_number}"
            )
        expected, name = match.groups()
        relative = Path(name)
        canonical_name = relative.as_posix()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or canonical_name != name
            or canonical_name in checksums
        ):
            raise CalibrationError(
                f"unsafe or duplicate history SHA256SUMS path on line {line_number}"
            )
        checksums[canonical_name] = expected
    missing = HISTORY_REQUIRED_FILES - set(checksums)
    if missing:
        raise CalibrationError(
            f"history SHA256SUMS omits required payloads: {sorted(missing)}"
        )

    payload_paths: dict[str, Path] = {}
    for name in sorted(checksums):
        target = _require_regular_input(history / name, f"history payload {name}")
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(history)
        except (OSError, ValueError) as exc:
            raise CalibrationError(f"unsafe history payload path {name}: {exc}") from exc
        payload_paths[name] = resolved

    verified: dict[str, str] = {}
    for name in sorted(HISTORY_REQUIRED_FILES):
        resolved = payload_paths[name]
        actual = file_sha256(resolved)
        if actual != checksums[name]:
            raise CalibrationError(f"checksum mismatch for history payload {name}")
        verified[name] = actual

    manifest = read_json(history / "run_manifest.json")
    if not isinstance(manifest, dict):
        raise CalibrationError("history run_manifest.json must contain an object")
    if type(manifest.get("schema_version")) is not int:
        raise CalibrationError("history manifest schema_version must be an integer")
    _require_equal(
        "history manifest schema_version",
        manifest.get("schema_version"),
        HISTORY_SCHEMA_VERSION,
    )
    _require_equal(
        "history manifest tool_version",
        manifest.get("tool_version"),
        HISTORY_TOOL_VERSION,
    )
    _require_equal(
        "history manifest extractor_git_commit",
        manifest.get("extractor_git_commit"),
        HISTORY_EXTRACTOR_GIT_COMMIT,
    )
    _require_equal(
        "history manifest extractor_git_commit_confidence",
        manifest.get("extractor_git_commit_confidence"),
        "exact",
    )
    if manifest.get("live_project_included") is not False:
        raise CalibrationError("history manifest live_project_included must be false")
    selected_snapshots = manifest.get("selected_snapshots")
    if (
        not isinstance(selected_snapshots, list)
        or len(selected_snapshots) != HISTORY_SELECTED_SNAPSHOT_COUNT
        or any(
            not isinstance(identity, str) or not identity
            for identity in selected_snapshots
        )
        or len(set(selected_snapshots)) != HISTORY_SELECTED_SNAPSHOT_COUNT
    ):
        raise CalibrationError(
            "history manifest must select exactly 32 unique snapshots"
        )
    return {
        "history_path": history,
        "history_corpus_sha256": hashlib.sha256(checksum_bytes).hexdigest(),
        "history_manifest_sha256": verified["run_manifest.json"],
        "unique_log_records_sha256": verified["unique_log_records.jsonl"],
        "checksums": checksums,
        "manifest": manifest,
        "selected_snapshots": set(selected_snapshots),
    }


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CalibrationError(f"{label} is missing or invalid")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise CalibrationError(f"{label} is invalid: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalibrationError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _whitespace_normalized(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return re.sub(r"\s+", " ", value).strip()


def _empty_context_recovery() -> dict[str, Any]:
    candidate_ids: list[str] = []
    rows: list[dict[str, Any]] = []
    return {
        "history_corpus_path": None,
        "history_corpus_sha256": None,
        "history_manifest_sha256": None,
        "unique_log_records_sha256": None,
        "history_corpus_verification_pass": False,
        "context_recovery_candidate_ids": candidate_ids,
        "context_recovery_candidate_ids_sha256": candidate_ids_sha256(candidate_ids),
        "recovered_context_count": 0,
        "context_recovery_failures": 0,
        "context_recovery_conflicts": 0,
        "context_recovery_provenance_sha256": value_sha256(rows),
        "rows": rows,
    }


def context_recovery_data(pack_data: dict[str, Any]) -> dict[str, Any]:
    recovery = pack_data.get("context_recovery")
    return recovery if isinstance(recovery, dict) else _empty_context_recovery()


def context_recovery_binding(pack_data: dict[str, Any]) -> dict[str, Any]:
    """Return the non-content fields that bind recovery into durable identities."""
    recovery = context_recovery_data(pack_data)
    return {
        "history_corpus_path": recovery["history_corpus_path"],
        "history_corpus_sha256": recovery["history_corpus_sha256"],
        "history_manifest_sha256": recovery["history_manifest_sha256"],
        "unique_log_records_sha256": recovery["unique_log_records_sha256"],
        "history_corpus_verification_pass": recovery[
            "history_corpus_verification_pass"
        ],
        "context_recovery_candidate_ids": recovery[
            "context_recovery_candidate_ids"
        ],
        "context_recovery_candidate_ids_sha256": recovery[
            "context_recovery_candidate_ids_sha256"
        ],
        "recovered_context_count": recovery["recovered_context_count"],
        "context_recovery_failures": recovery["context_recovery_failures"],
        "context_recovery_conflicts": recovery["context_recovery_conflicts"],
        "context_recovery_provenance_sha256": recovery[
            "context_recovery_provenance_sha256"
        ],
    }


def verify_replay_pack(
    pack_path: Path, *, case_set: str = "calibration"
) -> dict[str, Any]:
    """Verify frozen provenance and select one immutable candidate-ID case set."""
    if case_set not in CASE_SETS:
        raise CalibrationError(f"invalid case set: {case_set!r}")
    try:
        pack = pack_path.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError(f"replay pack does not exist: {pack_path}") from exc
    if not pack.is_dir():
        raise CalibrationError("replay pack path must be a directory")
    checksums, pack_sha256 = parse_and_verify_checksums(pack)
    manifest = read_json(pack / "run_manifest.json")
    plan = read_json(pack / "replay_plan.json")
    leakage = read_json(pack / "leakage_audit.json")
    if not all(isinstance(value, dict) for value in (manifest, plan, leakage)):
        raise CalibrationError("pack manifest, plan and leakage audit must be objects")

    required_manifest = {
        "schema_version": PACK_SCHEMA_VERSION,
        "tool_version": PACK_TOOL_VERSION,
        "case_pack_version": CASE_PACK_VERSION,
        "current_git_commit": FROZEN_GIT_COMMIT,
        "reply_strategy_sha256": FROZEN_REPLY_STRATEGY_SHA256,
        "selected_count": 48,
        "replay_ready_count": 48,
        "calibration_count": 6,
        "selected_quote_tweet_count": 11,
        "quote_contexts_recovered_from_snapshot_cache": 11,
        "quote_context_recovery_failures": 0,
        "quote_context_conflicts": 0,
    }
    for key, expected in required_manifest.items():
        _require_equal(f"run_manifest.{key}", manifest.get(key), expected)
    _require_equal("leakage_audit.result", leakage.get("result"), "pass")
    violations = leakage.get("violations")
    if not isinstance(violations, list) or violations:
        raise CalibrationError("leakage audit must contain zero violations")
    calibration_plan = plan.get("calibration")
    full_plan = plan.get("full_run")
    if not isinstance(calibration_plan, dict) or not isinstance(full_plan, dict):
        raise CalibrationError("replay plan execution counts are missing")
    _require_equal("replay_plan.calibration.pipeline_executions", calibration_plan.get("pipeline_executions"), 12)
    _require_equal("replay_plan.full_run.pipeline_executions", full_plan.get("pipeline_executions"), 96)
    _require_equal("replay_plan.model_calls_performed", plan.get("model_calls_performed"), 0)

    model_rows = read_jsonl(pack / "model_inputs.jsonl")
    historical_rows = read_jsonl(pack / "historical_baselines.jsonl")
    recent_rows = read_jsonl(pack / "recent_account_replies.jsonl")
    calibration_rows = read_jsonl(pack / "calibration_cases.jsonl")
    frozen_rows = read_jsonl(pack / "frozen_cases.jsonl")
    _require_equal("model input count", len(model_rows), 48)
    _require_equal("historical baseline count", len(historical_rows), 48)
    _require_equal("recent-account-reply count", len(recent_rows), 48)
    _require_equal("calibration case count", len(calibration_rows), 6)
    _require_equal("frozen case count", len(frozen_rows), 48)
    models = _index_unique(model_rows, "model inputs")
    historical = _index_unique(historical_rows, "historical baselines")
    recent = _index_unique(recent_rows, "recent account replies")
    calibration = _index_unique(calibration_rows, "calibration cases")
    frozen = _index_unique(frozen_rows, "frozen cases")
    _require_equal("model/historical candidate IDs", set(models), set(historical))
    _require_equal("model/recent candidate IDs", set(models), set(recent))
    _require_equal("model/frozen candidate IDs", set(models), set(frozen))
    if not set(calibration).issubset(models):
        raise CalibrationError("calibration cases do not join exactly to the frozen inputs")
    strata = Counter(row.get("final_stratum") for row in calibration_rows)
    if set(strata) != REQUIRED_STRATA or any(count != 1 for count in strata.values()):
        raise CalibrationError("calibration cases must contain exactly one case in each final stratum")

    full_strata = Counter(row.get("final_stratum") for row in frozen_rows)
    if set(full_strata) != REQUIRED_STRATA or any(
        count != 8 for count in full_strata.values()
    ):
        raise CalibrationError("frozen cases must contain exactly eight cases in each final stratum")
    for candidate_id, selection in calibration.items():
        if selection.get("final_stratum") != frozen[candidate_id].get("final_stratum"):
            raise CalibrationError(
                f"calibration/frozen final stratum mismatch for {candidate_id}"
            )

    calibration_ids = set(calibration)
    holdout_ids = set(models) - calibration_ids
    if (
        len(models) != 48
        or len(calibration_ids) != 6
        or len(holdout_ids) != 42
        or calibration_ids & holdout_ids
        or calibration_ids | holdout_ids != set(models)
    ):
        raise CalibrationError("calibration/holdout candidate-ID partition differs")
    holdout_strata = Counter(frozen[candidate_id].get("final_stratum") for candidate_id in holdout_ids)
    if set(holdout_strata) != REQUIRED_STRATA or any(
        count != 7 for count in holdout_strata.values()
    ):
        raise CalibrationError("holdout cases must contain exactly seven cases in each final stratum")

    all_cases: list[dict[str, Any]] = []
    for candidate_id, model_row in sorted(models.items()):
        recent_row = recent[candidate_id]
        frozen_row = frozen[candidate_id]
        context = model_row.get("validated_context")
        try:
            validated_context = reply_strategy.validate_reply_context(context)
        except (TypeError, ValueError) as exc:
            raise CalibrationError(f"invalid validated context for {candidate_id}: {exc}") from exc
        recent_text = model_row.get("recent_account_replies_text")
        recent_records = recent_row.get("recent_account_replies")
        if (
            not isinstance(recent_text, list)
            or not recent_text
            or any(not isinstance(value, str) or not value.strip() for value in recent_text)
            or not isinstance(recent_records, list)
            or recent_row.get("recent_account_replies_text") != recent_text
            or [row.get("reply_text") for row in recent_records] != recent_text
        ):
            raise CalibrationError(f"invalid recent-account-reply text for {candidate_id}")
        timestamps = [row.get("terminal_timestamp") for row in recent_records]
        if (
            any(not isinstance(value, str) or not value for value in timestamps)
            or timestamps != sorted(timestamps)
        ):
            raise CalibrationError(f"recent account replies are not chronological for {candidate_id}")
        if model_row.get("current_pipeline_lane") != validated_context["lane"]:
            raise CalibrationError(f"pipeline lane mismatch for {candidate_id}")
        if (
            frozen_row.get("replay_ready") is not True
            or frozen_row.get("validated_context") != validated_context
            or frozen_row.get("current_pipeline_lane") != validated_context["lane"]
            or frozen_row.get("recent_account_replies_text") != recent_text
        ):
            raise CalibrationError(f"frozen/model input mismatch for {candidate_id}")
        final_stratum = frozen_row.get("final_stratum")
        if final_stratum not in REQUIRED_STRATA:
            raise CalibrationError(f"invalid final stratum for {candidate_id}")
        all_cases.append({
            "candidate_id": candidate_id,
            "stratum": final_stratum,
            "context": validated_context,
            "recent_replies": list(recent_text),
            "historical": historical[candidate_id],
            "model_input_sha256": value_sha256(model_row),
            "first_timestamp": frozen_row.get("first_timestamp"),
            "terminal_timestamp": frozen_row.get("terminal_timestamp"),
        })

    selected_ids = calibration_ids if case_set == "calibration" else holdout_ids
    cases = [case for case in all_cases if case["candidate_id"] in selected_ids]
    expected_case_count = 6 if case_set == "calibration" else 42
    expected_per_stratum = 1 if case_set == "calibration" else 7
    selected_strata = Counter(case["stratum"] for case in cases)
    if (
        len(cases) != expected_case_count
        or len({case["candidate_id"] for case in cases}) != expected_case_count
        or set(selected_strata) != REQUIRED_STRATA
        or any(count != expected_per_stratum for count in selected_strata.values())
    ):
        raise CalibrationError(f"{case_set} case selection differs")

    strategy_hash = file_sha256(PROJECT_ROOT / "reply_strategy.py")
    _require_equal("current reply_strategy.py SHA-256", strategy_hash, FROZEN_REPLY_STRATEGY_SHA256)
    return {
        "pack_path": pack,
        "pack_sha256": pack_sha256,
        "checksums": checksums,
        "manifest": manifest,
        "replay_plan": plan,
        "leakage_audit": leakage,
        "case_set": case_set,
        "pack_case_count": len(models),
        "calibration_case_count": len(calibration_ids),
        "selected_case_count": len(cases),
        "excluded_calibration_case_count": (
            len(calibration_ids) if case_set == "holdout" else 0
        ),
        "cases_per_stratum": dict(sorted(selected_strata.items())),
        "calibration_candidate_ids": sorted(calibration_ids),
        "holdout_candidate_ids": sorted(holdout_ids),
        "calibration_candidate_ids_sha256": candidate_ids_sha256(calibration_ids),
        "holdout_candidate_ids_sha256": candidate_ids_sha256(holdout_ids),
        "selected_candidate_ids_sha256": candidate_ids_sha256(selected_ids),
        "selected_model_inputs_sha256": value_sha256([
            {
                "candidate_id": case["candidate_id"],
                "model_input_sha256": case["model_input_sha256"],
                "recent_replies_sha256": value_sha256(case["recent_replies"]),
            }
            for case in cases
        ]),
        "all_cases": all_cases,
        "cases": cases,
        "reply_strategy_sha256": strategy_hash,
    }


def recover_holdout_contexts(
    pack_data: dict[str, Any],
    history_corpus: Path | None,
    requested_candidate_ids: Iterable[str],
) -> dict[str, Any]:
    """Recover exact logged pipeline contexts for an explicit holdout subset."""
    if pack_data.get("case_set") != "holdout":
        raise CalibrationError("context recovery requires the holdout case set")
    requested_in_order = list(requested_candidate_ids)
    if any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in requested_in_order):
        raise CalibrationError("context recovery candidate IDs must be non-empty strings")
    if len(set(requested_in_order)) != len(requested_in_order):
        raise CalibrationError("context recovery candidate IDs must not repeat")
    requested = sorted(requested_in_order)
    if requested and history_corpus is None:
        raise CalibrationError(
            "--recover-context-candidate requires --history-corpus"
        )

    history = verify_history_corpus(history_corpus) if history_corpus is not None else None
    cases = {
        case["candidate_id"]: case
        for case in pack_data.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("candidate_id"), str)
    }
    holdout_ids = set(pack_data.get("holdout_candidate_ids", []))
    unknown = sorted(set(requested) - holdout_ids)
    if unknown:
        raise CalibrationError(
            "context recovery candidate is not in the 42-case holdout: "
            + ", ".join(unknown)
        )
    if any(candidate_id not in cases for candidate_id in requested):
        raise CalibrationError("context recovery candidate selection differs")
    if set(requested) & set(REAL_CONTEXT_RECOVERY_ASSERTIONS):
        _require_equal(
            "real holdout candidate-ID SHA-256",
            pack_data.get("holdout_candidate_ids_sha256"),
            HOLDOUT_CANDIDATE_IDS_SHA256,
        )

    if history is None:
        recovery = _empty_context_recovery()
        pack_data["context_recovery"] = recovery
        return recovery

    recovery_base = {
        "history_corpus_path": str(history["history_path"]),
        "history_corpus_sha256": history["history_corpus_sha256"],
        "history_manifest_sha256": history["history_manifest_sha256"],
        "unique_log_records_sha256": history["unique_log_records_sha256"],
        "history_corpus_verification_pass": True,
    }
    if not requested:
        recovery = {
            **_empty_context_recovery(),
            **recovery_base,
        }
        pack_data["context_recovery"] = recovery
        return recovery

    original_hashes = {
        candidate_id: value_sha256(case["context"])
        for candidate_id, case in cases.items()
    }
    matching: dict[str, list[dict[str, Any]]] = {
        candidate_id: [] for candidate_id in requested
    }
    log_path = history["history_path"] / "unique_log_records.jsonl"
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CalibrationError(
                        "invalid history JSONL record "
                        f"unique_log_records.jsonl:{line_number}: {exc}"
                    ) from exc
                if not isinstance(record, dict):
                    raise CalibrationError(
                        "history JSONL record must be an object: "
                        f"unique_log_records.jsonl:{line_number}"
                    )
                message = record.get("message")
                if (
                    record.get("function") != "log_json_debug"
                    or record.get("source_type") != "snapshot"
                    or record.get("parse_warnings") != []
                    or not isinstance(message, str)
                    or not message.startswith(HISTORY_CONTEXT_MESSAGE_PREFIX)
                ):
                    continue
                payload_text = message[len(HISTORY_CONTEXT_MESSAGE_PREFIX):].strip()
                try:
                    logged_context = json.loads(payload_text)
                except json.JSONDecodeError as exc:
                    raise CalibrationError(
                        f"invalid logged reply context on history line {line_number}: {exc}"
                    ) from exc
                if not isinstance(logged_context, dict):
                    raise CalibrationError(
                        f"logged reply context on history line {line_number} is not an object"
                    )

                for candidate_id in requested:
                    original_context = cases[candidate_id]["context"]
                    if (
                        logged_context.get("target_id") != original_context["target_id"]
                        or logged_context.get("lane") != original_context["lane"]
                        or _whitespace_normalized(
                            logged_context.get("incoming_contribution")
                        )
                        != _whitespace_normalized(
                            original_context["incoming_contribution"]
                        )
                    ):
                        continue
                    source_identity = record.get("source_identity")
                    if (
                        not isinstance(source_identity, str)
                        or source_identity not in history["selected_snapshots"]
                    ):
                        raise CalibrationError(
                            "matching history context comes from an unselected snapshot "
                            f"for {candidate_id}"
                        )
                    if set(logged_context) != {
                        "target_id",
                        "thread_id",
                        "lane",
                        "incoming_contribution",
                        "quoted_post",
                        "parent_thread",
                        "clarification_request",
                        "current_date",
                    }:
                        raise CalibrationError(
                            f"logged reply context fields differ for {candidate_id}"
                        )

                    source_timestamp = record.get("timestamp")
                    source_time = _parse_utc_timestamp(
                        source_timestamp, "history context timestamp"
                    )
                    first_time = _parse_utc_timestamp(
                        cases[candidate_id].get("first_timestamp"),
                        f"first timestamp for {candidate_id}",
                    )
                    terminal_time = _parse_utc_timestamp(
                        cases[candidate_id].get("terminal_timestamp"),
                        f"terminal timestamp for {candidate_id}",
                    )
                    if first_time > terminal_time or not first_time <= source_time <= terminal_time:
                        raise CalibrationError(
                            f"history context timestamp is incompatible for {candidate_id}"
                        )

                    record_id = record.get("record_id")
                    raw_record_sha256 = record.get("raw_record_sha256")
                    raw_record_text = record.get("raw_record_text")
                    source_sequence = record.get("source_stream_sequence")
                    occurrence_count = record.get("occurrence_count")
                    occurrence_ids = record.get("source_occurrence_ids")
                    if (
                        not isinstance(record_id, str)
                        or re.fullmatch(r"record-[0-9a-f]{64}", record_id) is None
                        or not isinstance(raw_record_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", raw_record_sha256) is None
                        or not isinstance(raw_record_text, str)
                        or hashlib.sha256(raw_record_text.encode("utf-8")).hexdigest()
                        != raw_record_sha256
                        or not isinstance(source_identity, str)
                        or type(source_sequence) is not int
                        or source_sequence < 0
                        or type(occurrence_count) is not int
                        or occurrence_count <= 0
                        or not isinstance(occurrence_ids, list)
                        or len(occurrence_ids) != occurrence_count
                        or any(not isinstance(value, str) or not value for value in occurrence_ids)
                        or len(set(occurrence_ids)) != occurrence_count
                    ):
                        raise CalibrationError(
                            f"matching history record metadata is invalid for {candidate_id}"
                        )

                    recovered_context = {
                        "target_id": original_context["target_id"],
                        "thread_id": logged_context["thread_id"],
                        "lane": original_context["lane"],
                        "incoming_contribution": original_context[
                            "incoming_contribution"
                        ],
                        "quoted_post": logged_context["quoted_post"],
                        "parent_thread": logged_context["parent_thread"],
                        "clarification_request": logged_context[
                            "clarification_request"
                        ],
                        "current_date": logged_context["current_date"],
                    }
                    try:
                        validated = reply_strategy.validate_reply_context(
                            recovered_context
                        )
                    except (TypeError, ValueError) as exc:
                        raise CalibrationError(
                            f"recovered context is invalid for {candidate_id}: {exc}"
                        ) from exc
                    matching[candidate_id].append({
                        "context": validated,
                        "context_sha256": value_sha256(validated),
                        "record_id": record_id,
                        "raw_record_sha256": raw_record_sha256,
                        "source_identity": source_identity,
                        "source_stream_sequence": source_sequence,
                        "source_timestamp": source_timestamp,
                        "source_occurrence_count": occurrence_count,
                    })
    except (OSError, UnicodeError) as exc:
        raise CalibrationError(f"cannot stream unique_log_records.jsonl: {exc}") from exc

    selected_matches: dict[str, dict[str, Any]] = {}
    for candidate_id in requested:
        rows = matching[candidate_id]
        if not rows:
            raise CalibrationError(
                f"no exact logged pipeline context found for {candidate_id}"
            )
        distinct_contexts = {row["context_sha256"] for row in rows}
        if len(distinct_contexts) != 1:
            raise CalibrationError(
                f"conflicting matching history contexts found for {candidate_id}"
            )
        canonical_records: dict[str, dict[str, Any]] = {}
        for row in rows:
            prior = canonical_records.get(row["record_id"])
            if prior is not None and prior != row:
                raise CalibrationError(
                    f"repeated canonical history record differs for {candidate_id}"
                )
            canonical_records[row["record_id"]] = row
        if len(canonical_records) != 1:
            raise CalibrationError(
                f"multiple matching canonical history records found for {candidate_id}"
            )
        selected = next(iter(canonical_records.values()))
        exact_assertions = REAL_CONTEXT_RECOVERY_ASSERTIONS.get(candidate_id)
        if exact_assertions is not None:
            for field, expected in exact_assertions.items():
                _require_equal(
                    f"authoritative recovery {field}", selected.get(field), expected
                )
        selected_matches[candidate_id] = selected

    recovery_rows: list[dict[str, Any]] = []
    for candidate_id in requested:
        case = cases[candidate_id]
        selected = selected_matches[candidate_id]
        original_context = case["context"]
        recovered_context = selected["context"]
        case["context"] = recovered_context
        parent_ids = [
            str(parent["post_id"]) for parent in recovered_context["parent_thread"]
        ]
        recovery_rows.append({
            "schema_version": 1,
            "runner_version": RUNNER_VERSION,
            "candidate_id": candidate_id,
            "target_id": recovered_context["target_id"],
            "recovery_status": "exact_logged_pipeline_context",
            "recovery_confidence": "exact",
            "history_manifest_sha256": history["history_manifest_sha256"],
            "unique_log_records_sha256": history["unique_log_records_sha256"],
            "record_id": selected["record_id"],
            "raw_record_sha256": selected["raw_record_sha256"],
            "source_identity": selected["source_identity"],
            "source_stream_sequence": selected["source_stream_sequence"],
            "source_timestamp": selected["source_timestamp"],
            "source_occurrence_count": selected["source_occurrence_count"],
            "original_context_sha256": original_hashes[candidate_id],
            "recovered_context_sha256": value_sha256(recovered_context),
            "recovered_thread_id": recovered_context["thread_id"],
            "recovered_parent_post_ids": parent_ids,
            "recovered_parent_post_count": len(parent_ids),
            "validator_result": "pass",
        })

    current_hashes = {
        candidate_id: value_sha256(case["context"])
        for candidate_id, case in cases.items()
    }
    changed_unrequested = sorted(
        candidate_id
        for candidate_id in cases
        if candidate_id not in requested
        and current_hashes[candidate_id] != original_hashes[candidate_id]
    )
    if changed_unrequested:
        raise CalibrationError(
            "context recovery changed unrequested candidates: "
            + ", ".join(changed_unrequested)
        )
    _require_equal(
        "holdout candidate-ID SHA-256 after context recovery",
        candidate_ids_sha256(cases),
        pack_data["holdout_candidate_ids_sha256"],
    )
    recovered_strata = Counter(case["stratum"] for case in cases.values())
    if (
        len(cases) != 42
        or set(recovered_strata) != REQUIRED_STRATA
        or any(count != 7 for count in recovered_strata.values())
    ):
        raise CalibrationError("holdout identity changed during context recovery")

    recovery = {
        **recovery_base,
        "context_recovery_candidate_ids": requested,
        "context_recovery_candidate_ids_sha256": candidate_ids_sha256(requested),
        "recovered_context_count": len(recovery_rows),
        "context_recovery_failures": 0,
        "context_recovery_conflicts": 0,
        "context_recovery_provenance_sha256": value_sha256(recovery_rows),
        "rows": recovery_rows,
    }
    pack_data["context_recovery"] = recovery
    return recovery


def execution_order(
    cases: list[dict[str, Any]], *, blind_seed: str, pack_sha256: str
) -> list[dict[str, Any]]:
    """Return a stable hash-shuffled sequence over all case/variant pairs."""
    rows = [
        {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "shuffle_key": sha256_text(
                "\0".join((blind_seed, pack_sha256, case["candidate_id"], variant))
            ),
        }
        for case in cases
        for variant in VARIANTS
    ]
    rows.sort(key=lambda row: (row["shuffle_key"], row["candidate_id"], row["variant"]))
    for index, row in enumerate(rows, 1):
        row["execution_index"] = index
    return rows


def blind_assignments(
    cases: list[dict[str, Any]], *, blind_seed: str, pack_sha256: str
) -> dict[str, dict[str, str]]:
    """Assign historical/current/compact outputs to A/B/C without random state."""
    assignments: dict[str, dict[str, str]] = {}
    for case in sorted(cases, key=lambda row: row["candidate_id"]):
        ordered = sorted(
            ("historical", "current", "compact"),
            key=lambda source: sha256_text(
                "\0".join((blind_seed, pack_sha256, case["candidate_id"], source))
            ),
        )
        assignments[case["candidate_id"]] = {
            f"Response {letter}": source
            for letter, source in zip(("A", "B", "C"), ordered, strict=True)
        }
    return assignments


def build_execution_plan(
    pack_data: dict[str, Any], manifests: dict[str, dict[str, Any]], blind_seed: str
) -> dict[str, Any]:
    cases = pack_data["cases"]
    case_lookup = {case["candidate_id"]: case for case in cases}
    order = execution_order(cases, blind_seed=blind_seed, pack_sha256=pack_data["pack_sha256"])
    rows = []
    for ordered in order:
        case = case_lookup[ordered["candidate_id"]]
        rows.append({
            **ordered,
            "profile_manifest_sha256": manifests[ordered["variant"]]["manifest_sha256"],
            "validated_context_sha256": value_sha256(case["context"]),
            "recent_replies_sha256": value_sha256(case["recent_replies"]),
            "recent_reply_count": len(case["recent_replies"]),
            "compact_reviewer_recent_reply_count": (
                min(5, len(case["recent_replies"])) if ordered["variant"] == "compact" else 0
            ),
        })
    plan = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "case_set": pack_data["case_set"],
        "selected_candidate_ids_sha256": pack_data[
            "selected_candidate_ids_sha256"
        ],
        "blind_seed": blind_seed,
        "pack_sha256": pack_data["pack_sha256"],
        "planned_variants": list(VARIANTS),
        "planned_pipeline_executions": len(rows),
        "order_derivation": "sha256(blind-seed, pack-sha256, candidate-id, variant)",
        "executions": rows,
    }
    if pack_data["case_set"] == "holdout":
        plan.update(context_recovery_binding(pack_data))
    return plan


def pipeline_execution_identity(
    case: dict[str, Any],
    variant: str,
    manifests: dict[str, dict[str, Any]],
    pack_sha256: str,
    model: str,
) -> dict[str, Any]:
    """Return the exact durable identity shared by calls in one pipeline run."""
    return {
        "candidate_id": case["candidate_id"],
        "stratum": case["stratum"],
        "variant": variant,
        "prompt_profile_version": manifests[variant]["profile_version"],
        "replay_pack_sha256": pack_sha256,
        "profile_manifest_sha256": manifests[variant]["manifest_sha256"],
        "validated_context_sha256": value_sha256(case["context"]),
        "recent_replies_sha256": value_sha256(case["recent_replies"]),
        "model": model,
    }


def pipeline_execution_binding(identity: dict[str, Any]) -> dict[str, str]:
    """Return the one execution hash and case ID shared by every durable record."""
    execution_identity_sha256 = value_sha256(identity)
    return {
        "execution_identity_sha256": execution_identity_sha256,
        "case_identity": (
            f"{identity['candidate_id']}:{identity['variant']}:"
            f"{execution_identity_sha256}"
        ),
    }


def build_prompt_preview_receipts(
    pack_data: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    repository: EvidenceRepository,
) -> list[dict[str, Any]]:
    """Hash initial prompts without constructing or invoking a model transport."""
    receipts: list[dict[str, Any]] = []
    for case in sorted(pack_data["cases"], key=lambda row: row["candidate_id"]):
        context_hash = value_sha256(case["context"])
        recent_hash = value_sha256(case["recent_replies"])
        resolved = repository.resolve_context_quotation(case["context"])
        for variant in VARIANTS:
            with activate_profile(
                variant, recent_account_replies=case["recent_replies"]
            ):
                proposer_system, proposer_user = reply_strategy._proposer_prompts(
                    case["context"],
                    case["recent_replies"],
                    resolved_quotation=resolved,
                    revision=None,
                )
            profile = manifests[variant]
            receipts.append({
                "candidate_id": case["candidate_id"],
                "stratum": case["stratum"],
                "variant": variant,
                "prompt_profile_version": profile["profile_version"],
                "profile_manifest_sha256": profile["manifest_sha256"],
                "validated_context_sha256": context_hash,
                "recent_reply_sha256": recent_hash,
                "proposer_system_prompt_sha256": sha256_text(proposer_system),
                "proposer_user_prompt_sha256": sha256_text(proposer_user),
                "reviewer_profile_sha256": profile["prompts"]["reviewer"]["sha256"],
                "no_reply_profile_sha256": profile["prompts"]["no_reply_review"]["sha256"],
                "claim_auditor_profile_sha256": profile["prompts"]["claim_auditor"]["sha256"],
            })
    return receipts


def output_inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def verify_private_permissions(output: Path) -> None:
    """Require a runner-owned tree with no links and exact private modes."""
    if output.is_symlink() or not output.is_dir():
        raise CalibrationError("resume output must be a private directory, not a link")
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise CalibrationError("resume output directory permissions must be 0700")
    for path in output.rglob("*"):
        if path.is_symlink():
            raise CalibrationError("resume output must not contain symbolic links")
        expected = 0o700 if path.is_dir() else 0o600
        if stat.S_IMODE(path.stat().st_mode) != expected:
            raise CalibrationError(
                f"resume output has non-private permissions: {path.relative_to(output)}"
            )


def prepare_output_directory(
    output_path: Path, pack_path: Path, *, resume: bool = False
) -> Path:
    """Create a new empty private directory or verify an existing resume tree."""
    if output_path.is_symlink():
        raise CalibrationError("output path must not be a symbolic link")
    output = output_path.resolve(strict=False)
    pack = pack_path.resolve(strict=True)
    worktree = PROJECT_ROOT.resolve(strict=True)
    if output_inside(output, pack):
        raise CalibrationError("output must not be inside the replay pack")
    if output_inside(output, worktree):
        raise CalibrationError("output must not be inside the Git worktree")
    if resume:
        if not output.exists():
            raise CalibrationError("--resume requires an existing output directory")
        verify_private_permissions(output)
        return output
    if output.exists():
        if not output.is_dir():
            raise CalibrationError("output path exists and is not a directory")
        if any(output.iterdir()):
            raise CalibrationError("non-empty output directory requires --resume")
    else:
        output.mkdir(mode=0o700, parents=False)
    output.chmod(0o700)
    return output


def atomic_bytes(path: Path, content: bytes) -> None:
    """Replace a private file atomically and durably."""
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(path, json_document_bytes(value))


def write_text(path: Path, value: str) -> None:
    atomic_bytes(path, value.encode("utf-8"))


def jsonl_document_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_bytes(path, jsonl_document_bytes(rows))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(canonical_json_bytes(row) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except BaseException:
        raise


def _markdown_quote(value: str) -> str:
    lines = value.splitlines() or [""]
    return "\n".join(f"> {line}" for line in lines)


def _context_post_text(post: dict[str, Any]) -> str:
    return str(post.get("text") or "")


def context_dependency_flags(context: dict[str, Any]) -> list[str]:
    """Return conservative, deterministic context-review warnings."""
    incoming = context["incoming_contribution"]
    folded = incoming.casefold()
    flags: list[str] = []
    short_rule = CONTEXT_AUDIT_RULES["short_elliptical_question"]
    word_count = len(re.findall(str(short_rule["word_pattern"]), incoming))
    if "?" in incoming and word_count <= int(short_rule["maximum_word_count"]):
        flags.append("short_elliptical_question")
    for flag in (
        "unresolved_third_person_pronoun",
        "demonstrative_reference",
        "what_did_mean_question",
        "source_or_attribution_question",
        "quote_or_above_reference",
    ):
        if re.search(str(CONTEXT_AUDIT_RULES[flag]["pattern"]), folded, re.IGNORECASE):
            flags.append(flag)

    quoted = context["quoted_post"]
    quoted_text = _context_post_text(quoted) if quoted is not None else ""
    if context["lane"] == "quote_tweet" and not quoted_text.strip():
        flags.append("missing_quoted_post_text")
    parent_texts = [_context_post_text(parent) for parent in context["parent_thread"]]
    dependency_flags = set(flags) & set(CONTEXT_DEPENDENCY_FLAG_ORDER[:6])
    if (
        dependency_flags
        and not quoted_text.strip()
        and not any(text.strip() for text in parent_texts)
    ):
        flags.append("missing_or_empty_bounded_parent_context")
    return [flag for flag in CONTEXT_DEPENDENCY_FLAG_ORDER if flag in flags]


def _render_holdout_context_review(reviewed_cases: list[dict[str, Any]]) -> str:
    """Render only the exact context permitted for manual sufficiency review."""
    markdown = ["# Holdout context review", ""]
    for case in reviewed_cases:
        context = case["context"]
        flags = context_dependency_flags(context)
        markdown.extend([
            f"## {case['candidate_id']}",
            "",
            f"Final stratum: {case['stratum']}",
            "",
            f"Lane: {context['lane']}",
            "",
            "Incoming contribution:",
            "",
            _markdown_quote(context["incoming_contribution"]),
            "",
            "Quoted-post context:",
            "",
        ])
        quoted = context["quoted_post"]
        if quoted is None:
            markdown.extend(["(none supplied)", ""])
        else:
            markdown.extend([
                f"Post ID: {quoted['post_id']}",
                "",
                f"Author role: {quoted['author_role']}",
                "",
                "Text:",
                "",
                _markdown_quote(_context_post_text(quoted)),
                "",
            ])
        markdown.extend(["Bounded parent context:", ""])
        if context["parent_thread"]:
            for index, parent in enumerate(context["parent_thread"], 1):
                markdown.extend([
                    f"Parent post {index}:",
                    "",
                    f"Post ID: {parent['post_id']}",
                    "",
                    f"Author role: {parent['author_role']}",
                    "",
                    "Text:",
                    "",
                    _markdown_quote(_context_post_text(parent)),
                    "",
                ])
        else:
            markdown.extend(["(none supplied)", ""])
        clarification = context["clarification_request"]
        markdown.extend(["Clarification request:", ""])
        if clarification is None:
            markdown.extend(["(none supplied)", ""])
        else:
            markdown.extend([
                "Original question:",
                "",
                _markdown_quote(clarification["original_question"]),
                "",
                "Correction:",
                "",
                _markdown_quote(clarification["correction"]),
                "",
            ])
        markdown.extend([
            "Deterministic warning flags: " + (", ".join(flags) if flags else "none"),
            "",
        ])
    return "\n".join(markdown)


CONTEXT_CLEARANCE_COLUMNS = (
    "context_audit_sha256",
    "candidate_id",
    "final_stratum",
    "decision",
    "reviewer_note",
)


def render_context_clearance_csv(
    reviewed_cases: list[dict[str, Any]],
    context_audit_sha256: str,
    decisions: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Render the private clearance template or a verified normalized clearance."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=CONTEXT_CLEARANCE_COLUMNS, lineterminator="\n"
    )
    writer.writeheader()
    for case in reviewed_cases:
        decision, note = (decisions or {}).get(case["candidate_id"], ("", ""))
        writer.writerow({
            "context_audit_sha256": context_audit_sha256,
            "candidate_id": case["candidate_id"],
            "final_stratum": case["stratum"],
            "decision": decision,
            "reviewer_note": note,
        })
    return buffer.getvalue()


def build_holdout_context_artifacts(pack_data: dict[str, Any]) -> dict[str, Any]:
    """Build the structural audit, manual review and blank clearance template."""
    if pack_data.get("case_set") != "holdout":
        raise CalibrationError("holdout context audit requires the holdout case set")
    if (
        pack_data.get("selected_case_count") != 42
        or pack_data.get("excluded_calibration_case_count") != 6
    ):
        raise CalibrationError("holdout context audit requires all 42 complement cases")
    audit_rows: list[dict[str, Any]] = []
    reviewed_cases: list[dict[str, Any]] = []
    warning_counts: Counter[str] = Counter()
    recovery = context_recovery_data(pack_data)
    recovered_ids = set(recovery["context_recovery_candidate_ids"])
    for case in sorted(pack_data["cases"], key=lambda row: row["candidate_id"]):
        context = case["context"]
        flags = context_dependency_flags(context)
        warning_counts.update(flags)
        manual_review_required = (
            case["stratum"] == "factual_or_historical_question" or bool(flags)
        )
        if manual_review_required:
            reviewed_cases.append(case)
        quoted = context["quoted_post"]
        quoted_text = _context_post_text(quoted) if quoted is not None else ""
        parent_texts = [
            _context_post_text(parent) for parent in context["parent_thread"]
        ]
        audit_rows.append({
            "candidate_id": case["candidate_id"],
            "final_stratum": case["stratum"],
            "lane": context["lane"],
            "validated_context_sha256": value_sha256(context),
            "context_recovered": case["candidate_id"] in recovered_ids,
            "incoming_text_sha256": sha256_text(context["incoming_contribution"]),
            "quoted_post_present": quoted is not None,
            "quoted_post_text_sha256": (
                sha256_text(quoted_text) if quoted is not None else None
            ),
            "parent_post_count": len(parent_texts),
            "nonempty_parent_post_count": sum(bool(text.strip()) for text in parent_texts),
            "parent_context_sha256": value_sha256(context["parent_thread"]),
            "clarification_request_present": context["clarification_request"] is not None,
            "context_dependency_flags": flags,
            "manual_review_required": manual_review_required,
        })
    if len(audit_rows) != 42 or len({row["candidate_id"] for row in audit_rows}) != 42:
        raise CalibrationError("holdout context audit inventory differs")
    factual_ids = {
        case["candidate_id"]
        for case in pack_data["cases"]
        if case["stratum"] == "factual_or_historical_question"
    }
    reviewed_ids = {case["candidate_id"] for case in reviewed_cases}
    if len(factual_ids) != 7 or not factual_ids.issubset(reviewed_ids):
        raise CalibrationError("all factual/historical holdout cases require review")
    audit_document = {
        "schema_version": 1,
        "audit_rule_version": CONTEXT_AUDIT_RULE_VERSION,
        "audit_rules": CONTEXT_AUDIT_RULES,
        "audit_rules_sha256": value_sha256(CONTEXT_AUDIT_RULES),
        "runner_source_sha256": file_sha256(Path(__file__).resolve()),
        "case_set": "holdout",
        "pack_sha256": pack_data["pack_sha256"],
        "selected_candidate_ids_sha256": pack_data[
            "selected_candidate_ids_sha256"
        ],
        "selected_model_inputs_sha256": pack_data["selected_model_inputs_sha256"],
        "selected_case_count": 42,
        "superseded_context_audit_sha256": STALE_HOLDOUT_CONTEXT_AUDIT_SHA256,
        **context_recovery_binding(pack_data),
        "manual_context_review_count": len(reviewed_cases),
        "context_warning_counts": {
            flag: warning_counts.get(flag, 0)
            for flag in CONTEXT_DEPENDENCY_FLAG_ORDER
        },
        "cases": audit_rows,
    }
    audit_bytes = json_document_bytes(audit_document)
    audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()
    if recovery["recovered_context_count"] and audit_sha256 == STALE_HOLDOUT_CONTEXT_AUDIT_SHA256:
        raise CalibrationError("recovered context audit did not supersede the stale audit")
    review_text = _render_holdout_context_review(reviewed_cases)
    clearance_text = render_context_clearance_csv(
        reviewed_cases, audit_sha256
    )
    return {
        "audit_document": audit_document,
        "audit_bytes": audit_bytes,
        "context_audit_sha256": audit_sha256,
        "reviewed_cases": reviewed_cases,
        "review_text": review_text,
        "clearance_template_text": clearance_text,
        "recovery_rows": recovery["rows"],
        "recovery_bytes": jsonl_document_bytes(recovery["rows"]),
        "manual_context_review_count": len(reviewed_cases),
        "context_warning_counts": audit_document["context_warning_counts"],
        "old_context_audit_sha256_rejected": (
            audit_sha256 != STALE_HOLDOUT_CONTEXT_AUDIT_SHA256
        ),
    }


def validate_context_clearance(
    path: Path,
    context_artifacts: dict[str, Any],
    *,
    require_all_ready: bool,
) -> dict[str, Any]:
    """Validate one human-edited clearance against the exact current audit."""
    if path.is_symlink() or not path.is_file():
        raise CalibrationError("context clearance must be a regular file")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(CONTEXT_CLEARANCE_COLUMNS):
                raise CalibrationError("context clearance columns differ")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CalibrationError(f"cannot read context clearance: {exc}") from exc

    expected_cases = {
        case["candidate_id"]: case
        for case in context_artifacts["reviewed_cases"]
    }
    expected_audit_sha256 = context_artifacts["context_audit_sha256"]
    decisions: dict[str, tuple[str, str]] = {}
    for row in rows:
        if (
            None in row
            or set(row) != set(CONTEXT_CLEARANCE_COLUMNS)
            or any(value is None for value in row.values())
        ):
            raise CalibrationError("context clearance row fields differ")
        candidate_id = row.get("candidate_id")
        if candidate_id in decisions:
            raise CalibrationError(f"context clearance repeats candidate {candidate_id}")
        case = expected_cases.get(str(candidate_id))
        if case is None:
            raise CalibrationError(
                f"context clearance has unexpected candidate {candidate_id}"
            )
        if row.get("context_audit_sha256") != expected_audit_sha256:
            raise CalibrationError(
                f"context clearance audit SHA-256 is stale for {candidate_id}"
            )
        if row.get("final_stratum") != case["stratum"]:
            raise CalibrationError(
                f"context clearance final stratum differs for {candidate_id}"
            )
        decision = row.get("decision")
        if decision not in {"", "ready", "needs_recovery", "exclude"}:
            raise CalibrationError(
                f"context clearance decision is invalid for {candidate_id}"
            )
        decisions[str(candidate_id)] = (str(decision), row.get("reviewer_note") or "")
    missing = sorted(set(expected_cases) - set(decisions))
    if missing:
        raise CalibrationError(
            "context clearance is missing reviewed candidates: " + ", ".join(missing)
        )
    if len(rows) != len(expected_cases):
        raise CalibrationError("context clearance candidate inventory differs")

    decision_values = {decision for decision, _note in decisions.values()}
    if "exclude" in decision_values:
        status = "exclude"
    elif "needs_recovery" in decision_values:
        status = "needs_recovery"
    elif decision_values == {"ready"}:
        status = "ready"
    else:
        status = "pending"
    if require_all_ready and status != "ready":
        if status == "exclude":
            raise CalibrationError("holdout context clearance contains exclude")
        if status == "needs_recovery":
            raise CalibrationError("holdout context clearance contains needs_recovery")
        raise CalibrationError("holdout context clearance is pending")
    normalized_text = render_context_clearance_csv(
        context_artifacts["reviewed_cases"], expected_audit_sha256, decisions
    )
    return {
        "status": status,
        "paid_execution_ready": status == "ready",
        "normalized_text": normalized_text,
        "normalized_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
    }


def _historical_public_output(record: dict[str, Any]) -> str | None:
    reply = record.get("historical_reply")
    return reply if isinstance(reply, str) and reply else None


def explicit_outcome(status: str, public_reply: str | None) -> dict[str, Any]:
    """Return only one of the two outcomes permitted in a blinded review."""
    if status == "approved" and isinstance(public_reply, str) and public_reply:
        return {"status": "approved", "public_reply": public_reply}
    if status == "no_reply" and public_reply is None:
        return {"status": "no_reply", "public_reply": None}
    raise CalibrationError(f"invalid calibration outcome status: {status}")


def render_outcome(outcome: dict[str, Any]) -> str:
    """Render an explicit valid outcome; operational states never become silence."""
    if not isinstance(outcome, dict) or set(outcome) != {"status", "public_reply"}:
        raise CalibrationError("blind review requires an explicit valid outcome object")
    valid = explicit_outcome(outcome.get("status"), outcome.get("public_reply"))
    return valid["public_reply"] if valid["status"] == "approved" else "NO REPLY"


def build_blind_review(
    cases: list[dict[str, Any]],
    assignments: dict[str, dict[str, str]],
    outputs: dict[tuple[str, str], dict[str, Any]] | None,
) -> tuple[str, str]:
    """Render blinded Markdown/CSV; mappings remain exclusively in blind_key.json."""
    markdown = ["# Blinded reply prompt calibration", ""]
    csv_buffer = io.StringIO(newline="")
    fieldnames = [
        "candidate_id", "final_stratum", "response_label", "response_text", *SCORING_COLUMNS
    ]
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for case in sorted(cases, key=lambda row: row["candidate_id"]):
        candidate_id = case["candidate_id"]
        context = case["context"]
        markdown.extend([
            f"## {candidate_id}",
            "",
            f"Final stratum: {case['stratum']}",
            "",
            "Incoming contribution:",
            "",
            _markdown_quote(context["incoming_contribution"]),
            "",
        ])
        if context["quoted_post"] is not None:
            markdown.extend([
                "Quoted context:", "", _markdown_quote(_context_post_text(context["quoted_post"])), ""
            ])
        if context["parent_thread"]:
            markdown.extend(["Parent context:", ""])
            for parent in context["parent_thread"]:
                markdown.extend([_markdown_quote(_context_post_text(parent)), ""])
        for label in ("Response A", "Response B", "Response C"):
            source = assignments[candidate_id][label]
            if outputs is None:
                rendered = ""
            else:
                outcome = outputs.get((candidate_id, source))
                if outcome is None:
                    raise CalibrationError(
                        f"blind review is missing outcome for {candidate_id}:{source}"
                    )
                rendered = render_outcome(outcome)
            markdown.extend([f"### {label}", "", rendered, ""])
            row = {
                "candidate_id": candidate_id,
                "final_stratum": case["stratum"],
                "response_label": label,
                "response_text": rendered,
                **{column: "" for column in SCORING_COLUMNS},
            }
            writer.writerow(row)
        markdown.extend([
            "Scoring:",
            "",
            "- Safety and factual integrity pass: ",
            "- Mode choice (1–5): ",
            "- Specificity (1–5): ",
            "- Added value (1–5): ",
            "- Voice (1–5): ",
            "- Wit appropriateness (1–5): ",
            "- No-reply correctness (1–5): ",
            "- Overall rank: ",
            "- Reviewer note: ",
            "",
        ])
    return "\n".join(markdown), csv_buffer.getvalue()


def public_pack_verification(pack_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pack_path": str(pack_data["pack_path"]),
        "pack_sha256": pack_data["pack_sha256"],
        "checksum_payload_count": len(pack_data["checksums"]),
        "checksum_payloads": dict(sorted(pack_data["checksums"].items())),
        "checksums_pass": True,
        "schema_version_pass": True,
        "tool_version_pass": True,
        "case_pack_version_pass": True,
        "git_commit_pass": True,
        "reply_strategy_sha256": pack_data["reply_strategy_sha256"],
        "reply_strategy_sha256_pass": True,
        "selected_count": 48,
        "replay_ready_count": 48,
        "calibration_count": 6,
        "holdout_count": 42,
        "calibration_holdout_overlap_count": 0,
        "calibration_candidate_ids_sha256": pack_data[
            "calibration_candidate_ids_sha256"
        ],
        "holdout_candidate_ids_sha256": pack_data[
            "holdout_candidate_ids_sha256"
        ],
        "calibration_strata": sorted(REQUIRED_STRATA),
        "exactly_one_calibration_case_per_stratum": True,
        "quote_contexts_recovered_from_snapshot_cache": 11,
        "quote_context_recovery_failures": 0,
        "quote_context_conflicts": 0,
        "leakage_result": "pass",
        "leakage_violation_count": 0,
        "calibration_pipeline_executions": 12,
        "full_run_pipeline_executions": 96,
        "pack_model_calls_performed": 0,
    }


def selection_manifest_fields(
    pack_data: dict[str, Any],
    plan: dict[str, Any],
    *,
    context_artifacts: dict[str, Any] | None = None,
    clearance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the common immutable case-set and context-gate summary."""
    is_holdout = pack_data["case_set"] == "holdout"
    return {
        "case_set": pack_data["case_set"],
        "pack_case_count": pack_data["pack_case_count"],
        "calibration_case_count": pack_data["calibration_case_count"],
        "selected_case_count": pack_data["selected_case_count"],
        "selected_unique_candidate_count": len(
            {case["candidate_id"] for case in pack_data["cases"]}
        ),
        "excluded_calibration_case_count": pack_data[
            "excluded_calibration_case_count"
        ],
        "calibration_holdout_overlap_count": 0,
        "cases_per_stratum": pack_data["cases_per_stratum"],
        "planned_pipeline_executions": plan["planned_pipeline_executions"],
        "calibration_candidate_ids_sha256": pack_data[
            "calibration_candidate_ids_sha256"
        ],
        "holdout_candidate_ids_sha256": pack_data[
            "holdout_candidate_ids_sha256"
        ],
        "selected_candidate_ids_sha256": pack_data[
            "selected_candidate_ids_sha256"
        ],
        **(context_recovery_binding(pack_data) if is_holdout else {}),
        "context_audit_sha256": (
            context_artifacts["context_audit_sha256"] if is_holdout else None
        ),
        "old_context_audit_sha256_rejected": (
            context_artifacts["old_context_audit_sha256_rejected"]
            if is_holdout and context_artifacts is not None
            else False
        ),
        "manual_context_review_count": (
            context_artifacts["manual_context_review_count"] if is_holdout else 0
        ),
        "context_warning_counts": (
            context_artifacts["context_warning_counts"] if is_holdout else {}
        ),
        "context_clearance_status": (
            clearance["status"] if is_holdout and clearance is not None else "not_applicable"
        ),
        "paid_execution_ready": (
            clearance["paid_execution_ready"]
            if is_holdout and clearance is not None
            else False
        ),
    }


def validation_report(
    pack_data: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    *,
    context_artifacts: dict[str, Any] | None = None,
    clearance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact_counts = {
        name: {
            "word_count": row["word_count"],
            "maximum_word_count": row["maximum_word_count"],
            "sha256": row["sha256"],
            "passes": row["word_count"] <= COMPACT_WORD_CAPS[name],
        }
        for name, row in manifests["compact"]["prompts"].items()
    }
    return {
        "schema_version": 1,
        "mode": "validate-only",
        **selection_manifest_fields(
            pack_data,
            plan,
            context_artifacts=context_artifacts,
            clearance=clearance,
        ),
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        "model_calls_performed": 0,
        "http_requests_performed": 0,
        "current_profile_uses_exact_production_prompt_functions": True,
        "compact_prompts": compact_counts,
        "compact_prompt_word_caps_pass": all(row["passes"] for row in compact_counts.values()),
        "current_reply_strategy_matches_frozen_pack": (
            pack_data["reply_strategy_sha256"] == FROZEN_REPLY_STRATEGY_SHA256
        ),
        "replay_pack_provenance_pass": True,
        "leakage_check_pass": True,
        "compact_reviewer_recent_reply_sample_maximum": 5,
        "compact_no_reply_recent_reply_sample_maximum": 5,
        "compact_no_reply_recent_reply_payload_contract_pass": True,
        "provider_endpoint": DEFAULT_XAI_BASE,
        "provider_endpoint_locked_to_xai": True,
        "posting_enabled": False,
        "search_enabled": False,
        "tools_enabled": False,
        "media_enabled": False,
}


def calibration_report_markdown(
    mode: str,
    model_calls: int,
    http_requests: int,
    *,
    case_set: str = "calibration",
    selected_case_count: int = 6,
    planned_pipeline_executions: int = 12,
) -> str:
    case_summary = (
        ["Calibration cases: 6"]
        if case_set == "calibration" and selected_case_count == 6
        else [f"Case set: {case_set}", "", f"Selected cases: {selected_case_count}"]
    )
    return "\n".join([
        "# Reply prompt calibration report",
        "",
        f"Mode: {mode}",
        "",
        *case_summary,
        "",
        "Variants: 2",
        "",
        f"Planned pipeline executions: {planned_pipeline_executions}",
        "",
        f"Model calls performed: {model_calls}",
        "",
        f"HTTP requests performed: {http_requests}",
        "",
        "Human scoring remains blank; no qualitative judgement was generated automatically.",
        "",
    ])


def write_sha256sums(output: Path) -> None:
    rows = []
    for path in sorted(output.rglob("*"), key=lambda item: item.relative_to(output).as_posix()):
        if path.is_file() and path.name != "SHA256SUMS":
            relative = path.relative_to(output).as_posix()
            rows.append(f"{file_sha256(path)}  {relative}\n")
    write_text(output / "SHA256SUMS", "".join(rows))


def verify_output_sha256sums(output: Path) -> None:
    expected_names = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    seen: set[str] = set()
    for line_number, line in enumerate(
        (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if match is None:
            raise CalibrationError(f"generated SHA256SUMS line {line_number} is invalid")
        expected, name = match.groups()
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name in seen:
            raise CalibrationError("generated SHA256SUMS contains an unsafe path")
        path = output / relative
        if not path.is_file() or file_sha256(path) != expected:
            raise CalibrationError(f"generated checksum failed for {name}")
        seen.add(name)
    if seen != expected_names:
        raise CalibrationError("generated SHA256SUMS inventory mismatch")


def enforce_private_permissions(output: Path) -> None:
    for path in output.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    output.chmod(0o700)


def assert_no_secret(output: Path, api_key: str | None) -> None:
    forbidden = [b"Authorization:", b'"Authorization"']
    if api_key:
        forbidden.append(api_key.encode("utf-8"))
    for path in output.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            if any(value and value in content for value in forbidden):
                raise CalibrationError(f"secret or authorization header leaked into {path.name}")


def build_repository() -> EvidenceRepository:
    corpus = PROJECT_ROOT / "semantic_alignment_research/quote_research_full_001"
    return EvidenceRepository(
        corpus,
        factual_evidence_path=PROJECT_ROOT / "reply_factual_evidence.json",
    )


def common_execution_provenance(
    *,
    plan: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    repository: EvidenceRepository,
    source_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Build the source, plan, profile and evidence portion of a run manifest."""
    return {
        **source_provenance,
        "reply_strategy_sha256": source_provenance["reply_strategy_sha256"],
        "reply_evidence_sha256": source_provenance["reply_evidence_sha256"],
        "evidence_repository_fingerprint": evidence_repository_fingerprint(repository),
        "execution_plan_sha256": value_sha256(plan),
        "current_profile_manifest_sha256": manifests["current"]["manifest_sha256"],
        "compact_profile_manifest_sha256": manifests["compact"]["manifest_sha256"],
    }


def validate_only_run(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    *,
    context_artifacts: dict[str, Any] | None = None,
    clearance: dict[str, Any] | None = None,
) -> Path:
    verify_current_production_objects()
    manifests = profile_manifests()
    verify_frozen_profile_manifests(manifests)
    repository = build_repository()
    plan = build_execution_plan(pack_data, manifests, args.blind_seed)
    provenance = common_execution_provenance(
        plan=plan,
        manifests=manifests,
        repository=repository,
        source_provenance=execution_provenance(),
    )
    previews = build_prompt_preview_receipts(pack_data, manifests, repository)
    assignments = blind_assignments(
        pack_data["cases"], blind_seed=args.blind_seed, pack_sha256=pack_data["pack_sha256"]
    )
    markdown, csv_text = build_blind_review(pack_data["cases"], assignments, None)
    output = prepare_output_directory(args.output, pack_data["pack_path"])
    run_manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "mode": "validate-only",
        "model": args.model,
        "xai_base": args.xai_base,
        "blind_seed": args.blind_seed,
        "pack_sha256": pack_data["pack_sha256"],
        **selection_manifest_fields(
            pack_data,
            plan,
            context_artifacts=context_artifacts,
            clearance=clearance,
        ),
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        **provenance,
        "completed_pipeline_executions": 0,
        "valid_approved_count": 0,
        "valid_no_reply_count": 0,
        "operational_failure_count": 0,
        "model_calls_performed": 0,
        "http_requests_performed": 0,
        "posting_enabled": False,
        "search_enabled": False,
        "tools_enabled": False,
        "media_enabled": False,
    }
    blind_key = {
        "schema_version": 1,
        "derivation": "sha256(blind-seed, pack-sha256, candidate-id, source)",
        "assignments": assignments,
    }
    write_json(output / "run_manifest.json", run_manifest)
    write_json(output / "pack_verification.json", public_pack_verification(pack_data))
    write_json(output / "profile_manifests.json", manifests)
    write_json(output / "execution_plan.json", plan)
    write_json(
        output / "validation_report.json",
        validation_report(
            pack_data,
            manifests,
            plan,
            context_artifacts=context_artifacts,
            clearance=clearance,
        ),
    )
    write_jsonl(output / "prompt_preview_receipts.jsonl", previews)
    write_text(output / "blind_review.md", markdown)
    write_text(output / "blind_review.csv", csv_text)
    write_json(output / "blind_key.json", blind_key)
    write_text(
        output / "calibration_report.md",
        calibration_report_markdown(
            "validate-only",
            0,
            0,
            case_set=pack_data["case_set"],
            selected_case_count=pack_data["selected_case_count"],
            planned_pipeline_executions=plan["planned_pipeline_executions"],
        ),
    )
    if pack_data["case_set"] == "holdout":
        if context_artifacts is None or clearance is None:
            raise CalibrationError("holdout context artifacts are missing")
        atomic_bytes(output / "holdout_context_audit.json", context_artifacts["audit_bytes"])
        write_text(
            output / "holdout_context_review.md", context_artifacts["review_text"]
        )
        write_text(
            output / "holdout_context_clearance.csv",
            context_artifacts["clearance_template_text"],
        )
        atomic_bytes(
            output / "holdout_context_recovery.jsonl",
            context_artifacts["recovery_bytes"],
        )
    enforce_private_permissions(output)
    assert_no_secret(output, None)
    write_sha256sums(output)
    enforce_private_permissions(output)
    verify_output_sha256sums(output)
    return output


class CountingHTTP:
    """Count the explicit metadata GET and model POST calls in execute mode."""

    def __init__(self) -> None:
        self.requests = 0

    def get(self, *args: Any, **kwargs: Any) -> Any:
        self.requests += 1
        return pilot.requests.get(*args, **kwargs)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        self.requests += 1
        return pilot.requests.post(*args, **kwargs)


def build_run_identity(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    provenance: dict[str, Any],
    *,
    context_artifacts: dict[str, Any] | None = None,
    clearance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind every immutable input to a paid run before its first HTTP request."""
    return {
        "schema_version": RUN_IDENTITY_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "replay_pack_sha256": pack_data["pack_sha256"],
        "case_set": pack_data["case_set"],
        "selected_candidate_ids_sha256": pack_data[
            "selected_candidate_ids_sha256"
        ],
        "calibration_candidate_ids_sha256": pack_data[
            "calibration_candidate_ids_sha256"
        ],
        "holdout_candidate_ids_sha256": pack_data[
            "holdout_candidate_ids_sha256"
        ],
        **(
            context_recovery_binding(pack_data)
            if pack_data["case_set"] == "holdout"
            else {}
        ),
        "context_audit_sha256": (
            context_artifacts["context_audit_sha256"]
            if context_artifacts is not None
            else None
        ),
        "context_clearance_sha256": (
            clearance["normalized_sha256"] if clearance is not None else None
        ),
        "execution_plan_sha256": value_sha256(plan),
        "current_profile_manifest_sha256": manifests["current"]["manifest_sha256"],
        "compact_profile_manifest_sha256": manifests["compact"]["manifest_sha256"],
        "model": args.model,
        "xai_endpoint": args.xai_base,
        "blind_seed": args.blind_seed,
        "hard_limit_usd": args.hard_limit_usd,
        "maximum_rate_limit_retries": args.maximum_rate_limit_retries,
        "maximum_server_error_retries": args.maximum_server_error_retries,
        "runner_git_commit_expected": provenance["runner_git_commit_expected"],
        "runner_git_commit_actual": provenance["runner_git_commit"],
        "worktree_clean": provenance["worktree_clean"],
        "reply_strategy_sha256": provenance["reply_strategy_sha256"],
        "reply_evidence_sha256": provenance["reply_evidence_sha256"],
        "pilot_ai_first_reply_strategy_sha256": provenance[
            "pilot_transport_source_sha256"
        ],
        "reply_prompt_profiles_sha256": provenance[
            "prompt_profiles_source_sha256"
        ],
        "run_reply_prompt_calibration_sha256": provenance["runner_source_sha256"],
        "evidence_repository_fingerprint": provenance[
            "evidence_repository_fingerprint"
        ],
    }


def validate_provider_metadata(metadata: Any, *, model: str) -> dict[str, Any]:
    """Validate the exact metadata document retained after the one permitted GET."""
    required = {
        "model",
        "retrieved_at",
        "usd_ticks_per_dollar",
        "prompt_text_token_price",
        "cached_prompt_text_token_price",
        "completion_text_token_price",
    }
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise CalibrationError("stored provider model metadata fields differ")
    if metadata.get("model") != model:
        raise CalibrationError("stored provider model metadata model differs")
    if not isinstance(metadata.get("retrieved_at"), str) or not metadata["retrieved_at"]:
        raise CalibrationError("stored provider model metadata timestamp is invalid")
    if metadata.get("usd_ticks_per_dollar") != pilot.USD_TICKS_PER_DOLLAR:
        raise CalibrationError("stored provider model metadata currency scale differs")
    price_fields = required - {"model", "retrieved_at", "usd_ticks_per_dollar"}
    if any(type(metadata.get(name)) is not int or metadata[name] <= 0 for name in price_fields):
        raise CalibrationError("stored provider model metadata prices are invalid")
    return metadata


def build_provider_phase_identity(
    *,
    identity_sha256: str,
    metadata_sha256: str,
    metadata: dict[str, Any],
    model: str,
    xai_endpoint: str,
) -> dict[str, Any]:
    """Bind authenticated pricing inputs before any model operation can begin."""
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_identity_sha256": identity_sha256,
        "provider_model_metadata_sha256": metadata_sha256,
        "model": model,
        "xai_endpoint": xai_endpoint,
        "usd_ticks_per_dollar": metadata["usd_ticks_per_dollar"],
        "prompt_text_token_price": metadata["prompt_text_token_price"],
        "cached_prompt_text_token_price": metadata[
            "cached_prompt_text_token_price"
        ],
        "completion_text_token_price": metadata[
            "completion_text_token_price"
        ],
    }


def validate_provider_phase_identity(
    phase: Any,
    *,
    identity_sha256: str,
    metadata_path: Path,
    metadata: dict[str, Any],
    model: str,
    xai_endpoint: str,
) -> dict[str, Any]:
    """Require an exact provider phase binding to the stored metadata bytes."""
    if not isinstance(phase, dict) or set(phase) != PROVIDER_PHASE_FIELDS:
        raise CalibrationError("stored provider phase identity fields differ")
    expected = build_provider_phase_identity(
        identity_sha256=identity_sha256,
        metadata_sha256=file_sha256(metadata_path),
        metadata=metadata,
        model=model,
        xai_endpoint=xai_endpoint,
    )
    if phase != expected:
        raise CalibrationError("stored provider phase identity differs")
    return phase


def read_and_verify_provider_phase(
    output: Path,
    *,
    identity_sha256: str,
    model: str,
    xai_endpoint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-read both authoritative provider files and verify their exact binding."""
    metadata_path = output / "provider_model_metadata.json"
    metadata = validate_provider_metadata(read_json(metadata_path), model=model)
    phase = validate_provider_phase_identity(
        read_json(output / "provider_phase_identity.json"),
        identity_sha256=identity_sha256,
        metadata_path=metadata_path,
        metadata=metadata,
        model=model,
        xai_endpoint=xai_endpoint,
    )
    return metadata, phase


def publish_provider_phase(
    output: Path,
    *,
    identity_sha256: str,
    model: str,
    xai_endpoint: str,
    api_key: str,
    get: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch, atomically publish, then re-read the complete provider phase."""
    metadata = validate_provider_metadata(
        pilot.fetch_model_metadata(
            api_key=api_key,
            base_url=xai_endpoint,
            model=model,
            get=get,
        ),
        model=model,
    )
    metadata_path = output / "provider_model_metadata.json"
    write_json(metadata_path, metadata)
    phase = build_provider_phase_identity(
        identity_sha256=identity_sha256,
        metadata_sha256=file_sha256(metadata_path),
        metadata=metadata,
        model=model,
        xai_endpoint=xai_endpoint,
    )
    write_json(output / "provider_phase_identity.json", phase)
    return read_and_verify_provider_phase(
        output,
        identity_sha256=identity_sha256,
        model=model,
        xai_endpoint=xai_endpoint,
    )


def validate_resume_ledger(
    ledger_data: Any, *, model: str, hard_limit_usd: float
) -> dict[str, Any]:
    """Refuse every cross-invocation operation that is not already completed."""
    if not isinstance(ledger_data, dict) or not isinstance(ledger_data.get("operations"), list):
        raise CalibrationError("existing cost ledger is invalid")
    if (
        ledger_data.get("schema_version") != pilot.SCHEMA_VERSION
        or ledger_data.get("pilot_version") != RUNNER_VERSION
        or ledger_data.get("model") != model
        or ledger_data.get("hard_limit_usd") != hard_limit_usd
    ):
        raise CalibrationError("existing cost ledger identity differs")
    if ledger_data.get("blocked") or str(ledger_data.get("status") or "").startswith(
        "blocked"
    ):
        raise CalibrationError(f"existing cost ledger is blocked: {ledger_data.get('blocked_reason')}")
    seen: set[str] = set()
    for operation in ledger_data["operations"]:
        if not isinstance(operation, dict):
            raise CalibrationError("existing cost ledger operation is invalid")
        logical_call_id = operation.get("logical_call_id")
        request_hash = operation.get("request_hash")
        if (
            not isinstance(logical_call_id, str)
            or not logical_call_id
            or logical_call_id in seen
            or not isinstance(request_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None
        ):
            raise CalibrationError("existing cost ledger operation identity is invalid")
        seen.add(logical_call_id)
        if operation.get("status") != "completed":
            raise CalibrationError(
                f"resume blocked by incomplete cost-ledger operation {logical_call_id} "
                f"with status {operation.get('status')}"
            )
    return ledger_data


def execution_evidence_exists(output: Path) -> bool:
    """Return whether any model-operation or pipeline evidence already exists."""
    if any((output / name).exists() for name in FINAL_OUTPUT_FILES):
        return True
    for name in (
        "prompt_receipts.jsonl",
        "pipeline_results.jsonl",
        "pipeline_audits.jsonl",
        "execution_failures.jsonl",
    ):
        path = output / name
        if path.exists() and read_jsonl(path):
            return True
    response_dir = output / "raw_responses"
    return response_dir.exists() and any(response_dir.iterdir())


def open_resume_ledger(
    output: Path, *, model: str, hard_limit_usd: float
) -> pilot.PilotLedger:
    """Open the ledger, or recreate one empty ledger before all provider evidence."""
    ledger_path = output / "cost_ledger.json"
    if not ledger_path.exists():
        if (
            execution_evidence_exists(output)
            or (output / "provider_model_metadata.json").exists()
            or (output / "provider_phase_identity.json").exists()
        ):
            raise CalibrationError(
                "missing cost ledger cannot be recreated after provider or execution evidence"
            )
        return pilot.PilotLedger(
            ledger_path,
            model=model,
            hard_limit_usd=hard_limit_usd,
            run_version=RUNNER_VERSION,
        )
    validate_resume_ledger(
        read_json(ledger_path), model=model, hard_limit_usd=hard_limit_usd
    )
    return pilot.PilotLedger(
        ledger_path,
        model=model,
        hard_limit_usd=hard_limit_usd,
        run_version=RUNNER_VERSION,
    )


def resume_or_publish_provider_phase(
    output: Path,
    *,
    ledger_data: dict[str, Any],
    identity_sha256: str,
    model: str,
    xai_endpoint: str,
    api_key: str,
    get: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resume authenticated pricing only while the ledger has zero operations."""
    metadata_path = output / "provider_model_metadata.json"
    phase_path = output / "provider_phase_identity.json"
    metadata_exists = metadata_path.exists()
    phase_exists = phase_path.exists()
    has_operations = bool(ledger_data.get("operations"))
    if has_operations:
        if not metadata_exists or not phase_exists:
            raise CalibrationError(
                "provider metadata and phase identity are mandatory after model operations"
            )
        return read_and_verify_provider_phase(
            output,
            identity_sha256=identity_sha256,
            model=model,
            xai_endpoint=xai_endpoint,
        )
    if metadata_exists and phase_exists:
        return read_and_verify_provider_phase(
            output,
            identity_sha256=identity_sha256,
            model=model,
            xai_endpoint=xai_endpoint,
        )
    if metadata_exists != phase_exists:
        (metadata_path if metadata_exists else phase_path).unlink()
    return publish_provider_phase(
        output,
        identity_sha256=identity_sha256,
        model=model,
        xai_endpoint=xai_endpoint,
        api_key=api_key,
        get=get,
    )


def verify_completed_response_caches(output: Path, ledger_data: dict[str, Any]) -> None:
    """Verify every completed operation's exact immutable response cache."""
    response_dir = output / "raw_responses"
    if not response_dir.is_dir():
        raise CalibrationError("resume output lacks raw_responses")
    for operation in ledger_data["operations"]:
        logical_call_id = operation["logical_call_id"]
        cache_path = response_dir / (
            hashlib.sha256(logical_call_id.encode("utf-8")).hexdigest() + ".json"
        )
        cached = read_json(cache_path)
        if (
            not isinstance(cached, dict)
            or cached.get("logical_call_id") != logical_call_id
            or cached.get("request_hash") != operation["request_hash"]
            or not isinstance(cached.get("raw"), dict)
        ):
            raise CalibrationError(f"completed response cache differs for {logical_call_id}")
        content = cached["raw"].get("choices", [{}])[0].get("message", {}).get("content")
        response_hash = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if response_hash != operation.get("response_hash"):
            raise CalibrationError(f"completed response hash differs for {logical_call_id}")


def index_execution_records(
    path: Path, *, label: str
) -> dict[tuple[str, str], dict[str, Any]]:
    """Index one append-only pipeline journal and reject all duplicate identities."""
    rows = read_jsonl(path) if path.exists() else []
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        candidate_id = row.get("candidate_id")
        variant = row.get("variant")
        if not isinstance(candidate_id, str) or variant not in VARIANTS:
            raise CalibrationError(f"{label} contains an invalid execution identity")
        key = (candidate_id, variant)
        if key in indexed:
            raise CalibrationError(f"{label} repeats execution identity {candidate_id}:{variant}")
        indexed[key] = row
    return indexed


def index_prompt_receipts(path: Path) -> dict[str, dict[str, Any]]:
    """Index exact logical request receipts and reject duplicate or altered rows."""
    indexed: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path) if path.exists() else []:
        logical_call_id = row.get("logical_call_id")
        request_hash = row.get("request_hash")
        if (
            not isinstance(logical_call_id, str)
            or not logical_call_id
            or not isinstance(request_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None
        ):
            raise CalibrationError("prompt receipt contains an invalid request identity")
        if logical_call_id in indexed:
            prior = indexed[logical_call_id]
            if prior.get("request_hash") != request_hash:
                raise CalibrationError(f"prompt receipt request hash differs for {logical_call_id}")
            raise CalibrationError(f"prompt receipt repeats logical call {logical_call_id}")
        indexed[logical_call_id] = row
    return indexed


def verify_prompt_receipt_contracts(
    receipts: dict[str, dict[str, Any]],
    *,
    cases: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    pack_sha256: str,
    model: str,
) -> None:
    """Verify that every stored receipt belongs exactly to its declared execution."""
    for logical_call_id, receipt in receipts.items():
        candidate_id = receipt.get("candidate_id")
        variant = receipt.get("variant")
        if candidate_id not in cases or variant not in VARIANTS:
            raise CalibrationError(f"prompt receipt execution identity differs for {logical_call_id}")
        expected = pipeline_execution_identity(
            cases[candidate_id], variant, manifests, pack_sha256, model
        )
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise CalibrationError(f"prompt receipt execution identity differs for {logical_call_id}")
        binding = pipeline_execution_binding(expected)
        if any(receipt.get(key) != value for key, value in binding.items()):
            raise CalibrationError(f"prompt receipt execution binding differs for {logical_call_id}")
        sequence = receipt.get("call_sequence")
        stage = receipt.get("stage")
        if (
            type(sequence) is not int
            or sequence <= 0
            or not isinstance(stage, str)
            or not stage
            or logical_call_id != f"{binding['case_identity']}:{sequence}:{stage}"
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("system_prompt_sha256")))
            is None
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("user_prompt_sha256")))
            is None
            or receipt.get("temperature") != 0
            or receipt.get("media_transmitted") is not False
            or receipt.get("transport_status")
            not in {
                "prepared_for_transport",
                "returned_from_completed_cache",
                "transmitted_and_completed",
            }
        ):
            raise CalibrationError(f"prompt receipt contract differs for {logical_call_id}")


def verify_receipts_against_ledger(
    receipts: dict[str, dict[str, Any]],
    ledger_data: dict[str, Any],
    *,
    require_complete_inventory: bool,
) -> None:
    """Cross-check receipt identities while leaving billing authority in the ledger."""
    operations = {
        row["logical_call_id"]: row for row in ledger_data.get("operations", [])
    }
    for logical_call_id in set(receipts) & set(operations):
        if receipts[logical_call_id].get("request_hash") != operations[logical_call_id].get(
            "request_hash"
        ):
            raise CalibrationError(f"prompt receipt request hash differs for {logical_call_id}")
    if require_complete_inventory and set(receipts) != set(operations):
        raise CalibrationError("prompt receipt and cost-ledger inventories differ")


def audit_model_stage_sequence(audit: Any) -> list[str]:
    """Derive model attempts in audit order under run_reply_pipeline's audit contract."""
    if not isinstance(audit, list):
        raise CalibrationError("pipeline audit is not a list")
    stages: list[str] = []
    for row in audit:
        if not isinstance(row, dict):
            raise CalibrationError("pipeline audit row is invalid")
        stage = row.get("stage")
        status = row.get("status")
        if stage not in MODEL_AUDIT_STAGES:
            continue
        if status in {"completed", "invalid", "invalid_response_retry"}:
            stages.append(stage)
        elif (
            status == "insufficient"
            and stage in {"evidence", "revision_evidence"}
            and row.get("reason") != "claim_without_candidate_passage"
        ):
            stages.append(stage)
    return stages


def collect_call_inventory(
    identity: dict[str, Any],
    *,
    audit: list[dict[str, Any]],
    receipts: dict[str, dict[str, Any]],
    ledger_data: dict[str, Any],
) -> dict[str, Any]:
    """Bind one pipeline to exactly its completed receipt and billed operations."""
    binding = pipeline_execution_binding(identity)
    case_identity = binding["case_identity"]
    matching_receipts = sorted(
        (
            row
            for row in receipts.values()
            if row.get("case_identity") == case_identity
        ),
        key=lambda row: int(row.get("call_sequence") or 0),
    )
    matching_operations = [
        row
        for row in ledger_data.get("operations", [])
        if row.get("case_id") == case_identity
    ]
    if matching_receipts and not matching_operations:
        raise CalibrationError(f"prompt receipt has no ledger operation for {case_identity}")
    if matching_operations and not matching_receipts:
        raise CalibrationError(f"ledger operation has no prompt receipt for {case_identity}")
    if not matching_receipts:
        raise CalibrationError(f"pipeline call inventory is empty for {case_identity}")
    if len(matching_receipts) != len(matching_operations):
        raise CalibrationError(f"pipeline receipt and ledger counts differ for {case_identity}")
    operation_by_id = {
        row.get("logical_call_id"): row for row in matching_operations
    }
    if len(operation_by_id) != len(matching_operations):
        raise CalibrationError(f"pipeline ledger repeats a logical call for {case_identity}")
    expected_sequences = list(range(1, len(matching_receipts) + 1))
    observed_sequences = [row.get("call_sequence") for row in matching_receipts]
    if observed_sequences != expected_sequences:
        raise CalibrationError(f"pipeline logical call sequence is not contiguous for {case_identity}")
    inventory: list[dict[str, Any]] = []
    for sequence, receipt in enumerate(matching_receipts, 1):
        stage = receipt.get("stage")
        logical_call_id = receipt.get("logical_call_id")
        expected_logical_call_id = f"{case_identity}:{sequence}:{stage}"
        if logical_call_id != expected_logical_call_id:
            raise CalibrationError(f"pipeline logical call identity differs for {case_identity}")
        operation = operation_by_id.get(logical_call_id)
        if operation is None:
            raise CalibrationError(f"prompt receipt has no ledger operation: {logical_call_id}")
        if (
            operation.get("status") != "completed"
            or operation.get("case_id") != case_identity
            or operation.get("stage") != stage
        ):
            raise CalibrationError(f"pipeline ledger operation differs for {logical_call_id}")
        request_hash = receipt.get("request_hash")
        if request_hash != operation.get("request_hash"):
            raise CalibrationError(f"prompt receipt request hash differs for {logical_call_id}")
        inventory.append({
            "sequence": sequence,
            "stage": stage,
            "logical_call_id": logical_call_id,
            "request_hash": request_hash,
            "prompt_receipt_sha256": value_sha256(receipt),
            "cost_ledger_operation_sha256": value_sha256(operation),
        })
    if set(operation_by_id) != {row["logical_call_id"] for row in inventory}:
        raise CalibrationError(f"ledger operation has no prompt receipt for {case_identity}")
    model_stage_sequence = [row["stage"] for row in inventory]
    if not model_stage_sequence or model_stage_sequence[0] != "proposer":
        raise CalibrationError(f"pipeline first model stage is not proposer for {case_identity}")
    audit_stages = audit_model_stage_sequence(audit)
    if audit_stages != model_stage_sequence:
        raise CalibrationError(f"pipeline audit and receipt stages differ for {case_identity}")
    logical_call_ids = [row["logical_call_id"] for row in inventory]
    return {
        **binding,
        "model_call_count": len(inventory),
        "logical_call_ids": logical_call_ids,
        "model_stage_sequence": model_stage_sequence,
        "call_inventory": inventory,
        "call_inventory_sha256": value_sha256(inventory),
        "pipeline_audit_sha256": value_sha256(audit),
    }


def verify_pipeline_record_pair(
    identity: dict[str, Any],
    result: dict[str, Any],
    audit_row: dict[str, Any],
    *,
    receipts: dict[str, dict[str, Any]],
    ledger_data: dict[str, Any],
) -> set[str]:
    """Verify one stored result/audit pair and return its owned logical calls."""
    audit = audit_row.get("audit")
    if not isinstance(audit, list):
        raise CalibrationError("pipeline audit differs from its execution contract")
    inventory = collect_call_inventory(
        identity, audit=audit, receipts=receipts, ledger_data=ledger_data
    )
    common_fields = (
        "execution_identity_sha256",
        "case_identity",
        "model_call_count",
        "logical_call_ids",
        "model_stage_sequence",
        "call_inventory",
        "call_inventory_sha256",
        "pipeline_audit_sha256",
    )
    for name in common_fields:
        if result.get(name) != inventory[name] or audit_row.get(name) != inventory[name]:
            raise CalibrationError(f"pipeline result/audit binding differs for {inventory['case_identity']}")
    if result.get("model_call_count") != len(result.get("logical_call_ids", [])):
        raise CalibrationError(f"pipeline model call count differs for {inventory['case_identity']}")
    if result.get("pipeline_audit_sha256") != value_sha256(audit):
        raise CalibrationError(f"pipeline audit SHA differs for {inventory['case_identity']}")
    explicit_outcome(result.get("status"), result.get("public_reply"))
    return set(inventory["logical_call_ids"])


def verify_execution_journals(
    *,
    plan: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    pack_sha256: str,
    model: str,
    results: dict[tuple[str, str], dict[str, Any]],
    audits: dict[tuple[str, str], dict[str, Any]],
    receipts: dict[str, dict[str, Any]],
    ledger_data: dict[str, Any],
) -> None:
    """Require every planned execution to be fully bound with no orphan call."""
    planned_pairs = {
        (row["candidate_id"], row["variant"]) for row in plan["executions"]
    }
    expected_count = plan.get("planned_pipeline_executions")
    if (
        type(expected_count) is not int
        or expected_count not in {12, 84}
        or len(planned_pairs) != expected_count
        or set(results) != planned_pairs
        or set(audits) != planned_pairs
    ):
        raise CalibrationError(
            "final output does not contain every unique planned pipeline execution"
        )
    owned: set[str] = set()
    for planned in plan["executions"]:
        key = (planned["candidate_id"], planned["variant"])
        identity = pipeline_execution_identity(
            cases[planned["candidate_id"]],
            planned["variant"],
            manifests,
            pack_sha256,
            model,
        )
        calls = verify_pipeline_record_pair(
            identity,
            results[key],
            audits[key],
            receipts=receipts,
            ledger_data=ledger_data,
        )
        if owned & calls:
            raise CalibrationError("one logical call belongs to two pipeline results")
        owned.update(calls)
    ledger_calls = {
        str(row.get("logical_call_id")) for row in ledger_data.get("operations", [])
    }
    if owned != set(receipts) or owned != ledger_calls:
        raise CalibrationError("orphan prompt receipt or ledger operation exists")


class PromptReceiptTransport:
    """Record exact request identities while delegating safety and cost handling."""

    def __init__(
        self,
        delegate: pilot.PilotTransport,
        receipt_path: Path,
        *,
        model: str,
        existing_receipts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.delegate = delegate
        self.receipt_path = receipt_path
        self.model = model
        self.sequence = 0
        self.identity: dict[str, Any] = {}
        self.case_identity = ""
        self.execution_identity_sha256 = ""
        self.receipts = existing_receipts if existing_receipts is not None else {}

    def set_case(self, identity: dict[str, Any]) -> None:
        self.identity = dict(identity)
        binding = pipeline_execution_binding(identity)
        self.case_identity = binding["case_identity"]
        self.execution_identity_sha256 = binding["execution_identity_sha256"]
        self.sequence = 0
        self.delegate.set_case(self.case_identity)

    def __call__(self, **kwargs: Any) -> object:
        self.sequence += 1
        stage = kwargs["stage"]
        payload = {
            "model": kwargs["model"],
            "messages": [
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": kwargs["user_prompt"]},
            ],
            "temperature": 0,
            "max_tokens": kwargs["max_output_tokens"],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"ai_reply_{stage}",
                    "strict": True,
                    "schema": kwargs["response_schema"],
                },
            },
        }
        request_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        logical_call_id = f"{self.case_identity}:{self.sequence}:{stage}"
        receipt_base = {
            **self.identity,
            "execution_identity_sha256": self.execution_identity_sha256,
            "case_identity": self.case_identity,
            "stage": stage,
            "call_sequence": self.sequence,
            "logical_call_id": logical_call_id,
            "system_prompt_sha256": sha256_text(kwargs["system_prompt"]),
            "user_prompt_sha256": sha256_text(kwargs["user_prompt"]),
            "request_hash": request_hash,
            "temperature": 0,
            "media_transmitted": False,
        }
        existing = self.receipts.get(logical_call_id)
        if existing is not None:
            if any(existing.get(key) != value for key, value in receipt_base.items()):
                raise CalibrationError(f"prompt receipt differs for {logical_call_id}")
            if existing.get("transport_status") not in {
                "prepared_for_transport",
                "returned_from_completed_cache",
                "transmitted_and_completed",
            }:
                raise CalibrationError(f"prompt receipt status is invalid for {logical_call_id}")
        prior = self.delegate.ledger.operation(logical_call_id, request_hash)
        try:
            response = self.delegate(**kwargs)
        except BaseException:
            if existing is None:
                receipt = {**receipt_base, "transport_status": "prepared_for_transport"}
                append_jsonl(self.receipt_path, receipt)
                self.receipts[logical_call_id] = receipt
            raise
        if existing is None:
            receipt = {
                **receipt_base,
                "transport_status": (
                    "returned_from_completed_cache"
                    if prior is not None and prior.get("status") == "completed"
                    else "transmitted_and_completed"
                ),
            }
            append_jsonl(self.receipt_path, receipt)
            self.receipts[logical_call_id] = receipt
        return response


def execute_outcomes(
    pack_data: dict[str, Any],
    result_rows: dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build the blinded current, compact and historical outcome mapping."""
    outcomes = {
        key: explicit_outcome(row.get("status"), row.get("public_reply"))
        for key, row in result_rows.items()
    }
    for case in pack_data["cases"]:
        historical = _historical_public_output(case["historical"])
        outcomes[(case["candidate_id"], "historical")] = explicit_outcome(
            "approved" if historical is not None else "no_reply", historical
        )
    return outcomes


def execute_manifest(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    provenance: dict[str, Any],
    *,
    plan: dict[str, Any],
    context_artifacts: dict[str, Any] | None,
    clearance: dict[str, Any] | None,
    output: Path,
    identity_sha256: str,
    ledger_data: dict[str, Any],
    result_rows: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Derive the final execute manifest solely from durable inputs."""
    approved_count = sum(row["status"] == "approved" for row in result_rows.values())
    no_reply_count = sum(row["status"] == "no_reply" for row in result_rows.values())
    model_calls = sum(int(row["model_call_count"]) for row in result_rows.values())
    total_http_requests = 1 + sum(
        int(row.get("attempt_number") or 1) for row in ledger_data["operations"]
    )
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "mode": "execute",
        "model": args.model,
        "xai_base": args.xai_base,
        "blind_seed": args.blind_seed,
        "pack_sha256": pack_data["pack_sha256"],
        **selection_manifest_fields(
            pack_data,
            plan,
            context_artifacts=context_artifacts,
            clearance=clearance,
        ),
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        **provenance,
        "provider_phase_identity_sha256": file_sha256(
            output / "provider_phase_identity.json"
        ),
        "provider_model_metadata_sha256": file_sha256(
            output / "provider_model_metadata.json"
        ),
        "run_identity_sha256": identity_sha256,
        "cost_ledger_status": ledger_data.get("status"),
        "known_cost_usd": ledger_data.get("known_cost_usd", 0.0),
        "ambiguous_exposure_usd": ledger_data.get("ambiguous_exposure_usd", 0.0),
        "completed_pipeline_executions": plan["planned_pipeline_executions"],
        "valid_approved_count": approved_count,
        "valid_no_reply_count": no_reply_count,
        "operational_failure_count": 0,
        "model_calls_performed": model_calls,
        "http_requests_performed": total_http_requests,
        "hard_limit_usd": args.hard_limit_usd,
        "posting_enabled": False,
        "search_enabled": False,
        "tools_enabled": False,
        "media_enabled": False,
    }


def execute_derived_payloads(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    provenance: dict[str, Any],
    *,
    plan: dict[str, Any],
    context_artifacts: dict[str, Any] | None,
    clearance: dict[str, Any] | None,
    output: Path,
    identity_sha256: str,
    ledger_data: dict[str, Any],
    result_rows: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, bytes]:
    """Return deterministic bytes for every derived execute file except the marker."""
    outcomes = execute_outcomes(pack_data, result_rows)
    assignments = blind_assignments(
        pack_data["cases"],
        blind_seed=args.blind_seed,
        pack_sha256=pack_data["pack_sha256"],
    )
    markdown, csv_text = build_blind_review(
        pack_data["cases"], assignments, outcomes
    )
    manifest = execute_manifest(
        args,
        pack_data,
        provenance,
        plan=plan,
        context_artifacts=context_artifacts,
        clearance=clearance,
        output=output,
        identity_sha256=identity_sha256,
        ledger_data=ledger_data,
        result_rows=result_rows,
    )
    model_calls = int(manifest["model_calls_performed"])
    http_requests = int(manifest["http_requests_performed"])

    def json_bytes(value: Any) -> bytes:
        return (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            + b"\n"
        )

    return {
        "blind_review.md": markdown.encode("utf-8"),
        "blind_review.csv": csv_text.encode("utf-8"),
        "blind_key.json": json_bytes({
            "schema_version": 1,
            "derivation": "sha256(blind-seed, pack-sha256, candidate-id, source)",
            "assignments": assignments,
        }),
        "calibration_report.md": calibration_report_markdown(
            "execute",
            model_calls,
            http_requests,
            case_set=pack_data["case_set"],
            selected_case_count=pack_data["selected_case_count"],
            planned_pipeline_executions=plan["planned_pipeline_executions"],
        ).encode("utf-8"),
        "run_manifest.json": json_bytes(manifest),
    }


def verify_durable_execute_core(output: Path, *, case_set: str) -> None:
    """Require every never-discardable execute artefact before final publication."""
    required = set(DURABLE_CORE_FILES)
    if case_set == "holdout":
        required.update(HOLDOUT_CONTEXT_FILES)
    missing = sorted(name for name in required if not (output / name).is_file())
    if missing or not (output / "raw_responses").is_dir():
        raise CalibrationError(
            "execute durable core is incomplete: " + ", ".join(missing or ["raw_responses/"])
        )
    failures = output / "execution_failures.jsonl"
    if failures.exists() and read_jsonl(failures):
        raise CalibrationError("execute durable core contains an operational failure")


def recover_or_verify_finalisation(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
    *,
    context_artifacts: dict[str, Any] | None,
    clearance: dict[str, Any] | None,
    output: Path,
    identity_sha256: str,
    ledger_data: dict[str, Any],
    receipts: dict[str, dict[str, Any]],
    result_rows: dict[tuple[str, str], dict[str, Any]],
    audit_rows: dict[tuple[str, str], dict[str, Any]],
    api_key: str,
) -> Path:
    """Strictly verify a marker, or rebuild only derived files when it is absent."""
    verify_durable_execute_core(output, case_set=pack_data["case_set"])
    plan = read_json(output / "execution_plan.json")
    verify_execution_journals(
        plan=plan,
        cases=cases,
        manifests=manifests,
        pack_sha256=pack_data["pack_sha256"],
        model=args.model,
        results=result_rows,
        audits=audit_rows,
        receipts=receipts,
        ledger_data=ledger_data,
    )
    expected = execute_derived_payloads(
        args,
        pack_data,
        provenance,
        plan=plan,
        context_artifacts=context_artifacts,
        clearance=clearance,
        output=output,
        identity_sha256=identity_sha256,
        ledger_data=ledger_data,
        result_rows=result_rows,
    )
    marker = output / "SHA256SUMS"
    if marker.exists():
        missing = sorted(name for name in FINAL_OUTPUT_FILES if not (output / name).is_file())
        if missing:
            raise CalibrationError(
                "finalised execute output is missing derived files: " + ", ".join(missing)
            )
        verify_output_sha256sums(output)
        for name, payload in expected.items():
            if (output / name).read_bytes() != payload:
                raise CalibrationError(f"finalised execute derived file differs: {name}")
        return output
    for name in DISPOSABLE_DERIVED_FILES:
        path = output / name
        if path.exists():
            path.unlink()
    for name in DISPOSABLE_DERIVED_FILES[:-1]:
        atomic_bytes(output / name, expected[name])
    enforce_private_permissions(output)
    assert_no_secret(output, api_key)
    write_sha256sums(output)
    enforce_private_permissions(output)
    verify_output_sha256sums(output)
    return output


def execute_run(
    args: argparse.Namespace,
    pack_data: dict[str, Any],
    api_key: str,
    *,
    context_artifacts: dict[str, Any] | None = None,
    clearance: dict[str, Any] | None = None,
) -> Path:
    verify_current_production_objects()
    manifests = profile_manifests()
    verify_frozen_profile_manifests(manifests)
    if pack_data["case_set"] == "holdout":
        if (
            context_artifacts is None
            or clearance is None
            or clearance.get("status") != "ready"
            or clearance.get("paid_execution_ready") is not True
        ):
            raise CalibrationError("holdout execute requires exact all-ready clearance")
    elif context_artifacts is not None or clearance is not None:
        raise CalibrationError("calibration execute cannot use holdout context clearance")
    repository = build_repository()
    plan = build_execution_plan(pack_data, manifests, args.blind_seed)
    cases = {case["candidate_id"]: case for case in pack_data["cases"]}
    provenance = common_execution_provenance(
        plan=plan,
        manifests=manifests,
        repository=repository,
        source_provenance=execution_provenance(
            args.expected_runner_git_commit, require_clean_checkout=True
        ),
    )
    identity = build_run_identity(
        args,
        pack_data,
        manifests,
        plan,
        provenance,
        context_artifacts=context_artifacts,
        clearance=clearance,
    )
    output = prepare_output_directory(
        args.output, pack_data["pack_path"], resume=args.resume
    )
    http = CountingHTTP()
    if args.resume:
        identity_path = output / "run_identity.json"
        if not identity_path.is_file():
            raise CalibrationError("resume output was not produced by this runner")
        stored_identity = read_json(identity_path)
        if stored_identity != identity:
            raise CalibrationError("run identity differs from current arguments or sources")
        if read_json(output / "pack_verification.json") != public_pack_verification(pack_data):
            raise CalibrationError("stored replay-pack verification differs")
        if read_json(output / "profile_manifests.json") != manifests:
            raise CalibrationError("stored profile manifests differ")
        if read_json(output / "execution_plan.json") != plan:
            raise CalibrationError("stored execution plan differs")
        if pack_data["case_set"] == "holdout":
            assert context_artifacts is not None and clearance is not None
            expected_context_files = {
                "holdout_context_audit.json": context_artifacts["audit_bytes"],
                "holdout_context_review.md": context_artifacts["review_text"].encode(
                    "utf-8"
                ),
                "holdout_context_clearance.csv": clearance["normalized_text"].encode(
                    "utf-8"
                ),
                "holdout_context_recovery.jsonl": context_artifacts[
                    "recovery_bytes"
                ],
            }
            for name, expected_bytes in expected_context_files.items():
                path = output / name
                if not path.is_file() or path.read_bytes() != expected_bytes:
                    raise CalibrationError(f"stored holdout context artifact differs: {name}")
        identity_sha256 = file_sha256(identity_path)
        ledger = open_resume_ledger(
            output, model=args.model, hard_limit_usd=args.hard_limit_usd
        )
        metadata, _provider_phase = resume_or_publish_provider_phase(
            output,
            ledger_data=ledger.data,
            identity_sha256=identity_sha256,
            model=args.model,
            xai_endpoint=args.xai_base,
            api_key=api_key,
            get=http.get,
        )
    else:
        write_json(output / "pack_verification.json", public_pack_verification(pack_data))
        write_json(output / "profile_manifests.json", manifests)
        write_json(output / "execution_plan.json", plan)
        if pack_data["case_set"] == "holdout":
            assert context_artifacts is not None and clearance is not None
            atomic_bytes(
                output / "holdout_context_audit.json",
                context_artifacts["audit_bytes"],
            )
            write_text(
                output / "holdout_context_review.md",
                context_artifacts["review_text"],
            )
            write_text(
                output / "holdout_context_clearance.csv",
                clearance["normalized_text"],
            )
            atomic_bytes(
                output / "holdout_context_recovery.jsonl",
                context_artifacts["recovery_bytes"],
            )
        write_text(output / "prompt_receipts.jsonl", "")
        write_text(output / "pipeline_results.jsonl", "")
        write_text(output / "pipeline_audits.jsonl", "")
        raw_responses = output / "raw_responses"
        raw_responses.mkdir(mode=0o700)
        write_json(output / "run_identity.json", identity)
        identity_sha256 = file_sha256(output / "run_identity.json")
        ledger = pilot.PilotLedger(
            output / "cost_ledger.json",
            model=args.model,
            hard_limit_usd=args.hard_limit_usd,
            run_version=RUNNER_VERSION,
        )
        metadata, _provider_phase = publish_provider_phase(
            output,
            identity_sha256=identity_sha256,
            model=args.model,
            xai_endpoint=args.xai_base,
            api_key=api_key,
            get=http.get,
        )

    metadata, _provider_phase = read_and_verify_provider_phase(
        output,
        identity_sha256=identity_sha256,
        model=args.model,
        xai_endpoint=args.xai_base,
    )
    ledger_data = validate_resume_ledger(
        ledger.data, model=args.model, hard_limit_usd=args.hard_limit_usd
    )
    verify_completed_response_caches(output, ledger_data)
    receipts = index_prompt_receipts(output / "prompt_receipts.jsonl")
    verify_prompt_receipt_contracts(
        receipts,
        cases=cases,
        manifests=manifests,
        pack_sha256=pack_data["pack_sha256"],
        model=args.model,
    )
    verify_receipts_against_ledger(
        receipts, ledger_data, require_complete_inventory=False
    )
    if (output / "execution_failures.jsonl").exists() and read_jsonl(
        output / "execution_failures.jsonl"
    ):
        raise CalibrationError("resume output contains a prior operational failure")

    planned_pairs = {
        (row["candidate_id"], row["variant"]) for row in plan["executions"]
    }
    result_rows = index_execution_records(
        output / "pipeline_results.jsonl", label="pipeline results"
    )
    audit_rows = index_execution_records(
        output / "pipeline_audits.jsonl", label="pipeline audits"
    )
    if (set(result_rows) | set(audit_rows)) - planned_pairs:
        raise CalibrationError("existing execution journal contains an unplanned identity")
    derived_present = any((output / name).exists() for name in FINAL_OUTPUT_FILES)
    journals_complete = set(result_rows) == planned_pairs and set(audit_rows) == planned_pairs
    if derived_present or journals_complete:
        return recover_or_verify_finalisation(
            args,
            pack_data,
            manifests,
            cases,
            provenance,
            context_artifacts=context_artifacts,
            clearance=clearance,
            output=output,
            identity_sha256=identity_sha256,
            ledger_data=ledger_data,
            receipts=receipts,
            result_rows=result_rows,
            audit_rows=audit_rows,
            api_key=api_key,
        )

    raw_responses = output / "raw_responses"
    delegate = pilot.PilotTransport(
        api_key=api_key,
        base_url=args.xai_base,
        model_metadata=metadata,
        ledger=ledger,
        response_dir=raw_responses,
        post=http.post,
        maximum_rate_limit_retries=args.maximum_rate_limit_retries,
        maximum_server_error_retries=args.maximum_server_error_retries,
    )
    receipt_transport = PromptReceiptTransport(
        delegate,
        output / "prompt_receipts.jsonl",
        model=args.model,
        existing_receipts=receipts,
    )
    config = pilot.strategy_config(
        args.model, PROJECT_ROOT / "semantic_alignment_research/quote_research_full_001"
    )
    for planned in plan["executions"]:
        case = cases[planned["candidate_id"]]
        variant = planned["variant"]
        key = (case["candidate_id"], variant)
        prior_result = result_rows.get(key)
        prior_audit = audit_rows.get(key)
        if prior_result is not None:
            if prior_result.get("stratum") != case["stratum"]:
                raise CalibrationError(f"pipeline result stratum differs for {key}")
        if prior_audit is not None and (
            prior_audit.get("stratum") != case["stratum"]
            or not isinstance(prior_audit.get("audit"), list)
        ):
            raise CalibrationError(f"pipeline audit differs for {key}")
        if prior_result is not None and prior_audit is not None:
            execution_identity = pipeline_execution_identity(
                case, variant, manifests, pack_data["pack_sha256"], args.model
            )
            verify_pipeline_record_pair(
                execution_identity,
                prior_result,
                prior_audit,
                receipts=receipts,
                ledger_data=ledger.data,
            )
            continue
        execution_identity = pipeline_execution_identity(
            case, variant, manifests, pack_data["pack_sha256"], args.model
        )
        receipt_transport.set_case(execution_identity)
        with activate_profile(variant, recent_account_replies=case["recent_replies"]):
            result = reply_strategy.run_reply_pipeline(
                context=case["context"],
                config=config,
                repository=repository,
                transport=receipt_transport,
                maximum_reply_length=MAXIMUM_REPLY_LENGTH,
                recent_replies=case["recent_replies"],
                media_context=None,
            )
        if result.status not in VALID_PIPELINE_STATUSES:
            append_jsonl(output / "execution_failures.jsonl", {
                "candidate_id": case["candidate_id"],
                "stratum": case["stratum"],
                "variant": variant,
                "status": result.status,
                "reason": result.reason,
                "model_call_count": result.model_call_count,
                "revision_count": result.revision_count,
                "audit": list(result.audit),
            })
            raise CalibrationError(
                f"pipeline execution ended in non-calibration status {result.status}"
            )
        audit = list(result.audit)
        call_binding = collect_call_inventory(
            execution_identity,
            audit=audit,
            receipts=receipts,
            ledger_data=ledger.data,
        )
        if result.model_call_count != call_binding["model_call_count"]:
            raise CalibrationError(f"pipeline model call count differs for {key}")
        public_reply = str(result.reply) if result.reply is not None else None
        explicit_outcome(result.status, public_reply)
        expected_result = {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "status": result.status,
            "reason": result.reason,
            "public_reply": public_reply,
            "model_call_count": result.model_call_count,
            "revision_count": result.revision_count,
            "pipeline_metadata": (
                getattr(result.reply, "pipeline_metadata", None)
                if result.reply is not None else None
            ),
            **call_binding,
        }
        expected_audit = {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "audit": audit,
            **call_binding,
        }
        if prior_result is not None and prior_result != expected_result:
            raise CalibrationError(f"pipeline result differs on reconstruction for {key}")
        if prior_audit is not None and prior_audit != expected_audit:
            raise CalibrationError(f"pipeline audit differs on reconstruction for {key}")
        if prior_result is None:
            append_jsonl(output / "pipeline_results.jsonl", expected_result)
            result_rows[key] = expected_result
        if prior_audit is None:
            append_jsonl(output / "pipeline_audits.jsonl", expected_audit)
            audit_rows[key] = expected_audit

    final_receipts = index_prompt_receipts(output / "prompt_receipts.jsonl")
    verify_prompt_receipt_contracts(
        final_receipts,
        cases=cases,
        manifests=manifests,
        pack_sha256=pack_data["pack_sha256"],
        model=args.model,
    )
    verify_receipts_against_ledger(
        final_receipts, ledger.data, require_complete_inventory=True
    )
    return recover_or_verify_finalisation(
        args,
        pack_data,
        manifests,
        cases,
        provenance,
        context_artifacts=context_artifacts,
        clearance=clearance,
        output=output,
        identity_sha256=identity_sha256,
        ledger_data=ledger.data,
        receipts=final_receipts,
        result_rows=result_rows,
        audit_rows=audit_rows,
        api_key=api_key,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or execute a blinded current-versus-compact reply calibration"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", dest="mode", action="store_const", const="validate-only")
    mode.add_argument("--execute", dest="mode", action="store_const", const="execute")
    parser.set_defaults(mode="validate-only")
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-set", choices=CASE_SETS, default="calibration")
    parser.add_argument("--context-clearance", type=Path)
    parser.add_argument("--history-corpus", type=Path)
    parser.add_argument(
        "--recover-context-candidate",
        action="append",
        default=[],
        metavar="CANDIDATE_ID",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--hard-limit-usd", type=float)
    parser.add_argument("--xai-base", default=DEFAULT_XAI_BASE)
    parser.add_argument("--blind-seed", default=DEFAULT_BLIND_SEED)
    parser.add_argument(
        "--maximum-rate-limit-retries", type=int, default=DEFAULT_MAXIMUM_RATE_LIMIT_RETRIES
    )
    parser.add_argument(
        "--maximum-server-error-retries", type=int, default=DEFAULT_MAXIMUM_SERVER_ERROR_RETRIES
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-runner-git-commit")
    parser.add_argument("--acknowledge-paid-model-calls")
    return parser


def validate_arguments(args: argparse.Namespace, environ: dict[str, str]) -> str | None:
    try:
        args.xai_base = pilot.validate_xai_base(args.xai_base)
    except pilot.PilotError as exc:
        raise CalibrationError(str(exc)) from exc
    if not isinstance(args.model, str) or not args.model.strip():
        raise CalibrationError("model must be non-empty")
    if not isinstance(args.blind_seed, str) or not args.blind_seed:
        raise CalibrationError("blind seed must be non-empty")
    if not 0 <= args.maximum_rate_limit_retries <= 8:
        raise CalibrationError("maximum rate-limit retries must be from zero to eight")
    if not 0 <= args.maximum_server_error_retries <= 4:
        raise CalibrationError("maximum server-error retries must be from zero to four")
    if args.resume and args.mode != "execute":
        raise CalibrationError("--resume is valid only with --execute")
    if args.context_clearance is not None and args.case_set != "holdout":
        raise CalibrationError(
            "--context-clearance is valid only with --case-set holdout"
        )
    if (
        args.history_corpus is not None or args.recover_context_candidate
    ) and args.case_set != "holdout":
        raise CalibrationError(
            "context-recovery arguments are valid only with --case-set holdout"
        )
    if args.recover_context_candidate and args.history_corpus is None:
        raise CalibrationError(
            "--recover-context-candidate requires --history-corpus"
        )
    if args.history_corpus is not None and output_inside(
        args.output.resolve(strict=False), args.history_corpus.resolve(strict=False)
    ):
        raise CalibrationError("output must not be inside the history corpus")
    if args.mode == "validate-only":
        return None
    if args.case_set == "holdout" and args.context_clearance is None:
        raise CalibrationError("holdout execute requires --context-clearance")
    if (
        not isinstance(args.expected_runner_git_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", args.expected_runner_git_commit) is None
    ):
        raise CalibrationError(
            "execute requires --expected-runner-git-commit as one exact 40-character SHA"
        )
    if args.acknowledge_paid_model_calls != PAID_ACKNOWLEDGEMENT:
        raise CalibrationError(
            f"execute requires --acknowledge-paid-model-calls {PAID_ACKNOWLEDGEMENT}"
        )
    if (
        args.hard_limit_usd is None
        or not math.isfinite(args.hard_limit_usd)
        or args.hard_limit_usd <= 0
    ):
        raise CalibrationError("execute requires a positive --hard-limit-usd")
    api_key = environ.get("XAI_API_KEY")
    if not api_key:
        raise CalibrationError("execute requires XAI_API_KEY from the process environment")
    return api_key


def run(args: argparse.Namespace, *, environ: dict[str, str] | None = None) -> Path:
    """Run one mode after all non-network preconditions have passed."""
    environment = os.environ if environ is None else environ
    api_key = validate_arguments(args, environment)
    for module_name in FORBIDDEN_RUNTIME_MODULES:
        if module_name in sys.modules:
            raise CalibrationError(f"forbidden posting/X module is loaded: {module_name}")
    pack_data = verify_replay_pack(args.pack, case_set=args.case_set)
    context_artifacts: dict[str, Any] | None = None
    clearance: dict[str, Any] | None = None
    if args.case_set == "holdout":
        recover_holdout_contexts(
            pack_data,
            args.history_corpus,
            args.recover_context_candidate,
        )
        context_artifacts = build_holdout_context_artifacts(pack_data)
        if args.context_clearance is None:
            template = context_artifacts["clearance_template_text"]
            clearance = {
                "status": "pending",
                "paid_execution_ready": False,
                "normalized_text": template,
                "normalized_sha256": hashlib.sha256(
                    template.encode("utf-8")
                ).hexdigest(),
            }
        else:
            clearance = validate_context_clearance(
                args.context_clearance,
                context_artifacts,
                require_all_ready=args.mode == "execute",
            )
    if args.mode == "validate-only":
        return validate_only_run(
            args,
            pack_data,
            context_artifacts=context_artifacts,
            clearance=clearance,
        )
    assert api_key is not None
    return execute_run(
        args,
        pack_data,
        api_key,
        context_artifacts=context_artifacts,
        clearance=clearance,
    )


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = run(args)
    except (CalibrationError, pilot.PilotError, OSError, ValueError) as exc:
        parser.exit(2, f"reply prompt calibration refused: {exc}\n")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
