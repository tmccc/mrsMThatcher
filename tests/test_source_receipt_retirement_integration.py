from __future__ import annotations

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

from tests.helpers.receipt_fixtures import production_lane_documents


def _mutation_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="source-retirement integration test",
    )


def _prepare_exact_receipt_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    source = Path(args[0])
    ledger, exchange = exact.retirement_ledger_paths(source)
    if not os.path.lexists(ledger) and not os.path.lexists(exchange):
        exact.initialise_retirement_ledger(
            source,
            mutation_authority=_mutation_authority(),
        )
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
        kwargs.setdefault("mutation_authority", bot.state_commit_mutation_authority(commit_proof, "restart exact retirement"))
        return exact.resume_interrupted_receipt_retirement(*args, **kwargs)

    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    action = sys.argv[2]
    point = sys.argv[3]
    receipt_path = Path(manifest["receipt_path"])
    receipt = manifest["confirmed"]
    lane = manifest["lane"]
    post_id = manifest["post_id"]
    bot.STATE_FILE = receipt_path.parent / "bot_state.json"
    bot.STATE_BACKUP_COUNT = 2
    bot.REGULAR_POST_RECEIPT_FILE = receipt_path.parent / "regular_post_receipt.json"
    bot.MEME_POST_RECEIPT_FILE = receipt_path.parent / "meme_post_receipt.json"
    bot.CONFIRMED_REPLY_RECEIPT_FILE = receipt_path.parent / "confirmed_reply_receipt.json"
    bot.LINES_USED_FILE = receipt_path.parent / "lines_used.json"
    bot.IMAGES_USED_FILE = receipt_path.parent / "images_used.json"
    commit_proof = None
    if lane != "historical_context_reply":
        if action == "crash":
            from mrs_bot_state_generation import record_receipt_commit
            state = bot.default_state()
            record_receipt_commit(state, receipt)
            if lane == "quote_image":
                lines, images = set(), set()
                bot._main_post_assembly().recovery_operation().apply_regular(receipt, lines, images, state)
                commit_proof = bot.save_regular_post_protected_state(lines, images, state, durable=True)
            else:
                if lane == "daily_meme":
                    bot._main_post_assembly().recovery_operation().apply_meme(receipt, state)
                else:
                    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)
                commit_proof = bot.save_state(state, durable=True)
        else:
            commit_proof = bot.recover_state_receipt_commit_proof(receipt_path)

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
            commit_proof=commit_proof,
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
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
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
    exact.initialise_retirement_ledger(
        receipt_path,
        mutation_authority=_mutation_authority(),
    )
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
    if lane_index == 3:
        from historical_context_outbox import HistoricalContextOutbox

        _sending, confirmed, _sending_bytes, data, reply_post_id = (
            production_lane_documents("historical_context_reply")
        )
        history_path = tmp_path / "historical_context_reply_history.json"
        outbox_path = tmp_path / "historical_context_reply_outbox.json"
        monkeypatch.setattr(
            bot,
            "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
            history_path,
        )
        monkeypatch.setattr(
            bot,
            "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
            outbox_path,
        )
        context.atomic_write_json(
            history_path,
            {
                "schema_version": 1,
                "items": {
                    confirmed["parent_post_id"]: {
                        **confirmed,
                        "status": "completed",
                    }
                },
            },
        )
        outbox = HistoricalContextOutbox(outbox_path)
        outbox.enqueue(
            confirmed["parent_post_id"],
            main_post_confirmed_epoch=1_800_000_000,
            quote_id=confirmed["quote_id"],
            quote_text="A reviewed historical-context quotation.",
        )
        outbox.claim_attempt(
            confirmed["parent_post_id"],
            started_epoch=1_800_000_001,
        )
        outbox.bind_attempt_source_receipt(
            confirmed["parent_post_id"],
            attempt_number=1,
            source_receipt_sha256=confirmed["source_receipt_sha256"],
            source_receipt_attempt_number=confirmed["attempt_number"],
        )
        outbox.mark_remote_transaction_started(
            confirmed["parent_post_id"],
            attempt_number=1,
        )
        outbox.record_confirmed(
            confirmed["parent_post_id"],
            attempt_number=1,
            reply_post_id=reply_post_id,
            confirmed_epoch=1_800_000_010,
        )
    else:
        from mrs_bot_state_generation import record_receipt_commit
        lane = ("quote_image", "daily_meme", "conversational_reply")[lane_index]
        _sending, confirmed, _sending_bytes, data, _post_id = production_lane_documents(lane)
        state = bot.default_state()
        record_receipt_commit(state, confirmed)
        if lane == "quote_image":
            lines, images = set(), set()
            bot._main_post_assembly().recovery_operation().apply_regular(confirmed, lines, images, state)
            bot.save_regular_post_protected_state(lines, images, state, durable=True)
        else:
            if lane == "daily_meme":
                bot._main_post_assembly().recovery_operation().apply_meme(confirmed, state)
            else:
                bot._reply_assembly()._confirmed_reply_state_applier()(state, confirmed)
            bot.save_state(state, durable=True)
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
        confirmation_epoch=1_800_000_010,
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


NON_SUCCESS_RESTART_DRIVER = textwrap.dedent(
    r"""
    import json
    import os
    import socket
    import sys
    from pathlib import Path

    def forbidden_network(*args, **kwargs):
        raise AssertionError("retirement recovery must stay offline")

    socket.create_connection = socket.socket.connect = forbidden_network
    os.environ["MRS_BASE_DIR"] = sys.argv[1]
    os.environ["MRS_LOG_FILE"] = str(Path(sys.argv[1]) / "test.log")
    import exact_receipt_retirement as exact
    import mrsMThatcher2 as bot
    from tests.helpers.receipt_fixtures import production_lane_documents

    lane, action, point = sys.argv[2:5]
    source, _, _, _, _ = production_lane_documents(lane)
    if lane != "conversational_reply":
        source["lifecycle_state"] = sys.argv[5]
    path = {
        "quote_image": bot.REGULAR_POST_RECEIPT_FILE,
        "daily_meme": bot.MEME_POST_RECEIPT_FILE,
        "conversational_reply": bot.CONFIRMED_REPLY_RECEIPT_FILE,
    }[lane]
    if action == "crash":
        exact.initialise_retirement_ledger(
            path, mutation_authority=bot.transaction_mutation_authority("test setup")
        )
        path.write_bytes(bot.canonical_atomic_json_bytes(source))
        path.chmod(0o600)
        original_move = exact._move_exact_to_cleanup

        def interrupted_move(*args, **kwargs):
            if point == "prepared_guard":
                os._exit(81)
            original_move(*args, **kwargs)
            if point == "source_moved" or kwargs.get("source_name") == exact.retirement_auxiliary_paths(path)[1].name:
                os._exit(82)

        exact._move_exact_to_cleanup = interrupted_move
        if lane == "conversational_reply":
            bot.remove_confirmed_reply_receipt(source, sending_disposition="definite_non_success")
        else:
            bot.remove_main_post_attempt(source, sending_disposition="definite_non_success")
        raise AssertionError("crash point was not reached")
    try:
        bot.reconcile_runtime_historical_context_state()
    except BaseException:
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        raise
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert not any(os.path.lexists(p) for p in exact.retirement_barrier_paths(path))
    assert exact.inspect_retirement_ledger(path).state == "completed"
    """
)


def run_non_success_restart(tmp_path, lane, action, point, lifecycle="sending"):
    return subprocess.run(
        [sys.executable, "-c", NON_SUCCESS_RESTART_DRIVER,
         str(tmp_path), lane, action, point, lifecycle],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True,
        check=False, timeout=20,
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme", "conversational_reply"])
@pytest.mark.parametrize("point", ["prepared_guard", "source_moved", "commit_guard_moved"])
def test_non_success_retirement_resumes_through_real_root_startup(tmp_path, lane, point):
    crashed = run_non_success_restart(tmp_path, lane, "crash", point)
    assert crashed.returncode in {81, 82}, crashed.stderr
    resumed = run_non_success_restart(tmp_path, lane, "resume", point)
    assert resumed.returncode == 0, resumed.stderr


@pytest.mark.parametrize("mutation", [
    "legacy_disposition", "null_disposition", "unknown_disposition",
    "forged_hash", "wrong_lane", "source_replaced", "source_symlink",
    "blocking_journal", "ledger_disposition",
])
def test_non_success_restart_preserves_invalid_or_unauthorised_namespace(tmp_path, mutation):
    point = "commit_guard_moved" if mutation == "ledger_disposition" else "prepared_guard"
    crashed = run_non_success_restart(tmp_path, "quote_image", "crash", point)
    assert crashed.returncode in {81, 82}, crashed.stderr
    path = tmp_path / "regular_post_receipt.json"
    guard = exact.retirement_auxiliary_paths(path)[0]
    changed = guard
    if mutation == "source_replaced":
        replacement = path.with_name("replacement")
        write_exact(replacement, path.read_bytes())
        os.replace(replacement, path)
    elif mutation == "source_symlink":
        replacement = path.with_name("replacement")
        path.rename(replacement)
        path.symlink_to(replacement)
    elif mutation == "blocking_journal":
        changed = journal.journal_path_for_receipt(path)
        write_exact(changed, b'{"unproved":"remote outcome"}\n')
    else:
        if mutation == "ledger_disposition":
            changed = exact.retirement_ledger_path_for_receipt(path)
        document = json.loads(changed.read_bytes())
        if mutation == "legacy_disposition":
            document.pop("disposition")
        elif mutation == "null_disposition":
            document["disposition"] = None
        elif mutation == "unknown_disposition":
            document["disposition"] = "confirmed_state_fallback"
        elif mutation == "forged_hash":
            document["expected_sha256"] = "0" * 64
        elif mutation == "wrong_lane":
            document["source_basename"] = "meme_post_receipt.json"
        else:
            document["source_binding"].pop("disposition")
        write_exact(changed, exact._canonical_json(document))
    before = namespace_snapshot(path)
    changed_bytes = changed.read_bytes()
    resumed = run_non_success_restart(tmp_path, "quote_image", "resume", point)
    assert resumed.returncode != 0
    assert namespace_snapshot(path) == before
    assert changed.read_bytes() == changed_bytes


@pytest.mark.parametrize("replacement_kind", ["same_bytes", "confirmed"])
def test_non_success_root_decision_binds_source_identity_through_resume(
    tmp_path, monkeypatch, replacement_kind,
):
    crashed = run_non_success_restart(tmp_path, "quote_image", "crash", "prepared_guard")
    assert crashed.returncode == 81, crashed.stderr
    path = configure_lane_paths(monkeypatch, tmp_path)[0]
    original_resume = bot.resume_interrupted_receipt_retirement
    substituted = []

    def replace_before_resume(source, **kwargs):
        guard = exact.retirement_auxiliary_paths(source)[0]
        marker = json.loads(guard.read_bytes())
        data = source.read_bytes()
        if replacement_kind == "confirmed":
            _, _, _, data, _ = production_lane_documents("quote_image")
            marker.pop("disposition")
        replacement = source.with_name("replacement")
        write_exact(replacement, data)
        os.replace(replacement, source)
        marker["source_identity"] = exact.FileIdentity.from_stat(source.stat()).to_document()
        marker["expected_sha256"] = hashlib.sha256(data).hexdigest()
        marker["expected_size"] = len(data)
        write_exact(guard, exact._canonical_json(marker))
        assert exact.inspect_interrupted_receipt_retirement(source).valid
        substituted.append(namespace_snapshot(source))
        return original_resume(source, **kwargs)

    monkeypatch.setattr(bot, "resume_interrupted_receipt_retirement", replace_before_resume)
    with pytest.raises(exact.ExactReceiptRetirementError, match="authorised disposition"):
        bot.resume_interrupted_source_receipt_retirement_if_present()
    assert len(substituted) == 1
    assert namespace_snapshot(path) == substituted[0]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_non_success_attempting_receipt_resumes_after_proved_failure(tmp_path, lane):
    crashed = run_non_success_restart(
        tmp_path, lane, "crash", "prepared_guard", lifecycle="attempting",
    )
    assert crashed.returncode == 81, crashed.stderr
    resumed = run_non_success_restart(tmp_path, lane, "resume", "prepared_guard")
    assert resumed.returncode == 0, resumed.stderr
