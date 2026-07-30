"""Adversarial confirmed-receipt directory-durability boundaries."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from tests.test_unit_helpers import (
    UNIT_REPLY_REPOSITORY,
    bot,
    configure_simple_meme_post,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    unit_sending_v4_reply_receipt,
)


CONFIRMATION_EPOCH = 1_800_000_000


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
            if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_FILE:
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
                Path(path) == bot.AMBIGUOUS_POST_OUTCOME_FILE
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
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists() is (
            marker_failure_mode != "before_write"
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

    try:
        with pytest.raises(KeyboardInterrupt):
            bot.durable_remote_write_safety_marker_exists()
        assert deliveries == [signal.SIGINT]
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert signal.getsignal(signal.SIGINT) is delivered_handler
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)


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

    fsync_attempts = 0

    def transient_parent_fsync(path: Path, *, strict: bool = False) -> None:
        nonlocal fsync_attempts
        assert path == bot.AMBIGUOUS_POST_OUTCOME_FILE
        assert strict is True
        fsync_attempts += 1
        if fsync_attempts == 1:
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
        assert fsync_attempts == 2
        assert sleep_calls == 2
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
        assert signal.getsignal(signal.SIGINT) is delivered_handler
        assert delivered == [signal.SIGINT]
        assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert bot.ambiguous_remote_post_is_blocking() is True
        assert sum(
            "All remote posting and reply lanes are paused" in record.getMessage()
            for record in caplog.records
        ) == 1
    finally:
        signal.signal(signal.SIGINT, original_signal_handler)
