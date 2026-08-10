#!/usr/bin/env python3
"""Prepare or execute a blinded current-versus-compact reply calibration.

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
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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


RUNNER_VERSION = "reply-prompt-calibration-v1"
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


def file_sha256(path: Path) -> str:
    """Hash a file without following any path supplied by its contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def verify_replay_pack(pack_path: Path) -> dict[str, Any]:
    """Verify frozen provenance and return only the six joined calibration cases."""
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
    _require_equal("model input count", len(model_rows), 48)
    _require_equal("historical baseline count", len(historical_rows), 48)
    _require_equal("recent-account-reply count", len(recent_rows), 48)
    _require_equal("calibration case count", len(calibration_rows), 6)
    models = _index_unique(model_rows, "model inputs")
    historical = _index_unique(historical_rows, "historical baselines")
    recent = _index_unique(recent_rows, "recent account replies")
    calibration = _index_unique(calibration_rows, "calibration cases")
    _require_equal("model/historical candidate IDs", set(models), set(historical))
    _require_equal("model/recent candidate IDs", set(models), set(recent))
    if not set(calibration).issubset(models):
        raise CalibrationError("calibration cases do not join exactly to the frozen inputs")
    strata = Counter(row.get("final_stratum") for row in calibration_rows)
    if set(strata) != REQUIRED_STRATA or any(count != 1 for count in strata.values()):
        raise CalibrationError("calibration cases must contain exactly one case in each final stratum")

    cases: list[dict[str, Any]] = []
    for candidate_id, selection in sorted(calibration.items()):
        model_row = models[candidate_id]
        recent_row = recent[candidate_id]
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
        cases.append({
            "candidate_id": candidate_id,
            "stratum": selection["final_stratum"],
            "context": validated_context,
            "recent_replies": list(recent_text),
            "historical": historical[candidate_id],
        })

    strategy_hash = file_sha256(PROJECT_ROOT / "reply_strategy.py")
    _require_equal("current reply_strategy.py SHA-256", strategy_hash, FROZEN_REPLY_STRATEGY_SHA256)
    return {
        "pack_path": pack,
        "pack_sha256": pack_sha256,
        "checksums": checksums,
        "manifest": manifest,
        "replay_plan": plan,
        "leakage_audit": leakage,
        "cases": cases,
        "reply_strategy_sha256": strategy_hash,
    }


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
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "blind_seed": blind_seed,
        "pack_sha256": pack_data["pack_sha256"],
        "planned_variants": list(VARIANTS),
        "planned_pipeline_executions": 12,
        "order_derivation": "sha256(blind-seed, pack-sha256, candidate-id, variant)",
        "executions": rows,
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


def prepare_output_directory(output_path: Path, pack_path: Path) -> Path:
    """Create or accept only an empty private directory outside protected trees."""
    output = output_path.resolve(strict=False)
    pack = pack_path.resolve(strict=True)
    worktree = PROJECT_ROOT.resolve(strict=True)
    if output_inside(output, pack):
        raise CalibrationError("output must not be inside the replay pack")
    if output_inside(output, worktree):
        raise CalibrationError("output must not be inside the Git worktree")
    if output.exists():
        if not output.is_dir():
            raise CalibrationError("output path exists and is not a directory")
        if any(output.iterdir()):
            raise CalibrationError("output directory must be empty")
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
    atomic_bytes(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
    )


def write_text(path: Path, value: str) -> None:
    atomic_bytes(path, value.encode("utf-8"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    atomic_bytes(path, content)


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


def _historical_public_output(record: dict[str, Any]) -> str | None:
    reply = record.get("historical_reply")
    return reply if isinstance(reply, str) and reply else None


def build_blind_review(
    cases: list[dict[str, Any]],
    assignments: dict[str, dict[str, str]],
    outputs: dict[tuple[str, str], str | None] | None,
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
            value = None if outputs is None else outputs.get((candidate_id, source))
            rendered = "" if outputs is None else (value if value is not None else "NO REPLY")
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


def validation_report(
    pack_data: dict[str, Any], manifests: dict[str, dict[str, Any]]
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
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        "planned_pipeline_executions": 12,
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
        "provider_endpoint": DEFAULT_XAI_BASE,
        "provider_endpoint_locked_to_xai": True,
        "posting_enabled": False,
        "search_enabled": False,
        "tools_enabled": False,
        "media_enabled": False,
    }


def calibration_report_markdown(mode: str, model_calls: int, http_requests: int) -> str:
    return "\n".join([
        "# Reply prompt calibration report",
        "",
        f"Mode: {mode}",
        "",
        "Calibration cases: 6",
        "",
        "Variants: 2",
        "",
        "Planned pipeline executions: 12",
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


def validate_only_run(args: argparse.Namespace, pack_data: dict[str, Any]) -> Path:
    verify_current_production_objects()
    manifests = profile_manifests()
    repository = build_repository()
    plan = build_execution_plan(pack_data, manifests, args.blind_seed)
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
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        "planned_pipeline_executions": 12,
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
    write_json(output / "validation_report.json", validation_report(pack_data, manifests))
    write_jsonl(output / "prompt_preview_receipts.jsonl", previews)
    write_text(output / "blind_review.md", markdown)
    write_text(output / "blind_review.csv", csv_text)
    write_json(output / "blind_key.json", blind_key)
    write_text(output / "calibration_report.md", calibration_report_markdown("validate-only", 0, 0))
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


class PromptReceiptTransport:
    """Record exact request identities while delegating safety and cost handling."""

    def __init__(
        self,
        delegate: pilot.PilotTransport,
        receipt_path: Path,
        *,
        model: str,
    ) -> None:
        self.delegate = delegate
        self.receipt_path = receipt_path
        self.model = model
        self.sequence = 0
        self.identity: dict[str, Any] = {}
        self.case_identity = ""

    def set_case(self, identity: dict[str, Any]) -> None:
        self.identity = dict(identity)
        identity_hash = value_sha256(identity)
        self.case_identity = (
            f"{identity['candidate_id']}:{identity['variant']}:{identity_hash}"
        )
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
        receipt = {
            **self.identity,
            "stage": stage,
            "call_sequence": self.sequence,
            "logical_call_id": logical_call_id,
            "system_prompt_sha256": sha256_text(kwargs["system_prompt"]),
            "user_prompt_sha256": sha256_text(kwargs["user_prompt"]),
            "request_hash": request_hash,
            "temperature": 0,
            "media_transmitted": False,
        }
        append_jsonl(self.receipt_path, receipt)
        return self.delegate(**kwargs)


def execute_run(args: argparse.Namespace, pack_data: dict[str, Any], api_key: str) -> Path:
    verify_current_production_objects()
    manifests = profile_manifests()
    repository = build_repository()
    plan = build_execution_plan(pack_data, manifests, args.blind_seed)
    output = prepare_output_directory(args.output, pack_data["pack_path"])
    write_json(output / "pack_verification.json", public_pack_verification(pack_data))
    write_json(output / "profile_manifests.json", manifests)
    write_json(output / "execution_plan.json", plan)
    write_text(output / "prompt_receipts.jsonl", "")
    write_text(output / "pipeline_results.jsonl", "")
    write_text(output / "pipeline_audits.jsonl", "")
    raw_responses = output / "raw_responses"
    raw_responses.mkdir(mode=0o700)

    http = CountingHTTP()
    metadata = pilot.fetch_model_metadata(
        api_key=api_key,
        base_url=args.xai_base,
        model=args.model,
        get=http.get,
    )
    write_json(output / "provider_model_metadata.json", metadata)
    ledger = pilot.PilotLedger(
        output / "cost_ledger.json",
        model=args.model,
        hard_limit_usd=args.hard_limit_usd,
        run_version=RUNNER_VERSION,
    )
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
    receipt_transport = PromptReceiptTransport(delegate, output / "prompt_receipts.jsonl", model=args.model)
    config = pilot.strategy_config(
        args.model, PROJECT_ROOT / "semantic_alignment_research/quote_research_full_001"
    )
    cases = {case["candidate_id"]: case for case in pack_data["cases"]}
    results: dict[tuple[str, str], str | None] = {}
    model_calls = 0
    for planned in plan["executions"]:
        case = cases[planned["candidate_id"]]
        variant = planned["variant"]
        identity = {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "prompt_profile_version": manifests[variant]["profile_version"],
            "replay_pack_sha256": pack_data["pack_sha256"],
            "profile_manifest_sha256": manifests[variant]["manifest_sha256"],
            "validated_context_sha256": value_sha256(case["context"]),
            "recent_replies_sha256": value_sha256(case["recent_replies"]),
            "model": args.model,
        }
        receipt_transport.set_case(identity)
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
        model_calls += result.model_call_count
        public_reply = str(result.reply) if result.reply is not None else None
        results[(case["candidate_id"], variant)] = public_reply
        append_jsonl(output / "pipeline_results.jsonl", {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "status": result.status,
            "reason": result.reason,
            "public_reply": public_reply,
            "model_call_count": result.model_call_count,
            "revision_count": result.revision_count,
            "pipeline_metadata": (
                result.reply.pipeline_metadata if result.reply is not None else None
            ),
        })
        append_jsonl(output / "pipeline_audits.jsonl", {
            "candidate_id": case["candidate_id"],
            "stratum": case["stratum"],
            "variant": variant,
            "audit": list(result.audit),
        })

    for case in pack_data["cases"]:
        results[(case["candidate_id"], "historical")] = _historical_public_output(case["historical"])
    assignments = blind_assignments(
        pack_data["cases"], blind_seed=args.blind_seed, pack_sha256=pack_data["pack_sha256"]
    )
    markdown, csv_text = build_blind_review(pack_data["cases"], assignments, results)
    write_text(output / "blind_review.md", markdown)
    write_text(output / "blind_review.csv", csv_text)
    write_json(output / "blind_key.json", {
        "schema_version": 1,
        "derivation": "sha256(blind-seed, pack-sha256, candidate-id, source)",
        "assignments": assignments,
    })
    write_text(
        output / "calibration_report.md",
        calibration_report_markdown("execute", model_calls, http.requests),
    )
    write_json(output / "run_manifest.json", {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "mode": "execute",
        "model": args.model,
        "xai_base": args.xai_base,
        "blind_seed": args.blind_seed,
        "pack_sha256": pack_data["pack_sha256"],
        "pack_cases": 48,
        "calibration_cases": 6,
        "planned_variants": 2,
        "planned_pipeline_executions": 12,
        "model_calls_performed": model_calls,
        "http_requests_performed": http.requests,
        "hard_limit_usd": args.hard_limit_usd,
        "posting_enabled": False,
        "search_enabled": False,
        "tools_enabled": False,
        "media_enabled": False,
    })
    enforce_private_permissions(output)
    assert_no_secret(output, api_key)
    write_sha256sums(output)
    enforce_private_permissions(output)
    verify_output_sha256sums(output)
    return output


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
    if args.mode == "validate-only":
        return None
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
    pack_data = verify_replay_pack(args.pack)
    if args.mode == "validate-only":
        return validate_only_run(args, pack_data)
    assert api_key is not None
    return execute_run(args, pack_data, api_key)


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
