from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import pytest

import exact_receipt_retirement as exact_retirement
import historical_context_formatter as context
import mrsMThatcher2 as bot
import remote_write_transport_journal as journal
from transaction_mutation_authority import issue_transaction_mutation_authority


def _mutation_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="confirmed-source lineage test",
    )


def _arm_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.arm_transport_transaction(*args, **kwargs)


def _confirm_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.confirm_transport_transaction(*args, **kwargs)


def _retire_confirmed_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.retire_confirmed_transport_transaction(*args, **kwargs)


@pytest.fixture(autouse=True)
def _reset_transport_authorities() -> None:
    journal.reset_consumed_authorities_for_tests()


def _write_exact(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _confirm_source(
    path: Path,
    source: dict,
    *,
    source_bytes: bytes,
    lane: str,
    post_id: str,
    validator_id: str = "tests.confirmed-source-lineage.v1",
    payload: dict | None = None,
) -> None:
    exact_retirement.initialise_retirement_ledger(
        path,
        mutation_authority=_mutation_authority(),
    )
    _write_exact(path, source_bytes)
    payload = payload or {"text": "Reviewed text"}
    prepared = journal.begin_transport_transaction(
        receipt_path=path,
        expected_receipt=source,
        lane=lane,
        payload=payload,
        source_validator_id=validator_id,
        source_validator=lambda *_args: True,
    )
    journal_path = journal.journal_path_for_receipt(path)
    armed = _arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(
        journal_path,
        armed,
        post_id=post_id,
        confirmation_epoch=1_800_000_010,
    )


def _replace_with_same_bytes(path: Path, data: bytes) -> None:
    """Replace a source pathname with equal bytes on a different inode."""

    original_inode = path.stat().st_ino
    replacement = path.with_name(f".{path.name}.replacement")
    _write_exact(replacement, data)
    os.replace(replacement, path)
    assert path.stat().st_ino != original_inode


def _peer_replace_with_same_bytes(path: Path, data: bytes) -> int:
    """Let a literal peer durably replace one pathname with equal bytes."""

    before = os.lstat(path)
    read_fd, write_fd = os.pipe()
    peer_pid = os.fork()
    if peer_pid == 0:
        try:
            os.close(read_fd)
            replacement = path.with_name(
                f".{path.name}.peer-replacement.{os.getpid()}"
            )
            _write_exact(replacement, data)
            os.replace(replacement, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            after = os.lstat(path)
            os.write(write_fd, str(int(after.st_ino)).encode("ascii"))
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(97)

    os.close(write_fd)
    try:
        peer_inode_bytes = os.read(read_fd, 128)
    finally:
        os.close(read_fd)
    waited_pid, status = os.waitpid(peer_pid, 0)
    assert waited_pid == peer_pid
    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0
    peer_inode = int(peer_inode_bytes.decode("ascii"))
    after = os.lstat(path)
    assert (int(after.st_dev), int(after.st_ino)) != (
        int(before.st_dev),
        int(before.st_ino),
    )
    assert int(after.st_ino) == peer_inode
    assert path.read_bytes() == data
    return peer_inode


def _assert_peer_inode_preserved_by_failed_exchange(
    journal_path: Path,
    peer_inode: int,
) -> None:
    """Prove the failed exchange retained both a barrier and the peer inode."""

    state = journal.inspect_transport_state(journal_path)
    assert state.blocking is True
    assert state.staging_names
    # A pre-exchange identity rejection leaves the raced peer at the source
    # pathname and the proposed replacement in staging.  A post-exchange
    # rejection may displace the peer into staging.  Either layout preserves
    # the peer inode plus an independently inventoried blocking entry.
    assert any(
        int(os.lstat(entry).st_ino) == peer_inode
        for entry in journal_path.parent.iterdir()
    )


def _main_attempt(lane: str, *, attempt_id_seed: str) -> dict:
    if lane == "quote_image":
        text = "A reviewed quotation."
        quote_hash = bot.quote_text_hash(text)
        attempt = bot.build_main_post_attempt(
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
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["reviewed.jpg"],
            },
            attempt_epoch=1_800_000_000,
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
                "meme_schedule_version": 2,
                "fallback_hour": 16,
                "fallback_minute": 0,
                "image_summary": "A reviewed poster.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
            attempt_epoch=1_800_000_000,
        )
    attempt["attempt_id"] = hashlib.sha256(attempt_id_seed.encode()).hexdigest()
    attempt["lifecycle_state"] = "attempting"
    assert bot.main_post_attempt_is_semantically_valid(attempt)
    return attempt


def _main_confirmed(attempt: dict, *, post_id: str) -> dict:
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id=post_id,
        confirmation_epoch=1_800_000_010,
        image_summary=(
            str(attempt["recovery_plan"]["image_summary"])
            if attempt["lane"] == "daily_meme"
            else ""
        ),
    )
    if attempt["lane"] == "quote_image":
        return bot.materialize_bound_regular_schedule_receipt(pending)
    return bot.materialize_bound_meme_schedule_receipt(pending)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_confirmed_receipt_cannot_retire_different_source_attempt(
    tmp_path: Path,
    lane: str,
) -> None:
    receipt_path = tmp_path / f"{lane}.json"
    original = _main_attempt(lane, attempt_id_seed="original")
    _confirm_source(
        receipt_path,
        original,
        source_bytes=bot.canonical_atomic_json_bytes(original),
        lane=lane,
        post_id="950001",
    )

    replacement = _main_attempt(lane, attempt_id_seed="replacement")
    confirmed = _main_confirmed(replacement, post_id="950001")
    assert (
        bot.regular_post_receipt_is_semantically_valid(confirmed)
        if lane == "quote_image"
        else bot.meme_post_receipt_is_semantically_valid(confirmed)
    )
    _write_exact(receipt_path, bot.canonical_atomic_json_bytes(confirmed))

    with pytest.raises(journal.TransportJournalError, match="lineage"):
        bot.retire_lane_transport_journal_if_present(
            receipt_path=receipt_path,
            receipt=confirmed,
            lane=lane,
            post_id="950001",
        )
    assert journal.transport_journal_is_blocking(
        journal.journal_path_for_receipt(receipt_path)
    )


def _conversational_sending(*, author_id: str) -> dict:
    return {
        "schema_version": 4,
        "lifecycle_state": "sending",
        "target_id": "111",
        "author_id": author_id,
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


def test_conversational_confirmed_receipt_cannot_retire_different_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_args: True)
    path = tmp_path / "confirmed_reply_receipt.json"
    original = _conversational_sending(author_id="42")
    assert bot.sending_reply_receipt_is_semantically_valid(original)
    _confirm_source(
        path,
        original,
        source_bytes=bot.canonical_atomic_json_bytes(original),
        lane="conversational_reply",
        post_id="950002",
    )
    replacement = _conversational_sending(author_id="43")
    confirmed = bot._confirmed_reply_receipt_from_sending(
        replacement,
        reply_post_id="950002",
        confirmation_epoch=1_800_000_010,
    )
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed)
    _write_exact(path, bot.canonical_atomic_json_bytes(confirmed))
    with pytest.raises(journal.TransportJournalError, match="lineage"):
        bot.retire_lane_transport_journal_if_present(
            receipt_path=path,
            receipt=confirmed,
            lane="conversational_reply",
            post_id="950002",
        )


def _historical_sending(*, attempt_number: int) -> dict:
    return {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "111",
        "quote_id": "a" * 64,
        "reply_text": "Verified context.",
        "reply_epoch": 1_800_000_000,
        "started_at": "2027-01-15T08:00:00Z",
        "attempt_number": attempt_number,
    }


def test_historical_confirmed_receipt_cannot_retire_different_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_reply_receipt.json"
    original = _historical_sending(attempt_number=1)
    _confirm_source(
        path,
        original,
        source_bytes=context.canonical_json_bytes(original),
        lane="historical_context_reply",
        post_id="950003",
    )
    replacement = _historical_sending(attempt_number=2)
    confirmed = {
        **copy.deepcopy(replacement),
        "lifecycle_state": "confirmed",
        "reply_post_id": "950003",
        "confirmed_at": "2027-01-15T08:00:10Z",
        "source_receipt_sha256": hashlib.sha256(
            context.canonical_json_bytes(replacement)
        ).hexdigest(),
    }
    assert context.HistoricalContextReplyStore._valid_receipt(confirmed)
    _write_exact(path, context.canonical_json_bytes(confirmed))
    with pytest.raises(journal.TransportJournalError, match="lineage"):
        _retire_confirmed_transport_transaction(
            receipt_path=path,
            expected_confirmed_receipt=confirmed,
            expected_source_receipt_bytes=(
                context.HistoricalContextReplyStore.source_receipt_bytes_from_confirmed(
                    confirmed
                )
            ),
            lane="historical_context_reply",
            post_id="950003",
        )


@pytest.mark.parametrize("schema_version", (1.0, True))
def test_historical_source_lineage_helper_requires_integer_schema(
    schema_version: object,
) -> None:
    source = _historical_sending(attempt_number=1)
    confirmed = {
        **copy.deepcopy(source),
        "lifecycle_state": "confirmed",
        "reply_post_id": "950003",
        "confirmed_at": "2027-01-15T08:00:10Z",
        "source_receipt_sha256": hashlib.sha256(
            context.canonical_json_bytes(source)
        ).hexdigest(),
    }
    confirmed["schema_version"] = schema_version

    with pytest.raises(ValueError, match="exact source lineage"):
        context.HistoricalContextReplyStore.sending_receipt_from_confirmed(
            confirmed
        )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_confirmed_receipt_rejects_derived_field_tampering(lane: str) -> None:
    receipt = _main_confirmed(
        _main_attempt(lane, attempt_id_seed="derived"),
        post_id="950004",
    )
    changed = copy.deepcopy(receipt)
    if lane == "quote_image":
        changed["next_quote_post_epoch"] += 1
        assert not bot.regular_post_receipt_is_semantically_valid(changed)
    else:
        changed["next_meme_post_epoch"] += 1
        assert not bot.meme_post_receipt_is_semantically_valid(changed)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("line_no", False),
        ("line_no", 0.0),
        ("source_line_number", True),
        ("source_line_number", 1.0),
        ("image_no", False),
        ("image_no", 0.0),
    ),
)
def test_regular_confirmed_source_lineage_requires_exact_integer_fields(
    field: str,
    invalid_value: object,
) -> None:
    attempt = _main_attempt("quote_image", attempt_id_seed="typed-lineage")
    confirmed = _main_confirmed(attempt, post_id="950005")
    confirmed[field] = invalid_value

    assert bot.regular_post_receipt_is_semantically_valid(confirmed) is False
    assert bot.confirmed_receipt_matches_main_attempt(confirmed, attempt) is False


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_reconciliation_rejects_wrong_source_before_state_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    path = tmp_path / f"{lane}.json"
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        path if lane == "quote_image" else tmp_path / "regular.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        path if lane == "daily_meme" else tmp_path / "meme.json",
    )
    original = _main_attempt(lane, attempt_id_seed="original-reconcile")
    _confirm_source(
        path,
        original,
        source_bytes=bot.canonical_atomic_json_bytes(original),
        lane=lane,
        post_id="950101",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
    )
    replacement = _main_attempt(lane, attempt_id_seed="replacement-reconcile")
    confirmed = _main_confirmed(replacement, post_id="950101")
    _write_exact(path, bot.canonical_atomic_json_bytes(confirmed))
    state = bot.default_state()
    state_before = copy.deepcopy(state)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    receipt_before = path.read_bytes()

    with pytest.raises(journal.TransportJournalError, match="source receipt"):
        if lane == "quote_image":
            bot.reconcile_regular_post_receipt(
                lines_used,
                images_used,
                state,
                process_auxiliary_context=False,
            )
        else:
            bot.reconcile_meme_post_receipt(state)

    assert state == state_before
    assert lines_used == set()
    assert images_used == set()
    assert path.read_bytes() == receipt_before


def test_conversational_reconciliation_rejects_wrong_source_before_state_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "confirmed_reply_receipt.json"
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_args: True)
    original = _conversational_sending(author_id="42")
    _confirm_source(
        path,
        original,
        source_bytes=bot.canonical_atomic_json_bytes(original),
        lane="conversational_reply",
        post_id="950102",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
    )
    replacement = _conversational_sending(author_id="43")
    confirmed = bot._confirmed_reply_receipt_from_sending(
        replacement,
        reply_post_id="950102",
        confirmation_epoch=1_800_000_010,
    )
    _write_exact(path, bot.canonical_atomic_json_bytes(confirmed))
    state = bot.default_state()
    state_before = copy.deepcopy(state)
    receipt_before = path.read_bytes()

    with pytest.raises(journal.TransportJournalError, match="source receipt"):
        bot.reconcile_confirmed_reply_receipt(state)

    assert state == state_before
    assert path.read_bytes() == receipt_before


def test_historical_reconciliation_rejects_wrong_source_before_history_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_reply_receipt.json"
    history_path = tmp_path / "history.json"
    original = _historical_sending(attempt_number=1)
    _confirm_source(
        path,
        original,
        source_bytes=context.canonical_json_bytes(original),
        lane="historical_context_reply",
        post_id="950103",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
    )
    replacement = _historical_sending(attempt_number=2)
    confirmed = {
        **replacement,
        "lifecycle_state": "confirmed",
        "reply_post_id": "950103",
        "confirmed_at": "2027-01-15T08:00:10Z",
        "source_receipt_sha256": hashlib.sha256(
            context.canonical_json_bytes(replacement)
        ).hexdigest(),
    }
    _write_exact(path, context.canonical_json_bytes(confirmed))
    receipt_before = path.read_bytes()
    store = context.HistoricalContextReplyStore(
        history_path,
        path,
        mutation_authority_provider=lambda operation: _mutation_authority(),
    )

    with pytest.raises(journal.TransportJournalError, match="source receipt"):
        store.reconcile_receipt()

    assert not history_path.exists()
    assert path.read_bytes() == receipt_before


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_promotion_rejects_equal_byte_replacement_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Parsed equality cannot replace journal-bound source identity."""

    path = tmp_path / f"{lane}.json"
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        path if lane == "quote_image" else tmp_path / "regular.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        path if lane == "daily_meme" else tmp_path / "meme.json",
    )
    source = _main_attempt(lane, attempt_id_seed="same-bytes-main")
    source_bytes = bot.canonical_atomic_json_bytes(source)
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane=lane,
        post_id="950201",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=bot.main_post_attempt_payload(source),
    )
    _replace_with_same_bytes(path, source_bytes)

    with pytest.raises(journal.TransportJournalError, match="source receipt changed"):
        bot.promote_main_post_attempt_to_confirmed_pending_schedule(
            source,
            post_id="950201",
            confirmation_epoch=1_800_000_010,
            image_summary=(
                str(source["recovery_plan"].get("image_summary") or "")
                if lane == "daily_meme"
                else ""
            ),
        )

    assert path.read_bytes() == source_bytes
    assert journal.transport_journal_is_blocking(
        journal.journal_path_for_receipt(path)
    )


def test_conversational_promotion_rejects_equal_byte_replacement_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "confirmed_reply_receipt.json"
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_args: True)
    source = _conversational_sending(author_id="42")
    source_bytes = bot.canonical_atomic_json_bytes(source)
    payload = {
        "text": source["reply_text"],
        "reply": {"in_reply_to_tweet_id": source["target_id"]},
    }
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane="conversational_reply",
        post_id="950202",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=payload,
    )
    _replace_with_same_bytes(path, source_bytes)

    with pytest.raises(journal.TransportJournalError, match="source receipt changed"):
        bot.promote_sending_reply_receipt(
            source,
            reply_post_id="950202",
            confirmation_epoch=1_800_000_010,
        )

    assert path.read_bytes() == source_bytes
    assert journal.transport_journal_is_blocking(
        journal.journal_path_for_receipt(path)
    )


def test_historical_promotion_rejects_equal_byte_replacement_inode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_reply_receipt.json"
    source = _historical_sending(attempt_number=1)
    source_bytes = context.canonical_json_bytes(source)
    payload = {
        "text": source["reply_text"],
        "reply": {"in_reply_to_tweet_id": source["parent_post_id"]},
    }
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane="historical_context_reply",
        post_id="950203",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=payload,
    )
    _replace_with_same_bytes(path, source_bytes)
    store = context.HistoricalContextReplyStore(
        tmp_path / "history.json",
        path,
        mutation_authority_provider=lambda operation: _mutation_authority(),
    )

    with pytest.raises(journal.TransportJournalError, match="source receipt changed"):
        store.promote_sending_receipt_from_confirmed_transport(
            source,
            reply_post_id="950203",
            confirmation_epoch=1_800_000_010,
            require_confirmed_transport=True,
        )

    assert path.read_bytes() == source_bytes
    assert not store.history_path.exists()
    assert journal.transport_journal_is_blocking(
        journal.journal_path_for_receipt(path)
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_promotion_rejects_peer_aba_after_source_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """The bound inode, not equal parsed content, owns confirmation promotion."""

    path = tmp_path / f"{lane}.json"
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        path if lane == "quote_image" else tmp_path / "regular.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        path if lane == "daily_meme" else tmp_path / "meme.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    source = _main_attempt(lane, attempt_id_seed=f"post-bind-peer-{lane}")
    source_bytes = bot.canonical_atomic_json_bytes(source)
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane=lane,
        post_id="950301",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=bot.main_post_attempt_payload(source),
    )
    real_replace = bot.replace_bound_source_receipt
    observed: dict[str, int] = {}

    def replace_after_peer_aba(
        binding: journal.SourceReceiptBinding,
        replacement_bytes: bytes,
        **kwargs,
    ) -> None:
        assert Path(binding.receipt_path) == path
        assert binding.receipt_bytes == source_bytes
        observed["peer_inode"] = _peer_replace_with_same_bytes(
            path,
            source_bytes,
        )
        real_replace(binding, replacement_bytes, **kwargs)

    monkeypatch.setattr(
        bot,
        "replace_bound_source_receipt",
        replace_after_peer_aba,
    )

    with pytest.raises(
        bot.ConfirmedPendingScheduleDurabilityUncertain,
        match="source receipt changed|promotion did not complete",
    ) as raised:
        bot.promote_main_post_attempt_to_confirmed_pending_schedule(
            source,
            post_id="950301",
            confirmation_epoch=1_800_000_010,
            image_summary=(
                str(source["recovery_plan"].get("image_summary") or "")
                if lane == "daily_meme"
                else ""
            ),
        )

    assert raised.value.durable_barrier is True
    assert "peer_inode" in observed
    _assert_peer_inode_preserved_by_failed_exchange(
        journal.journal_path_for_receipt(path),
        observed["peer_inode"],
    )


def test_conversational_promotion_rejects_peer_aba_after_source_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversational confirmation cannot clobber a post-bind peer inode."""

    path = tmp_path / "confirmed_reply_receipt.json"
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_args: True)
    source = _conversational_sending(author_id="42")
    source_bytes = bot.canonical_atomic_json_bytes(source)
    payload = {
        "text": source["reply_text"],
        "reply": {"in_reply_to_tweet_id": source["target_id"]},
    }
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane="conversational_reply",
        post_id="950302",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=payload,
    )
    real_replace = bot.replace_bound_source_receipt
    observed: dict[str, int] = {}

    def replace_after_peer_aba(
        binding: journal.SourceReceiptBinding,
        replacement_bytes: bytes,
        **kwargs,
    ) -> None:
        assert Path(binding.receipt_path) == path
        assert binding.receipt_bytes == source_bytes
        observed["peer_inode"] = _peer_replace_with_same_bytes(
            path,
            source_bytes,
        )
        real_replace(binding, replacement_bytes, **kwargs)

    monkeypatch.setattr(
        bot,
        "replace_bound_source_receipt",
        replace_after_peer_aba,
    )

    with pytest.raises(
        journal.BoundSourceReceiptTransitionError,
        match="promotion did not complete exactly",
    ):
        bot.promote_sending_reply_receipt(
            source,
            reply_post_id="950302",
            confirmation_epoch=1_800_000_010,
        )

    assert "peer_inode" in observed
    _assert_peer_inode_preserved_by_failed_exchange(
        journal.journal_path_for_receipt(path),
        observed["peer_inode"],
    )


def test_historical_promotion_rejects_peer_aba_after_source_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A historical confirmation cannot clobber a post-bind peer inode."""

    path = tmp_path / "historical_context_reply_receipt.json"
    source = _historical_sending(attempt_number=1)
    source_bytes = context.canonical_json_bytes(source)
    payload = {
        "text": source["reply_text"],
        "reply": {"in_reply_to_tweet_id": source["parent_post_id"]},
    }
    _confirm_source(
        path,
        source,
        source_bytes=source_bytes,
        lane="historical_context_reply",
        post_id="950303",
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        payload=payload,
    )
    store = context.HistoricalContextReplyStore(
        tmp_path / "history.json",
        path,
        mutation_authority_provider=lambda operation: _mutation_authority(),
    )
    real_replace = context.replace_bound_source_receipt
    observed: dict[str, int] = {}

    def replace_after_peer_aba(
        binding: journal.SourceReceiptBinding,
        replacement_bytes: bytes,
        **kwargs,
    ) -> None:
        assert Path(binding.receipt_path) == path
        assert binding.receipt_bytes == source_bytes
        observed["peer_inode"] = _peer_replace_with_same_bytes(
            path,
            source_bytes,
        )
        real_replace(binding, replacement_bytes, **kwargs)

    monkeypatch.setattr(
        context,
        "replace_bound_source_receipt",
        replace_after_peer_aba,
    )

    with pytest.raises(
        journal.BoundSourceReceiptTransitionError,
        match="promotion did not complete exactly",
    ):
        store.promote_sending_receipt_from_confirmed_transport(
            source,
            reply_post_id="950303",
            confirmation_epoch=1_800_000_010,
            require_confirmed_transport=True,
        )

    assert "peer_inode" in observed
    _assert_peer_inode_preserved_by_failed_exchange(
        journal.journal_path_for_receipt(path),
        observed["peer_inode"],
    )
    assert not store.history_path.exists()
