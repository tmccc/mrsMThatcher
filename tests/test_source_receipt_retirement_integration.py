from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import exact_receipt_retirement as exact
import historical_context_formatter as context
import mrsMThatcher2 as bot
import remote_write_transport_journal as journal
from tools import activate_remote_write_safety_protocol as activate
from transaction_mutation_authority import issue_transaction_mutation_authority


def _mutation_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="source-retirement integration test",
    )


def _prepare_exact_receipt_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return exact.prepare_exact_receipt_retirement(*args, **kwargs)


def _resume_interrupted_receipt_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return exact.resume_interrupted_receipt_retirement(*args, **kwargs)


def _arm_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.arm_transport_transaction(*args, **kwargs)


def _confirm_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.confirm_transport_transaction(*args, **kwargs)


def _retire_confirmed_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.retire_confirmed_transport_transaction(*args, **kwargs)


RESTART_DRIVER = textwrap.dedent(
    r"""
    import json
    import os
    import sys
    from pathlib import Path

    import exact_receipt_retirement as exact
    import historical_context_formatter as context
    import mrsMThatcher2 as bot
    import remote_write_transport_journal as journal
    from transaction_mutation_authority import issue_transaction_mutation_authority

    def _mutation_authority():
        return issue_transaction_mutation_authority(
            lambda _operation: None,
            operation="source-retirement restart subprocess",
        )

    def _prepare_exact_receipt_retirement(*args, **kwargs):
        kwargs.setdefault("mutation_authority", _mutation_authority())
        return exact.prepare_exact_receipt_retirement(*args, **kwargs)

    def _resume_interrupted_receipt_retirement(*args, **kwargs):
        kwargs.setdefault("mutation_authority", _mutation_authority())
        return exact.resume_interrupted_receipt_retirement(*args, **kwargs)

    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    action = sys.argv[2]
    point = sys.argv[3]
    receipt_path = Path(manifest["receipt_path"])
    receipt = manifest["confirmed"]
    lane = manifest["lane"]
    post_id = manifest["post_id"]

    def current_bytes():
        if lane == "historical_context_reply":
            return context.canonical_json_bytes(receipt)
        return bot.canonical_atomic_json_bytes(receipt)

    def retire_journal():
        return bot.retire_lane_transport_journal_if_present(
            receipt_path=receipt_path,
            receipt=receipt,
            lane=lane,
            post_id=post_id,
            current_receipt_bytes=current_bytes(),
        )

    if action == "crash":
        if point == "prepared_guard":
            _prepare_exact_receipt_retirement(receipt_path, current_bytes())
            os._exit(81)

        if point in {"journal_fence_unlink", "journal_unlink"}:
            original_unlink = journal.os.unlink
            journal_path = journal.journal_path_for_receipt(receipt_path)
            target = (
                journal.fence_path_for_journal(journal_path).name
                if point == "journal_fence_unlink"
                else journal_path.name
            )

            def exit_after_unlink(name, *args, **kwargs):
                original_unlink(name, *args, **kwargs)
                if name == target:
                    os._exit(82)

            journal.os.unlink = exit_after_unlink
            retire_journal()
            raise AssertionError("journal crash point was not reached")

        if point == "exact_source_retirement":
            retire_journal()
            original_move = exact._move_exact_to_cleanup

            def exit_after_source_move(*args, **kwargs):
                original_move(*args, **kwargs)
                if kwargs.get("source_name") == receipt_path.name:
                    os._exit(83)

            exact._move_exact_to_cleanup = exit_after_source_move
            _resume_interrupted_receipt_retirement(receipt_path)
            raise AssertionError("exact source crash point was not reached")

        raise AssertionError(f"unknown crash point: {point}")

    if action == "resume":
        if point == "exact_source_retirement":
            _resume_interrupted_receipt_retirement(receipt_path)
        else:
            retire_journal()
            _resume_interrupted_receipt_retirement(receipt_path)
        raise SystemExit(0)

    raise AssertionError(f"unknown action: {action}")
    """
)


def run_restart_driver(
    manifest_path: Path,
    action: str,
    point: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", RESTART_DRIVER, str(manifest_path), action, point],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def write_exact(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def configure_lane_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, ...]:
    paths = (
        tmp_path / "regular_post_receipt.json",
        tmp_path / "meme_post_receipt.json",
        tmp_path / "confirmed_reply_receipt.json",
        tmp_path / "historical_context_reply_receipt.json",
    )
    for name, path in zip(
        (
            "REGULAR_POST_RECEIPT_FILE",
            "MEME_POST_RECEIPT_FILE",
            "CONFIRMED_REPLY_RECEIPT_FILE",
            "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        ),
        paths,
        strict=True,
    ):
        monkeypatch.setattr(bot, name, path)
    return paths


def namespace_snapshot(source: Path) -> tuple[tuple[str, int, int, bytes], ...]:
    observed = []
    for path in exact.retirement_barrier_paths(source):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        data = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
        observed.append((path.name, metadata.st_ino, metadata.st_mode, data))
    return tuple(observed)


def main_post_source(lane: str) -> dict:
    if lane == "quote_image":
        text = "A reviewed quotation."
        quote_hash = bot.quote_text_hash(text)
        source = bot.build_main_post_attempt(
            lane=lane,
            text=text,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "reviewed.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 7200,
                "meme_delay_seconds": 3600,
                "meme_scheduling_enabled": True,
                "meme_trigger_after_hour": 12,
                "meme_schedule_version": 2,
                "meme_schedule_before": bot.bound_meme_schedule_state({}),
                "quote_history_after": [quote_hash],
                "image_history_after": ["reviewed.jpg"],
            },
            attempt_epoch=1_800_000_000,
        )
    else:
        source = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": 2,
                "fallback_hour": 16,
                "fallback_minute": 0,
                "image_summary": "A reviewed poster.",
            },
            attempt_epoch=1_800_000_000,
        )
    source["attempt_id"] = hashlib.sha256(
        f"restart-{lane}".encode("utf-8")
    ).hexdigest()
    source["lifecycle_state"] = "attempting"
    assert bot.main_post_attempt_is_semantically_valid(source)
    return source


def production_lane_documents(
    lane: str,
) -> tuple[dict, dict, bytes, bytes, str]:
    """Build source/confirmed documents through each production serializer."""

    post_id = {
        "quote_image": "951001",
        "daily_meme": "951002",
        "conversational_reply": "951003",
        "historical_context_reply": "951004",
    }[lane]
    if lane in {"quote_image", "daily_meme"}:
        source = main_post_source(lane)
        pending = bot.build_confirmed_pending_schedule_receipt(
            source,
            post_id=post_id,
            confirmation_epoch=1_800_000_010,
            image_summary=(
                str(source["recovery_plan"]["image_summary"])
                if lane == "daily_meme"
                else ""
            ),
        )
        confirmed = (
            bot.materialize_bound_regular_schedule_receipt(pending)
            if lane == "quote_image"
            else bot.materialize_bound_meme_schedule_receipt(pending)
        )
        source_bytes = bot.canonical_atomic_json_bytes(source)
        current_bytes = bot.canonical_atomic_json_bytes(confirmed)
    elif lane == "conversational_reply":
        source = {
            "schema_version": 4,
            "lifecycle_state": "sending",
            "target_id": "111",
            "author_id": "42",
            "candidate_source": "mention",
            "conversation_id": "111",
            "reply_text": "A reviewed reply.",
            "reply_context": {
                "target_id": "111",
                "thread_id": "111",
                "lane": "mention",
            },
            "ai_reply_draft": {},
            "attempt_epoch": 1_800_000_000,
            "reply_epoch": 1_800_000_000,
            "daily_reply_date": bot.epoch_date_str(1_800_000_000),
        }
        confirmed = bot._confirmed_reply_receipt_from_sending(
            source,
            reply_post_id=post_id,
            confirmation_epoch=1_800_000_010,
        )
        source_bytes = bot.canonical_atomic_json_bytes(source)
        current_bytes = bot.canonical_atomic_json_bytes(confirmed)
    else:
        source = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "111",
            "quote_id": "a" * 64,
            "reply_text": "Verified historical context.",
            "reply_epoch": 1_800_000_000,
            "started_at": "2027-01-15T08:00:00Z",
            "attempt_number": 1,
        }
        source_bytes = context.canonical_json_bytes(source)
        confirmed = {
            **copy.deepcopy(source),
            "lifecycle_state": "confirmed",
            "reply_post_id": post_id,
            "confirmed_at": "2027-01-15T08:00:10Z",
            "source_receipt_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        assert context.HistoricalContextReplyStore._valid_receipt(confirmed)
        current_bytes = context.canonical_json_bytes(confirmed)

    reconstructed = bot.expected_lane_transport_source_receipt_bytes(
        receipt=confirmed,
        lane=lane,
        current_receipt_bytes=current_bytes,
    )
    assert reconstructed == source_bytes
    return source, confirmed, source_bytes, current_bytes, post_id


def install_confirmed_lane_transaction(
    tmp_path: Path,
    lane: str,
) -> tuple[Path, Path, bytes, Path]:
    journal.reset_consumed_authorities_for_tests()
    source, confirmed, source_bytes, current_bytes, post_id = (
        production_lane_documents(lane)
    )
    receipt_path = tmp_path / {
        "quote_image": "regular_post_receipt.json",
        "daily_meme": "meme_post_receipt.json",
        "conversational_reply": "confirmed_reply_receipt.json",
        "historical_context_reply": "historical_context_reply_receipt.json",
    }[lane]
    write_exact(receipt_path, source_bytes)
    payload = {"text": f"reviewed {lane}"}
    authority = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=source,
        lane=lane,
        payload=payload,
        source_validator_id="tests.source-retirement-production-lanes.v1",
        source_validator=lambda *_args: True,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    authority = _arm_transport_transaction(journal_path, authority)
    journal.consume_transport_authority(
        journal_path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(
        journal_path,
        authority,
        post_id=post_id,
        confirmation_epoch=1_800_000_010,
    )
    write_exact(receipt_path, current_bytes)

    manifest_path = tmp_path / "restart-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "receipt_path": str(receipt_path),
                "confirmed": confirmed,
                "lane": lane,
                "post_id": post_id,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    unrelated = tmp_path / "unrelated-recovery-state.json"
    write_exact(unrelated, b'{"owner":"another-lane"}\n')
    return receipt_path, manifest_path, current_bytes, unrelated


@pytest.mark.parametrize("lane_index", range(4))
def test_pause_snapshot_preserves_then_resumes_each_lane_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_index: int,
) -> None:
    paths = configure_lane_paths(monkeypatch, tmp_path)
    source = paths[lane_index]
    data = b'{"confirmed":true,"lane":%d}\n' % lane_index
    write_exact(source, data)
    _prepare_exact_receipt_retirement(source, data)
    before = namespace_snapshot(source)

    assert bot.resume_source_receipt_retirement_for_control_snapshot(
        maintenance_paused=True,
    ) is False
    assert namespace_snapshot(source) == before

    assert bot.resume_source_receipt_retirement_for_control_snapshot(
        maintenance_paused=False,
    ) is True
    assert namespace_snapshot(source) == ()


def test_central_resumer_rejects_multiple_active_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = configure_lane_paths(monkeypatch, tmp_path)
    for index, source in enumerate(paths[:2]):
        data = b'{"confirmed":true,"lane":%d}\n' % index
        write_exact(source, data)
        _prepare_exact_receipt_retirement(source, data)

    with pytest.raises(
        exact.ExactReceiptRetirementError,
        match="multiple source-receipt retirement lanes",
    ):
        bot.resume_interrupted_source_receipt_retirement_if_present()


def test_prepared_source_guard_overlaps_journal_restart_without_hard_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal.reset_consumed_authorities_for_tests()
    source_path = tmp_path / "receipt.json"
    source = {"schema_version": 1, "lifecycle_state": "attempting", "token": "x"}
    source_bytes = journal.canonical_json_bytes(source)
    write_exact(source_path, source_bytes)
    payload = {"text": "reviewed"}
    authority = journal.begin_transport_transaction(
        receipt_path=source_path,
        expected_receipt=source,
        lane="test_lane",
        payload=payload,
        source_validator_id="tests.source-retirement-overlap.v1",
        source_validator=lambda *_args: True,
    )
    journal_path = journal.journal_path_for_receipt(source_path)
    authority = _arm_transport_transaction(journal_path, authority)
    journal.consume_transport_authority(
        journal_path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(
        journal_path,
        authority,
        post_id="123",
        confirmation_epoch=10,
    )
    confirmed = {**source, "lifecycle_state": "confirmed", "post_id": "123"}
    confirmed_bytes = journal.canonical_json_bytes(confirmed)
    write_exact(source_path, confirmed_bytes)
    _prepare_exact_receipt_retirement(source_path, confirmed_bytes)
    source_inode = source_path.stat().st_ino
    original_unlink = journal.os.unlink
    fence_name = journal.fence_path_for_journal(journal_path).name
    interrupted = False

    def fail_after_fence(name: str, *args: object, **kwargs: object) -> None:
        nonlocal interrupted
        original_unlink(name, *args, **kwargs)
        if name == fence_name and not interrupted:
            interrupted = True
            raise OSError("hard exit after fence retirement")

    monkeypatch.setattr(journal.os, "unlink", fail_after_fence)
    with pytest.raises(OSError, match="hard exit after fence"):
        _retire_confirmed_transport_transaction(
            receipt_path=source_path,
            expected_confirmed_receipt=confirmed,
            expected_source_receipt_bytes=source_bytes,
            expected_current_receipt_bytes=confirmed_bytes,
            source_retirement_prepared=True,
            lane="test_lane",
            post_id="123",
        )
    assert source_path.stat().st_ino == source_inode
    assert source_path.stat().st_nlink == 1
    assert exact.inspect_exact_receipt_retirement(
        source_path,
        confirmed_bytes,
    ).phase == "prepared"

    monkeypatch.setattr(journal.os, "unlink", original_unlink)
    _retire_confirmed_transport_transaction(
        receipt_path=source_path,
        expected_confirmed_receipt=confirmed,
        expected_source_receipt_bytes=source_bytes,
        expected_current_receipt_bytes=confirmed_bytes,
        source_retirement_prepared=True,
        lane="test_lane",
        post_id="123",
    )
    _resume_interrupted_receipt_retirement(source_path)
    assert not any(
        os.path.lexists(path)
        for path in exact.retirement_barrier_paths(source_path)
    )


def test_all_twenty_auxiliaries_are_inventoried_and_markers_are_private(
    tmp_path: Path,
) -> None:
    assert len(activate.RECEIPT_RETIREMENT_AUXILIARY_BASENAMES) == 20
    assert len(set(activate.RECEIPT_RETIREMENT_AUXILIARY_BASENAMES)) == 20
    source = tmp_path / "receipt.json"
    data = b'{"confirmed":true}\n'
    write_exact(source, data)
    _prepare_exact_receipt_retirement(source, data)
    guard = exact.retirement_auxiliary_paths(source)[0]
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert stat.S_IMODE(guard.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "lane",
    [
        "quote_image",
        "daily_meme",
        "conversational_reply",
        "historical_context_reply",
    ],
)
@pytest.mark.parametrize(
    "crash_point",
    [
        "prepared_guard",
        "journal_fence_unlink",
        "journal_unlink",
        "exact_source_retirement",
    ],
)
def test_fresh_process_resumes_every_production_lane_crash_boundary(
    tmp_path: Path,
    lane: str,
    crash_point: str,
) -> None:
    receipt_path, manifest_path, current_bytes, unrelated = (
        install_confirmed_lane_transaction(tmp_path, lane)
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    fence_path = journal.fence_path_for_journal(journal_path)

    crashed = run_restart_driver(manifest_path, "crash", crash_point)
    assert crashed.returncode in {81, 82, 83}, crashed.stderr
    assert unrelated.read_bytes() == b'{"owner":"another-lane"}\n'

    if crash_point == "prepared_guard":
        assert journal_path.is_file()
        assert fence_path.is_file()
        assert receipt_path.read_bytes() == current_bytes
    elif crash_point == "journal_fence_unlink":
        assert journal_path.is_file()
        assert not os.path.lexists(fence_path)
        assert receipt_path.read_bytes() == current_bytes
    elif crash_point == "journal_unlink":
        assert not os.path.lexists(journal_path)
        assert not os.path.lexists(fence_path)
        assert receipt_path.read_bytes() == current_bytes
    else:
        assert not os.path.lexists(journal_path)
        assert not os.path.lexists(fence_path)
        assert not os.path.lexists(receipt_path)

    assert any(
        os.path.lexists(path)
        for path in exact.retirement_auxiliary_paths(receipt_path)
    )
    resumed = run_restart_driver(manifest_path, "resume", crash_point)
    assert resumed.returncode == 0, resumed.stderr
    assert not os.path.lexists(journal_path)
    assert not os.path.lexists(fence_path)
    assert not any(
        os.path.lexists(path)
        for path in exact.retirement_barrier_paths(receipt_path)
    )
    assert unrelated.read_bytes() == b'{"owner":"another-lane"}\n'


@pytest.mark.parametrize(
    "crash_point",
    ["prepared_guard", "exact_source_retirement"],
)
@pytest.mark.parametrize("mutation", ["same_bytes_replacement", "directory"])
def test_fresh_process_resume_fails_closed_on_replacement_or_type_mutation(
    tmp_path: Path,
    crash_point: str,
    mutation: str,
) -> None:
    receipt_path, manifest_path, current_bytes, unrelated = (
        install_confirmed_lane_transaction(tmp_path, "quote_image")
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    fence_path = journal.fence_path_for_journal(journal_path)
    original_inode = receipt_path.stat().st_ino
    crashed = run_restart_driver(manifest_path, "crash", crash_point)
    assert crashed.returncode in {81, 83}, crashed.stderr

    if mutation == "same_bytes_replacement":
        replacement = receipt_path.with_name(f".{receipt_path.name}.intruder")
        write_exact(replacement, current_bytes)
        assert replacement.stat().st_ino != original_inode
        os.replace(replacement, receipt_path)
    else:
        if os.path.lexists(receipt_path):
            receipt_path.unlink()
        receipt_path.mkdir()

    namespace_before = namespace_snapshot(receipt_path)
    failed = run_restart_driver(manifest_path, "resume", crash_point)
    assert failed.returncode != 0
    assert namespace_snapshot(receipt_path) == namespace_before
    if mutation == "same_bytes_replacement":
        assert receipt_path.read_bytes() == current_bytes
    else:
        assert receipt_path.is_dir()
    assert unrelated.read_bytes() == b'{"owner":"another-lane"}\n'
    if crash_point == "prepared_guard":
        assert journal_path.is_file()
        assert fence_path.is_file()
    else:
        assert not os.path.lexists(journal_path)
        assert not os.path.lexists(fence_path)
