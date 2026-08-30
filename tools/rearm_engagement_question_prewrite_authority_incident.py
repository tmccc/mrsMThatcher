#!/usr/bin/env python3
"""Re-arm the exact failed engagement-question publication incident.

This is a stopped-service, one-shot recovery command for the 2026-08-30
``pre_write_authority_changed`` incident.  It does not infer recovery from a
generic invalid experiment state.  The command holds the production offline
lock set, requires the global runtime pause, binds the current state to an
external quiescent backup, independently reloads every experiment authority,
and permits exactly two protected-state changes.  It never calls a provider or
remote API and never performs receipt reconciliation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engagement_question_experiment as experiment  # noqa: E402
from tools import activate_remote_write_safety_protocol as activation  # noqa: E402
from tools import prepare_engagement_question_experiment as prepare  # noqa: E402
from tools.reconcile_remote_write_safety_marker import (  # noqa: E402
    _acquire_offline_instance_locks,
)


RESULT_SCHEMA_VERSION = 1
EXPECTED_EXPERIMENT_ID = "substantive-question-v1"
EXPECTED_PLAN_SHA256 = (
    "d543b32b36d6fc8f9a27b3765d9bfecfcc166f22ca1d152b5ff0ac8c3c988d06"
)
EXPECTED_PLAN_FILE_SHA256 = (
    "7a0a64586d38701a2acb4dd34e3fe728d1094a7efee28c1c54cb2d42c2ee65bd"
)
EXPECTED_CATALOGUE_SHA256 = (
    "cdce2b7f7a7ceb140e907716f1ef7392d77aab99061997bb492cddf98952f6ba"
)
EXPECTED_QUOTE_SOURCE_SHA256 = (
    "10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee"
)
EXPECTED_PAIR_ID = "pair-3ae2bf3b69a07dd3a7b605cf"
EXPECTED_PAIR_INDEX = 0
EXPECTED_MEMBER_POSITION = 1
EXPECTED_ARM = "control"
EXPECTED_QUOTE_ID = (
    "3e1a0b3d5d9dede45d7780b3032297c02d710093bb26876608d34d5ccc160586"
)
EXPECTED_APPROVED_QUESTION_SHA256 = (
    "8053bef930ac3391264253ff7c80820398408f76964d0a1f09c62bd438e1dd07"
)
EXPECTED_INVALID_REASON = {
    "code": "pre_write_authority_changed",
    "pair_id": EXPECTED_PAIR_ID,
    "member_position": EXPECTED_MEMBER_POSITION,
    "recorded_epoch": 1_788_091_216,
}
EXPECTED_CONFIG = {
    "engagement_question_experiment_enabled": True,
    "engagement_question_experiment_plan_path": (
        "engagement_question_experiment/active_plan.json"
    ),
    "engagement_question_notification_output_path": (
        "/opt/homeassistant/config/.runtime/"
        "mrs_m_thatcher_engagement_question.json"
    ),
}
CHANGED_FIELDS = (
    "engagement_question_experiment.status",
    "engagement_question_experiment.current_deferral_reason",
)
HEX64_RE = re.compile(r"[0-9a-f]{64}")


class RecoveryRefused(RuntimeError):
    """Represent one bounded fail-closed recovery refusal."""

    def __init__(
        self,
        reason: str,
        *,
        state_commit_may_have_occurred: bool = False,
    ) -> None:
        """Store a fixed, non-sensitive reason code."""

        super().__init__(reason)
        self.reason = reason
        self.state_commit_may_have_occurred = state_commit_may_have_occurred


@dataclass(frozen=True)
class _StateSnapshot:
    data: bytes
    document: dict[str, Any]
    identity: os.stat_result
    sha256: str


@dataclass(frozen=True)
class _AuthoritySnapshot:
    plan: dict[str, Any]
    catalogue: dict[str, Any]
    quote_text_by_id: dict[str, str]
    quote_source_bytes: bytes
    quote_source_sha256: str
    used_ids: frozenset[str]
    used_bytes: bytes
    used_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_result(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _strict_state_snapshot(bot: ModuleType, path: Path, *, reason: str) -> _StateSnapshot:
    try:
        present, data = bot.read_stable_owned_json_bytes_no_follow(path)
    except Exception as exc:
        raise RecoveryRefused(f"{reason}_unreadable") from exc
    if not present or data is None:
        raise RecoveryRefused(f"{reason}_missing")
    try:
        document = bot.load_strict_runtime_json(data, label="recovery state")
    except Exception as exc:
        raise RecoveryRefused(f"{reason}_malformed") from exc
    if not isinstance(document, dict):
        raise RecoveryRefused(f"{reason}_not_object")
    expected_bytes = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    if data != expected_bytes:
        raise RecoveryRefused(f"{reason}_noncanonical")
    try:
        identity = os.lstat(path)
    except OSError as exc:
        raise RecoveryRefused(f"{reason}_identity_unavailable") from exc
    return _StateSnapshot(
        data=data,
        document=document,
        identity=identity,
        sha256=_sha256(data),
    )


def _normalised_state_without_recovery(
    bot: ModuleType,
    primary: _StateSnapshot,
) -> dict[str, Any]:
    candidate = copy.deepcopy(primary.document)
    try:
        minimum = bot.require_compatible_state_reader(candidate, path=bot.STATE_FILE)
    except Exception as exc:
        raise RecoveryRefused("state_reader_incompatible") from exc
    fence = candidate.get("pending_reply_drafts")
    if (
        minimum >= bot.STATE_MINIMUM_READER_VERSION
        and fence != bot.STATE_READER_COMPATIBILITY_FENCE
    ):
        raise RecoveryRefused("state_reader_fence_mismatch")
    candidate.pop("pending_reply_drafts", None)
    candidate["minimum_reader_version"] = max(
        minimum,
        bot.STATE_MINIMUM_READER_VERSION,
    )
    recovery_events: list[dict[str, object]] = []
    try:
        normalised = bot.normalise_state_candidate(
            candidate,
            path=bot.STATE_FILE,
            recovery_events=recovery_events,
            recover_pending_identity=False,
        )
    except Exception as exc:
        raise RecoveryRefused("state_normalisation_failed") from exc
    if normalised is None or recovery_events:
        raise RecoveryRefused("state_requires_unrelated_recovery")
    try:
        round_trip = bot.state_document_for_persistence(normalised)
    except Exception as exc:
        raise RecoveryRefused("state_persistence_validation_failed") from exc
    if round_trip != primary.document:
        raise RecoveryRefused("state_would_change_outside_incident")
    return normalised


def _require_primary_backup_metadata(
    primary: _StateSnapshot,
    latest: _StateSnapshot,
) -> None:
    for snapshot in (primary, latest):
        identity = snapshot.identity
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_uid != os.geteuid()
            or stat.S_IMODE(identity.st_mode) != 0o600
        ):
            raise RecoveryRefused("primary_latest_backup_metadata_mismatch")
    if (
        latest.identity.st_uid != primary.identity.st_uid
        or latest.identity.st_gid != primary.identity.st_gid
        or latest.identity.st_size != primary.identity.st_size
        or (
            latest.identity.st_dev == primary.identity.st_dev
            and latest.identity.st_ino == primary.identity.st_ino
        )
    ):
        raise RecoveryRefused("primary_latest_backup_metadata_mismatch")


def _require_external_backup(
    bot: ModuleType,
    runtime_root: Path,
    backup_path: Path,
    primary: _StateSnapshot,
) -> _StateSnapshot:
    absolute_runtime = Path(os.path.abspath(os.fspath(runtime_root)))
    absolute_backup = Path(os.path.abspath(os.fspath(backup_path)))
    try:
        canonical_runtime = absolute_runtime.resolve(strict=True)
        canonical_backup = absolute_backup.resolve(strict=True)
    except OSError as exc:
        raise RecoveryRefused("quiescent_backup_identity_unavailable") from exc
    if canonical_backup != absolute_backup:
        raise RecoveryRefused("quiescent_backup_symlink_component")
    if (
        canonical_backup == canonical_runtime
        or canonical_runtime in canonical_backup.parents
    ):
        raise RecoveryRefused("quiescent_backup_inside_runtime")
    backup = _strict_state_snapshot(
        bot,
        absolute_backup,
        reason="quiescent_backup",
    )
    primary_mode = stat.S_IMODE(primary.identity.st_mode)
    backup_mode = stat.S_IMODE(backup.identity.st_mode)
    if (
        not stat.S_ISREG(backup.identity.st_mode)
        or backup.identity.st_nlink != 1
        or backup.identity.st_uid != os.geteuid()
        or backup_mode != 0o600
        or primary_mode != 0o600
        or backup.identity.st_uid != primary.identity.st_uid
        or backup.identity.st_gid != primary.identity.st_gid
        or backup.identity.st_size != primary.identity.st_size
        or backup.identity.st_mtime_ns != primary.identity.st_mtime_ns
    ):
        raise RecoveryRefused("quiescent_backup_metadata_mismatch")
    if (
        backup.identity.st_dev == primary.identity.st_dev
        and backup.identity.st_ino == primary.identity.st_ino
    ):
        raise RecoveryRefused("quiescent_backup_not_distinct")
    if backup.data != primary.data or backup.sha256 != primary.sha256:
        raise RecoveryRefused("quiescent_backup_content_mismatch")
    return backup


def _require_exact_incident_state(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("engagement_question_experiment")
    try:
        protected = experiment.validate_experiment_state(raw)
    except Exception as exc:
        raise RecoveryRefused("protected_experiment_state_invalid") from exc
    expected_scalars = {
        "schema_version": 1,
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "active_plan_sha256": EXPECTED_PLAN_SHA256,
        "status": "invalid",
        "current_pair_index": 0,
        "active_pair_id": EXPECTED_PAIR_ID,
        "next_pair_member_position": 1,
        "completed_pair_count": 0,
        "last_experimental_publication_local_date": None,
        "treatment_publication_count": 0,
    }
    if any(protected.get(key) != value for key, value in expected_scalars.items()):
        raise RecoveryRefused("incident_state_identity_mismatch")
    if protected.get("confirmed_publications") != []:
        raise RecoveryRefused("incident_publications_not_empty")
    if protected.get("treatment_notification_identities") != []:
        raise RecoveryRefused("incident_notifications_not_empty")
    if protected.get("current_deferral_reason") != EXPECTED_INVALID_REASON:
        raise RecoveryRefused("incident_deferral_mismatch")
    return protected


def _require_clean_transport(bot: ModuleType, directory_fd: int) -> None:
    try:
        protocol_active = bot.remote_write_safety_protocol_is_active()
    except Exception as exc:
        raise RecoveryRefused("remote_write_safety_protocol_unavailable") from exc
    if not protocol_active:
        raise RecoveryRefused("remote_write_safety_protocol_inactive")
    try:
        activation._require_clean_state(directory_fd)
    except Exception as exc:
        raise RecoveryRefused("transport_namespace_not_clean") from exc
    try:
        if bot.ambiguous_remote_post_is_blocking():
            raise RecoveryRefused("remote_write_barrier_present")
        bot.block_if_ambiguous_remote_post()
    except RecoveryRefused:
        raise
    except Exception as exc:
        raise RecoveryRefused("remote_write_barrier_present") from exc


def _require_exact_config(bot: ModuleType) -> None:
    try:
        overrides = bot.load_validated_local_config_overrides()
    except Exception as exc:
        raise RecoveryRefused("local_config_unreadable_or_invalid") from exc
    if not isinstance(overrides, dict) or any(
        overrides.get(key) != value for key, value in EXPECTED_CONFIG.items()
    ):
        raise RecoveryRefused("engagement_config_mismatch")


def _require_explicit_global_pause(
    bot: ModuleType,
    *,
    unreadable_reason: str,
    inactive_reason: str,
) -> tuple[bytes, tuple[object, ...]]:
    """Require one stable valid control file with literal ``pause_all: true``."""

    try:
        document, signature = bot._read_stable_runtime_control()
        control = bot.load_strict_runtime_json(
            document,
            label="runtime control",
            parse_floats_as_decimal=True,
        )
        control = bot.validate_control_document(control)
    except Exception as exc:
        raise RecoveryRefused(unreadable_reason) from exc
    if type(control.get("pause_all")) is not bool or control["pause_all"] is not True:
        raise RecoveryRefused(inactive_reason)
    return document, signature


def _load_authority(bot: ModuleType, runtime_root: Path) -> _AuthoritySnapshot:
    try:
        plan, catalogue, quote_text_by_id = (
            bot.load_engagement_question_runtime_plan()
        )
    except Exception as exc:
        raise RecoveryRefused("experiment_authority_unavailable") from exc
    try:
        (
            independently_loaded_quotes,
            _quote_source_lines,
            quote_source_bytes,
        ) = prepare.canonical_quote_source(runtime_root)
    except Exception as exc:
        raise RecoveryRefused("quote_source_unavailable_or_invalid") from exc
    quote_source_sha256 = _sha256(quote_source_bytes)
    if quote_source_sha256 != EXPECTED_QUOTE_SOURCE_SHA256:
        raise RecoveryRefused("quote_source_hash_mismatch")
    if independently_loaded_quotes != quote_text_by_id:
        raise RecoveryRefused("quote_source_reload_mismatch")
    if (
        plan.get("experiment_id") != EXPECTED_EXPERIMENT_ID
        or plan.get("plan_kind") != "live"
        or plan.get("plan_sha256") != EXPECTED_PLAN_SHA256
        or plan.get("approved_catalogue_sha256") != EXPECTED_CATALOGUE_SHA256
        or plan.get("pair_count") != 30
        or not isinstance(plan.get("pairs"), list)
        or len(plan["pairs"]) != 30
    ):
        raise RecoveryRefused("plan_identity_mismatch")
    plan_path = runtime_root / EXPECTED_CONFIG[
        "engagement_question_experiment_plan_path"
    ]
    catalogue_path = (
        runtime_root
        / "engagement_question_experiment"
        / "approved_question_catalogue.json"
    )
    try:
        if experiment.sha256_file(plan_path) != EXPECTED_PLAN_FILE_SHA256:
            raise RecoveryRefused("plan_file_hash_mismatch")
        if experiment.sha256_file(catalogue_path) != EXPECTED_CATALOGUE_SHA256:
            raise RecoveryRefused("catalogue_file_hash_mismatch")
    except RecoveryRefused:
        raise
    except Exception as exc:
        raise RecoveryRefused("authority_file_hash_unavailable") from exc
    pairs = plan["pairs"]
    members = [member for pair in pairs for member in pair.get("members", [])]
    quote_ids = [member.get("quote_id") for member in members]
    if (
        len(members) != 60
        or len(set(quote_ids)) != 60
        or any(type(quote_id) is not str for quote_id in quote_ids)
    ):
        raise RecoveryRefused("plan_roster_mismatch")
    pair = pairs[EXPECTED_PAIR_INDEX]
    if (
        pair.get("pair_id") != EXPECTED_PAIR_ID
        or not isinstance(pair.get("members"), list)
        or len(pair["members"]) != 2
    ):
        raise RecoveryRefused("plan_pair_mismatch")
    member = pair["members"][EXPECTED_MEMBER_POSITION - 1]
    if (
        member.get("position") != EXPECTED_MEMBER_POSITION
        or member.get("arm") != EXPECTED_ARM
        or member.get("quote_id") != EXPECTED_QUOTE_ID
        or member.get("approved_question_sha256")
        != EXPECTED_APPROVED_QUESTION_SHA256
    ):
        raise RecoveryRefused("plan_member_mismatch")
    try:
        used_ids, used_bytes, used_sha256 = prepare.capture_used_history(
            runtime_root
        )
    except Exception as exc:
        raise RecoveryRefused("used_history_unavailable_or_invalid") from exc
    if set(quote_ids) & used_ids:
        raise RecoveryRefused("planned_quote_already_used")
    return _AuthoritySnapshot(
        plan=copy.deepcopy(plan),
        catalogue=copy.deepcopy(catalogue),
        quote_text_by_id=copy.deepcopy(quote_text_by_id),
        quote_source_bytes=quote_source_bytes,
        quote_source_sha256=quote_source_sha256,
        used_ids=frozenset(used_ids),
        used_bytes=used_bytes,
        used_sha256=used_sha256,
    )


def _transformed_state(
    bot: ModuleType,
    state: dict[str, Any],
    primary_document: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    transformed = copy.deepcopy(state)
    protected = _require_exact_incident_state(transformed)
    protected["status"] = "active"
    protected["current_deferral_reason"] = None
    transformed["engagement_question_experiment"] = protected
    try:
        experiment.validate_experiment_state(protected, plan=plan)
        member, reason = experiment.member_for_current_opportunity(
            plan,
            protected,
            current_epoch=EXPECTED_INVALID_REASON["recorded_epoch"],
        )
    except Exception as exc:
        raise RecoveryRefused("rearmed_state_validation_failed") from exc
    if (
        reason is not None
        or not isinstance(member, dict)
        or member.get("pair_id") != EXPECTED_PAIR_ID
        or member.get("position") != EXPECTED_MEMBER_POSITION
        or member.get("arm") != EXPECTED_ARM
        or member.get("quote_id") != EXPECTED_QUOTE_ID
    ):
        raise RecoveryRefused("rearmed_member_mismatch")
    expected_document = copy.deepcopy(primary_document)
    expected_protected = expected_document["engagement_question_experiment"]
    expected_protected["status"] = "active"
    expected_protected["current_deferral_reason"] = None
    try:
        persisted = bot.state_document_for_persistence(transformed)
    except Exception as exc:
        raise RecoveryRefused("rearmed_state_persistence_invalid") from exc
    if persisted != expected_document:
        raise RecoveryRefused("recovery_changes_unrelated_state")
    return transformed, expected_document


def _authority_equal(first: _AuthoritySnapshot, second: _AuthoritySnapshot) -> bool:
    return bool(
        first.plan == second.plan
        and first.catalogue == second.catalogue
        and first.quote_text_by_id == second.quote_text_by_id
        and first.quote_source_bytes == second.quote_source_bytes
        and first.quote_source_sha256 == second.quote_source_sha256
        and first.used_ids == second.used_ids
        and first.used_bytes == second.used_bytes
        and first.used_sha256 == second.used_sha256
    )


def _result(
    *,
    operation: str,
    before_sha256: str,
    after_sha256: str,
    used_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "operation": operation,
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "plan_sha256": EXPECTED_PLAN_SHA256,
        "pair_id": EXPECTED_PAIR_ID,
        "member_position": EXPECTED_MEMBER_POSITION,
        "arm": EXPECTED_ARM,
        "quote_id": EXPECTED_QUOTE_ID,
        "prior_reason": EXPECTED_INVALID_REASON["code"],
        "prior_reason_epoch": EXPECTED_INVALID_REASON["recorded_epoch"],
        "state_before_sha256": before_sha256,
        "state_after_sha256": after_sha256,
        "used_history_sha256": used_sha256,
        "confirmed_publication_count": 0,
        "completed_pair_count": 0,
        "treatment_publication_count": 0,
        "changed_fields": list(CHANGED_FIELDS),
        "quiescent_backup_verified": True,
        "instance_lock_verified": True,
        "global_pause_verified": True,
        "transport_clean_verified": True,
        "network_requests_made": 0,
    }


def execute_recovery(args: argparse.Namespace, *, bot: ModuleType) -> dict[str, Any]:
    """Check or perform the exact incident-bound stopped-state recovery."""

    runtime_root = Path(args.runtime_root).resolve(strict=False)
    if runtime_root != Path(bot.BASE_DIR).resolve(strict=False):
        raise RecoveryRefused("runtime_root_mismatch")
    if not args.confirm_supervisor_stopped:
        raise RecoveryRefused("supervisor_stop_not_confirmed")
    if not args.confirm_global_pause_acknowledged:
        raise RecoveryRefused("global_pause_ack_not_confirmed")
    if not HEX64_RE.fullmatch(str(args.expected_state_sha256)):
        raise RecoveryRefused("expected_state_sha256_invalid")
    production_root = Path(bot.PRODUCTION_BASE_DIR).resolve(strict=False)
    test_process = bool(
        os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("MRS_TEST_MODE", "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )
    if args.apply and test_process and (
        runtime_root == production_root or production_root in runtime_root.parents
    ):
        raise RecoveryRefused("test_process_production_apply_refused")

    locks = None
    state_commit_attempted = False
    try:
        try:
            locks = _acquire_offline_instance_locks(runtime_root)
        except Exception as exc:
            raise RecoveryRefused("offline_instance_lock_unavailable") from exc
        try:
            locks.revalidate()
        except Exception as exc:
            raise RecoveryRefused("offline_instance_lock_lost") from exc
        identity = locks.project_identity
        if (
            int(identity.st_dev) != int(args.expected_project_device)
            or int(identity.st_ino) != int(args.expected_project_inode)
        ):
            raise RecoveryRefused("runtime_identity_mismatch")
        pause_snapshot = _require_explicit_global_pause(
            bot,
            unreadable_reason="global_pause_unreadable",
            inactive_reason="global_pause_not_active",
        )
        _require_exact_config(bot)
        _require_clean_transport(bot, locks.project_fd)

        primary = _strict_state_snapshot(bot, bot.STATE_FILE, reason="primary_state")
        latest = _strict_state_snapshot(
            bot,
            bot.STATE_FILE.with_name(f"{bot.STATE_FILE.name}.bak1"),
            reason="latest_state_backup",
        )
        _require_primary_backup_metadata(primary, latest)
        if primary.data != latest.data:
            raise RecoveryRefused("primary_latest_backup_diverge")
        if primary.sha256 != args.expected_state_sha256:
            raise RecoveryRefused("stopped_state_sha256_mismatch")
        _require_external_backup(
            bot,
            runtime_root,
            Path(args.quiescent_state_backup),
            primary,
        )
        state = _normalised_state_without_recovery(bot, primary)
        _require_exact_incident_state(state)
        authority = _load_authority(bot, runtime_root)
        transformed, expected_document = _transformed_state(
            bot,
            state,
            primary.document,
            authority.plan,
        )

        # Repeat every mutable authority immediately before the only commit.
        locks.revalidate()
        current_primary = _strict_state_snapshot(
            bot,
            bot.STATE_FILE,
            reason="primary_state",
        )
        current_latest = _strict_state_snapshot(
            bot,
            bot.STATE_FILE.with_name(f"{bot.STATE_FILE.name}.bak1"),
            reason="latest_state_backup",
        )
        _require_primary_backup_metadata(current_primary, current_latest)
        if current_primary.data != primary.data or current_latest.data != primary.data:
            raise RecoveryRefused("state_changed_before_commit")
        _require_external_backup(
            bot,
            runtime_root,
            Path(args.quiescent_state_backup),
            primary,
        )
        repeated_pause_snapshot = _require_explicit_global_pause(
            bot,
            unreadable_reason="global_pause_unreadable_before_commit",
            inactive_reason="global_pause_changed_before_commit",
        )
        if repeated_pause_snapshot != pause_snapshot:
            raise RecoveryRefused("global_pause_changed_before_commit")
        _require_exact_config(bot)
        _require_clean_transport(bot, locks.project_fd)
        repeated_authority = _load_authority(bot, runtime_root)
        if not _authority_equal(authority, repeated_authority):
            raise RecoveryRefused("experiment_authority_changed_before_commit")
        locks.revalidate()

        if args.check:
            return _result(
                operation="checked",
                before_sha256=primary.sha256,
                after_sha256=_sha256(
                    json.dumps(
                        expected_document,
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8")
                ),
                used_sha256=authority.used_sha256,
            )

        state_commit_attempted = True
        try:
            bot.save_state(transformed, durable=True)
        except Exception as exc:
            raise RecoveryRefused("durable_state_commit_failed") from exc

        committed = _strict_state_snapshot(
            bot,
            bot.STATE_FILE,
            reason="committed_state",
        )
        committed_latest = _strict_state_snapshot(
            bot,
            bot.STATE_FILE.with_name(f"{bot.STATE_FILE.name}.bak1"),
            reason="committed_latest_backup",
        )
        _require_primary_backup_metadata(committed, committed_latest)
        preserved_invalid = _strict_state_snapshot(
            bot,
            bot.STATE_FILE.with_name(f"{bot.STATE_FILE.name}.bak2"),
            reason="preserved_invalid_backup",
        )
        if committed.data != committed_latest.data:
            raise RecoveryRefused("postcommit_primary_latest_backup_diverge")
        if committed.document != expected_document:
            raise RecoveryRefused("postcommit_document_mismatch")
        if preserved_invalid.data != primary.data:
            raise RecoveryRefused("postcommit_invalid_backup_not_preserved")
        try:
            experiment.validate_experiment_state(
                committed.document["engagement_question_experiment"],
                plan=authority.plan,
            )
        except Exception as exc:
            raise RecoveryRefused("postcommit_experiment_state_invalid") from exc
        post_authority = _load_authority(bot, runtime_root)
        if not _authority_equal(authority, post_authority):
            raise RecoveryRefused("postcommit_authority_changed")
        postcommit_pause_snapshot = _require_explicit_global_pause(
            bot,
            unreadable_reason="global_pause_unreadable_after_commit",
            inactive_reason="global_pause_changed_after_commit",
        )
        if postcommit_pause_snapshot != pause_snapshot:
            raise RecoveryRefused("global_pause_changed_after_commit")
        _require_exact_config(bot)
        _require_external_backup(
            bot,
            runtime_root,
            Path(args.quiescent_state_backup),
            primary,
        )
        _require_clean_transport(bot, locks.project_fd)
        locks.revalidate()
        return _result(
            operation="rearmed",
            before_sha256=primary.sha256,
            after_sha256=committed.sha256,
            used_sha256=authority.used_sha256,
        )
    except RecoveryRefused as exc:
        if state_commit_attempted and not exc.state_commit_may_have_occurred:
            raise RecoveryRefused(
                exc.reason,
                state_commit_may_have_occurred=True,
            ) from exc
        raise
    except Exception as exc:
        if state_commit_attempted:
            raise RecoveryRefused(
                "unexpected_error_after_state_commit_attempt",
                state_commit_may_have_occurred=True,
            ) from exc
        raise
    finally:
        if locks is not None:
            locks.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit stopped-recovery command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-project-device", type=int, required=True)
    parser.add_argument("--expected-project-inode", type=int, required=True)
    parser.add_argument("--quiescent-state-backup", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--confirm-supervisor-stopped", action="store_true")
    parser.add_argument(
        "--confirm-global-pause-acknowledged",
        action="store_true",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def _duplicate_long_option(argv: Sequence[str]) -> bool:
    seen: set[str] = set()
    for value in argv:
        if not value.startswith("--"):
            continue
        option = value.split("=", 1)[0]
        if option in seen:
            return True
        seen.add(option)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded check or one-shot exact incident recovery."""

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _duplicate_long_option(raw_argv):
        print(
            _canonical_result(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "operation": "refused",
                    "reason": "duplicate_option",
                    "exception_class": "RecoveryRefused",
                    "state_commit_may_have_occurred": False,
                    "network_requests_made": 0,
                }
            )
        )
        return 2
    args = build_parser().parse_args(raw_argv)
    try:
        import mrsMThatcher2 as bot

        result = execute_recovery(args, bot=bot)
    except RecoveryRefused as exc:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "operation": "refused",
            "reason": exc.reason,
            "exception_class": type(exc).__name__,
            "state_commit_may_have_occurred": (
                exc.state_commit_may_have_occurred
            ),
            "network_requests_made": 0,
        }
        print(_canonical_result(result))
        return 1
    except Exception as exc:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "operation": "refused",
            "reason": "unexpected_error",
            "exception_class": type(exc).__name__,
            "state_commit_may_have_occurred": False,
            "network_requests_made": 0,
        }
        print(_canonical_result(result))
        return 1
    print(_canonical_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
