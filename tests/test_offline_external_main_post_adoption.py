"""Focused coverage for stopped external confirmation of quote/image posts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

import mrsMThatcher2 as bot
import remote_write_transport_journal as journal
import remote_write_safety_protocol as safety_protocol
from tests.helpers.protocol_activation import create_test_protocol_activation
from tools import reconcile_remote_write_safety_marker as reconcile
from transaction_mutation_authority import issue_transaction_mutation_authority


POST_ID = "2095519929172713755"
MEDIA_ID = "2095519926190530563"
ATTEMPT_EPOCH = 1_788_445_856
CONFIRMATION_EPOCH = 1_788_445_858
TEXT = (
    "The best reply to full-blooded Socialism is not milk and water "
    "Socialism, it is genuine Conservatism."
)
TEXT_SHA256 = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


def _authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="external main-post adoption focused test",
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _snapshot(project: Path) -> dict[str, tuple[tuple[int, ...], bytes]]:
    observed: dict[str, tuple[tuple[int, ...], bytes]] = {}
    for path in sorted(project.iterdir()):
        metadata = os.lstat(path)
        identity = (
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_mode),
            int(metadata.st_nlink),
            int(metadata.st_size),
            int(metadata.st_ctime_ns),
            int(metadata.st_mtime_ns),
        )
        data = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
        observed[path.name] = (identity, data)
    return observed


@dataclass
class MainIncident:
    project: Path
    evidence: Path
    source_path: Path
    source_bytes: bytes
    marker_path: Path
    marker_bytes: bytes
    journal_path: Path
    payload_bytes: bytes
    transaction_id: str
    journal_snapshot: journal.JournalSnapshot
    fence_snapshot: journal.JournalSnapshot

    def tool_kwargs(self, *, check_only: bool) -> dict[str, object]:
        source_stat = self.source_path.stat()
        marker_stat = self.marker_path.stat()
        return {
            "project_root": self.project,
            "expected_marker_sha256": hashlib.sha256(
                self.marker_bytes
            ).hexdigest(),
            "expected_marker_device": int(marker_stat.st_dev),
            "expected_marker_inode": int(marker_stat.st_ino),
            "expected_marker_ctime_ns": int(marker_stat.st_ctime_ns),
            "expected_marker_size": int(marker_stat.st_size),
            "expected_source_receipt_basename": self.source_path.name,
            "expected_source_receipt_sha256": hashlib.sha256(
                self.source_bytes
            ).hexdigest(),
            "expected_source_receipt_device": int(source_stat.st_dev),
            "expected_source_receipt_inode": int(source_stat.st_ino),
            "expected_source_receipt_ctime_ns": int(source_stat.st_ctime_ns),
            "expected_source_receipt_size": int(source_stat.st_size),
            "expected_source_lifecycle": "attempting",
            "expected_candidate_lane": "quote_image",
            "expected_target_id": "",
            "expected_text_sha256": TEXT_SHA256,
            "expected_transaction_id": self.transaction_id,
            "expected_transport_lane": "quote_image",
            "expected_canonical_payload_sha256": hashlib.sha256(
                self.payload_bytes
            ).hexdigest(),
            "expected_journal_sha256": self.journal_snapshot.sha256,
            "expected_journal_device": self.journal_snapshot.device,
            "expected_journal_inode": self.journal_snapshot.inode,
            "expected_journal_ctime_ns": self.journal_snapshot.ctime_ns,
            "expected_journal_size": len(self.journal_snapshot.data),
            "expected_fence_sha256": self.fence_snapshot.sha256,
            "expected_fence_device": self.fence_snapshot.device,
            "expected_fence_inode": self.fence_snapshot.inode,
            "expected_fence_ctime_ns": self.fence_snapshot.ctime_ns,
            "expected_fence_size": len(self.fence_snapshot.data),
            "confirmed_post_id": POST_ID,
            "confirmation_epoch": CONFIRMATION_EPOCH,
            "external_evidence_path": self.evidence,
            "expected_external_evidence_sha256": hashlib.sha256(
                self.evidence.read_bytes()
            ).hexdigest(),
            "reconciliation_reference": "authenticated-X-own-timeline-review",
            "confirm_external_publication_reviewed": True,
            "confirm_offline_reconciliation_complete": not check_only,
            "check_only": check_only,
            "now": lambda: CONFIRMATION_EPOCH,
            "source_kind": "quote_image",
        }

    def low_level_kwargs(self) -> dict[str, object]:
        values = self.tool_kwargs(check_only=True)
        return {
            "path": self.journal_path,
            "receipt_path": self.source_path,
            "mutation_authority": _authority(),
            "expected_transaction_id": self.transaction_id,
            "expected_lane": "quote_image",
            "expected_source_receipt_bytes": self.source_bytes,
            "expected_source_receipt_sha256": values[
                "expected_source_receipt_sha256"
            ],
            "expected_source_receipt_device": values[
                "expected_source_receipt_device"
            ],
            "expected_source_receipt_inode": values[
                "expected_source_receipt_inode"
            ],
            "expected_source_receipt_ctime_ns": values[
                "expected_source_receipt_ctime_ns"
            ],
            "expected_source_receipt_size": values[
                "expected_source_receipt_size"
            ],
            "expected_source_validator_id": journal.LANE_SOURCE_VALIDATOR_ID,
            "expected_payload_bytes": self.payload_bytes,
            "expected_payload_sha256": values[
                "expected_canonical_payload_sha256"
            ],
            "expected_reply_target_id": "",
            "expected_journal_sha256": values["expected_journal_sha256"],
            "expected_journal_device": values["expected_journal_device"],
            "expected_journal_inode": values["expected_journal_inode"],
            "expected_journal_ctime_ns": values["expected_journal_ctime_ns"],
            "expected_journal_size": values["expected_journal_size"],
            "expected_fence_sha256": values["expected_fence_sha256"],
            "expected_fence_device": values["expected_fence_device"],
            "expected_fence_inode": values["expected_fence_inode"],
            "expected_fence_ctime_ns": values["expected_fence_ctime_ns"],
            "expected_fence_size": values["expected_fence_size"],
            "confirmed_post_id": POST_ID,
            "confirmation_epoch": CONFIRMATION_EPOCH,
            "prepared_audit_basename": "external.prepared.json",
            "prepared_audit_sha256": "a" * 64,
            "evidence_archive_basename": "external.evidence.json",
            "evidence_sha256": values["expected_external_evidence_sha256"],
        }


def build_main_incident(
    tmp_path: Path,
    *,
    activate_protocol: bool = False,
) -> MainIncident:
    journal.reset_consumed_authorities_for_tests()
    project = tmp_path / "bot"
    project.mkdir()
    (project / reconcile.LOCK_BASENAME).write_text(
        "pid=2147483647\n",
        encoding="utf-8",
    )
    if activate_protocol:
        create_test_protocol_activation(
            project / safety_protocol.ACTIVATION_BASENAME
        )

    source = bot.build_main_post_attempt(
        lane="quote_image",
        text=TEXT,
        media_ids=[MEDIA_ID],
        made_with_ai=False,
        selected_identity={
            "quote_hash": TEXT_SHA256,
            "line_no": 297,
            "source_line_number": 298,
            "image_basename": "t34.jpg",
            "image_no": 33,
        },
        recovery_plan={
            "quote_delay_seconds": 8_814,
            "meme_delay_seconds": 3_155,
            "meme_scheduling_enabled": True,
            "meme_trigger_after_hour": 12,
            "meme_schedule_version": 2,
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            "meme_schedule_before": bot.bound_meme_schedule_state(
                {},
                schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
            ),
            "quote_history_after": [TEXT_SHA256],
            "image_history_after": ["t34.jpg"],
        },
        attempt_epoch=ATTEMPT_EPOCH,
    )
    source["attempt_id"] = hashlib.sha256(b"main-post-attempt").hexdigest()
    source["lifecycle_state"] = "attempting"
    assert bot.main_post_attempt_is_semantically_valid(source)
    source_bytes = _canonical(source)
    source_path = project / reconcile.REGULAR_POST_RECEIPT_BASENAME
    _write_private(source_path, source_bytes)

    payload = {"text": TEXT, "media": {"media_ids": [MEDIA_ID]}}
    binding = journal.bind_transport_source(
        receipt_path=source_path,
        expected_receipt=source,
        expected_receipt_bytes=source_bytes,
        lane="quote_image",
        payload=payload,
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        validator=lambda *_args: True,
    )
    prepared = journal.begin_transport_transaction(
        receipt_path=source_path,
        source_binding=binding,
    )
    journal_path = journal.journal_path_for_receipt(source_path)
    journal.arm_transport_transaction(
        journal_path,
        prepared,
        mutation_authority=_authority(),
    )
    state = journal.inspect_transport_state(journal_path)
    assert state.classification == "attempting_pair"
    assert state.journal is not None and state.fence is not None

    marker = {
        "made_with_ai": False,
        "media_ids": [MEDIA_ID],
        "outcome": "ambiguous_remote_post",
        "recorded_at_epoch": CONFIRMATION_EPOCH,
        "reply_to_id": "",
        "schema_version": 1,
        "text_sha256": TEXT_SHA256,
    }
    marker_bytes = _canonical(marker)
    marker_path = project / reconcile.MARKER_BASENAME
    _write_private(marker_path, marker_bytes)
    os.link(marker_path, project / reconcile.RESTART_BARRIER_BASENAME)
    evidence = tmp_path / "authenticated-x-own-timeline-evidence.json"
    _write_private(
        evidence,
        _canonical(
            {
                "created_at": "2026-09-03T14:30:58.000Z",
                "media_key": f"3_{MEDIA_ID}",
                "post_id": POST_ID,
                "text": TEXT,
            }
        ),
    )
    return MainIncident(
        project=project,
        evidence=evidence,
        source_path=source_path,
        source_bytes=source_bytes,
        marker_path=marker_path,
        marker_bytes=marker_bytes,
        journal_path=journal_path,
        payload_bytes=journal.canonical_json_bytes(payload),
        transaction_id=str(state.journal.document["transaction_id"]),
        journal_snapshot=state.journal,
        fence_snapshot=state.fence,
    )


def _cli_arguments(incident: MainIncident, *, check_only: bool) -> list[str]:
    values = incident.tool_kwargs(check_only=check_only)
    arguments = [
        "--project-root",
        os.fspath(values["project_root"]),
        "--expected-marker-sha256",
        str(values["expected_marker_sha256"]),
        "--adopt-externally-confirmed-main-post",
    ]
    options = (
        ("expected_marker_device", "expected-marker-device"),
        ("expected_marker_inode", "expected-marker-inode"),
        ("expected_marker_ctime_ns", "expected-marker-ctime-ns"),
        ("expected_marker_size", "expected-marker-size"),
        ("expected_source_receipt_basename", "expected-source-receipt-basename"),
        ("expected_source_receipt_sha256", "expected-source-receipt-sha256"),
        ("expected_source_receipt_device", "expected-source-receipt-device"),
        ("expected_source_receipt_inode", "expected-source-receipt-inode"),
        ("expected_source_receipt_ctime_ns", "expected-source-receipt-ctime-ns"),
        ("expected_source_receipt_size", "expected-source-receipt-size"),
        ("expected_source_lifecycle", "expected-source-lifecycle"),
        ("expected_candidate_lane", "expected-candidate-lane"),
        ("expected_target_id", "expected-target-id"),
        ("expected_text_sha256", "expected-text-sha256"),
        ("expected_transaction_id", "expected-transport-transaction-id"),
        ("expected_transport_lane", "expected-transport-lane"),
        ("expected_canonical_payload_sha256", "expected-canonical-payload-sha256"),
        ("expected_journal_sha256", "expected-journal-sha256"),
        ("expected_journal_device", "expected-journal-device"),
        ("expected_journal_inode", "expected-journal-inode"),
        ("expected_journal_ctime_ns", "expected-journal-ctime-ns"),
        ("expected_journal_size", "expected-journal-size"),
        ("expected_fence_sha256", "expected-fence-sha256"),
        ("expected_fence_device", "expected-fence-device"),
        ("expected_fence_inode", "expected-fence-inode"),
        ("expected_fence_ctime_ns", "expected-fence-ctime-ns"),
        ("expected_fence_size", "expected-fence-size"),
        ("confirmed_post_id", "confirmed-post-id"),
        ("confirmation_epoch", "confirmation-epoch"),
        ("external_evidence_path", "external-evidence-path"),
        ("expected_external_evidence_sha256", "expected-external-evidence-sha256"),
        ("reconciliation_reference", "reconciliation-reference"),
    )
    for key, option in options:
        value = values[key]
        arguments.extend(
            (
                f"--{option}",
                os.fspath(value) if isinstance(value, os.PathLike) else str(value),
            )
        )
    arguments.append("--confirm-external-publication-reviewed")
    if check_only:
        arguments.append("--check-only")
    else:
        arguments.append("--confirm-offline-reconciliation-complete")
    return arguments


def test_low_level_quote_image_adoption_is_exact_and_idempotent(
    tmp_path: Path,
) -> None:
    incident = build_main_incident(tmp_path)

    first = journal.adopt_externally_confirmed_transport_transaction(
        **incident.low_level_kwargs()
    )
    repeated = journal.adopt_externally_confirmed_transport_transaction(
        **incident.low_level_kwargs()
    )

    assert first.disposition == "first_adoption"
    assert repeated.disposition == "already_adopted"
    assert first.confirmed.post_id == POST_ID
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "confirmed_pair"


def test_main_post_check_only_preserves_namespace_then_apply_archives_marker(
    tmp_path: Path,
) -> None:
    incident = build_main_incident(tmp_path)
    before = _snapshot(incident.project)

    checked = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=True)
    )

    assert checked.operation == reconcile.EXTERNAL_MAIN_ADOPTION_OPERATION
    assert checked.execution == "check_only"
    assert checked.check_only_no_mutation is True
    assert _snapshot(incident.project) == before

    applied = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )

    assert applied.operation == reconcile.EXTERNAL_MAIN_ADOPTION_OPERATION
    assert applied.execution == "applied"
    assert applied.final_transport_classification == "confirmed_pair"
    assert applied.final_source_receipt_present is True
    assert incident.source_path.read_bytes() == incident.source_bytes
    assert not incident.marker_path.exists()
    assert not (incident.project / reconcile.RESTART_BARRIER_BASENAME).exists()
    completed = json.loads(
        (incident.project / str(applied.completed_audit_path)).read_bytes()
    )
    assert completed["document_kind"] == reconcile.EXTERNAL_MAIN_ADOPTION_AUDIT_KIND
    assert completed["operation"] == reconcile.EXTERNAL_MAIN_ADOPTION_OPERATION


def test_main_post_cli_routes_empty_root_target_without_mutation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    incident = build_main_incident(tmp_path)
    before = _snapshot(incident.project)

    assert reconcile.main(_cli_arguments(incident, check_only=True)) == 0
    captured = capfd.readouterr()
    result = json.loads(captured.out)

    assert captured.err == ""
    assert result["operation"] == reconcile.EXTERNAL_MAIN_ADOPTION_OPERATION
    assert result["target_id"] == ""
    assert result["execution"] == "check_only"
    assert _snapshot(incident.project) == before


def test_adopted_main_post_uses_existing_startup_recovery_without_x_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = build_main_incident(tmp_path, activate_protocol=True)
    offline = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert offline.final_transport_classification == "confirmed_pair"

    state_path = incident.project / "bot_state.json"
    monkeypatch.setattr(bot, "BASE_DIR", incident.project)
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "LINES_USED_FILE",
        incident.project / "lines_used.json",
    )
    monkeypatch.setattr(
        bot,
        "IMAGES_USED_FILE",
        incident.project / "images_used.json",
    )
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", incident.source_path)
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        incident.project / "meme_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        incident.project / "confirmed_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        incident.project / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        incident.project / reconcile.MARKER_BASENAME,
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        incident.project / safety_protocol.ACTIVATION_BASENAME,
    )
    monkeypatch.setattr(
        bot,
        "MEDIA_UPLOAD_RECEIPT_FILE",
        incident.project / "remote_media_upload_receipt.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(bot, "now_epoch", lambda: CONFIRMATION_EPOCH + 1)
    monkeypatch.setattr(
        bot,
        "transaction_mutation_authority",
        lambda _operation: _authority(),
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    def forbid_remote(*_args, **_kwargs):
        pytest.fail("startup main-post recovery attempted a duplicate X write")

    monkeypatch.setattr(bot, "x_request", forbid_remote)
    monkeypatch.setattr(bot, "create_post", forbid_remote)
    monkeypatch.setattr(bot.requests, "request", forbid_remote)
    monkeypatch.setattr(bot.requests, "post", forbid_remote)

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict[str, object] = {}
    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        lines_used,
        images_used,
        state,
    )

    assert recovered == {
        "historical_context": False,
        "conversational_reply": False,
        "regular": True,
        "meme": False,
    }
    assert state["last_main_post_id"] == POST_ID
    assert state["last_quote_post_epoch"] == CONFIRMATION_EPOCH
    assert TEXT_SHA256 in lines_used
    assert "t34.jpg" in images_used
    assert state_path.exists()
    assert not incident.source_path.exists()
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "clear"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("expected_source_receipt_basename", "confirmed_reply_receipt.json"),
        ("expected_source_lifecycle", "sending"),
        ("expected_candidate_lane", "daily_meme"),
        ("expected_target_id", "2092250326669668442"),
        ("expected_transport_lane", "conversational_reply"),
    ),
)
def test_main_post_mode_refuses_any_other_lane_identity(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    incident = build_main_incident(tmp_path)
    values = incident.tool_kwargs(check_only=True)
    values[field] = replacement
    before = _snapshot(incident.project)

    with pytest.raises(
        reconcile.ExternalReplyAdoptionError,
        match="limited to one attempting quote/image receipt",
    ):
        reconcile.adopt_externally_confirmed_reply_offline(**values)

    assert _snapshot(incident.project) == before


def test_quote_image_history_type_error_is_controlled() -> None:
    text_hash = hashlib.sha256(b"A reviewed quotation.").hexdigest()
    receipt = {
        "schema_version": 5,
        "lifecycle_state": "attempting",
        "lane": "quote_image",
        "attempt_id": "a" * 64,
        "attempt_epoch": ATTEMPT_EPOCH,
        "payload_revision": 1,
        "payload_sha256": "a" * 64,
        "text": "A reviewed quotation.",
        "text_sha256": text_hash,
        "media_ids": [MEDIA_ID],
        "reply_to_id": "",
        "made_with_ai": False,
        "selected_identity": {
            "quote_hash": text_hash,
            "line_no": 0,
            "source_line_number": 1,
            "image_basename": "t01.jpg",
            "image_no": 0,
        },
        "recovery_plan": {
            "quote_delay_seconds": 7_200,
            "meme_delay_seconds": 3_600,
            "quote_history_after": [{}],
            "image_history_after": ["t01.jpg"],
            "meme_scheduling_enabled": True,
            "meme_trigger_after_hour": 12,
            "meme_schedule_version": 2,
            "meme_schedule_before": {},
            "schedule_timezone": "Europe/London",
        },
    }

    with pytest.raises(
        reconcile.ExternalReplyAdoptionError,
        match="contain invalid identities",
    ):
        reconcile._require_external_quote_image_source_semantics(
            _canonical(receipt),
            text_sha256=text_hash,
        )
