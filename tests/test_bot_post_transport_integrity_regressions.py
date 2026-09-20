"""Regression tests for bot post transport integrity."""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import mrs_bot_asset_metadata as asset_metadata

from mrs_bot_main_post_receipt_storage import MainPostReceipts
from tests.helpers.bot_runtime import (
    IMPORT_ENV,
    bot,
)
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    image_analysis_for_paths,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    install_receipt_bound_x_request_stub,
    configure_simple_meme_post,
    schema_current_main_attempt,
)
from tests.helpers.reply_fixtures import prepare_unit_historical_context_create
import remote_write_transport_journal as transport_journal_module


pytestmark = pytest.mark.allow_loopback_network


def test_valid_receipt_epoch_uses_fixed_transaction_policy() -> None:
    assert bot.valid_receipt_epoch(1_499_999_999) is False
    assert bot.valid_receipt_epoch(1_500_000_000) is True
    assert bot.valid_receipt_epoch(4_102_444_800) is True
    assert bot.valid_receipt_epoch(4_102_444_801) is False


def test_durable_receipt_creation_rejects_replaced_coercion_equal_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "receipt.json"

    def replace_during_parent_sync(
        target: Path,
        *,
        strict: bool = False,
    ) -> None:
        assert strict is True
        target.unlink()
        target.write_bytes(bot.canonical_atomic_json_bytes({"field": True}))
        target.chmod(0o600)

    monkeypatch.setattr(bot, "fsync_parent_dir", replace_during_parent_sync)

    with pytest.raises(
        bot.UnsafeReceiptNamespace,
        match="changed before publication acknowledgement",
    ):
        bot.durable_create_receipt_json(path, {"field": 1})

    assert path.read_bytes() == bot.canonical_atomic_json_bytes({"field": True})


def test_confirmed_regular_post_sigint_is_delivered_only_after_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    stages: list[str] = []
    original_write = MainPostReceipts.write_regular

    guard_token = object()

    def begin_deferral() -> object:
        stages.append("begin")
        return guard_token

    def write_receipt(self, receipt: dict) -> None:
        stages.append("receipt")
        original_write(self, receipt)

    def deliver_pending_sigint(guard: object | None) -> None:
        assert guard is guard_token
        assert receipt_file.exists()
        stages.append("end")
        raise KeyboardInterrupt

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(MainPostReceipts, "write_regular", write_receipt)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_pending_sigint)

    with pytest.raises(KeyboardInterrupt):
        bot.post_random_quote(lines_used, images_used, state)

    assert stages == ["begin", "receipt", "end"]
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "950001"
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_regular_hard_death_after_remote_acceptance_leaves_restart_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    remote_acceptance = tmp_path / "remote-accepted"

    def accept_then_hard_exit(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        assert receipt_file.exists()
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os._exit(73)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=bot.post_random_quote,
        args=(lines_used, images_used, state),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 73
    assert remote_acceptance.read_bytes() == b"accepted"

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lane"] == "quote_image"
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"] == {
        "quote_hash": bot.quote_text_hash("Good quote."),
        "line_no": 0,
        "source_line_number": 1,
        "image_basename": "t01.jpg",
        "image_no": 0,
    }
    assert attempt["recovery_plan"]["quote_delay_seconds"] in range(
        bot.POST_SLEEP_MIN,
        bot.POST_SLEEP_MAX + 1,
    )
    assert attempt["recovery_plan"]["quote_history_after"] == [
        bot.quote_text_hash("Good quote."),
    ]
    assert attempt["recovery_plan"]["image_history_after"] == ["t01.jpg"]
    assert bot.ambiguous_remote_post_is_blocking() is True

    retry_calls = 0

    def forbidden_retry(**_kwargs: object) -> dict:
        nonlocal retry_calls
        retry_calls += 1
        return {"data": {"id": "950002"}}

    monkeypatch.setattr(bot, "create_post", forbidden_retry)
    for _restart in range(2):
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.post_random_quote(set(), set(), {})
    assert retry_calls == 0


@pytest.mark.parametrize("reset_lane", ["quote_history", "image_history"])
def test_regular_hard_death_preserves_exact_post_reset_cycle_histories(
    reset_lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        _receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    if reset_lane == "quote_history":
        lines_used.update({quote_hash, "a" * 64})
    else:
        image_dir = tmp_path / "images"
        second_image = image_dir / "t02.jpg"
        second_image.write_bytes(b"second")
        monkeypatch.setattr(
            asset_metadata.AssetMetadata,
            "load_image",
            lambda _owner: image_analysis_for_paths(
                [image_dir / "t01.jpg", second_image],
            ),
        )
        monkeypatch.setattr(
            bot.random,
            "choice",
            lambda choices: sorted(
                choices,
                key=lambda choice: str(
                    choice.get("basename")
                    if isinstance(choice, dict)
                    else choice
                ),
            )[0],
        )
        images_used.update({"t01.jpg", "t02.jpg"})

    def accept_then_hard_exit(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        os._exit(75)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    process = multiprocessing.get_context("fork").Process(
        target=bot.post_random_quote,
        args=(lines_used, images_used, state),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 75

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["recovery_plan"]["quote_history_after"] == [quote_hash]
    assert attempt["recovery_plan"]["image_history_after"] == ["t01.jpg"]
    assert "a" * 64 not in attempt["recovery_plan"]["quote_history_after"]
    assert "t02.jpg" not in attempt["recovery_plan"]["image_history_after"]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize(
    "boundary",
    [
        "pre_request",
        "response_before_confirmed_promotion",
        "confirmed_promotion_before_state",
        "full_receipt_before_state",
        "state_before_receipt_retirement",
    ],
)
def test_main_post_hard_death_boundaries_never_recreate_remote_post(
    lane: str,
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    if lane == "quote_image":
        lines_used, images_used, state, *_paths = configure_simple_quote_post(
            tmp_path,
            monkeypatch,
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        target = lambda: bot.post_random_quote(lines_used, images_used, state)
        original_write = bot.write_regular_post_receipt
        original_remove = bot.remove_regular_post_receipt
        confirmed_post_id = "950001"
    else:
        state, receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
        target = lambda: bot.post_next_meme(state)
        original_write = bot.write_meme_post_receipt
        original_remove = bot.remove_meme_post_receipt
        confirmed_post_id = "970001"

    original_promote = bot.promote_main_post_attempt_to_confirmed_pending_schedule
    original_finalize = bot.finalize_confirmed_pending_schedule_receipt

    remote_acceptance = tmp_path / f"{lane}-accepted-{boundary}"

    def confirmed_remote_request(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {"data": {"id": confirmed_post_id}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote_request)
    monkeypatch.setattr(
        bot,
        "enqueue_historical_context_obligation",
        lambda _receipt: None,
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda **_kwargs: [],
    )

    if boundary == "pre_request":
        monkeypatch.setattr(
            bot,
            "begin_confirmed_post_sigint_deferral",
            lambda: os._exit(76),
        )
        expected_exit = 76
    elif boundary == "response_before_confirmed_promotion":
        monkeypatch.setattr(
            bot,
            "promote_main_post_attempt_to_confirmed_pending_schedule",
            lambda *_args, **_kwargs: os._exit(77),
        )
        expected_exit = 77
    elif boundary == "confirmed_promotion_before_state":
        def promote_then_exit(*args: object, **kwargs: object) -> None:
            original_promote(*args, **kwargs)
            os._exit(78)

        monkeypatch.setattr(
            bot,
            "promote_main_post_attempt_to_confirmed_pending_schedule",
            promote_then_exit,
        )
        expected_exit = 78
    elif boundary == "full_receipt_before_state":
        def finalise_then_exit(*args: object, **kwargs: object) -> None:
            original_finalize(*args, **kwargs)
            os._exit(80)

        monkeypatch.setattr(
            bot,
            "finalize_confirmed_pending_schedule_receipt",
            finalise_then_exit,
        )
        expected_exit = 80
    else:
        if lane == "quote_image":
            monkeypatch.setattr(
                bot,
                "write_regular_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_regular_post_receipt",
                lambda *_args, **_kwargs: os._exit(79),
            )
        else:
            monkeypatch.setattr(
                bot,
                "write_meme_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_meme_post_receipt",
                lambda *_args, **_kwargs: os._exit(79),
            )
        expected_exit = 79

    process = multiprocessing.get_context("fork").Process(target=target)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == expected_exit
    assert receipt_path.exists()

    status, receipt = (
        bot.load_regular_post_receipt()
        if lane == "quote_image"
        else bot.load_meme_post_receipt()
    )
    assert receipt is not None
    if boundary == "pre_request":
        assert not remote_acceptance.exists()
        assert status == "sending"
        assert receipt["lifecycle_state"] == "attempting"
    elif boundary == "response_before_confirmed_promotion":
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "sending"
        assert receipt["lifecycle_state"] == "attempting"
    elif boundary == "confirmed_promotion_before_state":
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "pending_schedule"
        assert receipt["post_id"] == confirmed_post_id
    else:
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "valid"
        assert receipt["post_id"] == confirmed_post_id

    remote_recreates = 0

    def forbidden_recreate(*_args: object, **_kwargs: object) -> object:
        nonlocal remote_recreates
        remote_recreates += 1
        raise AssertionError("restart must not recreate a remote post")

    monkeypatch.setattr(bot, "create_post", forbidden_recreate)
    if status == "sending":
        assert bot.ambiguous_remote_post_is_blocking() is True
        if lane == "quote_image":
            assert bot.reconcile_regular_post_receipt(set(), set(), {}) is False
        else:
            assert bot.reconcile_meme_post_receipt({}) is False
    else:
        if lane == "quote_image":
            monkeypatch.setattr(
                bot,
                "write_regular_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_regular_post_receipt",
                original_remove,
            )
            assert bot.reconcile_regular_post_receipt(set(), set(), {}) is True
        else:
            monkeypatch.setattr(
                bot,
                "write_meme_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_meme_post_receipt",
                original_remove,
            )
            assert bot.reconcile_meme_post_receipt({}) is True
        assert not receipt_path.exists()
    assert remote_recreates == 0


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_atomic_exchange_interruption_leaves_old_or_new_valid_json(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                "quote_delay_seconds": bot.POST_SLEEP_MIN,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["t01.jpg"],
            },
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        loader = bot.load_regular_post_receipt
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
                "image_summary": "Unit meme image.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
        )
        receipt_path = bot.MEME_POST_RECEIPT_FILE
        loader = bot.load_meme_post_receipt
    bot.write_main_post_attempt(attempt)
    old_bytes = receipt_path.read_bytes()
    real_exchange = transport_journal_module._rename_exchange

    def exit_before_exchange(
        _directory_fd: int,
        _first: str,
        _second: str,
    ) -> None:
        os._exit(82)

    monkeypatch.setattr(
        transport_journal_module,
        "_rename_exchange",
        exit_before_exchange,
    )
    process = multiprocessing.get_context("fork").Process(
        target=bot.mark_main_post_attempt_attempting,
        args=(attempt,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 82
    assert receipt_path.read_bytes() == old_bytes
    assert loader() == ("sending", attempt)
    staging = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
    ]
    assert len(staging) == 1
    staging[0].unlink()

    def exit_after_exchange(
        directory_fd: int,
        first: str,
        second: str,
    ) -> None:
        real_exchange(directory_fd, first, second)
        os._exit(83)

    monkeypatch.setattr(
        transport_journal_module,
        "_rename_exchange",
        exit_after_exchange,
    )
    process = multiprocessing.get_context("fork").Process(
        target=bot.mark_main_post_attempt_attempting,
        args=(attempt,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 83
    status, current = loader()
    assert status == "sending"
    assert current is not None
    assert current["lifecycle_state"] == "attempting"
    assert current["attempt_id"] == attempt["attempt_id"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == current
    assert any(
        path.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
        for path in tmp_path.iterdir()
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_sending_to_attempting_rejects_same_bytes_new_inode_race(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    receipt_path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )
    bot.write_main_post_attempt(attempt)
    original_bytes = receipt_path.read_bytes()
    original_inode = receipt_path.stat().st_ino
    raced_inode: int | None = None
    real_replace_generation = (
        transport_journal_module.replace_exact_source_receipt_generation
    )

    def inject_same_bytes_new_inode(
        path: Path,
        **kwargs: object,
    ) -> None:
        nonlocal raced_inode
        assert Path(path) == receipt_path
        assert kwargs["expected_bytes"] == original_bytes
        peer = tmp_path / f"{lane}-same-bytes-peer.json"
        peer.write_bytes(original_bytes)
        peer.chmod(transport_journal_module.JOURNAL_MODE)
        raced_inode = peer.stat().st_ino
        assert raced_inode != original_inode
        os.replace(peer, receipt_path)
        directory_fd = os.open(
            tmp_path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        real_replace_generation(path, **kwargs)

    monkeypatch.setattr(
        transport_journal_module,
        "replace_exact_source_receipt_generation",
        inject_same_bytes_new_inode,
    )

    with pytest.raises(
        bot.BoundSourceReceiptTransitionError,
        match="replacement did not complete",
    ):
        bot.mark_main_post_attempt_attempting(attempt)

    assert raced_inode is not None
    assert receipt_path.stat().st_ino == raced_inode
    assert receipt_path.read_bytes() == original_bytes
    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )
    assert loader() == ("sending", attempt)
    assert any(
        item.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
        for item in tmp_path.iterdir()
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_to_full_receipt_atomic_replace_survives_hard_death(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if lane == "quote_image":
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        loader = bot.load_regular_post_receipt
        post_id = "950001"
    else:
        receipt_path = bot.MEME_POST_RECEIPT_FILE
        loader = bot.load_meme_post_receipt
        post_id = "970001"

    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    attempting = bot.mark_main_post_attempt_attempting(attempt)
    payload = bot.main_post_attempt_payload(attempting)
    source = bot.bind_lane_transport_source(
        receipt_path=receipt_path,
        receipt=attempting,
        lane=lane,
        payload=payload,
    )
    authority = bot.begin_transport_transaction(
        receipt_path=receipt_path,
        source_binding=source,
    )
    authority = bot.arm_transport_transaction(
        Path(authority.journal_path),
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )
    bot.consume_transport_authority(
        Path(authority.journal_path),
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    bot.confirm_transport_transaction(
        Path(authority.journal_path),
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport confirmation"
        ),
        post_id=post_id,
        confirmation_epoch=1_800_000_100,
    )
    pending = bot.promote_main_post_attempt_to_confirmed_pending_schedule(
        attempting,
        post_id=post_id,
        confirmation_epoch=1_800_000_100,
    )
    pending_bytes = receipt_path.read_bytes()
    real_replace = os.replace

    def exit_before_replace(source: object, destination: object) -> None:
        if Path(destination) == receipt_path:
            os._exit(84)
        real_replace(source, destination)

    monkeypatch.setattr(bot.os, "replace", exit_before_replace)
    process = multiprocessing.get_context("fork").Process(
        target=bot.finalize_confirmed_pending_schedule_receipt,
        args=(pending,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 84
    assert receipt_path.read_bytes() == pending_bytes
    assert loader() == ("pending_schedule", pending)

    def exit_after_replace(source: object, destination: object) -> None:
        real_replace(source, destination)
        if Path(destination) == receipt_path:
            os._exit(85)

    monkeypatch.setattr(bot.os, "replace", exit_after_replace)
    process = multiprocessing.get_context("fork").Process(
        target=bot.finalize_confirmed_pending_schedule_receipt,
        args=(pending,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 85
    status, full_receipt = loader()
    assert status == "valid"
    assert full_receipt is not None
    assert full_receipt["post_id"] == post_id


@pytest.mark.parametrize("lane", ("quote_image", "daily_meme"))
def test_main_attempt_requires_integer_schema_version(lane: str) -> None:
    attempt = schema_current_main_attempt(lane)
    for invalid_schema in (float(attempt["schema_version"]), True):
        changed = copy.deepcopy(attempt)
        changed["schema_version"] = invalid_schema
        assert bot.main_post_attempt_is_semantically_valid(changed) is False


@pytest.mark.parametrize("source_line_number", (1.0, True))
def test_main_attempt_requires_integer_source_line_number(
    source_line_number: object,
) -> None:
    attempt = schema_current_main_attempt("quote_image")
    attempt["selected_identity"]["source_line_number"] = source_line_number
    assert bot.main_post_attempt_is_semantically_valid(attempt) is False


@pytest.mark.parametrize(
    ("lane", "expected_schema"),
    (("quote_image", 3), ("daily_meme", 2)),
)
def test_main_attempt_keeps_supported_legacy_integer_schemas(
    lane: str,
    expected_schema: int,
) -> None:
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        attempt = schema_current_main_attempt(lane)
        attempt["schema_version"] = expected_schema
        attempt["recovery_plan"] = {
            "quote_delay_seconds": 7200,
            "meme_delay_seconds": None,
            "quote_history_after": [quote_hash],
            "image_history_after": ["t01.jpg"],
        }
    else:
        attempt = schema_current_main_attempt(lane)
        attempt["schema_version"] = expected_schema
        attempt["recovery_plan"] = {"next_schedule_mode": "fallback"}

    assert type(attempt["schema_version"]) is int
    assert attempt["schema_version"] == expected_schema
    assert bot.main_post_attempt_is_semantically_valid(attempt) is True


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_legacy_main_attempt_is_reader_only_and_cannot_enter_live_transport(
    lane: str,
) -> None:
    legacy = schema_current_main_attempt(lane)
    legacy["schema_version"] = 4
    legacy["recovery_plan"].pop("schedule_timezone")
    path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )
    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )

    assert bot.main_post_attempt_is_semantically_valid(legacy)
    assert not bot.current_main_post_attempt_is_semantically_valid(legacy)
    with pytest.raises(RuntimeError, match="current-schema"):
        bot.write_main_post_attempt(legacy)
    assert not path.exists()

    path.write_bytes(bot.canonical_atomic_json_bytes(legacy))
    path.chmod(0o600)
    before = path.read_bytes()
    assert loader() == ("sending", legacy)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="current-schema"):
        bot.mark_main_post_attempt_attempting(legacy)
    with pytest.raises(bot.TransportJournalError, match="current-schema"):
        bot.prepare_main_tweet_transport(legacy)
    assert path.read_bytes() == before
    assert loader() == ("sending", legacy)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_attempting_main_attempt_cannot_bypass_single_use_promotion(
    lane: str,
) -> None:
    attempting = schema_current_main_attempt(lane)
    attempting["lifecycle_state"] = "attempting"
    path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )

    assert bot.current_main_post_attempt_is_semantically_valid(attempting)
    with pytest.raises(RuntimeError, match="sending main-post attempt"):
        bot.write_main_post_attempt(attempting)
    assert not path.exists()

    path.write_bytes(bot.canonical_atomic_json_bytes(attempting))
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(
        bot.TransportJournalError,
        match="sending main-post attempt",
    ):
        bot.prepare_main_tweet_transport(attempting)
    assert path.read_bytes() == before
    assert not any(
        os.path.lexists(journal_path)
        for journal_path in bot.remote_write_transport_journal_paths()
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_current_attempt_cannot_bypass_confirmed_pending_schedule_receipt(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular_path = bot.REGULAR_POST_RECEIPT_FILE
    meme_path = bot.MEME_POST_RECEIPT_FILE
    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    attempting = bot.mark_main_post_attempt_attempting(attempt)
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )
    if lane == "quote_image":
        full = bot.materialize_bound_regular_schedule_receipt(pending)
        with pytest.raises(
            bot.UnresolvedRegularPostReceipt,
            match="pending-schedule",
        ):
            bot.write_regular_post_receipt(full)
        assert bot.load_regular_post_receipt() == ("sending", attempting)
    else:
        full = bot.materialize_bound_meme_schedule_receipt(pending)
        with pytest.raises(
            bot.UnresolvedMemePostReceipt,
            match="pending-schedule",
        ):
            bot.write_meme_post_receipt(full)
        assert bot.load_meme_post_receipt() == ("sending", attempting)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_confirmation_requires_consumed_attempt_and_clamps_clock_rollback(
    lane: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sending = schema_current_main_attempt(lane)
    post_id = "950001" if lane == "quote_image" else "970001"
    with pytest.raises(RuntimeError, match="invalid main-post attempt"):
        bot.build_confirmed_pending_schedule_receipt(
            sending,
            post_id=post_id,
            confirmation_epoch=1_800_000_100,
        )

    attempting = {**sending, "lifecycle_state": "attempting"}
    with pytest.raises(RuntimeError, match="invalid main-post attempt"):
        bot.build_confirmed_pending_schedule_receipt(
            attempting,
            post_id=post_id,
            confirmation_epoch=1_799_999_999,
        )
    confirmation_epoch = bot.confirmation_epoch_for_main_attempt(
        attempting,
        1_799_999_999,
    )
    assert confirmation_epoch == 1_800_000_000
    assert "Wall clock moved backwards" in caplog.text
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
    )
    assert pending["confirmation_epoch"] == attempting["attempt_epoch"]
    boolean_schema = copy.deepcopy(pending)
    boolean_schema["schema_version"] = True
    assert (
        bot.confirmed_pending_schedule_receipt_is_semantically_valid(
            boolean_schema,
            expected_lane=lane,
        )
        is False
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_authorises_exactly_one_remote_create(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    remote_calls = 0

    def create_once(*_args: object, **_kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "950001" if lane == "quote_image" else "970001"}}

    install_receipt_bound_x_request_stub(monkeypatch, create_once)

    bot.create_post(
        text=str(attempt["text"]),
        media_ids=list(attempt["media_ids"]),
        made_with_ai=bool(attempt["made_with_ai"]),
        prepared_main_post_attempt=attempt,
    )
    assert remote_calls == 1
    assert attempt["lifecycle_state"] == "attempting"

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            text=str(attempt["text"]),
            media_ids=list(attempt["media_ids"]),
            made_with_ai=bool(attempt["made_with_ai"]),
            prepared_main_post_attempt=attempt,
        )
    assert remote_calls == 1

    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )
    assert loader() == ("sending", attempt)


def test_regular_generic_4xx_retains_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )

    def definite_failure(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        raise bot.ApiError(
            "definite rejection",
            service="x",
            status_code=400,
            request_method="POST",
            request_path="/2/tweets",
        )

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, definite_failure)
    with pytest.raises(bot.ApiError, match="definite rejection"):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            bot.ApiError(
                "explicit create rejection",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "status unavailable",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "server failure",
                service="x",
                status_code=500,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "other service",
                service="xai",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "read request",
                service="x",
                status_code=400,
                request_method="GET",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "wrong endpoint",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/media/upload",
            ),
            False,
        ),
        (
            bot.AmbiguousRemotePostOutcome(
                "explicitly uncertain",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.RemoteOperationsPaused("local maintenance pause"),
            True,
        ),
    ],
)
def test_only_local_pause_proves_remote_non_success(
    error: BaseException,
    expected: bool,
) -> None:
    assert bot.api_error_proves_remote_non_success(error) is expected


@pytest.mark.parametrize(
    "failure_kind",
    ["http_500", "status_unknown", "unexpected", "transport"],
)
def test_regular_uncertain_remote_failure_retains_attempt_and_blocks_retry(
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    actual_x_request = bot.x_request
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    attempts = 0

    def uncertain_request(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if failure_kind == "http_500":
            raise bot.ApiError("uncertain 500", service="x", status_code=500)
        if failure_kind == "status_unknown":
            raise bot.ApiError("unknown status", service="x")
        raise RuntimeError("unexpected transport wrapper failure")

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    if failure_kind == "transport":
        monkeypatch.setattr(bot, "x_request", actual_x_request)

        def transport_failure(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise bot.requests.ConnectionError("connection dropped")

        monkeypatch.setattr(bot.requests, "request", transport_failure)
        expected_error: type[BaseException] = bot.AmbiguousRemotePostOutcome
    else:
        install_receipt_bound_x_request_stub(monkeypatch, uncertain_request)
        expected_error = (
            RuntimeError
            if failure_kind == "unexpected"
            else bot.ApiError
        )

    with pytest.raises(expected_error):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempts == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(set(), set(), {})
    assert attempts == 1


def test_regular_normal_success_atomically_promotes_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    observed_attempt: dict = {}

    def confirmed_create(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        observed_attempt.update(attempt)
        return {"data": {"id": "950001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_create)
    monkeypatch.setattr(
        bot, "remove_regular_post_receipt", lambda *_args, **_kwargs: None
    )
    bot.post_random_quote(lines_used, images_used, state)

    status, confirmed = bot.load_regular_post_receipt()
    assert status == "valid"
    assert confirmed is not None
    assert confirmed["post_id"] == "950001"
    assert confirmed["attempt_id"] == observed_attempt["attempt_id"]
    assert (
        confirmed["attempt_payload_sha256"]
        == observed_attempt["payload_sha256"]
    )
    assert receipt_file.exists()


@pytest.mark.parametrize(
    ("context_required", "expected_context_state"),
    [
        (True, "context_reply_pending"),
        (False, "context_reply_not_required"),
    ],
)
def test_regular_promotion_failure_records_context_before_attempt_retirement(
    context_required: bool,
    expected_context_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "950001"}},
    )
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("confirmed promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {
            **bot.historical_context_reply,
            "enabled": context_required,
        },
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        None,
    )

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="failed local recovery receipt",
    ):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.load_regular_post_receipt() == ("absent", None)
    obligation = bot.historical_context_outbox_store().get("950001")
    assert obligation is not None
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"]["state"] == expected_context_state


def test_regular_promotion_and_required_context_enqueue_failure_retains_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "950001"}},
    )
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("confirmed promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        None,
    )
    monkeypatch.setattr(
        bot,
        "enqueue_historical_context_obligation",
        lambda _receipt: (_ for _ in ()).throw(
            OSError("required context enqueue failed")
        ),
    )

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="historical-context disposition",
    ):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"]["quote_hash"] == bot.quote_text_hash(
        "Good quote."
    )
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_regular_ambiguous_create_without_marker_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )

    def ambiguous_create(**kwargs: object) -> dict:
        mock_confirmed_main_post(kwargs, {})
        bot.record_ambiguous_remote_post({"text": "Good quote."})
        raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")

    monkeypatch.setattr(bot, "create_post", ambiguous_create)
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_confirmed_post_sigint_deferral_restores_handler_and_delivers_pending() -> None:
    prior_handler = signal.getsignal(signal.SIGINT)
    guard = bot.begin_confirmed_post_sigint_deferral()
    try:
        guard.handle(signal.SIGINT, None)
        assert guard.pending is True
        with pytest.raises(KeyboardInterrupt):
            bot.end_confirmed_post_sigint_deferral(guard)
    finally:
        signal.signal(signal.SIGINT, prior_handler)

    assert signal.getsignal(signal.SIGINT) == prior_handler


def test_confirmed_post_sigint_deferral_handles_process_signal_from_worker() -> None:
    code = """
import os
import signal
import threading
import time
import mrsMThatcher2 as bot

guard = bot.begin_confirmed_post_sigint_deferral()
sender = threading.Thread(target=lambda: os.kill(os.getpid(), signal.SIGINT))
sender.start()
sender.join()
deadline = time.monotonic() + 2
while not guard.pending and time.monotonic() < deadline:
    time.sleep(0.01)
if not guard.pending:
    raise SystemExit("SIGINT was not deferred")
try:
    bot.end_confirmed_post_sigint_deferral(guard)
except KeyboardInterrupt:
    print("deferred-and-delivered")
else:
    raise SystemExit("pending SIGINT was not delivered")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(bot.__file__).resolve().parent,
        env={**os.environ, **IMPORT_ENV},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("deferred-and-delivered")


def test_confirmation_clock_failure_uses_durable_attempt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    state.update(
        {
            "last_main_post_id": "940000",
            "last_quote_post_epoch": 1_700_000_000,
            "next_quote_post_epoch": 1_700_007_200,
        }
    )
    clock_calls = 0

    def fail_after_preflight() -> int:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 1_800_000_000
        raise RuntimeError("clock failed after confirmation")

    monkeypatch.setattr(
        bot,
        "now_epoch",
        fail_after_preflight,
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda **_kwargs: [],
    )

    bot.post_random_quote(lines_used, images_used, state)

    assert clock_calls == 2
    assert bot.ambiguous_remote_post_is_blocking() is False
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert state["last_quote_post_epoch"] == 1_800_000_000
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.inspect_transport_state(
        bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE)
    ).blocking


@pytest.mark.parametrize("observed", [1_499_999_999, 4_102_444_801])
def test_out_of_range_confirmation_clock_uses_durable_attempt_epoch(
    monkeypatch: pytest.MonkeyPatch,
    observed: int,
) -> None:
    source = schema_current_main_attempt("quote_image")
    fallback = int(source["attempt_epoch"])
    monkeypatch.setattr(bot, "now_epoch", lambda: observed)

    assert bot.confirmation_epoch_after_remote_success(source) == fallback


def test_confirmation_clock_requires_valid_durable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)

    with pytest.raises(
        bot.TransportJournalError,
        match="no durable confirmation-time fallback",
    ):
        bot.confirmation_epoch_after_remote_success(
            {"attempt_epoch": 4_102_444_801}
        )


def test_common_receipt_loader_requires_canonical_owned_private_file(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"{}")
    receipt.chmod(0o600)
    with pytest.raises(bot.UnsafeReceiptNamespace, match="not canonical"):
        bot.load_receipt_json_no_follow(receipt)

    receipt.write_bytes(bot.canonical_atomic_json_bytes({}))
    receipt.chmod(0o644)
    with pytest.raises(
        bot.UnsafeReceiptNamespace,
        match="unsafe ownership or permissions",
    ):
        bot.load_receipt_json_no_follow(receipt)


def test_common_receipt_loader_rejects_same_inode_mutation_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(bot.canonical_atomic_json_bytes({}))
    receipt.chmod(0o600)
    real_lstat = bot.os.lstat
    calls = {"count": 0}

    def mutate_before_final_path_snapshot(path: os.PathLike[str] | str):
        if Path(path) == receipt:
            calls["count"] += 1
        if Path(path) == receipt and calls["count"] == 2:
            receipt.write_bytes(
                bot.canonical_atomic_json_bytes({"changed": True})
            )
        return real_lstat(path)

    monkeypatch.setattr(bot.os, "lstat", mutate_before_final_path_snapshot)

    with pytest.raises(bot.UnsafeReceiptNamespace, match="changed while reading"):
        bot.load_receipt_json_no_follow(receipt)


def test_current_main_attempt_requires_exact_string_identifiers() -> None:
    attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text="A meme post.",
        media_ids=["700001"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "fallback_hour": 13,
            "fallback_minute": 0,
            "image_summary": "A meme image.",
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )
    assert bot.main_post_attempt_is_semantically_valid(attempt)

    changed = copy.deepcopy(attempt)
    changed["attempt_id"] = int("1" * 64)
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    changed = copy.deepcopy(attempt)
    changed["reply_to_id"] = 0
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    changed = copy.deepcopy(attempt)
    changed["selected_identity"]["meme_basename"] = 123
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    attempting = {**attempt, "lifecycle_state": "attempting"}
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="950001",
        confirmation_epoch=1_800_000_001,
        image_summary="A meme image.",
    )
    pending["post_id"] = 950001
    assert (
        bot.confirmed_pending_schedule_receipt_is_semantically_valid(pending)
        is False
    )


@pytest.mark.parametrize("post_id", ["123456", 123456])
def test_create_post_accepts_valid_numeric_ids(monkeypatch: pytest.MonkeyPatch, post_id: object) -> None:
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: {"data": {"id": post_id}},
    )
    receipt = prepare_unit_historical_context_create(text="hello")

    assert bot.create_post(
        "hello",
        reply_to_id=str(receipt["parent_post_id"]),
        prepared_historical_context_reply_receipt=receipt,
    ) == {"data": {"id": post_id}}


def test_create_post_passes_long_text_without_280_character_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Historically grounded context. " * 20
    assert len(text) > 280
    requests = []
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: requests.append((args, kwargs))
        or {"data": {"id": "123456"}},
    )
    receipt = prepare_unit_historical_context_create(
        text=text,
        parent_post_id="654321",
    )

    bot.create_post(
        text,
        reply_to_id="654321",
        prepared_historical_context_reply_receipt=receipt,
    )

    assert requests[0][0] == ("POST", "/2/tweets")
    assert requests[0][1]["json"]["text"] == text
    assert requests[0][1]["json"]["reply"] == {"in_reply_to_tweet_id": "654321"}


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"id": "banana"}},
        {"data": {"id": ""}},
        {"data": {}},
        {"data": []},
        {"data": "not an object"},
        {},
    ],
)
def test_create_post_rejects_invalid_or_missing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict,
) -> None:
    marker = tmp_path / "ambiguous_post.json"
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", marker)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: response,
    )
    receipt = prepare_unit_historical_context_create(text="hello")

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            "hello",
            reply_to_id=str(receipt["parent_post_id"]),
            prepared_historical_context_reply_receipt=receipt,
        )

    assert json.loads(marker.read_text(encoding="utf-8"))["outcome"] == "ambiguous_remote_post"


def test_parse_tweet_id_rejects_oversized_numeric_value() -> None:
    assert bot.parse_tweet_id("9" * 5_000, context="test tweet") is None
