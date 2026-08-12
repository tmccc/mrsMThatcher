#!/usr/bin/env python3
"""Validate and package profile-blind pre-output reply adjudications.

The two supported operations only prepare review material or finalise manually
entered proposed decisions.  This module has no response generator, model
profile execution, network client, posting path, sampling path, or key/seed
creation path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TOOL_VERSION = "reply-hybrid-preoutput-adjudication-v1"
SCHEMA_VERSION = 1
RESEARCH_ROOT = Path("/disks/disk1/research")
CORRECTED_INPUT_DIRECTORY = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-hybrid-evaluation-audit-data-20260812T144330Z"
)
PRINCIPAL_PRODUCTION_REPOSITORY = Path("/disks/disk1/etc/mrsMThatcher")
WORKTREE_NAME_PREFIX = "mrsMThatcher-reply-hybrid-preoutput-adjudication-"
BRANCH_PREFIX = "research/reply-hybrid-preoutput-adjudication-"
ACCEPTED_AUDIT_COMMIT = "f224a5f9b7a2ebd4d27c20b5133b3536cea36b3f"
ACCEPTED_AUDIT_BRANCH = "research/reply-hybrid-evaluation-audit-20260812T111858Z"
ANCESTRY = (
    "0b7cd1da11d7c9ff5b3051c3d6fc4a70ae45176d",
    "00083cc9f3501f1f4af037352d10d8e530ee18d5",
    ACCEPTED_AUDIT_COMMIT,
)
EXPECTED_INPUT_SHA256SUMS_SHA256 = (
    "f52684112d6edff957d6894c0c8ad6ecb52656ea0d5f6bd55acd2ff5e9c7b7fc"
)
EXPECTED_INPUT_HASHES = {
    "context_audit.jsonl":
        "aeb7ce0c54bfc9a9158b161fc502b717ca39e3dc041254491e881f466f93bf37",
    "context_audit_summary.json":
        "9b552b1062e238d165220c774c63360c381fa5ddd855aae0358d9f1f7e6cfb6e",
    "development_exclusion_registry.json":
        "4b3f3672c39e06d7c803e533ff208f3a268c041f67013037c058ea5580b619fc",
    "excluded_candidates.jsonl":
        "e60a1ce41d9b6db024bc1245db279f7a21922a9310a0569b67710160ce2558fa",
    "fresh_candidate_inventory.jsonl":
        "32b6807162dd02a12481b9a19c0ad3fd71e8a2b68065d787437cd6cdcb3c6de4",
    "freshness_boundary.json":
        "786192da342fc029c5008d46ce5a0361f9d1826dbff67ed2b814442d28ab0659",
    "manual_context_clearance.csv":
        "eb6006c26d34063edc073b44c543f52914d476687020ef0d1ba8dee3c7515710",
    "manual_context_review.md":
        "b9dbd114f00b2f4005fe224c3dff203a0885520621bf6751ee0bc29f1f552999",
    "manual_semantic_classification.csv":
        "8baae8753a1a1814ce54554bcffb894c5c2a4ceb934d832129a9c065fcff37e1",
    "manual_semantic_review.md":
        "7c2021f015e8bfe9b410999ef6ef5c9275865703ca5a42b1d43b0d1c41f9a1da",
    "no_cost_audit_report.md":
        "df974729732f9e8d52cca13863670621f7914fe95e7a0fb2ee0d5f1fe88da4dc",
    "profile_identity_verification.json":
        "91b834b767e543369fbd042ed1293578fd4c02e9f706bf605621d4a5d9766d7d",
    "run_manifest.json":
        "4abce54ab315b375b7f31a93e0f7e0f90f7b1776185cf13be09d1e61eb6265a5",
    "sampling_readiness.json":
        "94bb65468ee42f6583a73c6c0217a8a83e6f6dab938d1a72a71233b2d5d70a96",
    "source_inventory.json":
        "3adf4967e7f3f09e0a6699ff8dd34f2e75fefaa8538184a65f04c9560ab92dec",
    "strata_inventory.json":
        "7048b2d962c074e88975ee68bdda311ff7b09bcc412526496d901c81b45b5b8f",
}
ACCEPTED_SOURCE_HASHES = {
    "tools/build_reply_hybrid_evaluation_audit.py":
        "7d3453f46af1cd5004ba1e745306812c7fbc4409a9a02a6b4bca714951bf89f6",
    "tests/test_reply_hybrid_evaluation_audit.py":
        "7feb40546872e23b9b22933c03c99be732aea2ae831f31f0da5f6a3a65e8338d",
    "reply_hybrid_evaluation_preregistration.md":
        "3e61873f92288ae5a191ce7dc25c27ac3fcb200c468fd6823defcdeea3070863",
}
EXPECTED_CUTOFF = "2026-08-10T05:16:15Z"
ACCEPTED_AUDIT_FINISH_UTC = "2026-08-12T14:44:00Z"
EXPECTED_ELIGIBLE_COUNT = 36
EXPECTED_AUTOMATIC_COUNT = 14
EXPECTED_MANUAL_COUNT = 22

CONTEXT_DECISIONS = ("clear", "exclude", "requires_adjudication")
CONTEXT_REASON_CODES = {
    "clear": {
        "self_contained_despite_flag",
        "resolved_by_parent_thread",
        "resolved_by_quoted_post",
        "resolved_by_clarification_context",
        "resolved_by_combined_bounded_context",
    },
    "exclude": {
        "missing_material_parent_context",
        "missing_material_quoted_context",
        "unresolved_referent",
        "elliptical_or_symbol_only_unresolvable",
        "source_or_attribution_context_insufficient",
        "context_conflict_or_temporal_defect",
        "unsafe_or_nonoriginal_replay_context",
        "other_material_context_failure",
    },
    "requires_adjudication": {"genuinely_ambiguous_after_review"},
}
CONTEXT_REVIEWER = "codex-preoutput-context-review-v1"
SEMANTIC_REVIEWER = "codex-preoutput-semantic-review-v1"
ADJUDICATOR_KIND = "codex_language_model_case_by_case_review"
GLOBAL_REVIEW_STATUS = "proposed_pending_independent_review"

SEMANTIC_STATUSES = (
    "completed",
    "blocked_context_excluded",
    "requires_adjudication",
)
SEMANTIC_TAGS = (
    "genuine_social_courtesy",
    "substantive_agreement_with_reason_or_principle",
    "civil_challenge_criticism_or_disagreement",
    "analogy_distinction_or_recommendation",
    "substantive_political_or_moral_proposition",
    "direct_factual_or_historical_question",
    "safe_contribution_specific_wit_opportunity",
    "unsupported_allegation_or_sensitive_factual_correction_context",
    "justified_safety_no_reply",
    "other_safe_conversational_contribution",
)
PRIMARY_STRATA = (
    "genuine_social_courtesy",
    "substantive_argument_or_principle",
    "civil_challenge_or_disagreement",
    "factual_or_historical_question",
    "safe_wit_opportunity",
    "justified_safety_no_reply",
    "other_safe_conversational_contribution",
)
SEMANTIC_REASON_CODES = {
    "principal_act_social",
    "principal_act_substantive_agreement",
    "principal_act_civil_challenge",
    "principal_act_analogy_or_recommendation",
    "principal_act_political_or_moral_proposition",
    "principal_act_factual_question",
    "principal_act_wit_opportunity",
    "principal_act_safety_no_reply",
    "principal_act_other_safe",
    "mixed_act_primary_selected",
    "semantic_ambiguity_requires_adjudication",
    "blocked_by_context_exclusion",
}
PRIMARY_REQUIRED_TAGS = {
    "genuine_social_courtesy": {"genuine_social_courtesy"},
    "substantive_argument_or_principle": {
        "substantive_agreement_with_reason_or_principle",
        "analogy_distinction_or_recommendation",
        "substantive_political_or_moral_proposition",
    },
    "civil_challenge_or_disagreement": {
        "civil_challenge_criticism_or_disagreement"
    },
    "factual_or_historical_question": {"direct_factual_or_historical_question"},
    "safe_wit_opportunity": {"safe_contribution_specific_wit_opportunity"},
    "justified_safety_no_reply": {"justified_safety_no_reply"},
    "other_safe_conversational_contribution": {
        "other_safe_conversational_contribution"
    },
}
PRIMARY_REASON_CODES = {
    "genuine_social_courtesy": {"principal_act_social"},
    "substantive_argument_or_principle": {
        "principal_act_substantive_agreement",
        "principal_act_analogy_or_recommendation",
        "principal_act_political_or_moral_proposition",
    },
    "civil_challenge_or_disagreement": {"principal_act_civil_challenge"},
    "factual_or_historical_question": {"principal_act_factual_question"},
    "safe_wit_opportunity": {"principal_act_wit_opportunity"},
    "justified_safety_no_reply": {"principal_act_safety_no_reply"},
    "other_safe_conversational_contribution": {"principal_act_other_safe"},
}
REASON_REQUIRED_TAG = {
    "principal_act_social": "genuine_social_courtesy",
    "principal_act_substantive_agreement":
        "substantive_agreement_with_reason_or_principle",
    "principal_act_civil_challenge": "civil_challenge_criticism_or_disagreement",
    "principal_act_analogy_or_recommendation":
        "analogy_distinction_or_recommendation",
    "principal_act_political_or_moral_proposition":
        "substantive_political_or_moral_proposition",
    "principal_act_factual_question": "direct_factual_or_historical_question",
    "principal_act_wit_opportunity": "safe_contribution_specific_wit_opportunity",
    "principal_act_safety_no_reply": "justified_safety_no_reply",
    "principal_act_other_safe": "other_safe_conversational_contribution",
}
CONTEXT_FIELDS = (
    "candidate_id",
    "context_audit_record_sha256",
    "proposed_context_decision",
    "controlled_reason_code",
    "reviewer_note",
    "adjudicator_identity",
    "decision_time_utc",
    "receipt_sha256",
)
SEMANTIC_FIELDS = (
    "candidate_id",
    "source_record_fingerprint",
    "context_clearance_receipt_sha256",
    "context_audit_record_sha256",
    "semantic_adjudication_status",
    "accepted_semantic_tags_json",
    "accepted_primary_stratum",
    "genuine_social_courtesy_decision",
    "safe_wit_opportunity_decision",
    "justified_safety_no_reply_decision",
    "controlled_reason_code",
    "reviewer_note",
    "adjudicator_identity",
    "decision_time_utc",
    "receipt_sha256",
)
OUTPUT_FILES = (
    "input_verification.json",
    "context_adjudication_packet.md",
    "context_decisions.completed.csv",
    "context_clearance_receipts.jsonl",
    "semantic_adjudication_packet.md",
    "semantic_decisions.completed.csv",
    "semantic_classification_receipts.jsonl",
    "adjudication_conflicts.jsonl",
    "proposed_coverage_inventory.json",
    "protocol_findings.json",
    "sampling_readiness_after_adjudication.json",
    "adjudication_report.md",
    "run_manifest.json",
    "workflow_receipt.json",
    "review_attestation.json",
    "manual_context_review.md",
    "manual_context_clearance.original.csv",
    "manual_semantic_review.md",
    "manual_semantic_classification.original.csv",
)
ORIGINAL_COPIES = {
    "manual_context_review.md": "manual_context_review.md",
    "manual_context_clearance.csv": "manual_context_clearance.original.csv",
    "manual_semantic_review.md": "manual_semantic_review.md",
    "manual_semantic_classification.csv":
        "manual_semantic_classification.original.csv",
}
CONTEXT_STAGE_FILES = frozenset({
    "input_verification.json",
    "context_adjudication_packet.md",
    "context_decisions.completed.csv",
    "adjudication_conflicts.jsonl",
    "workflow_receipt.json",
    "review_attestation.json",
    *ORIGINAL_COPIES.values(),
})
SEMANTIC_STAGE_FILES = frozenset({
    *CONTEXT_STAGE_FILES,
    "context_clearance_receipts.jsonl",
    "semantic_adjudication_packet.md",
    "semantic_decisions.completed.csv",
})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
FORBIDDEN_NOTE_PATTERNS = (
    "current profile",
    "hybrid profile",
    "profile comparison",
    "historical outcome",
    "historical public reply",
    "generated response",
    "expected winner",
    "old score",
)


class AdjudicationError(RuntimeError):
    """A fail-closed pre-output adjudication validation failed."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value as compact deterministic UTF-8 JSON."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def receipt_sha256(receipt: dict[str, Any]) -> str:
    """Hash a receipt canonically after excluding its self-hash field."""
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one regular file."""
    if path.is_symlink() or not path.is_file():
        raise AdjudicationError(f"checksum target is not a regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_text() -> str:
    """Return the current UTC time in canonical whole-second form."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_utc(value: str) -> datetime:
    """Parse and strictly validate one canonical UTC timestamp."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise AdjudicationError(f"timestamp is not canonical UTC: {value!r}")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise AdjudicationError(f"timestamp is not UTC: {value!r}")
    return parsed


def _validate_decision_time(
    value: str, candidate_id: str, minimum: str, maximum: str
) -> None:
    parsed = parse_utc(value)
    if parsed < parse_utc(minimum) or parsed > parse_utc(maximum):
        raise AdjudicationError(
            f"decision timestamp falls outside the review window: {candidate_id}"
        )


def _json_document(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdjudicationError(f"expected JSON object: {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise AdjudicationError(f"blank JSONL line in {path.name}:{number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise AdjudicationError(f"non-object JSONL row in {path.name}:{number}")
        rows.append(value)
    return rows


def _read_csv(path: Path, expected_fields: Sequence[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(expected_fields):
            raise AdjudicationError(
                f"wrong CSV header in {path.name}: {reader.fieldnames!r}"
            )
        rows = list(reader)
    if any(None in row for row in rows):
        raise AdjudicationError(f"malformed extra CSV columns in {path.name}")
    return rows


def _write_private(path: Path, data: str | bytes) -> None:
    payload = data.encode("utf-8") if isinstance(data, str) else data
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AdjudicationError(f"refusing unsafe output path: {path.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _write_json(path: Path, value: Any) -> None:
    _write_private(path, _json_document(value))


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_private(
        path,
        b"".join(canonical_json_bytes(row) + b"\n" for row in rows),
    )


def _render_csv(rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return output.getvalue()


def verify_checksum_manifest(
    directory: Path,
    expected_manifest_hash: str,
    expected_hashes: dict[str, str],
) -> dict[str, str]:
    """Verify an exact complete checksum manifest and every listed file."""
    directory = directory.resolve(strict=True)
    manifest_path = directory / "SHA256SUMS"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise AdjudicationError("SHA256SUMS must be a regular non-symlink file")
    if file_sha256(manifest_path) != expected_manifest_hash:
        raise AdjudicationError("input SHA256SUMS digest differs from the accepted digest")
    observed: dict[str, str] = {}
    for number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\x00]+)", line)
        if not match:
            raise AdjudicationError(f"malformed SHA256SUMS line {number}")
        digest, name = match.groups()
        if name in observed:
            raise AdjudicationError(f"duplicate SHA256SUMS member: {name}")
        observed[name] = digest
    if observed != expected_hashes:
        raise AdjudicationError("complete input checksum inventory differs")
    actual_names = {
        path.name for path in directory.iterdir() if path.name != "SHA256SUMS"
    }
    if actual_names != set(expected_hashes):
        raise AdjudicationError("input directory member inventory differs")
    for name, expected in expected_hashes.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise AdjudicationError(f"input member is not a regular file: {name}")
        if file_sha256(path) != expected:
            raise AdjudicationError(f"input member digest differs: {name}")
    return dict(sorted(observed.items()))


def write_sha256sums(directory: Path) -> dict[str, str]:
    """Write checksums for every output payload and verify them independently."""
    sums_path = directory / "SHA256SUMS"
    if sums_path.exists():
        raise AdjudicationError("refusing to replace an existing SHA256SUMS")
    members = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    sums = {path.name: file_sha256(path) for path in members}
    _write_private(
        sums_path,
        "".join(f"{digest}  {name}\n" for name, digest in sums.items()),
    )
    verify_sha256sums(directory)
    return sums


def verify_sha256sums(directory: Path) -> dict[str, str]:
    """Independently parse and verify an output SHA256SUMS file."""
    rows: dict[str, str] = {}
    for number, line in enumerate(
        (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\x00]+)", line)
        if not match:
            raise AdjudicationError(f"malformed output checksum line {number}")
        digest, name = match.groups()
        if name in rows:
            raise AdjudicationError(f"duplicate output checksum member: {name}")
        path = directory / name
        if path.is_symlink() or not path.is_file() or file_sha256(path) != digest:
            raise AdjudicationError(f"output checksum verification failed: {name}")
        rows[name] = digest
    members = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in members):
        raise AdjudicationError("output checksum directory contains a non-regular member")
    actual = {path.name for path in members if path.name != "SHA256SUMS"}
    if set(rows) != actual:
        raise AdjudicationError("output checksum inventory is incomplete")
    return rows


def _git(
    repository: Path, *arguments: str, text: bool = True
) -> str | bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=repository, check=True, text=text,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
    )
    return completed.stdout.strip() if text else completed.stdout


def _verify_repository(repository: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    if repository == PRINCIPAL_PRODUCTION_REPOSITORY.resolve(strict=True):
        raise AdjudicationError("principal production repository is not an adjudication worktree")
    if repository.parent != RESEARCH_ROOT.resolve(strict=True) or not repository.name.startswith(
        WORKTREE_NAME_PREFIX
    ):
        raise AdjudicationError("repository is not the required persistent research worktree")
    head = str(_git(repository, "rev-parse", "HEAD"))
    branch = str(_git(repository, "branch", "--show-current"))
    if not branch.startswith(BRANCH_PREFIX):
        raise AdjudicationError("repository branch has the wrong pre-output prefix")
    for parent, child in zip(ANCESTRY, ANCESTRY[1:]):
        actual_parent = str(_git(repository, "rev-parse", f"{child}^"))
        if actual_parent != parent:
            raise AdjudicationError("required accepted audit ancestry is not direct")
    try:
        _git(repository, "merge-base", "--is-ancestor", ACCEPTED_AUDIT_COMMIT, head)
    except subprocess.CalledProcessError as exc:
        raise AdjudicationError("current HEAD does not descend from accepted audit") from exc
    source_hashes: dict[str, str] = {}
    for path, expected in ACCEPTED_SOURCE_HASHES.items():
        blob = _git(
            repository, "show", f"{ACCEPTED_AUDIT_COMMIT}:{path}", text=False
        )
        if not isinstance(blob, bytes):
            raise AdjudicationError("accepted source blob was not read as bytes")
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            raise AdjudicationError(f"accepted source identity differs: {path}")
        if _git(repository, "diff", "--name-only", ACCEPTED_AUDIT_COMMIT, "--", path):
            raise AdjudicationError(f"accepted audit source was modified: {path}")
        source_hashes[path] = actual
    return {
        "head": head,
        "branch": branch,
        "status_short": str(_git(repository, "status", "--short")).splitlines(),
        "accepted_source_hashes": source_hashes,
        "ancestry": list(ANCESTRY),
    }


def _unique_by_id(rows: Sequence[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise AdjudicationError(f"{label} has missing candidate ID")
        if candidate_id in result:
            raise AdjudicationError(f"{label} repeats candidate ID: {candidate_id}")
        result[candidate_id] = row
    return result


def _canonical_record_hash(row: dict[str, Any], field: str) -> str:
    payload = {key: value for key, value in row.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _verify_input_controls(input_directory: Path) -> dict[str, Any]:
    manifest = _read_json(input_directory / "run_manifest.json")
    summary = _read_json(input_directory / "context_audit_summary.json")
    boundary = _read_json(input_directory / "freshness_boundary.json")
    readiness = _read_json(input_directory / "sampling_readiness.json")
    source = _read_json(input_directory / "source_inventory.json")
    strata = _read_json(input_directory / "strata_inventory.json")
    profile = _read_json(input_directory / "profile_identity_verification.json")
    expected_manifest = {
        "commit": ACCEPTED_AUDIT_COMMIT,
        "branch": ACCEPTED_AUDIT_BRANCH,
        "base_hybrid_commit": ANCESTRY[0],
        "working_tree_clean": True,
        "working_tree_status_short": [],
        "automatic_context_clearance_count": EXPECTED_AUTOMATIC_COUNT,
        "manual_context_review_count": EXPECTED_MANUAL_COUNT,
        "manual_semantic_decisions_pending": EXPECTED_ELIGIBLE_COUNT,
        "semantic_coverage_status": "unadjudicated",
        "model_calls": 0,
        "provider_http_requests": 0,
        "posting_actions": 0,
        "production_state_changes": 0,
        "live_project_included": False,
        "response_generation_authorised": False,
        "final_sample_selected": False,
        "execution_seed_created": False,
        "blind_key_created": False,
        "fresh_candidates_removed_for_text_duplication": 0,
        "fresh_exact_text_cluster_count": 1,
        "fresh_exact_text_clustered_candidate_count": 5,
    }
    for key, expected in expected_manifest.items():
        if manifest.get(key) != expected:
            raise AdjudicationError(f"input manifest control differs: {key}")
    if manifest.get("candidate_counts", {}).get("eligible_candidates") != 36:
        raise AdjudicationError("input manifest eligible count differs")
    summary_expectations = {
        "automatic_context_clearance_count": 14,
        "manual_context_review_count": 22,
        "manual_context_decisions_pending": 22,
        "manual_semantic_decisions_pending": 36,
        "semantic_coverage_status": "unadjudicated",
        "fresh_exact_text_cluster_count": 1,
        "fresh_exact_text_clustered_candidate_count": 5,
        "fresh_candidates_removed_for_text_duplication": 0,
        "future_parent_or_quote_violation_count": 0,
        "historical_reply_self_leak_count": 0,
    }
    for key, expected in summary_expectations.items():
        if summary.get(key) != expected:
            raise AdjudicationError(f"context summary control differs: {key}")
    if summary.get("candidate_counts", {}).get("eligible_candidates") != 36:
        raise AdjudicationError("context summary eligible count differs")
    if boundary.get("development_cutoff") != EXPECTED_CUTOFF:
        raise AdjudicationError("development cutoff differs")
    if source.get("retained_eligible_count") != 36:
        raise AdjudicationError("source retained eligible count differs")
    if source.get("pre_cluster_eligible_count") != 36:
        raise AdjudicationError("source pre-cluster count differs")
    if source.get("live_mutable_data_excluded") is not True:
        raise AdjudicationError("mutable live project was not excluded")
    if strata.get("eligible_count") != 36:
        raise AdjudicationError("strata eligible count differs")
    if readiness.get("ready_to_freeze_sample") is not False:
        raise AdjudicationError("input unexpectedly permits sample freeze")
    if readiness.get("final_replay_pack_built") is not False:
        raise AdjudicationError("input unexpectedly contains final replay pack")
    if profile.get("profile_code_executed") is not False:
        raise AdjudicationError("input says profile code was executed")
    if profile.get("profile_code_imported") is not False:
        raise AdjudicationError("input says profile code was imported")
    for path, expected in ACCEPTED_SOURCE_HASHES.items():
        manifest_key = {
            "tools/build_reply_hybrid_evaluation_audit.py": "audit_tool",
            "tests/test_reply_hybrid_evaluation_audit.py": "audit_tests",
            "reply_hybrid_evaluation_preregistration.md": "preregistration",
        }[path]
        if manifest.get("source_hashes", {}).get(manifest_key) != expected:
            raise AdjudicationError(f"input source receipt differs: {path}")
    return {
        "manifest": manifest,
        "summary": summary,
        "boundary": boundary,
        "readiness": readiness,
        "source": source,
        "strata": strata,
        "profile": profile,
    }


def _verify_original_templates(
    input_directory: Path,
    fresh: dict[str, dict[str, Any]],
    context: dict[str, dict[str, Any]],
) -> set[str]:
    context_fields = (
        "candidate_id", "context_audit_sha256",
        "candidate_context_audit_record_sha256", "manual_decision", "reviewer_note",
    )
    context_rows = _read_csv(input_directory / "manual_context_clearance.csv", context_fields)
    context_ids: set[str] = set()
    for row in context_rows:
        candidate_id = row["candidate_id"]
        if candidate_id in context_ids:
            raise AdjudicationError(f"original context CSV repeats {candidate_id}")
        context_ids.add(candidate_id)
        if row["context_audit_sha256"] != EXPECTED_INPUT_HASHES["context_audit.jsonl"]:
            raise AdjudicationError("original context CSV audit hash differs")
        audit = context.get(candidate_id)
        if audit is None or row["candidate_context_audit_record_sha256"] != audit.get(
            "context_audit_record_sha256"
        ):
            raise AdjudicationError(
                f"original context CSV record link differs: {candidate_id}"
            )
        if row["manual_decision"] or row["reviewer_note"]:
            raise AdjudicationError("original context decisions are not blank")
    semantic_fields = (
        "candidate_id", "source_record_fingerprint", "context_audit_record_sha256",
        "context_clearance_status", "accepted_semantic_tags",
        "accepted_primary_stratum", "genuine_social_courtesy_decision",
        "safe_wit_opportunity_decision", "justified_safety_no_reply_decision",
        "controlled_reason_code", "reviewer_note", "adjudicator_identity",
        "decision_time_utc", "receipt_sha256",
    )
    semantic_rows = _read_csv(
        input_directory / "manual_semantic_classification.csv", semantic_fields
    )
    semantic_ids: set[str] = set()
    decision_fields = semantic_fields[4:]
    for row in semantic_rows:
        candidate_id = row["candidate_id"]
        if candidate_id in semantic_ids:
            raise AdjudicationError(f"original semantic CSV repeats {candidate_id}")
        semantic_ids.add(candidate_id)
        candidate = fresh.get(candidate_id)
        audit = context.get(candidate_id)
        if candidate is None or audit is None:
            raise AdjudicationError(
                f"original semantic CSV has non-eligible identity: {candidate_id}"
            )
        expected_status = (
            "automatic_clearance"
            if audit.get("automatic_context_clearance") is True
            else "pending_manual_review"
        )
        expected_links = {
            "source_record_fingerprint": candidate.get("source_record_fingerprint"),
            "context_audit_record_sha256": audit.get("context_audit_record_sha256"),
            "context_clearance_status": expected_status,
        }
        if any(row[key] != value for key, value in expected_links.items()):
            raise AdjudicationError(
                f"original semantic CSV record link differs: {candidate_id}"
            )
        if any(row[field] for field in decision_fields):
            raise AdjudicationError("original semantic decisions are not blank")
    if semantic_ids != set(fresh):
        raise AdjudicationError("original semantic CSV identity coverage differs")
    return context_ids


def _load_verified_case_data(input_directory: Path) -> dict[str, Any]:
    fresh_rows = _read_jsonl(input_directory / "fresh_candidate_inventory.jsonl")
    context_rows = _read_jsonl(input_directory / "context_audit.jsonl")
    fresh = _unique_by_id(fresh_rows, "fresh candidate inventory")
    context = _unique_by_id(context_rows, "context audit")
    if len(fresh) != EXPECTED_ELIGIBLE_COUNT:
        raise AdjudicationError("fresh eligible candidate count differs")
    manual_template_ids = _verify_original_templates(input_directory, fresh, context)
    eligible_context: dict[str, dict[str, Any]] = {}
    automatic_ids: set[str] = set()
    manual_ids: set[str] = set()
    fingerprints: set[str] = set()
    target_ids: set[str] = set()
    for candidate_id, candidate in fresh.items():
        audit = context.get(candidate_id)
        if audit is None:
            raise AdjudicationError(f"eligible candidate lacks context audit: {candidate_id}")
        claimed = audit.get("context_audit_record_sha256")
        if claimed != _canonical_record_hash(audit, "context_audit_record_sha256"):
            raise AdjudicationError(f"context record hash differs: {candidate_id}")
        if candidate.get("context_audit_record_sha256") != claimed:
            raise AdjudicationError(f"candidate/context record link differs: {candidate_id}")
        for key in ("lane", "context_dependency_flags", "replay_context", "component_sha256"):
            candidate_value = candidate.get(key)
            audit_value = audit.get("replay_context", {}).get(key) if key == "lane" else audit.get(key)
            if key == "replay_context":
                audit_value = audit.get("replay_context")
            if candidate_value != audit_value:
                raise AdjudicationError(f"candidate/context field differs: {candidate_id}:{key}")
        if candidate.get("incoming_contribution") != audit.get("replay_context", {}).get(
            "incoming_contribution"
        ):
            raise AdjudicationError(f"incoming contribution link differs: {candidate_id}")
        fingerprint = candidate.get("source_record_fingerprint")
        target_id = candidate.get("target_id")
        if not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint):
            raise AdjudicationError(f"bad source fingerprint: {candidate_id}")
        if fingerprint in fingerprints or target_id in target_ids:
            raise AdjudicationError("eligible source identities are not unique")
        fingerprints.add(fingerprint)
        target_ids.add(str(target_id))
        flags = audit.get("context_dependency_flags")
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            raise AdjudicationError(f"malformed context flags: {candidate_id}")
        if audit.get("context_unrecoverable") or audit.get("problems"):
            raise AdjudicationError(f"eligible candidate has context problems: {candidate_id}")
        if audit.get("temporal_violations") or audit.get(
            "historical_reply_self_leak_post_ids"
        ):
            raise AdjudicationError(f"eligible candidate has unsafe context: {candidate_id}")
        is_automatic = audit.get("automatic_context_clearance") is True
        is_manual = audit.get("manual_context_review_required") is True
        if is_automatic == is_manual or is_automatic != (not flags):
            raise AdjudicationError(f"context clearance path contradicts flags: {candidate_id}")
        (automatic_ids if is_automatic else manual_ids).add(candidate_id)
        eligible_context[candidate_id] = audit
    if len(automatic_ids) != 14 or len(manual_ids) != 22:
        raise AdjudicationError("eligible automatic/manual split differs")
    if manual_ids != manual_template_ids:
        raise AdjudicationError("manual context template identity coverage differs")
    clusters: dict[str, list[tuple[int, int]]] = {}
    for candidate_id, candidate in fresh.items():
        cluster_id = candidate.get("fresh_exact_text_cluster_id")
        rank = candidate.get("fresh_exact_text_cluster_rank")
        size = candidate.get("fresh_exact_text_cluster_size")
        if (
            not isinstance(cluster_id, str) or not cluster_id
            or not isinstance(rank, int) or isinstance(rank, bool)
            or not isinstance(size, int) or isinstance(size, bool) or size < 1
        ):
            raise AdjudicationError(
                f"malformed exact-text cluster assignment: {candidate_id}"
            )
        clusters.setdefault(cluster_id, []).append((rank, size))
    for cluster_id, assignments in clusters.items():
        sizes = {size for _rank, size in assignments}
        ranks = sorted(rank for rank, _size in assignments)
        if sizes != {len(assignments)} or ranks != list(range(1, len(assignments) + 1)):
            raise AdjudicationError(
                f"inconsistent exact-text cluster assignment: {cluster_id}"
            )
    multi_member = [members for members in clusters.values() if len(members) > 1]
    if len(clusters) != 32 or len(multi_member) != 1 or len(multi_member[0]) != 5:
        raise AdjudicationError("exact-text cluster coverage differs")
    return {
        "fresh": fresh,
        "context": eligible_context,
        "automatic_ids": automatic_ids,
        "manual_ids": manual_ids,
    }


def verify_input_bundle(input_directory: Path, repository: Path) -> dict[str, Any]:
    """Verify all immutable input hashes, controls, identities, counts, and joins."""
    input_directory = input_directory.resolve(strict=True)
    if input_directory != CORRECTED_INPUT_DIRECTORY.resolve(strict=True):
        raise AdjudicationError("input is not the exact corrected official audit directory")
    hashes = verify_checksum_manifest(
        input_directory,
        EXPECTED_INPUT_SHA256SUMS_SHA256,
        EXPECTED_INPUT_HASHES,
    )
    repository_receipt = _verify_repository(repository)
    controls = _verify_input_controls(input_directory)
    cases = _load_verified_case_data(input_directory)
    return {
        "schema_version": SCHEMA_VERSION,
        "verification_status": "pass",
        "verified_at_utc": utc_now_text(),
        "input_directory": str(input_directory),
        "sha256sums_sha256": EXPECTED_INPUT_SHA256SUMS_SHA256,
        "input_hashes": hashes,
        "accepted_audit_commit": ACCEPTED_AUDIT_COMMIT,
        "accepted_audit_branch": ACCEPTED_AUDIT_BRANCH,
        "accepted_ancestry": list(ANCESTRY),
        "repository": repository_receipt,
        "development_cutoff": controls["boundary"]["development_cutoff"],
        "eligible_candidate_count": len(cases["fresh"]),
        "automatic_context_clearance_count": len(cases["automatic_ids"]),
        "manual_context_review_count": len(cases["manual_ids"]),
        "blank_semantic_decision_count": len(cases["fresh"]),
        "fresh_exact_text_cluster_count": 1,
        "fresh_exact_text_clustered_candidate_count": 5,
        "fresh_candidates_removed_for_text_duplication": 0,
        "semantic_coverage_status": "unadjudicated",
        "ready_to_freeze_sample": False,
        "input_manifest_controls": {
            "model_calls": 0,
            "provider_http_requests": 0,
            "posting_actions": 0,
            "production_state_changes": 0,
            "live_project_included": False,
        },
    }


def _markdown_quote(value: str) -> str:
    return "\n".join(f"> {line}" for line in (value.splitlines() or [""]))


def _render_context_object(context: dict[str, Any]) -> list[str]:
    lines = ["Quoted-post context:", ""]
    quoted = context.get("quoted_post")
    if quoted:
        lines.extend([
            f"- Post ID: `{quoted['post_id']}`",
            f"- Author role: `{quoted['author_role']}`", "",
            _markdown_quote(str(quoted.get("text") or "")), "",
        ])
    else:
        lines.extend(["(none supplied)", ""])
    lines.extend(["Bounded parent thread (production order, oldest to newest):", ""])
    parents = context.get("parent_thread") or []
    if not parents:
        lines.extend(["(none supplied)", ""])
    for parent in parents:
        lines.extend([
            f"- `{parent['post_id']}` (`{parent['author_role']}`)", "",
            _markdown_quote(str(parent.get("text") or "")), "",
        ])
    lines.extend(["Clarification context:", ""])
    clarification = context.get("clarification_request")
    if clarification:
        lines.extend([
            "Original question:", "",
            _markdown_quote(str(clarification.get("original_question") or "")), "",
            "Correction:", "",
            _markdown_quote(str(clarification.get("correction") or "")), "",
        ])
    else:
        lines.extend(["(none supplied)", ""])
    return lines


def render_context_packet(cases: dict[str, Any]) -> str:
    """Render exactly the manual-review cases from strict safe projections."""
    lines = [
        "# Proposed pre-output context adjudication packet", "",
        "Profile-blind case material for Codex language-model review. Decisions remain ",
        "proposed pending independent review. Retrospective dispositions, earlier public ",
        "responses, profile identifiers, generated material, scores, and operational results ",
        "are not included.", "",
        f"Candidate count: {len(cases['manual_ids'])}", "",
    ]
    for candidate_id in sorted(cases["manual_ids"]):
        candidate = cases["fresh"][candidate_id]
        audit = cases["context"][candidate_id]
        context = audit["replay_context"]
        production = audit["production_context_at_candidate"]
        lines.extend([
            f"## {candidate_id}", "",
            f"Source-record fingerprint: `{candidate['source_record_fingerprint']}`", "",
            f"Candidate context-audit-record SHA-256: `{audit['context_audit_record_sha256']}`", "",
            "Context-dependency flags: " + ", ".join(audit["context_dependency_flags"]), "",
            f"Lane: `{context['lane']}`", "",
            "Exact incoming contribution:", "",
            _markdown_quote(str(context["incoming_contribution"])), "",
        ])
        lines.extend(_render_context_object(context))
        lines.extend(["Recent account replies (stored production order):", ""])
        recent = audit.get("recent_account_replies") or []
        if not recent:
            lines.extend(["(none supplied)", ""])
        for reply in recent:
            lines.extend([
                f"- `{reply['post_id']}` at `{reply['created_at']}`", "",
                _markdown_quote(str(reply.get("text") or "")), "",
            ])
        lines.extend(["Resolved quotation metadata:", ""])
        resolved = audit.get("resolved_quotation")
        if resolved:
            lines.extend(["```json", json.dumps(resolved, ensure_ascii=False, sort_keys=True, indent=2), "```", ""])
        else:
            lines.extend(["(none resolved)", ""])
        date_receipt = {
            "replay_current_date": context.get("current_date"),
            "production_current_date": production.get("current_date"),
            "current_date_convention": audit.get("current_date_convention"),
        }
        provenance = {
            "candidate_timestamp": audit.get("candidate_timestamp"),
            "first_consideration_timestamp": audit.get("first_consideration_timestamp"),
            "target_source_provenance": candidate.get("target_source_provenance"),
            "component_sha256": audit.get("component_sha256"),
            "parent_context_bindings": audit.get("parent_context_bindings"),
            "quoted_post_binding": audit.get("quoted_post_binding"),
            "clarification_binding": audit.get("clarification_binding"),
            "persisted_context_verification": audit.get("persisted_context_verification"),
            "temporal_violations": audit.get("temporal_violations"),
            "context_problems": audit.get("problems"),
        }
        lines.extend([
            "Current-date receipt:", "", "```json",
            json.dumps(date_receipt, ensure_ascii=False, sort_keys=True, indent=2),
            "```", "", "Relevant temporal and provenance receipts:", "", "```json",
            json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2),
            "```", "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _blank_context_rows(cases: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "candidate_id": candidate_id,
            "context_audit_record_sha256": cases["context"][candidate_id][
                "context_audit_record_sha256"
            ],
            **{field: "" for field in CONTEXT_FIELDS[2:]},
        }
        for candidate_id in sorted(cases["manual_ids"])
    ]


def _validate_note(note: str, reason_code: str, candidate_id: str) -> None:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", note)
    if len(note.strip()) < 32 or len(words) < 6:
        raise AdjudicationError(f"reviewer note is not substantive: {candidate_id}")
    normalised_note = re.sub(r"[^a-z0-9]+", "_", note.casefold()).strip("_")
    if normalised_note == reason_code or note.strip().casefold() == reason_code.replace(
        "_", " "
    ):
        raise AdjudicationError(f"reviewer note merely repeats reason: {candidate_id}")
    lowered = note.casefold()
    if any(pattern in lowered for pattern in FORBIDDEN_NOTE_PATTERNS):
        raise AdjudicationError(f"reviewer note contains blinded comparison: {candidate_id}")


def _manual_context_receipt(
    row: dict[str, str], candidate: dict[str, Any]
) -> dict[str, Any]:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "context_clearance",
        "candidate_id": row["candidate_id"],
        "source_record_fingerprint": candidate["source_record_fingerprint"],
        "context_audit_record_sha256": row["context_audit_record_sha256"],
        "context_decision": row["proposed_context_decision"],
        "controlled_reason_code": row["controlled_reason_code"],
        "reviewer_note": row["reviewer_note"],
        "adjudicator_identity": row["adjudicator_identity"],
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "decision_time_utc": row["decision_time_utc"],
        "review_status": GLOBAL_REVIEW_STATUS,
        "context_clearance_path": "manual_codex_case_by_case_review",
        "input_sha256sums_sha256": EXPECTED_INPUT_SHA256SUMS_SHA256,
    }
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    return receipt


def _automatic_context_receipt(
    candidate_id: str, candidate: dict[str, Any], audit: dict[str, Any], decision_time: str
) -> dict[str, Any]:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "context_clearance",
        "candidate_id": candidate_id,
        "source_record_fingerprint": candidate["source_record_fingerprint"],
        "context_audit_record_sha256": audit["context_audit_record_sha256"],
        "context_decision": "automatic_clearance",
        "controlled_reason_code": "automatic_audit_no_dependency_flags",
        "reviewer_note": "Inherited from the accepted audit's empty dependency flags and clean context checks.",
        "adjudicator_identity": "reply-hybrid-evaluation-audit-v2",
        "adjudicator_kind": "automatic_audit_decision",
        "human_adjudicator": False,
        "decision_time_utc": decision_time,
        "review_status": (
            "inherited_automatic_audit_decision_pending_independent_review"
        ),
        "context_clearance_path": "automatic_audit_clearance",
        "input_sha256sums_sha256": EXPECTED_INPUT_SHA256SUMS_SHA256,
    }
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    return receipt


def validate_context_decisions(
    rows: Sequence[dict[str, str]], cases: dict[str, Any], automatic_time: str,
    minimum_decision_time: str = ACCEPTED_AUDIT_FINISH_UTC,
    maximum_decision_time: str | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate all manual context decisions and construct all 36 receipts."""
    candidate_ids = [row.get("candidate_id", "") for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise AdjudicationError("duplicate context decision candidate ID")
    if len(rows) != len(cases["manual_ids"]):
        raise AdjudicationError("missing or extra manual context decisions")
    by_id: dict[str, dict[str, str]] = {}
    manual_receipts: list[dict[str, Any]] = []
    notes: set[str] = set()
    maximum = maximum_decision_time or utc_now_text()
    for row in rows:
        candidate_id = row["candidate_id"]
        if candidate_id in by_id:
            raise AdjudicationError(f"duplicate context decision: {candidate_id}")
        if candidate_id not in cases["manual_ids"]:
            raise AdjudicationError(f"unexpected context decision: {candidate_id}")
        by_id[candidate_id] = dict(row)
        expected_hash = cases["context"][candidate_id]["context_audit_record_sha256"]
        if row["context_audit_record_sha256"] != expected_hash:
            raise AdjudicationError(f"context decision hash differs: {candidate_id}")
        decision = row["proposed_context_decision"]
        if decision not in CONTEXT_DECISIONS:
            raise AdjudicationError(f"invalid or missing context decision: {candidate_id}")
        reason = row["controlled_reason_code"]
        if reason not in CONTEXT_REASON_CODES[decision]:
            raise AdjudicationError(f"context reason contradicts decision: {candidate_id}")
        _validate_note(row["reviewer_note"], reason, candidate_id)
        note_key = row["reviewer_note"].strip().casefold()
        if note_key in notes:
            raise AdjudicationError(
                f"context reviewer note is not candidate-specific: {candidate_id}"
            )
        notes.add(note_key)
        if row["adjudicator_identity"] != CONTEXT_REVIEWER:
            raise AdjudicationError(f"wrong context adjudicator identity: {candidate_id}")
        _validate_decision_time(
            row["decision_time_utc"], candidate_id, minimum_decision_time, maximum
        )
        receipt = _manual_context_receipt(row, cases["fresh"][candidate_id])
        supplied_hash = row["receipt_sha256"]
        if supplied_hash and supplied_hash != receipt["receipt_sha256"]:
            raise AdjudicationError(f"context receipt hash differs: {candidate_id}")
        by_id[candidate_id]["receipt_sha256"] = receipt["receipt_sha256"]
        manual_receipts.append(receipt)
    if set(by_id) != cases["manual_ids"]:
        raise AdjudicationError("context decision identity coverage differs")
    automatic_receipts = [
        _automatic_context_receipt(
            candidate_id, cases["fresh"][candidate_id], cases["context"][candidate_id],
            automatic_time,
        )
        for candidate_id in sorted(cases["automatic_ids"])
    ]
    receipts = sorted(
        [*automatic_receipts, *manual_receipts], key=lambda item: item["candidate_id"]
    )
    completed_rows = [by_id[candidate_id] for candidate_id in sorted(by_id)]
    return completed_rows, receipts


def render_semantic_packet(
    cases: dict[str, Any], context_receipts: Sequence[dict[str, Any]]
) -> str:
    """Render cleared candidates only, in reverse ID order, without lexical hints."""
    receipt_by_id = {row["candidate_id"]: row for row in context_receipts}
    eligible_ids = {
        candidate_id for candidate_id, receipt in receipt_by_id.items()
        if receipt["context_decision"] in {"automatic_clearance", "clear"}
    }
    lines = [
        "# Proposed pre-output semantic adjudication packet", "",
        "Profile-blind contribution and cleared-context material for case-by-case Codex ",
        "language-model review. Decisions remain proposed pending independent review. ",
        "No retrospective disposition, earlier public response, profile identifier, generated ",
        "material, score, operational result, or navigation hint is included.", "",
        "Review order: reverse candidate-ID order.", "",
        f"Candidate count: {len(eligible_ids)}", "",
    ]
    for candidate_id in sorted(eligible_ids, reverse=True):
        candidate = cases["fresh"][candidate_id]
        audit = cases["context"][candidate_id]
        context = audit["replay_context"]
        receipt = receipt_by_id[candidate_id]
        lines.extend([
            f"## {candidate_id}", "",
            f"Source-record fingerprint: `{candidate['source_record_fingerprint']}`", "",
            f"Context-clearance receipt SHA-256: `{receipt['receipt_sha256']}`", "",
            f"Context-audit-record SHA-256: `{audit['context_audit_record_sha256']}`", "",
            f"Lane: `{context['lane']}`", "",
            f"Current date: `{context['current_date']}`", "",
            "Exact incoming contribution:", "",
            _markdown_quote(str(context["incoming_contribution"])), "",
            "Exact cleared replay context:", "", "```json",
            json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2),
            "```", "",
        ])
        lines.extend(_render_context_object(context))
        lines.extend(["Recent account replies (stored production order):", ""])
        recent = audit.get("recent_account_replies") or []
        if not recent:
            lines.extend(["(none supplied)", ""])
        for reply in recent:
            lines.extend([
                f"- `{reply['post_id']}` at `{reply['created_at']}`", "",
                _markdown_quote(str(reply.get("text") or "")), "",
            ])
        lines.extend(["Resolved quotation metadata:", ""])
        resolved = audit.get("resolved_quotation")
        if resolved:
            lines.extend(["```json", json.dumps(resolved, ensure_ascii=False, sort_keys=True, indent=2), "```", ""])
        else:
            lines.extend(["(none resolved)", ""])
    return "\n".join(lines).rstrip() + "\n"


def _blank_semantic_rows(
    cases: dict[str, Any], context_receipts: Sequence[dict[str, Any]]
) -> list[dict[str, str]]:
    receipts = {row["candidate_id"]: row for row in context_receipts}
    rows: list[dict[str, str]] = []
    for candidate_id in sorted(cases["fresh"], reverse=True):
        context_decision = receipts[candidate_id]["context_decision"]
        derived: dict[str, str] = {}
        if context_decision == "exclude":
            derived = {
                "semantic_adjudication_status": "blocked_context_excluded",
                "controlled_reason_code": "blocked_by_context_exclusion",
            }
        elif context_decision == "requires_adjudication":
            derived = {
                "semantic_adjudication_status": "requires_adjudication",
                "controlled_reason_code": "semantic_ambiguity_requires_adjudication",
            }
        row = {
            "candidate_id": candidate_id,
            "source_record_fingerprint": cases["fresh"][candidate_id][
                "source_record_fingerprint"
            ],
            "context_clearance_receipt_sha256": receipts[candidate_id]["receipt_sha256"],
            "context_audit_record_sha256": cases["context"][candidate_id][
                "context_audit_record_sha256"
            ],
            **{field: "" for field in SEMANTIC_FIELDS[4:]},
        }
        row.update(derived)
        rows.append(row)
    return rows


def _parse_tags(value: str, candidate_id: str) -> list[str]:
    try:
        tags = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AdjudicationError(f"semantic tags are malformed: {candidate_id}") from exc
    if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) for tag in tags):
        raise AdjudicationError(f"semantic tags must be a nonempty string array: {candidate_id}")
    if len(tags) != len(set(tags)):
        raise AdjudicationError(f"semantic tags repeat a value: {candidate_id}")
    if any(tag not in SEMANTIC_TAGS for tag in tags):
        raise AdjudicationError(f"uncontrolled semantic tag: {candidate_id}")
    if tags != [tag for tag in SEMANTIC_TAGS if tag in tags]:
        raise AdjudicationError(f"semantic tags are not in fixed order: {candidate_id}")
    return tags


def _boolean(value: str, candidate_id: str, field: str) -> bool:
    if value not in {"true", "false"}:
        raise AdjudicationError(f"{field} must be exact Boolean text: {candidate_id}")
    return value == "true"


def _semantic_receipt(row: dict[str, str], tags: list[str] | None) -> dict[str, Any]:
    completed = row["semantic_adjudication_status"] == "completed"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "semantic_classification",
        "candidate_id": row["candidate_id"],
        "source_record_fingerprint": row["source_record_fingerprint"],
        "context_clearance_receipt_sha256": row["context_clearance_receipt_sha256"],
        "context_audit_record_sha256": row["context_audit_record_sha256"],
        "semantic_adjudication_status": row["semantic_adjudication_status"],
        "accepted_semantic_tags": tags if completed else None,
        "accepted_primary_stratum": row["accepted_primary_stratum"] if completed else None,
        "genuine_social_courtesy_decision": (
            row["genuine_social_courtesy_decision"] == "true" if completed else None
        ),
        "safe_wit_opportunity_decision": (
            row["safe_wit_opportunity_decision"] == "true" if completed else None
        ),
        "justified_safety_no_reply_decision": (
            row["justified_safety_no_reply_decision"] == "true" if completed else None
        ),
        "controlled_reason_code": row["controlled_reason_code"],
        "reviewer_note": row["reviewer_note"],
        "adjudicator_identity": row["adjudicator_identity"],
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "decision_time_utc": row["decision_time_utc"],
        "review_status": GLOBAL_REVIEW_STATUS,
        "input_sha256sums_sha256": EXPECTED_INPUT_SHA256SUMS_SHA256,
    }
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    return receipt


def validate_semantic_decisions(
    rows: Sequence[dict[str, str]], cases: dict[str, Any],
    context_receipts: Sequence[dict[str, Any]],
    minimum_decision_time: str = ACCEPTED_AUDIT_FINISH_UTC,
    maximum_decision_time: str | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate one semantic accounting row per eligible candidate and hash receipts."""
    if len(rows) != len(cases["fresh"]):
        raise AdjudicationError("missing or extra semantic accounting rows")
    context_by_id = {row["candidate_id"]: row for row in context_receipts}
    by_id: dict[str, dict[str, str]] = {}
    receipts: list[dict[str, Any]] = []
    notes: set[str] = set()
    maximum = maximum_decision_time or utc_now_text()
    for row in rows:
        candidate_id = row["candidate_id"]
        if any(
            "formulaic_substantive_posted" in str(row.get(field, ""))
            for field in SEMANTIC_FIELDS[4:]
        ):
            raise AdjudicationError(
                "formulaic_substantive_posted is not a semantic classification"
            )
        if candidate_id in by_id:
            raise AdjudicationError(f"duplicate semantic decision: {candidate_id}")
        if candidate_id not in cases["fresh"]:
            raise AdjudicationError(f"unexpected semantic decision: {candidate_id}")
        context_receipt = context_by_id.get(candidate_id)
        if context_receipt is None:
            raise AdjudicationError(f"semantic row lacks context receipt: {candidate_id}")
        identity_expectations = {
            "source_record_fingerprint": cases["fresh"][candidate_id][
                "source_record_fingerprint"
            ],
            "context_clearance_receipt_sha256": context_receipt["receipt_sha256"],
            "context_audit_record_sha256": cases["context"][candidate_id][
                "context_audit_record_sha256"
            ],
        }
        if any(row[key] != value for key, value in identity_expectations.items()):
            raise AdjudicationError(f"semantic identity receipt differs: {candidate_id}")
        status = row["semantic_adjudication_status"]
        if status not in SEMANTIC_STATUSES:
            raise AdjudicationError(f"invalid or missing semantic status: {candidate_id}")
        context_decision = context_receipt["context_decision"]
        if context_decision == "exclude" and status != "blocked_context_excluded":
            raise AdjudicationError(f"context-excluded row is not blocked: {candidate_id}")
        if context_decision == "requires_adjudication" and status != "requires_adjudication":
            raise AdjudicationError(f"unresolved context row has semantic classification: {candidate_id}")
        if context_decision in {"automatic_clearance", "clear"} and status == "blocked_context_excluded":
            raise AdjudicationError(f"cleared context row is marked excluded: {candidate_id}")
        reason = row["controlled_reason_code"]
        if reason not in SEMANTIC_REASON_CODES:
            raise AdjudicationError(f"uncontrolled semantic reason: {candidate_id}")
        _validate_note(row["reviewer_note"], reason, candidate_id)
        note_key = row["reviewer_note"].strip().casefold()
        if note_key in notes:
            raise AdjudicationError(f"semantic reviewer note is not candidate-specific: {candidate_id}")
        notes.add(note_key)
        if row["adjudicator_identity"] != SEMANTIC_REVIEWER:
            raise AdjudicationError(f"wrong semantic adjudicator identity: {candidate_id}")
        _validate_decision_time(
            row["decision_time_utc"], candidate_id, minimum_decision_time, maximum
        )
        tags: list[str] | None = None
        semantic_value_fields = (
            "accepted_semantic_tags_json", "accepted_primary_stratum",
            "genuine_social_courtesy_decision", "safe_wit_opportunity_decision",
            "justified_safety_no_reply_decision",
        )
        if status == "completed":
            tags = _parse_tags(row["accepted_semantic_tags_json"], candidate_id)
            primary = row["accepted_primary_stratum"]
            if primary not in PRIMARY_STRATA:
                raise AdjudicationError(f"uncontrolled primary stratum: {candidate_id}")
            if not (set(tags) & PRIMARY_REQUIRED_TAGS[primary]):
                raise AdjudicationError(f"primary stratum lacks corresponding tag: {candidate_id}")
            social = _boolean(row["genuine_social_courtesy_decision"], candidate_id, "social decision")
            wit = _boolean(row["safe_wit_opportunity_decision"], candidate_id, "wit decision")
            safety = _boolean(row["justified_safety_no_reply_decision"], candidate_id, "safety decision")
            checks = (
                (social, "genuine_social_courtesy", "genuine_social_courtesy"),
                (wit, "safe_contribution_specific_wit_opportunity", None),
                (safety, "justified_safety_no_reply", "justified_safety_no_reply"),
            )
            for decision, tag, required_primary in checks:
                if decision != (tag in tags):
                    raise AdjudicationError(f"special Boolean contradicts tags: {candidate_id}")
                if decision and required_primary and primary != required_primary:
                    raise AdjudicationError(f"special Boolean contradicts primary: {candidate_id}")
            if reason != "mixed_act_primary_selected" and reason not in PRIMARY_REASON_CODES[primary]:
                raise AdjudicationError(f"semantic reason contradicts primary: {candidate_id}")
            required_tag = REASON_REQUIRED_TAG.get(reason)
            if required_tag is not None and required_tag not in tags:
                raise AdjudicationError(
                    f"semantic reason lacks its corresponding tag: {candidate_id}"
                )
            if reason == "mixed_act_primary_selected" and len(tags) < 2:
                raise AdjudicationError(
                    f"mixed-act reason requires multiple semantic tags: {candidate_id}"
                )
            if reason in {"semantic_ambiguity_requires_adjudication", "blocked_by_context_exclusion"}:
                raise AdjudicationError(f"completed row uses unresolved reason: {candidate_id}")
        else:
            if any(row[field] for field in semantic_value_fields):
                raise AdjudicationError(f"blocked/unresolved semantic fields are not blank: {candidate_id}")
            expected_reason = (
                "blocked_by_context_exclusion"
                if status == "blocked_context_excluded"
                else "semantic_ambiguity_requires_adjudication"
            )
            if reason != expected_reason:
                raise AdjudicationError(f"semantic status/reason contradiction: {candidate_id}")
        receipt = _semantic_receipt(row, tags)
        if row["receipt_sha256"] and row["receipt_sha256"] != receipt["receipt_sha256"]:
            raise AdjudicationError(f"semantic receipt hash differs: {candidate_id}")
        completed_row = dict(row)
        if tags is not None:
            completed_row["accepted_semantic_tags_json"] = json.dumps(
                tags, ensure_ascii=False, separators=(",", ":")
            )
        completed_row["receipt_sha256"] = receipt["receipt_sha256"]
        by_id[candidate_id] = completed_row
        receipts.append(receipt)
    if set(by_id) != set(cases["fresh"]):
        raise AdjudicationError("semantic accounting identity coverage differs")
    return (
        [by_id[candidate_id] for candidate_id in sorted(by_id, reverse=True)],
        sorted(receipts, key=lambda item: item["candidate_id"]),
    )


def _validate_context_conflict_decision(
    value: Any, candidate_id: str
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "context_decision", "controlled_reason_code"
    }:
        raise AdjudicationError(
            f"context conflict decision fields differ: {candidate_id}"
        )
    decision = value.get("context_decision")
    reason = value.get("controlled_reason_code")
    if decision not in CONTEXT_DECISIONS or reason not in CONTEXT_REASON_CODES[decision]:
        raise AdjudicationError(
            f"context conflict decision is uncontrolled: {candidate_id}"
        )
    return value


def _validate_semantic_conflict_decision(
    value: Any, candidate_id: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "semantic_adjudication_status", "accepted_semantic_tags",
        "accepted_primary_stratum", "controlled_reason_code",
    }:
        raise AdjudicationError(
            f"semantic conflict decision fields differ: {candidate_id}"
        )
    status = value.get("semantic_adjudication_status")
    tags = value.get("accepted_semantic_tags")
    primary = value.get("accepted_primary_stratum")
    reason = value.get("controlled_reason_code")
    if status == "requires_adjudication":
        if tags is not None or primary is not None or reason != (
            "semantic_ambiguity_requires_adjudication"
        ):
            raise AdjudicationError(
                f"unresolved semantic conflict decision is contradictory: {candidate_id}"
            )
        return value
    if status != "completed":
        raise AdjudicationError(
            f"semantic conflict status is not review-eligible: {candidate_id}"
        )
    if (
        not isinstance(tags, list) or not tags
        or not all(isinstance(tag, str) for tag in tags)
        or len(tags) != len(set(tags))
        or tags != [tag for tag in SEMANTIC_TAGS if tag in tags]
    ):
        raise AdjudicationError(
            f"semantic conflict tags are uncontrolled: {candidate_id}"
        )
    if primary not in PRIMARY_STRATA or not (set(tags) & PRIMARY_REQUIRED_TAGS[primary]):
        raise AdjudicationError(
            f"semantic conflict primary is contradictory: {candidate_id}"
        )
    if reason not in SEMANTIC_REASON_CODES or reason in {
        "semantic_ambiguity_requires_adjudication", "blocked_by_context_exclusion"
    }:
        raise AdjudicationError(
            f"semantic conflict reason is uncontrolled: {candidate_id}"
        )
    if reason != "mixed_act_primary_selected" and reason not in PRIMARY_REASON_CODES[primary]:
        raise AdjudicationError(
            f"semantic conflict reason contradicts primary: {candidate_id}"
        )
    if reason == "mixed_act_primary_selected" and len(tags) < 2:
        raise AdjudicationError(
            f"semantic conflict mixed act lacks multiple tags: {candidate_id}"
        )
    required_tag = REASON_REQUIRED_TAG.get(str(reason))
    if required_tag is not None and required_tag not in tags:
        raise AdjudicationError(
            f"semantic conflict reason lacks its tag: {candidate_id}"
        )
    return value


def _validate_conflicts(
    path: Path,
    context_receipts: Sequence[dict[str, Any]],
    semantic_receipts: Sequence[dict[str, Any]],
    maximum_review_time: str | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        raise AdjudicationError("adjudication conflict file is missing")
    if not path.read_text(encoding="utf-8"):
        return []
    rows = _read_jsonl(path)
    context_by_id = {row["candidate_id"]: row for row in context_receipts}
    semantic_by_id = {row["candidate_id"]: row for row in semantic_receipts}
    eligible_ids = set(context_by_id)
    if set(semantic_by_id) != eligible_ids:
        raise AdjudicationError("conflict receipt universes differ")
    maximum = maximum_review_time or utc_now_text()
    seen: set[tuple[str, str]] = set()
    required = {
        "candidate_id", "phase", "first_pass_decision", "second_pass_decision",
        "conflict_basis", "resolution", "unresolved", "review_time_utc",
    }
    for row in rows:
        if set(row) != required:
            raise AdjudicationError("conflict row fields differ from controlled schema")
        candidate_id = row["candidate_id"]
        phase = row["phase"]
        if candidate_id not in eligible_ids or phase not in {"context", "semantic"}:
            raise AdjudicationError("conflict row identity or phase is invalid")
        if (candidate_id, phase) in seen:
            raise AdjudicationError("duplicate adjudication conflict")
        seen.add((candidate_id, phase))
        first = row["first_pass_decision"]
        second = row["second_pass_decision"]
        if phase == "context":
            if context_by_id[candidate_id]["context_clearance_path"] != (
                "manual_codex_case_by_case_review"
            ):
                raise AdjudicationError(
                    "automatic context receipt cannot have a Codex context conflict"
                )
            _validate_context_conflict_decision(first, candidate_id)
            _validate_context_conflict_decision(second, candidate_id)
        else:
            if context_by_id[candidate_id]["context_decision"] not in {
                "automatic_clearance", "clear"
            }:
                raise AdjudicationError(
                    "semantic conflict candidate was not context-cleared"
                )
            _validate_semantic_conflict_decision(first, candidate_id)
            _validate_semantic_conflict_decision(second, candidate_id)
        if first == second:
            raise AdjudicationError("conflict row does not record a disagreement")
        if not isinstance(row["unresolved"], bool):
            raise AdjudicationError("conflict unresolved flag must be Boolean")
        _validate_note(str(row["conflict_basis"]), "conflict", candidate_id)
        if phase == "context":
            receipt = context_by_id[candidate_id]
            resolution = {
                "context_decision": receipt["context_decision"],
                "controlled_reason_code": receipt["controlled_reason_code"],
            }
            is_unresolved = receipt["context_decision"] == "requires_adjudication"
        else:
            receipt = semantic_by_id[candidate_id]
            resolution = {
                "semantic_adjudication_status": receipt[
                    "semantic_adjudication_status"
                ],
                "accepted_semantic_tags": receipt["accepted_semantic_tags"],
                "accepted_primary_stratum": receipt["accepted_primary_stratum"],
                "controlled_reason_code": receipt["controlled_reason_code"],
            }
            is_unresolved = (
                receipt["semantic_adjudication_status"] == "requires_adjudication"
            )
        if row["resolution"] != resolution:
            raise AdjudicationError("conflict resolution differs from final receipt")
        if row["unresolved"] is not is_unresolved:
            raise AdjudicationError("conflict unresolved flag differs from final receipt")
        _validate_decision_time(
            str(row["review_time_utc"]), candidate_id,
            ACCEPTED_AUDIT_FINISH_UTC, maximum,
        )
    return rows


def build_coverage_inventory(
    cases: dict[str, Any], context_receipts: Sequence[dict[str, Any]],
    semantic_receipts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Derive proposed coverage counts without consulting retrospective outcomes."""
    context_by_id = {row["candidate_id"]: row for row in context_receipts}
    semantic_by_id = {row["candidate_id"]: row for row in semantic_receipts}
    if set(context_by_id) != set(cases["fresh"]) or set(semantic_by_id) != set(
        cases["fresh"]
    ):
        raise AdjudicationError("coverage input receipts do not cover every candidate")
    context_counts = Counter(row["context_decision"] for row in context_receipts)
    status_counts = Counter(row["semantic_adjudication_status"] for row in semantic_receipts)
    tag_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    subset_counts = Counter()
    lane_counts: Counter[str] = Counter()
    path_counts = Counter(row["context_clearance_path"] for row in context_receipts)
    cluster_rows: dict[str, dict[str, Any]] = {}
    for candidate_id, candidate in cases["fresh"].items():
        semantic = semantic_by_id[candidate_id]
        context = context_by_id[candidate_id]
        lane_counts[str(candidate["lane"])] += 1
        if semantic["semantic_adjudication_status"] == "completed":
            tag_counts.update(semantic["accepted_semantic_tags"])
            primary_counts[str(semantic["accepted_primary_stratum"])] += 1
            subset_counts["genuine_social_courtesy"] += int(
                semantic["genuine_social_courtesy_decision"] is True
            )
            subset_counts["safe_wit_opportunity"] += int(
                semantic["safe_wit_opportunity_decision"] is True
            )
            subset_counts["justified_safety_no_reply"] += int(
                semantic["justified_safety_no_reply_decision"] is True
            )
        cluster_id = str(candidate["fresh_exact_text_cluster_id"])
        cluster = cluster_rows.setdefault(
            cluster_id,
            {
                "declared_cluster_size": candidate["fresh_exact_text_cluster_size"],
                "candidate_count": 0,
                "context_decisions": Counter(),
                "semantic_statuses": Counter(),
                "semantic_tags": Counter(),
                "primary_strata": Counter(),
            },
        )
        cluster["candidate_count"] += 1
        cluster["context_decisions"][context["context_decision"]] += 1
        cluster["semantic_statuses"][semantic["semantic_adjudication_status"]] += 1
        if semantic["semantic_adjudication_status"] == "completed":
            cluster["semantic_tags"].update(semantic["accepted_semantic_tags"])
            cluster["primary_strata"][semantic["accepted_primary_stratum"]] += 1
    serial_clusters = {
        cluster_id: {
            key: dict(sorted(value.items())) if isinstance(value, Counter) else value
            for key, value in cluster.items()
        }
        for cluster_id, cluster in sorted(cluster_rows.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_status": "proposed_pending_independent_review",
        "eligible_candidate_count": len(cases["fresh"]),
        "counts_by_context_decision": {
            key: context_counts[key]
            for key in ("automatic_clearance", *CONTEXT_DECISIONS)
        },
        "counts_by_semantic_adjudication_status": {
            key: status_counts[key] for key in SEMANTIC_STATUSES
        },
        "counts_by_accepted_semantic_tag": {
            key: tag_counts[key] for key in SEMANTIC_TAGS
        },
        "counts_by_accepted_primary_stratum": {
            key: primary_counts[key] for key in PRIMARY_STRATA
        },
        "genuine_social_courtesy_subset_count": subset_counts[
            "genuine_social_courtesy"
        ],
        "safe_wit_opportunity_subset_count": subset_counts["safe_wit_opportunity"],
        "justified_safety_no_reply_subset_count": subset_counts[
            "justified_safety_no_reply"
        ],
        "counts_by_lane": {
            key: lane_counts[key] for key in ("mention", "quote_tweet")
        },
        "counts_by_context_clearance_path": {
            key: path_counts[key]
            for key in (
                "automatic_audit_clearance",
                "manual_codex_case_by_case_review",
            )
        },
        "counts_by_exact_text_cluster": serial_clusters,
        "formulaic_substantive_posted": None,
        "retrospective_outcome_used_in_semantic_counts": False,
    }


def build_sampling_readiness(
    coverage: dict[str, Any], conflicts: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Build the always-false sampling-readiness receipt and explicit blockers."""
    context_unresolved = coverage["counts_by_context_decision"].get(
        "requires_adjudication", 0
    )
    semantic_unresolved = coverage["counts_by_semantic_adjudication_status"].get(
        "requires_adjudication", 0
    )
    unresolved_conflicts = sum(bool(row["unresolved"]) for row in conflicts)
    manual_complete = not (context_unresolved or semantic_unresolved or unresolved_conflicts)
    blockers = [
        "independent review of the proposed Codex adjudications remains pending",
        "separate profile-blind historical-baseline formulaicity review remains unperformed",
    ]
    if context_unresolved:
        blockers.append(f"{context_unresolved} context decisions require adjudication")
    if semantic_unresolved:
        blockers.append(f"{semantic_unresolved} semantic decisions require adjudication")
    if unresolved_conflicts:
        blockers.append(f"{unresolved_conflicts} receipt conflicts remain unresolved")
    return {
        "schema_version": SCHEMA_VERSION,
        "manual_adjudication_complete": manual_complete,
        "coverage_inventory_complete": True,
        "ready_for_independent_adjudication_review": True,
        "independent_review_complete": False,
        "final_sample_selected": False,
        "final_replay_pack_built": False,
        "execution_seed_created": False,
        "response_label_seed_created": False,
        "blind_key_created": False,
        "ready_to_freeze_sample": False,
        "blockers": blockers,
    }


def _prepare_output(path: Path, research_root: Path) -> Path:
    research_root = research_root.resolve(strict=True)
    requested = path.absolute()
    if requested.parent.resolve(strict=True) != research_root:
        raise AdjudicationError("output directory must be directly below research root")
    if not requested.name.startswith(
        "mrsMThatcher-reply-hybrid-preoutput-adjudication-"
    ):
        raise AdjudicationError("output directory name has the wrong prefix")
    if requested.exists():
        raise AdjudicationError("prepare context requires a new output directory")
    requested.mkdir(mode=0o700)
    requested.chmod(stat.S_IRWXU)
    return requested.resolve(strict=True)


def _verify_stage_directory(
    path: Path, research_root: Path, expected_files: set[str] | frozenset[str]
) -> Path:
    absolute = path.absolute()
    if absolute.is_symlink():
        raise AdjudicationError("output directory must not be a symlink")
    output = absolute.resolve(strict=True)
    root = research_root.resolve(strict=True)
    if output.parent != root:
        raise AdjudicationError("output directory must be directly below research root")
    if not output.name.startswith(
        "mrsMThatcher-reply-hybrid-preoutput-adjudication-"
    ):
        raise AdjudicationError("output directory name has the wrong prefix")
    if not output.is_dir() or stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise AdjudicationError("output directory must be a mode-0700 directory")
    entries = list(output.iterdir())
    names = {entry.name for entry in entries}
    if names != set(expected_files):
        raise AdjudicationError(
            "staged output inventory differs: "
            f"missing={sorted(set(expected_files) - names)!r} "
            f"extra={sorted(names - set(expected_files))!r}"
        )
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise AdjudicationError(f"staged output member is unsafe: {entry.name}")
        if stat.S_IMODE(entry.stat().st_mode) != 0o600:
            raise AdjudicationError(f"staged output member is not mode 0600: {entry.name}")
    return output


def _require_file_bytes(path: Path, expected: str | bytes) -> None:
    payload = expected.encode("utf-8") if isinstance(expected, str) else expected
    if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
        raise AdjudicationError(f"prepared artefact differs: {path.name}")


def _verify_original_copies(input_directory: Path, output: Path) -> None:
    for source_name, target_name in ORIGINAL_COPIES.items():
        target = output / target_name
        if target.is_symlink() or not target.is_file():
            raise AdjudicationError(f"copied original is unsafe: {target_name}")
        if file_sha256(target) != EXPECTED_INPUT_HASHES[source_name]:
            raise AdjudicationError(f"copied original digest differs: {source_name}")


def _verify_input_verification_copy(
    path: Path, current_verification: dict[str, Any]
) -> None:
    recorded = _read_json(path)
    if set(recorded) != set(current_verification):
        raise AdjudicationError("prepared input verification fields differ")
    recorded_time = recorded.get("verified_at_utc")
    if not isinstance(recorded_time, str):
        raise AdjudicationError("prepared input verification time is missing")
    parse_utc(recorded_time)
    stable_recorded = json.loads(json.dumps({
        key: value for key, value in recorded.items() if key != "verified_at_utc"
    }))
    stable_current = json.loads(json.dumps({
        key: value for key, value in current_verification.items()
        if key != "verified_at_utc"
    }))
    for stable in (stable_recorded, stable_current):
        repository = stable.get("repository")
        if isinstance(repository, dict):
            repository.pop("head", None)
            repository.pop("status_short", None)
    if stable_recorded != stable_current:
        raise AdjudicationError("prepared input verification receipt differs")


def _stage_invocation(command_line: str, start_utc: str, finish_utc: str) -> dict[str, str]:
    if not command_line.strip():
        raise AdjudicationError("workflow command line is blank")
    start = parse_utc(start_utc)
    finish = parse_utc(finish_utc)
    if finish < start:
        raise AdjudicationError("workflow finish precedes its start")
    return {
        "exact_command_line": command_line,
        "start_utc": start_utc,
        "finish_utc": finish_utc,
    }


def _read_workflow_receipt(
    path: Path, *, require_semantic: bool
) -> dict[str, Any]:
    workflow = _read_json(path)
    if set(workflow) != {
        "schema_version", "tool_version", "context_prepare", "semantic_prepare",
        "finalise",
    }:
        raise AdjudicationError("workflow receipt fields differ")
    if workflow["schema_version"] != SCHEMA_VERSION or workflow[
        "tool_version"
    ] != TOOL_VERSION:
        raise AdjudicationError("workflow receipt identity differs")
    for stage in ("context_prepare", "semantic_prepare", "finalise"):
        value = workflow[stage]
        if value is None:
            continue
        if not isinstance(value, dict) or set(value) != {
            "exact_command_line", "start_utc", "finish_utc"
        }:
            raise AdjudicationError(f"workflow stage is malformed: {stage}")
        if _stage_invocation(
            str(value["exact_command_line"]), str(value["start_utc"]),
            str(value["finish_utc"]),
        ) != value:
            raise AdjudicationError(f"workflow stage values are malformed: {stage}")
    if workflow["context_prepare"] is None:
        raise AdjudicationError("context prepare workflow receipt is missing")
    if require_semantic and workflow["semantic_prepare"] is None:
        raise AdjudicationError("semantic prepare workflow receipt is missing")
    if not require_semantic and workflow["semantic_prepare"] is not None:
        raise AdjudicationError("semantic prepare workflow receipt already exists")
    if workflow["finalise"] is not None:
        raise AdjudicationError("workflow was already finalised")
    if workflow["semantic_prepare"] is not None and parse_utc(
        workflow["semantic_prepare"]["start_utc"]
    ) < parse_utc(workflow["context_prepare"]["finish_utc"]):
        raise AdjudicationError("semantic prepare precedes context prepare")
    return workflow


def _blank_review_attestation() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "adjudicator_identity": "codex-preoutput-consistency-review-v1",
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "global_review_status": GLOBAL_REVIEW_STATUS,
        "semantic_first_pass_completed": None,
        "semantic_first_pass_order": None,
        "context_second_pass_completed": None,
        "context_second_pass_order": None,
        "semantic_second_pass_completed": None,
        "semantic_second_pass_order": None,
        "similar_cases_consistency_checked": None,
        "coverage_not_inspected_until_individual_semantic_drafts_complete": None,
        "decisions_changed_to_improve_coverage": None,
        "attestation_time_utc": None,
    }


def _validate_review_attestation(
    path: Path, minimum_time: str, maximum_time: str
) -> dict[str, Any]:
    attestation = _read_json(path)
    expected_fields = set(_blank_review_attestation())
    if set(attestation) != expected_fields:
        raise AdjudicationError("review attestation fields differ")
    expected_values = {
        "schema_version": SCHEMA_VERSION,
        "adjudicator_identity": "codex-preoutput-consistency-review-v1",
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "global_review_status": GLOBAL_REVIEW_STATUS,
        "semantic_first_pass_completed": True,
        "semantic_first_pass_order": "reverse_candidate_id",
        "context_second_pass_completed": True,
        "context_second_pass_order": "reverse_candidate_id",
        "semantic_second_pass_completed": True,
        "semantic_second_pass_order": "forward_candidate_id",
        "similar_cases_consistency_checked": True,
        "coverage_not_inspected_until_individual_semantic_drafts_complete": True,
        "decisions_changed_to_improve_coverage": False,
    }
    for field, expected in expected_values.items():
        if attestation.get(field) != expected:
            raise AdjudicationError(f"review attestation is incomplete: {field}")
    timestamp = attestation.get("attestation_time_utc")
    if not isinstance(timestamp, str):
        raise AdjudicationError("review attestation timestamp is missing")
    _validate_decision_time(timestamp, "review-attestation", minimum_time, maximum_time)
    return attestation


def _copy_originals(input_directory: Path, output: Path) -> None:
    for source_name, target_name in ORIGINAL_COPIES.items():
        _write_private(output / target_name, (input_directory / source_name).read_bytes())
        if file_sha256(output / target_name) != EXPECTED_INPUT_HASHES[source_name]:
            raise AdjudicationError(f"copied original digest differs: {source_name}")


def prepare_context(
    input_directory: Path, output: Path, repository: Path, research_root: Path,
    command_line: str = "library prepare --stage context",
    start_utc: str | None = None,
) -> Path:
    """Verify immutable inputs and prepare a blank 22-case context review."""
    observed_start = start_utc or utc_now_text()
    parse_utc(observed_start)
    verification = verify_input_bundle(input_directory, repository)
    cases = _load_verified_case_data(input_directory.resolve(strict=True))
    output = _prepare_output(output, research_root)
    _write_json(output / "input_verification.json", verification)
    _write_private(output / "context_adjudication_packet.md", render_context_packet(cases))
    _write_private(
        output / "context_decisions.completed.csv",
        _render_csv(_blank_context_rows(cases), CONTEXT_FIELDS),
    )
    _write_private(output / "adjudication_conflicts.jsonl", b"")
    _write_json(output / "review_attestation.json", _blank_review_attestation())
    _copy_originals(input_directory.resolve(strict=True), output)
    workflow = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "context_prepare": _stage_invocation(
            command_line, observed_start, utc_now_text()
        ),
        "semantic_prepare": None,
        "finalise": None,
    }
    _write_json(output / "workflow_receipt.json", workflow)
    for path in output.iterdir():
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return output


def prepare_semantic(
    input_directory: Path, output: Path, repository: Path,
    research_root: Path = RESEARCH_ROOT,
    command_line: str = "library prepare --stage semantic",
    start_utc: str | None = None,
) -> Path:
    """Validate entered context decisions and prepare a blank semantic review."""
    observed_start = start_utc or utc_now_text()
    parse_utc(observed_start)
    verification = verify_input_bundle(input_directory, repository)
    output = _verify_stage_directory(output, research_root, CONTEXT_STAGE_FILES)
    cases = _load_verified_case_data(input_directory.resolve(strict=True))
    workflow = _read_workflow_receipt(
        output / "workflow_receipt.json", require_semantic=False
    )
    _verify_input_verification_copy(output / "input_verification.json", verification)
    _require_file_bytes(output / "context_adjudication_packet.md", render_context_packet(cases))
    _verify_original_copies(input_directory.resolve(strict=True), output)
    _require_file_bytes(output / "adjudication_conflicts.jsonl", b"")
    _require_file_bytes(
        output / "review_attestation.json",
        _json_document(_blank_review_attestation()),
    )
    context_rows = _read_csv(output / "context_decisions.completed.csv", CONTEXT_FIELDS)
    automatic_time = _read_json(input_directory / "run_manifest.json")["audit_created_at"]
    completed_context, context_receipts = validate_context_decisions(
        context_rows, cases, automatic_time,
        workflow["context_prepare"]["finish_utc"], observed_start,
    )
    _write_private(
        output / "context_decisions.completed.csv",
        _render_csv(completed_context, CONTEXT_FIELDS),
    )
    _write_jsonl(output / "context_clearance_receipts.jsonl", context_receipts)
    _write_private(
        output / "semantic_adjudication_packet.md",
        render_semantic_packet(cases, context_receipts),
    )
    _write_private(
        output / "semantic_decisions.completed.csv",
        _render_csv(_blank_semantic_rows(cases, context_receipts), SEMANTIC_FIELDS),
    )
    workflow["semantic_prepare"] = _stage_invocation(
        command_line, observed_start, utc_now_text()
    )
    _write_json(output / "workflow_receipt.json", workflow)
    return output


def _protocol_findings(
    attestation: dict[str, Any], conflict_count: int = 0
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "global_review_status": GLOBAL_REVIEW_STATUS,
        "formulaic_substantive_posted_status": "not_adjudicated_in_semantic_phase",
        "separate_profile_blind_historical_baseline_review_required": True,
        "current_or_hybrid_profile_output_exists_in_this_phase": False,
        "profile_execution_performed": False,
        "semantic_decisions_generated_automatically": False,
        "context_decisions_generated_automatically": False,
        "review_sequence_attestation": attestation,
        "adjudication_conflict_count": conflict_count,
    }


def _render_report(
    coverage: dict[str, Any], readiness: dict[str, Any], conflicts: Sequence[dict[str, Any]]
) -> str:
    return "\n".join([
        "# Proposed pre-output adjudication report", "",
        "These are profile-blind Codex language-model proposals pending independent review; ",
        "they are not human decisions or finally accepted receipts.", "",
        f"Eligible candidates: {coverage['eligible_candidate_count']}", "",
        "Context decisions: `" + json.dumps(
            coverage["counts_by_context_decision"], sort_keys=True
        ) + "`", "",
        "Semantic statuses: `" + json.dumps(
            coverage["counts_by_semantic_adjudication_status"], sort_keys=True
        ) + "`", "",
        "Accepted semantic tags (proposed): `" + json.dumps(
            coverage["counts_by_accepted_semantic_tag"], sort_keys=True
        ) + "`", "",
        "Accepted primary strata (proposed): `" + json.dumps(
            coverage["counts_by_accepted_primary_stratum"], sort_keys=True
        ) + "`", "",
        f"Two-pass conflict records: {len(conflicts)}", "",
        "The continuity property `formulaic_substantive_posted` was not adjudicated in ",
        "this semantic phase; a separate profile-blind baseline review is required.", "",
        f"Manual adjudication complete: {str(readiness['manual_adjudication_complete']).lower()}", "",
        "Independent review complete: false", "",
        "Sample-freeze readiness: false", "",
    ]).rstrip() + "\n"


def finalise(
    input_directory: Path, output: Path, repository: Path, command_line: str,
    start_utc: str, research_root: Path = RESEARCH_ROOT,
) -> Path:
    """Validate all proposed decisions and finalise the immutable review bundle."""
    verification = verify_input_bundle(input_directory, repository)
    output = _verify_stage_directory(output, research_root, SEMANTIC_STAGE_FILES)
    cases = _load_verified_case_data(input_directory.resolve(strict=True))
    workflow = _read_workflow_receipt(
        output / "workflow_receipt.json", require_semantic=True
    )
    if parse_utc(start_utc) < parse_utc(workflow["semantic_prepare"]["finish_utc"]):
        raise AdjudicationError("finalise precedes semantic prepare")
    _verify_input_verification_copy(output / "input_verification.json", verification)
    _require_file_bytes(output / "context_adjudication_packet.md", render_context_packet(cases))
    _verify_original_copies(input_directory.resolve(strict=True), output)
    context_rows = _read_csv(output / "context_decisions.completed.csv", CONTEXT_FIELDS)
    automatic_time = _read_json(input_directory / "run_manifest.json")["audit_created_at"]
    completed_context, context_receipts = validate_context_decisions(
        context_rows, cases, automatic_time,
        workflow["context_prepare"]["finish_utc"],
        workflow["semantic_prepare"]["start_utc"],
    )
    _require_file_bytes(
        output / "context_decisions.completed.csv",
        _render_csv(completed_context, CONTEXT_FIELDS),
    )
    _require_file_bytes(
        output / "context_clearance_receipts.jsonl",
        b"".join(canonical_json_bytes(row) + b"\n" for row in context_receipts),
    )
    _require_file_bytes(
        output / "semantic_adjudication_packet.md",
        render_semantic_packet(cases, context_receipts),
    )
    semantic_rows = _read_csv(output / "semantic_decisions.completed.csv", SEMANTIC_FIELDS)
    completed_semantic, semantic_receipts = validate_semantic_decisions(
        semantic_rows, cases, context_receipts,
        workflow["semantic_prepare"]["finish_utc"], start_utc,
    )
    attestation = _validate_review_attestation(
        output / "review_attestation.json",
        workflow["semantic_prepare"]["finish_utc"], start_utc,
    )
    conflicts = _validate_conflicts(
        output / "adjudication_conflicts.jsonl", context_receipts,
        semantic_receipts, start_utc,
    )
    coverage = build_coverage_inventory(cases, context_receipts, semantic_receipts)
    readiness = build_sampling_readiness(coverage, conflicts)
    _write_private(output / "semantic_decisions.completed.csv", _render_csv(completed_semantic, SEMANTIC_FIELDS))
    _write_jsonl(output / "semantic_classification_receipts.jsonl", semantic_receipts)
    _write_json(output / "proposed_coverage_inventory.json", coverage)
    _write_json(
        output / "protocol_findings.json",
        _protocol_findings(attestation, len(conflicts)),
    )
    _write_json(output / "sampling_readiness_after_adjudication.json", readiness)
    _write_private(output / "adjudication_report.md", _render_report(coverage, readiness, conflicts))
    finalise_finish_utc = utc_now_text()
    workflow["finalise"] = _stage_invocation(
        command_line, start_utc, finalise_finish_utc
    )
    _write_json(output / "workflow_receipt.json", workflow)
    repository_receipt = verification["repository"]
    source_hashes = {
        "validator": file_sha256(Path(__file__).resolve()),
        "protocol": file_sha256(repository / "reply_hybrid_preoutput_adjudication_protocol.md"),
        "tests": file_sha256(repository / "tests/test_reply_hybrid_preoutput_adjudication.py"),
    }
    output_hashes = {
        path.name: file_sha256(path)
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name not in {"run_manifest.json", "SHA256SUMS"}
    }
    context_counts = coverage["counts_by_context_decision"]
    semantic_counts = coverage["counts_by_semantic_adjudication_status"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "branch": repository_receipt["branch"],
        "commit": repository_receipt["head"],
        "base_commit": ACCEPTED_AUDIT_COMMIT,
        "working_tree_clean": not repository_receipt["status_short"],
        "working_tree_status_short": repository_receipt["status_short"],
        "exact_input_path": str(input_directory.resolve(strict=True)),
        "input_sha256sums_sha256": EXPECTED_INPUT_SHA256SUMS_SHA256,
        "exact_input_hashes": EXPECTED_INPUT_HASHES,
        "exact_command_line": command_line,
        "start_utc": workflow["context_prepare"]["start_utc"],
        "finish_utc": finalise_finish_utc,
        "exact_command_lines": {
            stage: workflow[stage]["exact_command_line"]
            for stage in ("context_prepare", "semantic_prepare", "finalise")
        },
        "workflow_stage_times": {
            stage: {
                "start_utc": workflow[stage]["start_utc"],
                "finish_utc": workflow[stage]["finish_utc"],
            }
            for stage in ("context_prepare", "semantic_prepare", "finalise")
        },
        "validator_source_hash": source_hashes["validator"],
        "protocol_hash": source_hashes["protocol"],
        "test_hash": source_hashes["tests"],
        "adjudicator_kind": ADJUDICATOR_KIND,
        "human_adjudicator": False,
        "adjudication_status": GLOBAL_REVIEW_STATUS,
        "codex_language_model_adjudication_performed": True,
        "evaluated_profile_model_calls": 0,
        "repository_provider_http_requests": 0,
        "posting_actions": 0,
        "production_state_changes": 0,
        "live_project_included": False,
        "receipt_counts": {
            "automatic_context": context_counts.get("automatic_clearance", 0),
            "manual_context": len(context_receipts) - context_counts.get("automatic_clearance", 0),
            "context_total": len(context_receipts),
            "semantic_total": len(semantic_receipts),
        },
        "unresolved_counts": {
            "context": context_counts.get("requires_adjudication", 0),
            "semantic": semantic_counts.get("requires_adjudication", 0),
            "conflicts": sum(bool(row["unresolved"]) for row in conflicts),
        },
        "output_hashes_before_manifest_and_sha256sums": output_hashes,
        "no_profile_execution": True,
        "final_sample_selected": False,
        "final_replay_pack_built": False,
        "execution_seed_created": False,
        "response_label_seed_created": False,
        "blind_key_created": False,
        "ready_to_freeze_sample": False,
    }
    _write_json(output / "run_manifest.json", manifest)
    expected_members = set(OUTPUT_FILES)
    final_members = list(output.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in final_members):
        raise AdjudicationError("final output contains a non-regular member")
    actual_before_sums = {
        path.name for path in final_members if path.name != "SHA256SUMS"
    }
    if actual_before_sums != expected_members:
        raise AdjudicationError(
            "final output inventory differs: "
            f"missing={sorted(expected_members - actual_before_sums)!r} "
            f"extra={sorted(actual_before_sums - expected_members)!r}"
        )
    write_sha256sums(output)
    for path in output.iterdir():
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise AdjudicationError("final output directory mode differs from 0700")
    verify_sha256sums(output)
    return output


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the two-operation offline command-line parser."""
    parser = argparse.ArgumentParser(
        description="Prepare or finalise offline profile-blind adjudication receipts."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--stage", required=True, choices=("context", "semantic"))
    prepare_parser.add_argument("--input", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--repository", type=Path, required=True)
    finalise_parser = subparsers.add_parser("finalise")
    finalise_parser.add_argument("--input", type=Path, required=True)
    finalise_parser.add_argument("--output", type=Path, required=True)
    finalise_parser.add_argument("--repository", type=Path, required=True)
    return parser


def _verify_cli_paths(args: argparse.Namespace) -> None:
    input_path = Path(os.path.abspath(args.input))
    repository = Path(os.path.abspath(args.repository))
    output = Path(os.path.abspath(args.output))
    if input_path != CORRECTED_INPUT_DIRECTORY or input_path.is_symlink():
        raise AdjudicationError("CLI input must be the exact corrected official directory")
    if repository == PRINCIPAL_PRODUCTION_REPOSITORY or repository.is_symlink():
        raise AdjudicationError("CLI repository must not be the principal production checkout")
    if repository.parent != RESEARCH_ROOT or not repository.name.startswith(
        WORKTREE_NAME_PREFIX
    ):
        raise AdjudicationError("CLI repository must be the persistent pre-output worktree")
    if output.parent != RESEARCH_ROOT or not output.name.startswith(WORKTREE_NAME_PREFIX):
        raise AdjudicationError("CLI output must be the stamped private research directory")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fail-closed offline prepare or finalise operation."""
    os.umask(0o077)
    parser = build_argument_parser()
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(parsed_argv)
    start_utc = utc_now_text()
    process_argv = [sys.executable, str(Path(__file__).resolve()), *parsed_argv]
    command_line = shlex.join(process_argv)
    try:
        _verify_cli_paths(args)
        if args.operation == "prepare" and args.stage == "context":
            result = prepare_context(
                args.input, args.output, args.repository, RESEARCH_ROOT,
                command_line, start_utc,
            )
        elif args.operation == "prepare":
            result = prepare_semantic(
                args.input, args.output, args.repository, RESEARCH_ROOT,
                command_line, start_utc,
            )
        else:
            result = finalise(
                args.input, args.output, args.repository, command_line, start_utc,
                RESEARCH_ROOT,
            )
    except (
        AdjudicationError, OSError, ValueError, KeyError, TypeError,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(2, f"pre-output adjudication refused: {exc}\n")
    print(f"Offline pre-output adjudication operation complete: {result}")
    print(
        "evaluated_profile_model_calls=0 repository_provider_http_requests=0 "
        "posting_actions=0 production_state_changes=0 ready_to_freeze_sample=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
