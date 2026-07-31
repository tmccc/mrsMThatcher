"""Adversarial confirmed-receipt directory-durability boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import fcntl
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.test_unit_helpers import (
    UNIT_REPLY_REPOSITORY,
    bot,
    configure_simple_meme_post,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    unit_sending_v4_reply_receipt,
)


CONFIRMATION_EPOCH = 1_800_000_000


def _establish_current_barrier_pair() -> None:
    """Add and synchronise the successor for a test-created legacy pathname."""

    os.link(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        follow_symlinks=False,
    )
    descriptor = os.open(
        bot.AMBIGUOUS_POST_OUTCOME_FILE.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@pytest.fixture(autouse=True)
def isolate_transaction_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every adversarial durability artefact inside the test directory."""
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        tmp_path / "regular_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        tmp_path / "meme_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        tmp_path / "confirmed_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        tmp_path / "ambiguous_post_outcome.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        tmp_path / "ambiguous_post_outcome.restart_barrier.json",
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        tmp_path / ".mrs_remote_write_safety_protocol_v1",
    )
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        tmp_path / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        tmp_path / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {
            "signature": None,
            "data": {},
            "has_valid": False,
            "failure_signature": None,
        },
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        None,
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON",
        None,
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_SEMANTIC_GATE",
        SimpleNamespace(
            available=True,
            ledger_sha256="unit-test-ledger",
            projection_sha256="unit-test-projection",
            disposition=lambda _quote_id: None,
        ),
    )
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: UNIT_REPLY_REPOSITORY,
    )
    monkeypatch.setattr(
        bot,
        "completed_research_quote_hashes",
        lambda: {
            bot.quote_text_hash(line)
            for line in Path(bot.LINES_FILE)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        },
    )


def _install_production_instance_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ofd_owner: bool = True,
) -> tuple[Path, object, socket.socket, int]:
    """Install one production-equivalent lock and abstract singleton."""

    lock_path = tmp_path / "mrsMThatcher.lock"
    lock_path.write_text(f"pid={os.getpid()}\n", encoding="ascii")
    held = lock_path.open("r+", encoding="ascii")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    if ofd_owner:
        fcntl.fcntl(
            held.fileno(),
            fcntl.F_OFD_SETLK,
            bot.ofd_lock_record(fcntl.F_WRLCK),
        )
    held_stat = os.fstat(held.fileno())
    singleton_name = bot.instance_lock_abstract_socket_name(tmp_path)
    singleton = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    singleton.bind(singleton_name)
    state_directory_fd = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    fcntl.flock(
        state_directory_fd,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )
    state_directory_identity = os.fstat(state_directory_fd)
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", held)
    monkeypatch.setattr(
        bot,
        "_LOCK_ACQUISITION_IDENTITY",
        (held_stat.st_dev, held_stat.st_ino, os.getpid()),
    )
    monkeypatch.setattr(bot, "_LOCK_SOCKET", singleton)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", singleton_name)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", state_directory_fd)
    monkeypatch.setattr(
        bot,
        "_STATE_DIR_LOCK_IDENTITY",
        (state_directory_identity.st_dev, state_directory_identity.st_ino),
    )
    return lock_path, held, singleton, state_directory_fd


def _receipt_path(lane: str) -> Path:
    return (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )


def _load_lane_receipt(lane: str) -> tuple[str, dict | None]:
    return (
        bot.load_regular_post_receipt()
        if lane == "quote_image"
        else bot.load_meme_post_receipt()
    )


def _install_pending_receipt(lane: str) -> dict:
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 7_200,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "meme_schedule_before": bot.bound_meme_schedule_state({}),
                "quote_history_after": [quote_hash],
                "image_history_after": ["t01.jpg"],
            },
            attempt_epoch=CONFIRMATION_EPOCH,
        )
    else:
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
                "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
            },
            attempt_epoch=CONFIRMATION_EPOCH,
        )
    bot.write_main_post_attempt(attempt)
    attempting = bot.mark_main_post_attempt_attempting(attempt)
    pending = bot.promote_main_post_attempt_to_confirmed_pending_schedule(
        attempting,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=CONFIRMATION_EPOCH,
        image_summary="" if lane == "quote_image" else "Unit meme",
    )
    assert _load_lane_receipt(lane) == ("pending_schedule", pending)
    return pending


def _configure_confirmed_lane(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Callable[[], None], Path]:
    if lane == "quote_image":
        lines_used, images_used, state, *_paths = configure_simple_quote_post(
            tmp_path,
            monkeypatch,
        )

        def invoke() -> None:
            bot.post_random_quote(lines_used, images_used, state)

    else:
        state, _receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
        monkeypatch.setattr(
            bot,
            "create_post",
            lambda **kwargs: mock_confirmed_main_post(
                kwargs,
                {"data": {"id": "970001"}},
            ),
        )

        def invoke() -> None:
            bot.post_next_meme(state)

    return invoke, _receipt_path(lane)


def _inject_pending_parent_fsync_failure(
    monkeypatch: pytest.MonkeyPatch,
    receipt_path: Path,
    *,
    once: bool,
) -> list[dict]:
    """Fail only after replacement has made the pending receipt visible."""
    original_fsync_parent_dir = bot.fsync_parent_dir
    failures: list[dict] = []

    def adversarial_fsync(path: Path, *, strict: bool = False) -> None:
        resolved = Path(path)
        if resolved == receipt_path and resolved.exists():
            try:
                current = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = None
            if (
                isinstance(current, dict)
                and current.get("receipt_type") == "confirmed_pending_schedule"
                and (not once or not failures)
            ):
                failures.append(current)
                raise OSError(
                    "injected parent-directory fsync failure after pending replace"
                )
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", adversarial_fsync)
    return failures


def _actual_conversational_create_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    actual_create_post: Callable[..., dict],
) -> None:
    """Exercise the real reply transaction through its X-create preflight."""
    reply = unit_sending_v4_reply_receipt(
        attempt_epoch=CONFIRMATION_EPOCH,
        target_id="880001",
    )
    x_boundaries: list[tuple[str, str]] = []

    def x_boundary(method: str, path: str, **_kwargs: object) -> dict:
        x_boundaries.append((method, path))
        return {"data": {"id": "980001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    monkeypatch.setattr(bot, "x_request", x_boundary)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=reply,
            reply_text=str(reply["reply_text"]),
            reply_to_id=str(reply["target_id"]),
            made_with_ai=False,
            lane="mention",
        )
    assert x_boundaries == []


def _ambiguous_marker_payload(text: str = "Good quote.") -> dict:
    """Return one structurally complete ambiguity marker for durability tests."""
    return {
        "schema_version": 1,
        "recorded_at_epoch": CONFIRMATION_EPOCH,
        "outcome": "ambiguous_remote_post",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "reply_to_id": "",
        "media_ids": [],
        "made_with_ai": False,
    }


def _install_marker_disappearance_during_parent_fsync(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Callable[..., None], list[Path]]:
    """Remove the marker before performing the real parent-directory fsync."""
    original_fsync_parent_dir = bot.fsync_parent_dir
    removed: list[Path] = []

    def remove_then_fsync(path: Path, *, strict: bool = False) -> None:
        resolved = Path(path)
        if (
            resolved == bot.AMBIGUOUS_POST_OUTCOME_FILE
            and resolved.exists()
            and not removed
        ):
            resolved.unlink()
            removed.append(resolved)
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", remove_then_fsync)
    return original_fsync_parent_dir, removed


def _install_marker_namespace_mutation_during_parent_fsync(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> list[str]:
    """Mutate one acknowledged marker name before the real directory fsync."""
    original_fsync_parent_dir = bot.fsync_parent_dir
    mutations: list[str] = []

    def mutate_then_fsync(path: Path, *, strict: bool = False) -> None:
        resolved = Path(path)
        if (
            resolved == bot.AMBIGUOUS_POST_OUTCOME_FILE
            and os.path.lexists(resolved)
            and not mutations
        ):
            original_bytes = resolved.read_bytes()
            if mutation == "replace":
                replacement = resolved.with_name("replacement-marker.json")
                replacement.write_bytes(original_bytes)
                os.replace(replacement, resolved)
            elif mutation == "content":
                resolved.write_bytes(
                    bot.canonical_atomic_json_bytes(
                        _ambiguous_marker_payload("Changed quote.")
                    )
                )
                with open(resolved, "rb") as handle:
                    os.fsync(handle.fileno())
            elif mutation == "symlink":
                target = resolved.with_name("marker-symlink-target.json")
                target.write_bytes(original_bytes)
                resolved.unlink()
                resolved.symlink_to(target.name)
            elif mutation == "dangling_symlink":
                resolved.unlink()
                resolved.symlink_to("missing-marker-target.json")
            elif mutation == "directory":
                resolved.unlink()
                resolved.mkdir()
            else:  # pragma: no cover - test helper contract
                raise AssertionError(f"unknown marker mutation: {mutation}")
            mutations.append(mutation)
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", mutate_then_fsync)
    return mutations


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_one_shot_pending_parent_fsync_failure_is_revalidated_and_completed(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible exact pending receipt must survive one failed directory fsync."""
    original_signal_handler = signal.getsignal(signal.SIGINT)
    invoke, receipt_path = _configure_confirmed_lane(lane, tmp_path, monkeypatch)
    failures = _inject_pending_parent_fsync_failure(
        monkeypatch,
        receipt_path,
        once=True,
    )

    try:
        invoke()
        assert len(failures) == 1
        assert not receipt_path.exists()
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
        assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert signal.getsignal(signal.SIGINT) == original_signal_handler
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize(
    "marker_failure_mode",
    ["none", "before_write", "after_replace"],
)
def test_persistent_pending_parent_fsync_failure_latches_before_other_writes(
    lane: str,
    marker_failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uncertain receipt durability must latch before any unrelated X create."""
    actual_create_post = bot.create_post
    original_signal_handler = signal.getsignal(signal.SIGINT)
    invoke, receipt_path = _configure_confirmed_lane(lane, tmp_path, monkeypatch)
    failures = _inject_pending_parent_fsync_failure(
        monkeypatch,
        receipt_path,
        once=False,
    )
    marker_recovery_fsync: Callable[..., None] | None = None
    if marker_failure_mode == "before_write":
        original_atomic_write_json = bot.atomic_write_json

        def marker_fails(
            path: Path,
            value: object,
            *,
            durable: bool = False,
        ) -> None:
            if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
                raise OSError("injected ambiguity-marker failure")
            original_atomic_write_json(path, value, durable=durable)

        monkeypatch.setattr(bot, "atomic_write_json", marker_fails)
    elif marker_failure_mode == "after_replace":
        receipt_fsync = bot.fsync_parent_dir
        marker_recovery_fsync = receipt_fsync

        def marker_parent_fsync_fails(
            path: Path,
            *,
            strict: bool = False,
        ) -> None:
            if (
                Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
                and Path(path).exists()
            ):
                raise OSError(
                    "injected ambiguity-marker parent fsync failure after replace"
                )
            receipt_fsync(path, strict=strict)

        monkeypatch.setattr(bot, "fsync_parent_dir", marker_parent_fsync_fails)

    try:
        with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
            invoke()

        assert failures
        status, pending = _load_lane_receipt(lane)
        assert status == "pending_schedule"
        assert pending == failures[0]
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is (
            marker_failure_mode != "none"
        )
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists() is (
            marker_failure_mode != "before_write"
        )
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists() is (
            marker_failure_mode == "none"
        )
        if marker_failure_mode != "none":
            leaked_handler = signal.getsignal(signal.SIGINT)
            assert getattr(leaked_handler, "__self__", None).__class__ is (
                bot.ConfirmedPostSigintDeferral
            )
            assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is getattr(
                leaked_handler,
                "__self__",
                None,
            )
        else:
            marker = json.loads(
                bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8")
            )
            assert marker["outcome"] == (
                "confirmed_remote_post_local_persistence_failed"
            )
            assert marker["lane"] == lane
            assert marker["post_id"] == (
                "950001" if lane == "quote_image" else "970001"
            )
            assert "pending_schedule_parent_fsync" in marker["failure_components"]
            assert signal.getsignal(signal.SIGINT) == original_signal_handler

        assert bot.durable_remote_write_safety_barrier_exists() is (
            marker_failure_mode == "none"
        )
        if marker_failure_mode == "after_replace":
            # A restart forgets the process-local uncertainty flag.  Marker
            # visibility must still require a fresh successful parent fsync.
            bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
            assert bot.durable_remote_write_safety_barrier_exists() is False
            bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
        if marker_failure_mode == "none":
            bot.wait_for_durable_barrier_before_one_shot_exit(lane=lane)
        else:
            def stop_instead_of_waiting(_seconds: int) -> None:
                raise RuntimeError("one-shot process remained alive")

            monkeypatch.setattr(bot, "sleep", stop_instead_of_waiting)
            with pytest.raises(RuntimeError, match="remained alive"):
                bot.wait_for_durable_barrier_before_one_shot_exit(lane=lane)
        _actual_conversational_create_is_blocked(
            monkeypatch,
            actual_create_post,
        )
        if marker_failure_mode == "after_replace":
            assert marker_recovery_fsync is not None
            retained = bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD
            assert isinstance(retained, bot.ConfirmedPostSigintDeferral)
            delivered: list[int] = []

            def delivered_handler(signum: int, _frame: object | None) -> None:
                delivered.append(signum)

            retained.previous_handler = delivered_handler
            retained.handle(signal.SIGINT, None)
            monkeypatch.setattr(
                bot,
                "fsync_parent_dir",
                marker_recovery_fsync,
            )

            assert bot.durable_remote_write_safety_barrier_exists() is True
            assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
            assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
            assert signal.getsignal(signal.SIGINT) is delivered_handler
            assert delivered == [signal.SIGINT]
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_changed_pending_receipt_bytes_fail_closed_before_other_writes(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible but non-identical promoted receipt must never be trusted."""
    actual_create_post = bot.create_post
    prior_handler = signal.getsignal(signal.SIGINT)
    invoke, receipt_path = _configure_confirmed_lane(lane, tmp_path, monkeypatch)
    original_fsync_parent_dir = bot.fsync_parent_dir
    changed = False

    def corrupt_after_replace(path: Path, *, strict: bool = False) -> None:
        nonlocal changed
        resolved = Path(path)
        if resolved == receipt_path and resolved.exists() and not changed:
            current = json.loads(resolved.read_text(encoding="utf-8"))
            if current.get("receipt_type") == "confirmed_pending_schedule":
                current["post_id"] = "999999"
                resolved.write_text(
                    json.dumps(current, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                changed = True
                raise OSError("injected changed pending receipt after replace")
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", corrupt_after_replace)
    try:
        with pytest.raises(bot.ConfirmedPendingScheduleDurabilityUncertain):
            invoke()
        assert changed is True
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        marker = json.loads(
            bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8")
        )
        assert "pending_schedule_receipt_identity" in marker["failure_components"]
        _actual_conversational_create_is_blocked(
            monkeypatch,
            actual_create_post,
        )
    finally:
        signal.signal(signal.SIGINT, prior_handler)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_semantically_identical_noncanonical_pending_bytes_fail_closed(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsed JSON equality cannot substitute for the exact replaced bytes."""
    actual_create_post = bot.create_post
    prior_handler = signal.getsignal(signal.SIGINT)
    invoke, receipt_path = _configure_confirmed_lane(lane, tmp_path, monkeypatch)
    original_fsync_parent_dir = bot.fsync_parent_dir
    reformatted: list[dict] = []

    def reformat_after_replace(path: Path, *, strict: bool = False) -> None:
        resolved = Path(path)
        if resolved == receipt_path and resolved.exists() and not reformatted:
            current = json.loads(resolved.read_text(encoding="utf-8"))
            if current.get("receipt_type") == "confirmed_pending_schedule":
                reformatted.append(current)
                resolved.write_text(
                    json.dumps(current, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                raise OSError("injected noncanonical bytes after replace")
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", reformat_after_replace)
    try:
        with pytest.raises(bot.ConfirmedPendingScheduleDurabilityUncertain):
            invoke()
        assert reformatted
        assert json.loads(receipt_path.read_text(encoding="utf-8")) == reformatted[0]
        assert receipt_path.read_bytes() != bot.canonical_atomic_json_bytes(
            reformatted[0]
        )
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        _actual_conversational_create_is_blocked(
            monkeypatch,
            actual_create_post,
        )
    finally:
        signal.signal(signal.SIGINT, prior_handler)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_bytes_changed_between_refsync_and_second_read_fail_closed(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-fsync exact-byte reread must detect a concurrent byte change."""
    actual_create_post = bot.create_post
    prior_handler = signal.getsignal(signal.SIGINT)
    invoke, receipt_path = _configure_confirmed_lane(lane, tmp_path, monkeypatch)
    original_fsync_parent_dir = bot.fsync_parent_dir
    pending_fsync_calls = 0
    rewritten: list[dict] = []

    def mutate_after_recovery_fsync(path: Path, *, strict: bool = False) -> None:
        nonlocal pending_fsync_calls
        resolved = Path(path)
        if resolved == receipt_path and resolved.exists():
            current = json.loads(resolved.read_text(encoding="utf-8"))
            if current.get("receipt_type") == "confirmed_pending_schedule":
                pending_fsync_calls += 1
                if pending_fsync_calls == 1:
                    raise OSError("injected initial pending parent-fsync failure")
                if pending_fsync_calls == 2:
                    original_fsync_parent_dir(path, strict=strict)
                    rewritten.append(current)
                    resolved.write_text(
                        json.dumps(current, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    return
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", mutate_after_recovery_fsync)
    try:
        with pytest.raises(bot.ConfirmedPendingScheduleDurabilityUncertain):
            invoke()
        assert pending_fsync_calls == 2
        assert rewritten
        assert json.loads(receipt_path.read_text(encoding="utf-8")) == rewritten[0]
        assert receipt_path.read_bytes() != bot.canonical_atomic_json_bytes(
            rewritten[0]
        )
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        _actual_conversational_create_is_blocked(
            monkeypatch,
            actual_create_post,
        )
    finally:
        signal.signal(signal.SIGINT, prior_handler)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_schedule_receipt_blocks_actual_conversational_create_preflight(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending confirmed main post must block an unrelated reply X write."""
    actual_create_post = bot.create_post
    pending = _install_pending_receipt(lane)
    assert _load_lane_receipt(lane) == ("pending_schedule", pending)
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    # Scheduler-level ambiguity detection must remain false so the owning lane
    # can reach local-only reconciliation; every X-create preflight still
    # treats the pending receipt as a cross-lane barrier.
    assert bot.unresolved_main_post_attempt_is_blocking() is False
    assert bot.ambiguous_remote_post_is_blocking() is False

    _actual_conversational_create_is_blocked(monkeypatch, actual_create_post)
    assert _load_lane_receipt(lane) == ("pending_schedule", pending)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_restart_reconciles_pending_receipt_locally_without_x_create(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart may finalise the exact pending plan, but may never post again."""
    pending = _install_pending_receipt(lane)
    x_boundaries: list[tuple[object, ...]] = []

    def forbidden_remote(*args: object, **_kwargs: object) -> dict:
        x_boundaries.append(args)
        pytest.fail("pending-schedule reconciliation must remain local")

    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "create_post", forbidden_remote)
    monkeypatch.setattr(bot, "x_request", forbidden_remote)
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda **_kwargs: [],
    )

    if lane == "quote_image":
        lines_used: set[str] = set()
        images_used: set[str] = set()
        state: dict = {}
        assert bot.reconcile_regular_post_receipt(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=CONFIRMATION_EPOCH,
        )
        assert bot.reconcile_regular_post_receipt(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=CONFIRMATION_EPOCH,
        ) is False
        assert state["last_main_post_id"] == pending["post_id"]
        assert bot.quote_text_hash("Good quote.") in lines_used
        assert "t01.jpg" in images_used
    else:
        state = {}
        assert bot.reconcile_meme_post_receipt(state)
        assert bot.reconcile_meme_post_receipt(state) is False
        assert state["last_main_post_id"] == pending["post_id"]
        assert state["posted_meme_filenames"] == ["001_meme.png"]

    assert not _receipt_path(lane).exists()
    assert x_boundaries == []


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_receipt_defers_context_claim_without_consuming_retry(
    lane: str,
) -> None:
    """A pending main receipt must not consume an auxiliary context attempt."""
    _install_pending_receipt(lane)
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "880099",
        main_post_confirmed_epoch=CONFIRMATION_EPOCH,
        quote_id="a" * 64,
        quote_text="A context obligation awaiting a clear main transaction.",
    )
    before = store.path.read_bytes()
    before_snapshot = store.snapshot()

    assert bot.process_due_historical_context_obligations(limit=1) == []

    assert store.path.read_bytes() == before
    assert store.snapshot() == before_snapshot


def test_recovered_marker_delivers_deferred_sigint_once() -> None:
    """Signal delivery may raise after restoration without retaining stale state."""
    original_signal_handler = signal.getsignal(signal.SIGINT)
    guard = bot.ConfirmedPostSigintDeferral()
    deliveries: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        deliveries.append(signum)
        raise KeyboardInterrupt

    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    bot.AMBIGUOUS_POST_OUTCOME_FILE.write_text("{}\n", encoding="utf-8")
    _establish_current_barrier_pair()

    try:
        with pytest.raises(KeyboardInterrupt):
            bot.durable_remote_write_safety_marker_exists()
        assert deliveries == [signal.SIGINT]
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert signal.getsignal(signal.SIGINT) is delivered_handler
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


def test_marker_disappearance_during_fsync_keeps_durable_helper_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory fsync cannot acknowledge a marker absent from that directory."""
    original_signal_handler = signal.getsignal(signal.SIGINT)
    delivered: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        delivered.append(signum)

    guard = bot.ConfirmedPostSigintDeferral()
    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    bot._AMBIGUOUS_REMOTE_POST_SEEN = True
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )
    _establish_current_barrier_pair()
    _original_fsync, removed = _install_marker_disappearance_during_parent_fsync(
        monkeypatch
    )

    try:
        assert bot.durable_remote_write_safety_marker_exists() is True
        assert removed == [bot.AMBIGUOUS_POST_OUTCOME_FILE]
        assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.stat().st_nlink == 1
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert signal.getsignal(signal.SIGINT) is delivered_handler
        assert delivered == [signal.SIGINT]
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


def test_fresh_process_marker_disappearance_latches_and_blocks_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marker observed after restart must latch before its acknowledgement."""
    actual_create_post = bot.create_post
    marker_payload = _ambiguous_marker_payload()
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        marker_payload,
        durable=True,
    )
    _establish_current_barrier_pair()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
    _original_fsync, removed = _install_marker_disappearance_during_parent_fsync(
        monkeypatch
    )

    assert bot.durable_remote_write_safety_marker_exists() is True
    assert removed == [bot.AMBIGUOUS_POST_OUTCOME_FILE]
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.stat().st_nlink == 1
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()
    _actual_conversational_create_is_blocked(
        monkeypatch,
        actual_create_post,
    )


def test_fresh_process_marker_inspection_failure_latches_both_barriers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable marker namespace cannot be interpreted as safely empty."""
    original_lstat = bot.os.lstat

    def fail_marker_lstat(path: object, *args: object, **kwargs: object):
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_FILE:
            raise OSError("injected marker namespace inspection failure")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(bot.os, "lstat", fail_marker_lstat)

    assert bot.durable_remote_write_safety_marker_exists() is False
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()


def test_fresh_process_marker_probe_latches_before_later_disappearance() -> None:
    """The lightweight namespace probe must close its own lookup-to-sync race."""
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )

    assert bot.remote_write_safety_marker_path_present_or_unsafe() is True
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    bot.AMBIGUOUS_POST_OUTCOME_FILE.unlink()

    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()


def test_durability_uncertainty_blocks_low_level_remote_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X and provider helpers cannot bypass the process-wide uncertainty latch."""
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True

    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.require_remote_operation_unpaused("synthetic provider operation")


def test_durability_uncertainty_blocks_x_and_provider_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport wrappers must stop before requests on uncertainty alone."""
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    request_calls: list[str] = []

    def unexpected_request(*_args: object, **_kwargs: object) -> object:
        request_calls.append("reached")
        pytest.fail("remote transport was reached through the uncertainty latch")

    monkeypatch.setattr(bot.requests, "request", unexpected_request)
    monkeypatch.setattr(bot.requests, "post", unexpected_request)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request("POST", "/2/tweets", json={"text": "synthetic"})
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.xai_structured_reply_call(
            stage="synthetic",
            model="synthetic-model",
            system_prompt="system",
            user_prompt="user",
            response_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            timeout_seconds=1,
            max_output_tokens=1,
            media_context=None,
        )
    assert request_calls == []


def test_clean_fresh_process_without_marker_allows_remote_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed offline reconciliation must not permanently close later starts."""
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False

    bot.require_remote_operation_unpaused("synthetic clean operation")


@pytest.mark.parametrize(
    "mutation",
    ["replace", "content", "symlink", "dangling_symlink", "directory"],
)
@pytest.mark.parametrize("initially_latched", [False, True])
def test_marker_namespace_mutation_during_fsync_keeps_acknowledgement_fail_closed(
    mutation: str,
    initially_latched: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every namespace change latches fresh and already-running processes."""
    actual_create_post = bot.create_post
    original_signal_handler = signal.getsignal(signal.SIGINT)
    delivered: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        delivered.append(signum)

    guard = bot.ConfirmedPostSigintDeferral()
    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    bot._AMBIGUOUS_REMOTE_POST_SEEN = initially_latched
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = initially_latched
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )
    _establish_current_barrier_pair()
    mutations = _install_marker_namespace_mutation_during_parent_fsync(
        monkeypatch,
        mutation,
    )

    try:
        assert bot.durable_remote_write_safety_marker_exists() is False
        assert mutations == [mutation]
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is guard
        assert getattr(signal.getsignal(signal.SIGINT), "__self__", None) is guard
        assert delivered == []
        assert bot.ambiguous_remote_post_is_blocking() is True
        _actual_conversational_create_is_blocked(
            monkeypatch,
            actual_create_post,
        )
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


def test_marker_disappearance_during_fsync_keeps_confirmed_latch_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing marker must remain present before latch durability is claimed."""
    monkeypatch.setattr(bot, "now_epoch", lambda: CONFIRMATION_EPOCH)
    marker_arguments = {
        "lane": "daily_meme",
        "post_id": "970001",
        "failure_components": ["meme_post_receipt", "state"],
    }
    assert bot.latch_confirmed_post_persistence_failure(**marker_arguments) is True
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    _original_fsync, removed = _install_marker_disappearance_during_parent_fsync(
        monkeypatch
    )

    assert bot.latch_confirmed_post_persistence_failure(**marker_arguments) is True
    assert removed == [bot.AMBIGUOUS_POST_OUTCOME_FILE]
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.stat().st_nlink == 1
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False


def test_marker_disappearance_during_fsync_keeps_ambiguous_record_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing an existing ambiguity marker must revalidate its post-fsync path."""
    monkeypatch.setattr(bot, "now_epoch", lambda: CONFIRMATION_EPOCH)
    payload = {"text": "Good quote."}
    bot.record_ambiguous_remote_post(payload)
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    _original_fsync, removed = _install_marker_disappearance_during_parent_fsync(
        monkeypatch
    )

    bot.record_ambiguous_remote_post(payload)
    assert removed == [bot.AMBIGUOUS_POST_OUTCOME_FILE]
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.stat().st_nlink == 1
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False


@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_new_marker_atomic_write_mutation_keeps_ambiguous_record_fail_closed(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact newly written successor must exist when it is acknowledged."""
    actual_create_post = bot.create_post
    original_signal_handler = signal.getsignal(signal.SIGINT)
    original_atomic_write_json = bot.atomic_write_json
    original_fsync_parent_dir = bot.fsync_parent_dir
    mutations: list[str] = []
    delivered: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        delivered.append(signum)

    guard = bot.ConfirmedPostSigintDeferral()
    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard

    def write_then_mutate(
        path: Path,
        value: object,
        *,
        durable: bool = False,
    ) -> None:
        original_atomic_write_json(path, value, durable=durable)
        resolved = Path(path)
        if resolved != bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            return
        if mutation == "disappear":
            resolved.unlink()
        else:
            replacement = resolved.with_name("replacement-ambiguity-marker.json")
            replacement.write_bytes(
                bot.canonical_atomic_json_bytes(
                    _ambiguous_marker_payload("Different incident.")
                )
            )
            os.replace(replacement, resolved)
        original_fsync_parent_dir(resolved, strict=True)
        mutations.append(mutation)

    monkeypatch.setattr(bot, "atomic_write_json", write_then_mutate)

    try:
        bot.record_ambiguous_remote_post({"text": "Good quote."})

        assert mutations == [mutation]
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is guard
        assert getattr(signal.getsignal(signal.SIGINT), "__self__", None) is guard
        assert delivered == []
        _actual_conversational_create_is_blocked(monkeypatch, actual_create_post)
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


def test_new_marker_atomic_write_does_not_swallow_hard_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard process exit is not an ordinary recoverable write exception."""

    class HardProcessExit(BaseException):
        pass

    def hard_exit(
        _path: Path,
        _value: object,
        *,
        durable: bool = False,
    ) -> None:
        assert durable is True
        raise HardProcessExit

    monkeypatch.setattr(bot, "atomic_write_json", hard_exit)

    with pytest.raises(HardProcessExit):
        bot.ensure_durable_remote_write_safety_marker(
            _ambiguous_marker_payload()
        )
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_hard_linked_marker_cannot_be_acknowledged(
    tmp_path: Path,
) -> None:
    """A second name for the marker defeats namespace-bound reconciliation."""
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )
    alias = tmp_path / "marker-alias.json"
    os.link(bot.AMBIGUOUS_POST_OUTCOME_FILE, alias)

    assert bot.durable_remote_write_safety_marker_exists() is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert alias.exists()


def test_production_acknowledgement_rejects_replaced_instance_lock_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon's held lock inode must still own the supported lock pathname."""
    lock_path, held_lock, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
    )
    expected_lock = f"pid={os.getpid()}\n"
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )
    _establish_current_barrier_pair()

    try:
        assert bot.durable_remote_write_safety_marker_exists() is True

        replacement = tmp_path / "replacement.lock"
        replacement.write_text(expected_lock, encoding="ascii")
        os.replace(replacement, lock_path)
        bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True

        assert bot.durable_remote_write_safety_marker_exists() is False
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    finally:
        held_lock.close()
        singleton.close()
        os.close(state_directory_fd)


def test_live_endpoint_test_override_requires_real_instance_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live-write-capable test mode may not bypass production lock ownership."""
    monkeypatch.setattr(bot, "TEST_MODE", True)
    monkeypatch.setenv(
        "MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST",
        bot.LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE,
    )
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )

    assert bot.durable_remote_write_safety_marker_exists() is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True


def test_test_mode_custom_external_endpoint_cannot_bypass_instance_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only explicit loopback fixtures receive the fake-endpoint test bypass."""

    transmitted: list[str] = []
    monkeypatch.setattr(bot, "TEST_MODE", True)
    monkeypatch.delenv("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST", raising=False)
    monkeypatch.setattr(
        bot,
        "X_BASE",
        "https://external-write-proxy.invalid",
    )
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_IDENTITY", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", None)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: transmitted.append("sent"),
    )

    with pytest.raises(
        (RuntimeError, bot.AmbiguousRemotePostOutcome),
        match="instance lock|internal exact-receipt authorization",
    ):
        bot.x_request("POST", "/2/tweets", json={"text": "never sent"})
    assert transmitted == []


def test_unlocked_matching_descriptor_cannot_self_authorise_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another fd's lock cannot be misattributed to the designated daemon fd."""
    lock_path = tmp_path / "mrsMThatcher.lock"
    lock_path.write_text(f"pid={os.getpid()}\n", encoding="ascii")
    unlocked = lock_path.open("r+", encoding="ascii")
    independent_owner = lock_path.open("r+", encoding="ascii")
    fcntl.fcntl(
        independent_owner.fileno(),
        fcntl.F_OFD_SETLK,
        bot.ofd_lock_record(fcntl.F_WRLCK),
    )
    held_stat = os.fstat(unlocked.fileno())
    singleton_name = bot.instance_lock_abstract_socket_name(tmp_path)
    singleton = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    singleton.bind(singleton_name)
    state_directory_fd = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    fcntl.flock(
        state_directory_fd,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )
    state_directory_identity = os.fstat(state_directory_fd)
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", unlocked)
    monkeypatch.setattr(
        bot,
        "_LOCK_ACQUISITION_IDENTITY",
        (held_stat.st_dev, held_stat.st_ino, os.getpid()),
    )
    monkeypatch.setattr(bot, "_LOCK_SOCKET", singleton)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", singleton_name)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", state_directory_fd)
    monkeypatch.setattr(
        bot,
        "_STATE_DIR_LOCK_IDENTITY",
        (state_directory_identity.st_dev, state_directory_identity.st_ino),
    )
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )

    try:
        assert bot.durable_remote_write_safety_marker_exists() is False
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    finally:
        os.close(state_directory_fd)
        singleton.close()
        independent_owner.close()
        unlocked.close()


def test_shared_ofd_lock_cannot_masquerade_as_exclusive_daemon_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The designated descriptor must own a write lock, not merely a read lock."""

    _lock_path, held, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
        ofd_owner=False,
    )
    fcntl.fcntl(
        held.fileno(),
        fcntl.F_OFD_SETLK,
        bot.ofd_lock_record(fcntl.F_RDLCK),
    )
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        _ambiguous_marker_payload(),
        durable=True,
    )
    try:
        assert bot.durable_remote_write_safety_marker_exists() is False
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    finally:
        singleton.close()
        held.close()
        os.close(state_directory_fd)


def test_other_directory_flock_cannot_masquerade_as_daemon_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The designated directory fd—not merely the inode—must own the flock."""

    _lock_path, held, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
    )
    fcntl.flock(state_directory_fd, fcntl.LOCK_UN)
    independent_owner = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    fcntl.flock(
        independent_owner,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )
    try:
        with pytest.raises(RuntimeError, match="state-directory descriptor"):
            bot.require_remote_operation_unpaused("synthetic X write")
    finally:
        os.close(independent_owner)
        os.close(state_directory_fd)
        singleton.close()
        held.close()


@pytest.mark.parametrize("failure", ["unavailable", "malformed"])
def test_instance_lock_fdinfo_proof_failure_blocks_remote_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Missing or unparseable Linux fdinfo must fail closed before a write."""

    _lock_path, held, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
    )
    original_read_text = Path.read_text

    def controlled_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path(f"/proc/self/fdinfo/{state_directory_fd}"):
            if failure == "unavailable":
                raise OSError("injected unavailable fdinfo")
            return "lock: malformed\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read_text)
    try:
        with pytest.raises(RuntimeError, match="state-directory descriptor"):
            bot.require_remote_operation_unpaused("synthetic X write")
    finally:
        os.close(state_directory_fd)
        singleton.close()
        held.close()


def test_replaced_lock_path_blocks_remote_preflight_without_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal remote-write preflight must detect a second lock namespace."""
    lock_path, held, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
    )
    expected = f"pid={os.getpid()}\n"

    replacement = tmp_path / "replacement.lock"
    replacement.write_text(expected, encoding="ascii")
    os.replace(replacement, lock_path)
    try:
        with pytest.raises(RuntimeError, match="instance-lock"):
            bot.require_remote_operation_unpaused("synthetic X write")
    finally:
        held.close()
        singleton.close()
        os.close(state_directory_fd)


def test_instance_lock_acquisition_binds_path_inode_and_continuous_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production acquisition records an inode a second fd cannot lock."""
    lock_path = tmp_path / "mrsMThatcher.lock"
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_IDENTITY", None)

    bot.acquire_instance_lock()
    acquired = bot._LOCK_FH
    try:
        assert acquired is not None
        metadata = os.fstat(acquired.fileno())
        assert bot._LOCK_ACQUISITION_IDENTITY == (
            metadata.st_dev,
            metadata.st_ino,
            os.getpid(),
        )
        assert lock_path.read_text(encoding="ascii") == f"pid={os.getpid()}\n"
        bot.require_instance_lock_for_remote_write("synthetic X write")

        probe = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(OSError) as conflict:
                fcntl.fcntl(
                    probe,
                    fcntl.F_OFD_SETLK,
                    bot.ofd_lock_record(fcntl.F_WRLCK),
                )
            assert conflict.value.errno in {11, 13}
        finally:
            os.close(probe)

        competitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                competitor.bind(bot.instance_lock_abstract_socket_name(tmp_path))
        finally:
            competitor.close()
    finally:
        if acquired is not None:
            acquired.close()
        if bot._LOCK_SOCKET is not None:
            bot._LOCK_SOCKET.close()
        if bot._STATE_DIR_LOCK_FD is not None:
            os.close(bot._STATE_DIR_LOCK_FD)


def test_abstract_singleton_survives_instance_lock_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the pathname cannot create a second supported live owner."""

    lock_path, held, singleton, state_directory_fd = _install_production_instance_lock(
        tmp_path,
        monkeypatch,
    )
    replacement = tmp_path / "replacement.lock"
    replacement.write_text(f"pid={os.getpid()}\n", encoding="ascii")
    os.replace(replacement, lock_path)

    competitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            competitor.bind(bot.instance_lock_abstract_socket_name(tmp_path))
        with pytest.raises(RuntimeError, match="instance-lock path"):
            bot.require_remote_operation_unpaused("synthetic X write")
    finally:
        competitor.close()
        singleton.close()
        held.close()
        os.close(state_directory_fd)


def test_instance_lock_acquisition_failure_releases_all_ownership_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-flock identity failure must not leak a hidden process owner."""

    lock_path = tmp_path / "mrsMThatcher.lock"
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_IDENTITY", None)
    original_stat = os.stat
    lock_stat_calls = 0

    def fail_second_lock_stat(path: object, *args: object, **kwargs: object):
        nonlocal lock_stat_calls
        if path == lock_path.name and kwargs.get("dir_fd") is not None:
            lock_stat_calls += 1
            if lock_stat_calls == 2:
                raise FileNotFoundError("injected post-flock pathname loss")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(bot.os, "stat", fail_second_lock_stat)
    with pytest.raises(FileNotFoundError, match="post-flock"):
        bot.acquire_instance_lock()

    assert bot._LOCK_FH is None
    assert bot._LOCK_SOCKET is None
    competitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    descriptor = os.open(lock_path, os.O_RDWR)
    directory_probe = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        competitor.bind(bot.instance_lock_abstract_socket_name(tmp_path))
        fcntl.flock(
            directory_probe,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.fcntl(
            descriptor,
            fcntl.F_OFD_SETLK,
            bot.ofd_lock_record(fcntl.F_WRLCK),
        )
    finally:
        os.close(directory_probe)
        os.close(descriptor)
        competitor.close()


def test_instance_lock_short_owner_write_releases_all_ownership_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial canonical PID record cannot leave a hidden process owner."""

    lock_path = tmp_path / "mrsMThatcher.lock"
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_IDENTITY", None)
    original_pwrite = os.pwrite

    def short_pwrite(descriptor: int, data: bytes, offset: int) -> int:
        return original_pwrite(descriptor, data[:-1], offset)

    monkeypatch.setattr(bot.os, "pwrite", short_pwrite)
    with pytest.raises(RuntimeError, match="write was incomplete"):
        bot.acquire_instance_lock()

    assert bot._LOCK_FH is None
    assert bot._LOCK_ACQUISITION_IDENTITY is None
    assert bot._STATE_DIR_LOCK_FD is None
    assert bot._STATE_DIR_LOCK_IDENTITY is None
    assert bot._LOCK_SOCKET is None
    assert bot._LOCK_SOCKET_NAME is None

    directory_probe = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    file_probe = os.open(lock_path, os.O_RDWR)
    competitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        fcntl.flock(directory_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(file_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.fcntl(
            file_probe,
            fcntl.F_OFD_SETLK,
            bot.ofd_lock_record(fcntl.F_WRLCK),
        )
        competitor.bind(bot.instance_lock_abstract_socket_name(tmp_path))
    finally:
        competitor.close()
        os.close(file_probe)
        os.close(directory_probe)


def test_instance_lock_acquisition_rejects_state_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file lock cannot be committed under a replacement BASE_DIR."""

    lock_path = tmp_path / "mrsMThatcher.lock"
    displaced = tmp_path.with_name(f"{tmp_path.name}-displaced")
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", lock_path)
    monkeypatch.setattr(bot, "_LOCK_FH", None)
    monkeypatch.setattr(bot, "_LOCK_ACQUISITION_IDENTITY", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    monkeypatch.setattr(bot, "_LOCK_SOCKET_NAME", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_FD", None)
    monkeypatch.setattr(bot, "_STATE_DIR_LOCK_IDENTITY", None)
    original_open = os.open
    swapped: list[Path] = []

    def swap_before_lock_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        if (
            path == lock_path.name
            and kwargs.get("dir_fd") is not None
            and not swapped
        ):
            tmp_path.rename(displaced)
            tmp_path.mkdir()
            swapped.append(displaced)
        return original_open(path, flags, *args, **kwargs)

    original_identity = os.stat(tmp_path)
    monkeypatch.setattr(bot.os, "open", swap_before_lock_open)
    with pytest.raises(RuntimeError, match="pathname changed"):
        bot.acquire_instance_lock()

    assert swapped == [displaced]
    assert bot._LOCK_FH is None
    assert bot._STATE_DIR_LOCK_FD is None
    assert bot._LOCK_SOCKET is None
    old_directory_probe = original_open(
        displaced,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    competitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        fcntl.flock(
            old_directory_probe,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        competitor.bind(
            bot.instance_lock_abstract_socket_name_for_identity(
                original_identity.st_dev,
                original_identity.st_ino,
            )
        )
    finally:
        competitor.close()
        os.close(old_directory_probe)


def test_later_valid_marker_recovery_releases_sigint_once_without_remote_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed acknowledgement may recover later without opening a write lane."""
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: CONFIRMATION_EPOCH)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )

    remote_actions: list[str] = []

    def remote_lane_reached(*_args: object, **_kwargs: object) -> None:
        remote_actions.append("reached")
        pytest.fail("marker recovery must not open any remote-action lane")

    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        remote_lane_reached,
    )
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", remote_lane_reached)
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(bot, "xai_structured_reply_call", remote_lane_reached)

    original_signal_handler = signal.getsignal(signal.SIGINT)
    delivered: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        delivered.append(signum)

    guard = bot.ConfirmedPostSigintDeferral()
    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    bot._AMBIGUOUS_REMOTE_POST_SEEN = True
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    marker_payload = _ambiguous_marker_payload()
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        marker_payload,
        durable=True,
    )
    _establish_current_barrier_pair()
    original_fsync_parent_dir = bot.fsync_parent_dir
    parent_fsync_paths: list[Path] = []

    def fail_first_barrier_fsync(
        path: Path,
        *,
        strict: bool = False,
    ) -> None:
        resolved = Path(path)
        parent_fsync_paths.append(resolved)
        original_fsync_parent_dir(path, strict=strict)
        if len(parent_fsync_paths) == 1:
            assert resolved == bot.AMBIGUOUS_POST_OUTCOME_FILE
            raise OSError("barrier parent fsync reported a transient failure")

    monkeypatch.setattr(bot, "fsync_parent_dir", fail_first_barrier_fsync)

    class TwoBlockedTicksComplete(Exception):
        pass

    sleep_calls = 0

    def recover_then_stop(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            assert delivered == []
            assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is guard
            assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
            assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
            assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
            return
        raise TwoBlockedTicksComplete

    monkeypatch.setattr(bot, "sleep", recover_then_stop)

    try:
        with pytest.raises(TwoBlockedTicksComplete):
            bot.main()
        assert parent_fsync_paths == [
            bot.AMBIGUOUS_POST_OUTCOME_FILE,
            bot.AMBIGUOUS_POST_OUTCOME_FILE,
        ]
        assert sleep_calls == 2
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert signal.getsignal(signal.SIGINT) is delivered_handler
        assert delivered == [signal.SIGINT]
        assert remote_actions == []

        assert bot.durable_remote_write_safety_marker_exists() is True
        assert delivered == [signal.SIGINT]
        assert remote_actions == []
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


@pytest.mark.parametrize("activation_state", ["absent", "malformed"])
def test_inactive_protocol_is_not_an_incident_specific_durable_acknowledgement(
    activation_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic protocol refusal blocks fresh writes but cannot release a guard."""

    activation = bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    activation.unlink()
    if activation_state == "malformed":
        activation.write_bytes(b"malformed protocol activation\n")
        activation.chmod(0o400)

    retained_guard = object()
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = retained_guard
    bot._AMBIGUOUS_REMOTE_POST_SEEN = True
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True

    assert bot.remote_write_safety_protocol_is_active() is False
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_remote_write_safety_incident_latched()
    assert bot.durable_remote_write_safety_barrier_exists() is False
    assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is retained_guard

    class StillWaitingForIncidentBarrier(Exception):
        pass

    def stop_wait(_seconds: float) -> None:
        raise StillWaitingForIncidentBarrier

    monkeypatch.setattr(bot, "sleep", stop_wait)
    with pytest.raises(StillWaitingForIncidentBarrier):
        bot.wait_for_durable_barrier_before_one_shot_exit(
            lane="inactive-protocol-regression",
        )
    assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is retained_guard

    # A genuinely fresh process has no incident-specific guard, but the same
    # absent or malformed protocol permission still fails closed for writes.
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = None
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()


def test_main_rechecks_marker_durability_on_every_blocked_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A later marker fsync recovery must release the real daemon's SIGINT guard."""
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )

    def remote_lane_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the ambiguity barrier must block every remote-action lane")

    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        remote_lane_reached,
    )
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", remote_lane_reached)
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(bot, "xai_structured_reply_call", remote_lane_reached)

    original_signal_handler = signal.getsignal(signal.SIGINT)
    delivered: list[int] = []

    def delivered_handler(signum: int, _frame: object | None) -> None:
        delivered.append(signum)

    guard = bot.ConfirmedPostSigintDeferral()
    guard.previous_handler = delivered_handler
    guard.pending = True
    signal.signal(signal.SIGINT, guard.handle)
    bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    bot._AMBIGUOUS_REMOTE_POST_SEEN = True
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    bot.AMBIGUOUS_POST_OUTCOME_FILE.write_text("{}\n", encoding="utf-8")
    _establish_current_barrier_pair()

    fsync_paths: list[Path] = []
    real_fsync_parent_dir = bot.fsync_parent_dir

    def transient_parent_fsync(path: Path, *, strict: bool = False) -> None:
        assert strict is True
        fsync_paths.append(Path(path))
        real_fsync_parent_dir(path, strict=strict)
        if len(fsync_paths) == 1:
            assert Path(path) == bot.AMBIGUOUS_POST_OUTCOME_FILE
            raise OSError("first parent-directory fsync failed")

    monkeypatch.setattr(bot, "fsync_parent_dir", transient_parent_fsync)

    class TwoBlockedTicksComplete(Exception):
        pass

    sleep_calls = 0

    def stop_after_two_blocked_ticks(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise TwoBlockedTicksComplete

    monkeypatch.setattr(bot, "sleep", stop_after_two_blocked_ticks)

    try:
        with pytest.raises(TwoBlockedTicksComplete):
            bot.main()
        assert fsync_paths == [
            bot.AMBIGUOUS_POST_OUTCOME_FILE,
            bot.AMBIGUOUS_POST_OUTCOME_FILE,
        ]
        assert sleep_calls == 2
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
        assert signal.getsignal(signal.SIGINT) is delivered_handler
        assert delivered == [signal.SIGINT]
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
        assert bot.ambiguous_remote_post_is_blocking() is True
        assert sum(
            "All remote posting and reply lanes are paused" in record.getMessage()
            for record in caplog.records
        ) == 1
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


def test_fresh_process_marker_disappearance_blocks_multiple_real_daemon_ticks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart-only marker must become a process latch before it can vanish."""
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        lambda: None,
    )
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(
        bot,
        "validate_original_editorial_shadow_startup",
        lambda: None,
    )
    monkeypatch.setattr(
        bot,
        "validate_generated_identity_shadow_startup",
        lambda: None,
    )
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(
        bot,
        "reconcile_startup_main_post_receipts",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        bot,
        "seed_recent_own_post_ids_from_cache",
        lambda _state: None,
    )
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: CONFIRMATION_EPOCH)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (
            int(state_arg.get(key, current)),
            False,
        ),
    )

    remote_actions: list[str] = []

    def remote_lane_reached(*_args: object, **_kwargs: object) -> None:
        remote_actions.append("reached")
        pytest.fail("fresh-process marker loss must not open a remote lane")

    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        remote_lane_reached,
    )
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", remote_lane_reached)
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(bot, "xai_structured_reply_call", remote_lane_reached)

    marker_payload = _ambiguous_marker_payload()
    bot.atomic_write_json(
        bot.AMBIGUOUS_POST_OUTCOME_FILE,
        marker_payload,
        durable=True,
    )
    _establish_current_barrier_pair()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
    _original_fsync, removed = _install_marker_disappearance_during_parent_fsync(
        monkeypatch
    )

    class ThreeBlockedTicksComplete(Exception):
        pass

    sleep_calls = 0

    def stop_after_three_blocked_ticks(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 3:
            raise ThreeBlockedTicksComplete

    monkeypatch.setattr(bot, "sleep", stop_after_three_blocked_ticks)

    with pytest.raises(ThreeBlockedTicksComplete):
        bot.main()
    assert removed == [bot.AMBIGUOUS_POST_OUTCOME_FILE]
    assert sleep_calls == 3
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.stat().st_nlink == 1
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
    assert remote_actions == []
