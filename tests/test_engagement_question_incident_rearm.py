from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

import engagement_question_experiment as experiment
import mrsMThatcher2 as bot
from tests import test_engagement_question_experiment as engagement_tests
from tools import rearm_engagement_question_prewrite_authority_incident as rearm


INCIDENT_EPOCH = 1_777_777_777
REAL_REQUIRE_CLEAN_TRANSPORT = rearm._require_clean_transport
REAL_LOAD_AUTHORITY = rearm._load_authority
REAL_REQUIRE_EXPLICIT_GLOBAL_PAUSE = rearm._require_explicit_global_pause


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _state_json_bytes(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _replace_backup_with_symlink(runtime: "IncidentRuntime") -> None:
    target = runtime.external_backup.with_name("actual-bot-state-backup.json")
    shutil.copy2(runtime.external_backup, target)
    runtime.external_backup.unlink()
    runtime.external_backup.symlink_to(target)


def _replace_backup_parent_with_symlink(runtime: "IncidentRuntime") -> None:
    linked_parent = runtime.external_backup.parent
    real_parent = linked_parent.with_name("actual-quiescent-backup")
    real_parent.mkdir()
    shutil.copy2(runtime.external_backup, real_parent / runtime.external_backup.name)
    runtime.external_backup.unlink()
    linked_parent.rmdir()
    linked_parent.symlink_to(real_parent, target_is_directory=True)


def _semantic_differences(
    before: object,
    after: object,
    *,
    prefix: str = "",
) -> set[str]:
    if type(before) is not type(after):
        return {prefix}
    if isinstance(before, dict):
        changed: set[str] = set()
        for key in set(before) | set(after):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                changed.add(path)
            else:
                changed.update(
                    _semantic_differences(before[key], after[key], prefix=path)
                )
        return changed
    if isinstance(before, list):
        if len(before) != len(after):
            return {prefix}
        changed = set()
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            changed.update(
                _semantic_differences(
                    left,
                    right,
                    prefix=f"{prefix}[{index}]",
                )
            )
        return changed
    return set() if before == after else {prefix}


def _namespace_snapshot(root: Path, external_backup: Path) -> tuple:
    observed = []
    paths = sorted(root.rglob("*")) + [external_backup]
    for path in paths:
        metadata = os.lstat(path)
        data = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
        observed.append(
            (
                str(path),
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_mtime_ns,
                data,
            )
        )
    return tuple(observed)


@dataclass
class IncidentRuntime:
    root: Path
    external_backup: Path
    state_path: Path
    bak1_path: Path
    plan_path: Path
    catalogue_path: Path
    used_path: Path
    control_path: Path
    config_path: Path
    quote_source_path: Path
    state: dict
    experiment_state: dict
    plan: dict
    catalogue: dict
    quote_text_by_id: dict[str, str]
    member: dict
    notification_path: str
    state_bytes: bytes

    def arguments(self, *, apply: bool) -> argparse.Namespace:
        root_stat = os.stat(self.root)
        return argparse.Namespace(
            runtime_root=self.root,
            expected_project_device=root_stat.st_dev,
            expected_project_inode=root_stat.st_ino,
            quiescent_state_backup=self.external_backup,
            expected_state_sha256=_sha256(self.state_bytes),
            confirm_supervisor_stopped=True,
            confirm_global_pause_acknowledged=True,
            check=not apply,
            apply=apply,
        )

    def call(self, *, apply: bool) -> dict:
        return rearm.execute_recovery(self.arguments(apply=apply), bot=bot)


@pytest.fixture
def incident_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> IncidentRuntime:
    bundle = engagement_tests._live_synthetic_bundle(
        engagement_tests.synthetic_plan_bundle.__wrapped__()
    )
    plan = copy.deepcopy(bundle["plan"])
    catalogue = copy.deepcopy(bundle["catalogue"])
    quote_text_by_id = dict(bundle["quote_text_by_id"])
    experiment_state = experiment.new_experiment_state(plan)
    experiment.start_next_pair(experiment_state, plan)
    experiment.mark_experiment_invalid(
        experiment_state,
        code="pre_write_authority_changed",
        recorded_epoch=INCIDENT_EPOCH,
    )
    member = {
        **plan["pairs"][0]["members"][0],
        "pair_id": plan["pairs"][0]["pair_id"],
    }
    assert member["position"] == 1
    assert experiment_state["confirmed_publications"] == []

    state = bot.default_state()
    state.update(
        {
            "minimum_reader_version": (
                bot.ENGAGEMENT_QUESTION_EXPERIMENT_STATE_MINIMUM_READER_VERSION
            ),
            "pending_reply_drafts": copy.deepcopy(
                bot.STATE_READER_COMPATIBILITY_FENCE
            ),
            "engagement_question_experiment": copy.deepcopy(experiment_state),
        }
    )
    state_bytes = _state_json_bytes(state)
    root = tmp_path / "runtime"
    root.mkdir()
    state_path = root / "bot_state.json"
    bak1_path = root / "bot_state.json.bak1"
    external_backup = tmp_path / "quiescent-backup" / "bot_state.json"
    _write_private(state_path, state_bytes)
    shutil.copy2(state_path, bak1_path)
    external_backup.parent.mkdir()
    shutil.copy2(state_path, external_backup)

    plan_path = root / "engagement_question_experiment" / "active_plan.json"
    catalogue_path = (
        root
        / "engagement_question_experiment"
        / "approved_question_catalogue.json"
    )
    plan_bytes = _json_bytes(plan)
    catalogue_bytes = experiment.canonical_json_bytes(catalogue)
    _write_private(plan_path, plan_bytes)
    _write_private(catalogue_path, catalogue_bytes)

    quote_source_path = root / "mrsMThatcher.txt"
    quote_source = (
        "\n".join(quote_text_by_id.values()) + "\n"
    ).encode("utf-8")
    _write_private(quote_source_path, quote_source)
    used_path = root / "lines_used.json"
    _write_private(
        used_path,
        experiment.canonical_used_history_file_bytes([]),
    )

    notification_path = str(tmp_path / "DO_NOT_EXPOSE_CONFIG_MARKER.json")
    config_path = root / "mrsMThatcher.local.json"
    _write_private(
        config_path,
        _json_bytes(
            {
                "engagement_question_experiment_enabled": True,
                "engagement_question_experiment_plan_path": (
                    "engagement_question_experiment/active_plan.json"
                ),
                "engagement_question_notification_output_path": (
                    notification_path
                ),
            }
        ),
    )
    control_path = root / "mrsMThatcher.control.json"
    _write_private(control_path, _json_bytes({"pause_all": True}))
    _write_private(
        root / "mrsMThatcher.lock",
        f"pid={os.getpid()}\n".encode("ascii"),
    )

    expected_deferral = copy.deepcopy(
        experiment_state["current_deferral_reason"]
    )
    expected_config = {
        "engagement_question_experiment_enabled": True,
        "engagement_question_experiment_plan_path": (
            "engagement_question_experiment/active_plan.json"
        ),
        "engagement_question_notification_output_path": notification_path,
    }
    monkeypatch.setattr(rearm, "EXPECTED_EXPERIMENT_ID", experiment.EXPERIMENT_ID)
    monkeypatch.setattr(rearm, "EXPECTED_PLAN_SHA256", plan["plan_sha256"])
    monkeypatch.setattr(rearm, "EXPECTED_PLAN_FILE_SHA256", _sha256(plan_bytes))
    monkeypatch.setattr(
        rearm,
        "EXPECTED_QUOTE_SOURCE_SHA256",
        _sha256(quote_source),
    )
    monkeypatch.setattr(
        rearm,
        "EXPECTED_CATALOGUE_SHA256",
        bundle["catalogue_sha256"],
    )
    monkeypatch.setattr(rearm, "EXPECTED_CONFIG", expected_config)
    monkeypatch.setattr(rearm, "EXPECTED_PAIR_ID", member["pair_id"])
    monkeypatch.setattr(rearm, "EXPECTED_PAIR_INDEX", 0)
    monkeypatch.setattr(rearm, "EXPECTED_MEMBER_POSITION", 1)
    monkeypatch.setattr(rearm, "EXPECTED_ARM", member["arm"])
    monkeypatch.setattr(rearm, "EXPECTED_QUOTE_ID", member["quote_id"])
    monkeypatch.setattr(
        rearm,
        "EXPECTED_APPROVED_QUESTION_SHA256",
        member["approved_question_sha256"],
    )
    monkeypatch.setattr(rearm, "EXPECTED_INVALID_REASON", expected_deferral)
    monkeypatch.setattr(bot, "BASE_DIR", root)
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "CONTROL_FILE", control_path)
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_path)
    monkeypatch.setattr(bot, "LINES_FILE", quote_source_path)
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        root / "ambiguous_post_outcome.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        root / "ambiguous_post_outcome.successor.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {})
    monkeypatch.setattr(bot, "_ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256", None)
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", lambda: True)
    monkeypatch.setattr(bot, "ambiguous_remote_post_is_blocking", lambda: False)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)

    original_load_catalogue = experiment.load_approved_catalogue

    def load_synthetic_catalogue(
        path: Path,
        current_quotes: dict[str, str],
        **_kwargs: object,
    ) -> tuple[dict, str]:
        return original_load_catalogue(
            path,
            current_quotes,
            expected_sha256=_sha256(catalogue_bytes),
            expected_entry_count=len(catalogue["entries"]),
        )

    monkeypatch.setattr(
        bot.engagement_question_trial,
        "load_approved_catalogue",
        load_synthetic_catalogue,
    )

    return IncidentRuntime(
        root=root,
        external_backup=external_backup,
        state_path=state_path,
        bak1_path=bak1_path,
        plan_path=plan_path,
        catalogue_path=catalogue_path,
        used_path=used_path,
        control_path=control_path,
        config_path=config_path,
        quote_source_path=quote_source_path,
        state=state,
        experiment_state=experiment_state,
        plan=plan,
        catalogue=catalogue,
        quote_text_by_id=quote_text_by_id,
        member=member,
        notification_path=notification_path,
        state_bytes=state_bytes,
    )


def _assert_refused_without_save(
    runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    *,
    match: str | None = None,
) -> None:
    before = _namespace_snapshot(runtime.root, runtime.external_backup)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail("refused recovery must not save"),
    )
    with pytest.raises(rearm.RecoveryRefused, match=match):
        runtime.call(apply=True)
    assert _namespace_snapshot(runtime.root, runtime.external_backup) == before


def test_check_mode_is_bounded_and_does_not_mutate(
    incident_runtime: IncidentRuntime,
) -> None:
    before = _namespace_snapshot(
        incident_runtime.root,
        incident_runtime.external_backup,
    )
    result = incident_runtime.call(apply=False)

    assert result["operation"] == "checked"
    assert result["network_requests_made"] == 0
    assert result["changed_fields"] == list(rearm.CHANGED_FIELDS)
    assert _namespace_snapshot(
        incident_runtime.root,
        incident_runtime.external_backup,
    ) == before
    rendered = json.dumps(result, sort_keys=True)
    assert incident_runtime.notification_path not in rendered
    assert incident_runtime.member["approved_question_body"] not in rendered
    assert (
        incident_runtime.quote_text_by_id[incident_runtime.member["quote_id"]]
        not in rendered
    )


def test_apply_changes_only_two_fields_and_uses_one_durable_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_save_state = bot.save_state
    calls: list[bool] = []

    def save_once(state: dict, *, durable: bool = False) -> None:
        calls.append(durable)
        real_save_state(state, durable=durable)

    monkeypatch.setattr(bot, "save_state", save_once)
    before = json.loads(incident_runtime.state_path.read_text(encoding="utf-8"))
    result = incident_runtime.call(apply=True)
    after = json.loads(incident_runtime.state_path.read_text(encoding="utf-8"))

    assert calls == [True]
    assert result["operation"] == "rearmed"
    assert result["network_requests_made"] == 0
    assert _semantic_differences(before, after) == set(rearm.CHANGED_FIELDS)
    assert after["engagement_question_experiment"]["status"] == "active"
    assert after["engagement_question_experiment"]["current_deferral_reason"] is None
    assert incident_runtime.state_path.read_bytes() == (
        incident_runtime.bak1_path.read_bytes()
    )
    assert (
        incident_runtime.root / "bot_state.json.bak2"
    ).read_bytes() == incident_runtime.state_bytes
    assert incident_runtime.external_backup.read_bytes() == (
        incident_runtime.state_bytes
    )

    with pytest.raises(rearm.RecoveryRefused):
        incident_runtime.call(apply=True)
    assert calls == [True]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda runtime: _write_private(
                runtime.control_path,
                _json_bytes({"pause_all": False}),
            ),
            id="global-pause-inactive",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.config_path,
                _json_bytes(
                    {
                        "engagement_question_experiment_enabled": False,
                        "engagement_question_experiment_plan_path": (
                            "engagement_question_experiment/active_plan.json"
                        ),
                        "engagement_question_notification_output_path": (
                            runtime.notification_path
                        ),
                    }
                ),
            ),
            id="experiment-disabled",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.config_path,
                _json_bytes(
                    {
                        "engagement_question_experiment_enabled": True,
                        "engagement_question_experiment_plan_path": (
                            "engagement_question_experiment/other-plan.json"
                        ),
                        "engagement_question_notification_output_path": (
                            runtime.notification_path
                        ),
                    }
                ),
            ),
            id="plan-path-changed",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.config_path,
                _json_bytes(
                    {
                        "engagement_question_experiment_enabled": True,
                        "engagement_question_experiment_plan_path": (
                            "engagement_question_experiment/active_plan.json"
                        ),
                        "engagement_question_notification_output_path": (
                            runtime.notification_path + ".changed"
                        ),
                    }
                ),
            ),
            id="notification-path-changed",
        ),
        pytest.param(
            lambda runtime: _write_private(runtime.control_path, b'{"pause_all":'),
            id="malformed-control",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.control_path,
                _json_bytes({"pause_all": "true"}),
            ),
            id="string-pause-not-literal-boolean",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.control_path,
                _json_bytes({"disable_all": True}),
            ),
            id="disable-all-without-pause",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.bak1_path,
                runtime.state_bytes + b" ",
            ),
            id="bak1-differs",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.external_backup,
                runtime.state_bytes + b" ",
            ),
            id="external-backup-differs",
        ),
        pytest.param(
            lambda runtime: runtime.state_path.chmod(0o644),
            id="primary-metadata",
        ),
        pytest.param(
            lambda runtime: runtime.bak1_path.chmod(0o644),
            id="bak1-metadata",
        ),
        pytest.param(
            lambda runtime: runtime.bak1_path.unlink(),
            id="bak1-missing",
        ),
        pytest.param(
            lambda runtime: runtime.external_backup.chmod(0o644),
            id="external-backup-metadata",
        ),
        pytest.param(
            _replace_backup_with_symlink,
            id="external-backup-symlink",
        ),
        pytest.param(
            _replace_backup_parent_with_symlink,
            id="external-backup-parent-symlink",
        ),
        pytest.param(
            lambda runtime: _write_private(
                runtime.used_path,
                experiment.canonical_used_history_file_bytes(
                    [runtime.member["quote_id"]]
                ),
            ),
            id="planned-quote-used",
        ),
        pytest.param(
            lambda runtime: _write_private(runtime.used_path, b'{"bad":true}\n'),
            id="malformed-used-history",
        ),
    ],
)
def test_unsafe_filesystem_and_configuration_inputs_are_refused_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[IncidentRuntime], None],
) -> None:
    mutate(incident_runtime)
    _assert_refused_without_save(incident_runtime, monkeypatch)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "paused"),
        ("current_pair_index", 1),
        ("next_pair_member_position", 2),
        ("completed_pair_count", 1),
        ("treatment_publication_count", 1),
        ("confirmed_publications", [{"unexpected": "progress"}]),
        (
            "current_deferral_reason",
            {
                "code": "different_reason",
                "pair_id": None,
                "member_position": None,
                "recorded_epoch": INCIDENT_EPOCH,
            },
        ),
    ],
)
def test_non_exact_incident_or_nonzero_progress_is_refused_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    document = copy.deepcopy(incident_runtime.state)
    document["engagement_question_experiment"][field] = value
    changed = _state_json_bytes(document)
    _write_private(incident_runtime.state_path, changed)
    shutil.copy2(incident_runtime.state_path, incident_runtime.bak1_path)
    shutil.copy2(
        incident_runtime.state_path,
        incident_runtime.external_backup,
    )
    incident_runtime.state_bytes = changed
    _assert_refused_without_save(incident_runtime, monkeypatch)


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        ("EXPECTED_PLAN_SHA256", "f" * 64),
        ("EXPECTED_PLAN_FILE_SHA256", "e" * 64),
        ("EXPECTED_CATALOGUE_SHA256", "d" * 64),
        ("EXPECTED_PAIR_ID", "pair-" + "f" * 24),
        ("EXPECTED_MEMBER_POSITION", 2),
        ("EXPECTED_ARM", "treatment"),
        ("EXPECTED_QUOTE_ID", "b" * 64),
        ("EXPECTED_APPROVED_QUESTION_SHA256", "a" * 64),
    ],
)
def test_changed_plan_catalogue_pair_or_member_authority_is_refused(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    value: object,
) -> None:
    monkeypatch.setattr(rearm, constant, value)
    _assert_refused_without_save(incident_runtime, monkeypatch)


def test_authority_change_between_independent_reads_is_refused_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_before = incident_runtime.state_path.read_bytes()
    bak1_before = incident_runtime.bak1_path.read_bytes()
    captures = 0

    def change_used_history_after_first_capture(
        module: object,
        runtime_root: Path,
    ) -> object:
        nonlocal captures
        authority = REAL_LOAD_AUTHORITY(module, runtime_root)
        captures += 1
        if captures == 1:
            _write_private(
                incident_runtime.used_path,
                experiment.canonical_used_history_file_bytes(["f" * 64]),
            )
        return authority

    monkeypatch.setattr(rearm, "_load_authority", change_used_history_after_first_capture)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "changed authority must be refused before save"
        ),
    )

    with pytest.raises(
        rearm.RecoveryRefused,
        match="experiment_authority_changed_before_commit",
    ):
        incident_runtime.call(apply=True)

    assert captures == 2
    assert incident_runtime.state_path.read_bytes() == state_before
    assert incident_runtime.bak1_path.read_bytes() == bak1_before


def test_control_replacement_between_reads_is_refused_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_before = incident_runtime.state_path.read_bytes()
    bak1_before = incident_runtime.bak1_path.read_bytes()
    captures = 0

    def replace_control_after_first_capture(
        module: object,
        *,
        unreadable_reason: str,
        inactive_reason: str,
    ) -> tuple[bytes, tuple[object, ...]]:
        nonlocal captures
        snapshot = REAL_REQUIRE_EXPLICIT_GLOBAL_PAUSE(
            module,
            unreadable_reason=unreadable_reason,
            inactive_reason=inactive_reason,
        )
        captures += 1
        if captures == 1:
            replacement = incident_runtime.control_path.with_name(
                ".mrsMThatcher.control.replacement"
            )
            _write_private(
                replacement,
                _json_bytes({"generation": 1, "pause_all": True}),
            )
            os.replace(replacement, incident_runtime.control_path)
        return snapshot

    monkeypatch.setattr(
        rearm,
        "_require_explicit_global_pause",
        replace_control_after_first_capture,
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "changed runtime control must be refused before save"
        ),
    )

    with pytest.raises(
        rearm.RecoveryRefused,
        match="global_pause_changed_before_commit",
    ):
        incident_runtime.call(apply=True)

    assert captures == 2
    assert incident_runtime.state_path.read_bytes() == state_before
    assert incident_runtime.bak1_path.read_bytes() == bak1_before


def test_quote_source_line_order_change_is_refused_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = incident_runtime.quote_source_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 2
    lines[0], lines[1] = lines[1], lines[0]
    _write_private(
        incident_runtime.quote_source_path,
        ("\n".join(lines) + "\n").encode("utf-8"),
    )
    _assert_refused_without_save(incident_runtime, monkeypatch)


@pytest.mark.parametrize(
    "name",
    [
        "regular_post_receipt.json",
        "remote_media_upload_receipt.json",
        "ambiguous_post_outcome.json",
        "ambiguous_post_outcome.restart_barrier.json",
        ".remote_write_transport_journal.json.transition."
        "0123456789abcdef0123456789abcdef",
    ],
)
def test_transport_receipts_ambiguity_barriers_and_prefixes_are_refused(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    _write_private(incident_runtime.root / name, b"{}\n")
    monkeypatch.setattr(
        rearm,
        "_require_clean_transport",
        REAL_REQUIRE_CLEAN_TRANSPORT,
    )
    monkeypatch.setattr(bot, "ambiguous_remote_post_is_blocking", lambda: False)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    _assert_refused_without_save(incident_runtime, monkeypatch)


@pytest.mark.parametrize(
    ("case", "protocol_probe", "reason"),
    [
        pytest.param(
            "missing-or-malformed-activation",
            lambda: False,
            "remote_write_safety_protocol_inactive",
            id="missing-or-malformed-activation",
        ),
        pytest.param(
            "blocking-retirement-ledger",
            lambda: False,
            "remote_write_safety_protocol_inactive",
            id="blocking-retirement-ledger",
        ),
        pytest.param(
            "activation-inspection-error",
            lambda: (_ for _ in ()).throw(OSError("synthetic inspection failure")),
            "remote_write_safety_protocol_unavailable",
            id="activation-inspection-error",
        ),
    ],
)
def test_missing_malformed_or_retirement_blocked_protocol_refuses_before_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    protocol_probe: Callable[[], bool],
    reason: str,
) -> None:
    assert case
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", protocol_probe)
    _assert_refused_without_save(
        incident_runtime,
        monkeypatch,
        match=reason,
    )


def test_root_identity_and_operator_confirmations_are_mandatory(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (
        {"expected_project_inode": os.stat(incident_runtime.root).st_ino + 1},
        {"expected_project_device": os.stat(incident_runtime.root).st_dev + 1},
        {"confirm_supervisor_stopped": False},
        {"confirm_global_pause_acknowledged": False},
        {"expected_state_sha256": "0" * 64},
    )
    for override in cases:
        before = _namespace_snapshot(
            incident_runtime.root,
            incident_runtime.external_backup,
        )
        args = incident_runtime.arguments(apply=True)
        for key, value in override.items():
            setattr(args, key, value)
        monkeypatch.setattr(
            bot,
            "save_state",
            lambda *_args, **_kwargs: pytest.fail("guard failure must not save"),
        )
        with pytest.raises(rearm.RecoveryRefused) as raised:
            rearm.execute_recovery(args, bot=bot)
        assert raised.value.state_commit_may_have_occurred is False
        assert _namespace_snapshot(
            incident_runtime.root,
            incident_runtime.external_backup,
        ) == before


def test_postcommit_mismatch_is_reported_not_hidden(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_save_state = bot.save_state

    def save_then_damage_latest_backup(
        state: dict,
        *,
        durable: bool = False,
    ) -> None:
        real_save_state(state, durable=durable)
        incident_runtime.bak1_path.write_bytes(b'{"postcommit":"mismatch"}\n')

    monkeypatch.setattr(bot, "save_state", save_then_damage_latest_backup)
    with pytest.raises(rearm.RecoveryRefused, match="post.?commit|bak1|backup"):
        incident_runtime.call(apply=True)
    assert incident_runtime.state_path.read_bytes() != (
        incident_runtime.bak1_path.read_bytes()
    )


@pytest.mark.parametrize(
    "changed_component",
    ["pause", "config"],
)
def test_pause_or_config_change_during_commit_is_a_visible_postcommit_refusal(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    changed_component: str,
) -> None:
    real_save_state = bot.save_state
    remote_calls: list[str] = []

    def save_then_change_authority(
        state: dict,
        *,
        durable: bool = False,
    ) -> None:
        real_save_state(state, durable=durable)
        if changed_component == "pause":
            _write_private(
                incident_runtime.control_path,
                _json_bytes({"pause_all": False}),
            )
        else:
            _write_private(
                incident_runtime.config_path,
                _json_bytes(
                    {
                        "engagement_question_experiment_enabled": False,
                        "engagement_question_experiment_plan_path": (
                            "engagement_question_experiment/active_plan.json"
                        ),
                        "engagement_question_notification_output_path": (
                            incident_runtime.notification_path
                        ),
                    }
                ),
            )

    monkeypatch.setattr(bot, "save_state", save_then_change_authority)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: remote_calls.append("create_post"),
    )
    monkeypatch.setattr(
        bot,
        "upload_media_v2",
        lambda **_kwargs: remote_calls.append("upload_media_v2"),
    )

    expected_reason = (
        "global_pause_changed_after_commit"
        if changed_component == "pause"
        else "engagement_config_mismatch"
    )
    with pytest.raises(rearm.RecoveryRefused, match=expected_reason):
        incident_runtime.call(apply=True)

    committed = json.loads(
        incident_runtime.state_path.read_text(encoding="utf-8")
    )
    assert committed["engagement_question_experiment"]["status"] == "active"
    assert (
        committed["engagement_question_experiment"]["current_deferral_reason"]
        is None
    )
    assert incident_runtime.state_path.read_bytes() == (
        incident_runtime.bak1_path.read_bytes()
    )
    assert (
        incident_runtime.root / "bot_state.json.bak2"
    ).read_bytes() == incident_runtime.state_bytes
    assert remote_calls == []


def test_save_exception_after_real_commit_reports_possible_partial_save(
    incident_runtime: IncidentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_save_state = bot.save_state
    real_execute_recovery = rearm.execute_recovery
    save_calls: list[bool] = []
    remote_calls: list[str] = []
    observed_refusals: list[rearm.RecoveryRefused] = []

    def save_then_raise(
        state: dict,
        *,
        durable: bool = False,
    ) -> None:
        save_calls.append(durable)
        real_save_state(state, durable=durable)
        raise OSError("synthetic exception after the durable state save")

    def capture_refusal(
        args: argparse.Namespace,
        *,
        bot: object,
    ) -> dict:
        try:
            return real_execute_recovery(args, bot=bot)
        except rearm.RecoveryRefused as exc:
            observed_refusals.append(exc)
            raise

    monkeypatch.setattr(bot, "save_state", save_then_raise)
    monkeypatch.setattr(rearm, "execute_recovery", capture_refusal)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: remote_calls.append("create_post"),
    )
    monkeypatch.setattr(
        bot,
        "upload_media_v2",
        lambda **_kwargs: remote_calls.append("upload_media_v2"),
    )

    args = incident_runtime.arguments(apply=True)
    exit_code = rearm.main(
        [
            "--runtime-root",
            str(args.runtime_root),
            "--expected-project-device",
            str(args.expected_project_device),
            "--expected-project-inode",
            str(args.expected_project_inode),
            "--quiescent-state-backup",
            str(args.quiescent_state_backup),
            "--expected-state-sha256",
            args.expected_state_sha256,
            "--confirm-supervisor-stopped",
            "--confirm-global-pause-acknowledged",
            "--apply",
        ]
    )

    assert exit_code == 1
    assert save_calls == [True]
    assert len(observed_refusals) == 1
    refusal = observed_refusals[0]
    assert refusal.reason == "durable_state_commit_failed"
    assert str(refusal) == "durable_state_commit_failed"
    assert refusal.state_commit_may_have_occurred is True

    captured_stdout = capsys.readouterr().out
    output_lines = [line for line in captured_stdout.splitlines() if line.strip()]
    result = json.loads(output_lines[-1])
    assert result == {
        "schema_version": rearm.RESULT_SCHEMA_VERSION,
        "operation": "refused",
        "reason": "durable_state_commit_failed",
        "exception_class": "RecoveryRefused",
        "state_commit_may_have_occurred": True,
        "network_requests_made": 0,
    }
    assert incident_runtime.notification_path not in captured_stdout
    assert incident_runtime.member["approved_question_body"] not in captured_stdout
    assert (
        incident_runtime.quote_text_by_id[incident_runtime.member["quote_id"]]
        not in captured_stdout
    )

    primary = json.loads(incident_runtime.state_path.read_text(encoding="utf-8"))
    assert primary["engagement_question_experiment"]["status"] == "active"
    assert primary["engagement_question_experiment"]["current_deferral_reason"] is None
    assert incident_runtime.state_path.read_bytes() == (
        incident_runtime.bak1_path.read_bytes()
    )
    assert (
        incident_runtime.root / "bot_state.json.bak2"
    ).read_bytes() == incident_runtime.state_bytes
    assert incident_runtime.external_backup.read_bytes() == (
        incident_runtime.state_bytes
    )
    assert remote_calls == []
