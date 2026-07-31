from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


_IMPORT_TEMPORARY = tempfile.TemporaryDirectory(
    prefix="mrsMThatcher-production-consistency-import-"
)
_IMPORT_BASE = Path(_IMPORT_TEMPORARY.name)
_IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(_IMPORT_BASE),
    "MRS_LOG_FILE": str(_IMPORT_BASE / "test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "XAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
_ORIGINAL_ENV = {name: os.environ.get(name) for name in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

import historical_context_formatter as context_formatter  # noqa: E402
import historical_context_outbox as outbox_module  # noqa: E402
import mrsMThatcher2 as bot  # noqa: E402

for _name, _value in _ORIGINAL_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


def _forbid(operation: str):
    def forbidden(*_args, **_kwargs):
        pytest.fail(f"test attempted forbidden live operation: {operation}")

    return forbidden


@pytest.fixture(autouse=True)
def isolated_incident_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "BASE_DIR": tmp_path,
        "LINES_FILE": tmp_path / "quotes.txt",
        "LINES_USED_FILE": tmp_path / "lines_used.json",
        "IMAGES_USED_FILE": tmp_path / "images_used.json",
        "STATE_FILE": tmp_path / "bot_state.json",
        "REGULAR_POST_RECEIPT_FILE": tmp_path / "regular_post_receipt.json",
        "MEME_POST_RECEIPT_FILE": tmp_path / "meme_post_receipt.json",
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE": tmp_path / "context_history.json",
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE": tmp_path / "context_receipt.json",
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE": tmp_path / "context_outbox.json",
        "CONFIRMED_REPLY_RECEIPT_FILE": tmp_path / "confirmed_reply_receipt.json",
        "AMBIGUOUS_POST_OUTCOME_FILE": tmp_path / "ambiguous_post_outcome.json",
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE": (
            tmp_path / "ambiguous_post_outcome.restart_barrier.json"
        ),
        "CONTROL_FILE": tmp_path / "control.json",
        "COMPLETED_QUOTE_RESEARCH_FILE": tmp_path / "research_packets.json",
        "HISTORICAL_CONTEXT_RESEARCH_DIR": tmp_path / "research",
        "MEME_DIR": tmp_path / "memes",
        "MEME_ANALYSIS_FILE": tmp_path / "meme_analysis.json",
    }
    for name, value in paths.items():
        monkeypatch.setattr(bot, name, value)

    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", None)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
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

    monkeypatch.setattr(bot, "x_request", _forbid("X request"))
    monkeypatch.setattr(bot.requests, "request", _forbid("requests.request"))
    monkeypatch.setattr(bot, "create_post", _forbid("X post creation"))
    monkeypatch.setattr(bot, "upload_media", _forbid("X media upload"))
    monkeypatch.setattr(bot, "save_state", _forbid("unscoped bot state write"))
    monkeypatch.setattr(
        bot,
        "save_quote_used_hashes",
        _forbid("unscoped quote-history write"),
    )
    monkeypatch.setattr(
        bot,
        "save_image_used_basenames",
        _forbid("unscoped image-history write"),
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "maybe_schedule_meme_after_quote_post",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)


def _quote_analysis(text: str) -> dict:
    quote_id = bot.quote_text_hash(text)
    source = f"{text}\n"
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()
        },
        "line_index": {"1": quote_id},
        "items": {
            quote_id: {
                "text": text,
                "quote_hash": quote_id,
                "line_numbers": [1],
                "analysis": {
                    "seasonality": {
                        "hard_exclude_outside_windows": False,
                        "preferred_windows": [],
                        "relevance": "none",
                    }
                },
            }
        },
    }


def _regular_receipt(
    *,
    post_id: str = "950001",
    text: str = "Good quote.",
    quote_post_epoch: int = 1_800_000_000,
) -> dict:
    return {
        "schema_version": 1,
        "post_id": post_id,
        "quote_hash": bot.quote_text_hash(text),
        "image_basename": "t01.jpg",
        "quote_post_epoch": quote_post_epoch,
        "next_quote_post_epoch": quote_post_epoch + 7_200,
        "text": text,
    }


def test_context_semantic_block_does_not_remove_ordinary_quote_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_text = "An ordinarily eligible quotation."
    quote_id = bot.quote_text_hash(quote_text)
    bot.LINES_FILE.write_text(f"{quote_text}\n", encoding="utf-8")
    gate = SimpleNamespace(
        available=True,
        ledger_sha256="incident-ledger",
        projection_sha256="incident-projection",
        disposition=lambda candidate_id: (
            "future_correction_needed" if candidate_id == quote_id else None
        ),
    )
    packet = {"quote_id": quote_id, "quote_text": quote_text}
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(bot, "load_quote_analysis", lambda: _quote_analysis(quote_text))
    monkeypatch.setattr(
        bot,
        "completed_research_quote_hashes",
        lambda: {quote_id},
    )
    monkeypatch.setattr(bot, "current_datetime", lambda: bot.datetime(2026, 7, 23))
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        _forbid("formatting semantically blocked context"),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    before = bot.quote_candidates_for_current_cycle(set())
    context_result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text=quote_text,
        parent_post_id="950001",
    )
    after = bot.quote_candidates_for_current_cycle(set())

    assert [item["quote_hash"] for item in before] == [quote_id]
    assert [item["quote_hash"] for item in after] == [quote_id]
    assert context_result["status"] == "skipped_future_policy"
    assert context_result["reason"] == "open_semantic_review"
    assert events[-1][0] == "historical_context_reply"
    assert events[-1][1]["status"] == "skipped_future_policy"
    assert not bot.LINES_USED_FILE.exists()
    assert not bot.STATE_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()


def test_confirmed_main_receipt_replay_has_exact_decoupling_order_and_no_x_repost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt()
    bot.REGULAR_POST_RECEIPT_FILE.write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: receipt["quote_post_epoch"])

    order: list[str] = []
    x_post_calls: list[dict] = []
    original_apply = bot.apply_regular_post_receipt
    original_enqueue = bot.enqueue_historical_context_obligation
    original_remove = bot.remove_regular_post_receipt

    def tracked_apply(*args, **kwargs):
        result = original_apply(*args, **kwargs)
        order.append("main_state_applied")
        return result

    def tracked_save(*_args, **_kwargs):
        order.append("main_state_saved")

    def tracked_enqueue(value: dict):
        obligation = original_enqueue(value)
        order.append("context_obligation_enqueued")
        return obligation

    def tracked_remove():
        assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()
        original_remove()
        order.append("main_receipt_removed")

    def context_only_attempt(**_kwargs):
        assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
        order.append("context_attempted")
        return {"status": "skipped_future_policy"}

    def forbidden_x_post(**kwargs):
        x_post_calls.append(kwargs)
        pytest.fail("confirmed receipt replay must never recreate the X main post")

    monkeypatch.setattr(bot, "apply_regular_post_receipt", tracked_apply)
    monkeypatch.setattr(bot, "save_regular_post_protected_state", tracked_save)
    monkeypatch.setattr(bot, "enqueue_historical_context_obligation", tracked_enqueue)
    monkeypatch.setattr(bot, "remove_regular_post_receipt", tracked_remove)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        context_only_attempt,
    )
    monkeypatch.setattr(bot, "create_post", forbidden_x_post)

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict = {}
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False

    assert order == [
        "main_state_applied",
        "main_state_saved",
        "context_obligation_enqueued",
        "main_receipt_removed",
        "context_attempted",
    ]
    assert x_post_calls == []
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    assert state["last_main_post_id"] == receipt["post_id"]
    obligation = bot.historical_context_outbox_store().get(receipt["post_id"])
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert (
        obligation["context_reply"]["state"]
        == "context_reply_not_required"
    )
    assert not bot.LINES_USED_FILE.exists()
    assert not bot.IMAGES_USED_FILE.exists()
    assert not bot.STATE_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


def test_confirmed_main_reconciliation_survives_unexpected_auxiliary_worker_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt(post_id="950002")
    bot.REGULAR_POST_RECEIPT_FILE.write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: receipt["quote_post_epoch"])
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "process_due_historical_context_obligations",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected auxiliary worker fault")
        ),
    )

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict = {}
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True

    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert state["last_main_post_id"] == receipt["post_id"]
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    obligation = bot.historical_context_outbox_store().get(receipt["post_id"])
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"]["state"] == "context_reply_pending"
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON


def test_quote_identity_normalisation_is_stable_without_punctuation_fuzzing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_text = "Stable quotation identity — with punctuation."
    canonical_id = bot.quote_text_hash(canonical_text)
    assert bot.quote_text_hash(
        "  Stable quotation identity —\nwith punctuation.  "
    ) == canonical_id
    assert bot.quote_text_hash(
        "Stable quotation identity - with punctuation."
    ) != canonical_id

    packet = {"quote_id": canonical_id, "quote_text": canonical_text}
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({canonical_id: packet}, set()),
    )
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda _packets, _unresolved, _quote_hash, quote_text: (
            packet
            if " ".join(quote_text.split())
            == " ".join(canonical_text.split())
            else None
        ),
    )

    assert bot.canonical_context_obligation_quote_id(
        "9" * 64,
        " Stable quotation identity —\nwith punctuation. ",
    ) == canonical_id
    assert bot.canonical_context_obligation_quote_id(
        "9" * 64,
        "Stable quotation identity - with punctuation.",
    ) == "9" * 64


@pytest.mark.parametrize(
    "existing_context_state",
    ["pending", "retryable", "confirmed", "not_required"],
)
def test_main_receipt_replay_accepts_all_existing_outbox_terminal_and_active_states(
    existing_context_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt(post_id="950002")
    bot.REGULAR_POST_RECEIPT_FILE.write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    store = bot.historical_context_outbox_store()
    if existing_context_state == "not_required":
        store.enqueue(
            receipt["post_id"],
            main_post_confirmed_epoch=receipt["quote_post_epoch"],
            not_required_reason="historical_context_reply_disabled",
        )
    else:
        store.enqueue(
            receipt["post_id"],
            main_post_confirmed_epoch=receipt["quote_post_epoch"],
            quote_id=receipt["quote_hash"],
            quote_text=receipt["text"],
        )
        if existing_context_state == "retryable":
            store.claim_attempt(
                receipt["post_id"],
                started_epoch=receipt["quote_post_epoch"],
            )
            store.record_retryable_failure(
                receipt["post_id"],
                attempt_number=1,
                error="temporary failure",
                failed_epoch=receipt["quote_post_epoch"] + 10,
            )
        elif existing_context_state == "confirmed":
            store.claim_attempt(
                receipt["post_id"],
                started_epoch=receipt["quote_post_epoch"],
            )
            store.record_confirmed(
                receipt["post_id"],
                attempt_number=1,
                reply_post_id="950003",
                confirmed_epoch=receipt["quote_post_epoch"] + 10,
            )

    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: receipt["quote_post_epoch"] + 20,
    )
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "skipped_future_policy"},
    )

    assert bot.reconcile_regular_post_receipt(set(), set(), {}) is True
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert store.get(receipt["post_id"])["main_post"]["state"] == (
        "main_post_confirmed"
    )


def test_retryable_and_terminal_context_outcomes_preserve_confirmed_main_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_state = {
        "last_main_post_id": "810002",
        "last_quote_post_epoch": 8_000,
        "next_quote_post_epoch": 18_000,
    }
    state_before = copy.deepcopy(quote_state)
    sentinels = {
        bot.LINES_USED_FILE: b'["quote-sentinel"]\n',
        bot.IMAGES_USED_FILE: b'["image-sentinel"]\n',
        bot.STATE_FILE: b'{"last_main_post_id":"810002"}\n',
    }
    for path, payload in sentinels.items():
        path.write_bytes(payload)

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "810001",
        main_post_confirmed_epoch=8_000,
        quote_id="a" * 64,
        quote_text="Retryable context quotation.",
    )
    store.enqueue(
        "810002",
        main_post_confirmed_epoch=8_001,
        quote_id="b" * 64,
        quote_text="Terminal context quotation.",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)

    def context_result(*, parent_post_id: str, **_kwargs):
        if parent_post_id == "810001":
            return {"status": "failed", "error": "temporary formatter outage"}
        return {"status": "failed_terminal", "reason": "unsupported packet"}

    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context_result)

    results = bot.process_due_historical_context_obligations(limit=2)

    assert [item["parent_post_id"] for item in results] == ["810001", "810002"]
    assert store.get("810001")["main_post"]["state"] == "main_post_confirmed"
    assert (
        store.get("810001")["context_reply"]["state"]
        == "context_reply_failed_retryable"
    )
    assert store.get("810002")["main_post"]["state"] == "main_post_confirmed"
    assert (
        store.get("810002")["context_reply"]["state"]
        == "context_reply_failed_terminal"
    )
    assert quote_state == state_before
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_requested_parent_is_not_starved_by_an_older_due_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800001",
        main_post_confirmed_epoch=8_000,
        quote_id="d" * 64,
        quote_text="An older pending context.",
    )
    store.enqueue(
        "800002",
        main_post_confirmed_epoch=8_001,
        quote_id="e" * 64,
        quote_text="The requested pending context.",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    attempted: list[str] = []

    def context_result(*, parent_post_id: str, **_kwargs):
        attempted.append(parent_post_id)
        return {"status": "skipped_future_policy"}

    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context_result)

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800002",
    )

    assert attempted == ["800002"]
    assert result[0]["context_reply_state"] == "context_reply_not_required"
    assert store.get("800001")["context_reply"]["state"] == "context_reply_pending"


def test_policy_disable_after_retry_becomes_terminal_without_retry_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800003",
        main_post_confirmed_epoch=8_000,
        quote_id="f" * 64,
        quote_text="A context obligation with one definite failure.",
    )
    store.claim_attempt("800003", started_epoch=8_000)
    store.record_retryable_failure(
        "800003",
        attempt_number=1,
        error="temporary preparation failure",
        failed_epoch=8_010,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "disabled"},
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800003",
    )

    assert result[0]["context_reply_state"] == "context_reply_failed_terminal"
    terminal = store.get("800003")["context_reply"]
    assert terminal["attempt_count"] == 2
    assert "disabled after a prior retryable" in terminal["failure"]["error"]
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800003",
    ) == []


def test_process_scoped_context_incompatibility_defers_without_abandoning_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800004",
        main_post_confirmed_epoch=8_000,
        quote_id="1" * 64,
        quote_text="A context obligation retained for a corrected restart.",
    )
    before = store.snapshot()
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        "RuntimeError: source-role policy mismatch",
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("context attempt while process-scoped runtime is unavailable"),
    )

    assert bot.process_due_historical_context_obligations(limit=1) == []
    assert store.snapshot() == before


def test_outbox_outcome_write_failure_latches_retry_after_durable_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800005",
        main_post_confirmed_epoch=8_000,
        quote_id="4" * 64,
        quote_text="A durably claimed context attempt.",
    )
    writes = {"count": 0}
    real_write = outbox_module._atomic_write_json

    def fail_outcome_write(path: Path, value: dict) -> None:
        writes["count"] += 1
        if writes["count"] == 2:
            raise OSError("injected outbox outcome write failure")
        real_write(path, value)

    attempts: list[str] = []
    monkeypatch.setattr(outbox_module, "_atomic_write_json", fail_outcome_write)
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: attempts.append("attempt") or {
            "status": "failed",
            "error": "definite preparation failure",
        },
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800005",
    )

    assert result[0]["status"] == "outbox_persistence_failed"
    claimed = store.get("800005")["context_reply"]
    assert claimed["state"] == "context_reply_attempting"
    assert claimed["attempt_count"] == 1
    assert attempts == ["attempt"]
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800005",
    ) == []
    assert attempts == ["attempt"]


def test_concurrent_context_worker_cannot_claim_a_second_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    for parent_id, quote_id in (("800020", "1" * 64), ("800021", "2" * 64)):
        store.enqueue(
            parent_id,
            main_post_confirmed_epoch=8_000,
            quote_id=quote_id,
            quote_text=f"Concurrent worker fixture {parent_id}.",
        )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("second worker remote work"),
    )

    with store.worker_lock():
        assert bot.process_due_historical_context_obligations(limit=2) == []

    assert store.get("800020")["context_reply"]["state"] == "context_reply_pending"
    assert store.get("800021")["context_reply"]["state"] == "context_reply_pending"


def test_interrupted_claim_is_recovered_with_backoff_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800008",
        main_post_confirmed_epoch=8_000,
        quote_id="7" * 64,
        quote_text="An interrupted context obligation.",
    )
    claimed = store.claim_attempt("800008", started_epoch=8_100)
    assert claimed["context_reply"]["state"] == "context_reply_attempting"
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": {
                    "800008": {
                        "parent_post_id": "800008",
                        "quote_id": "7" * 64,
                        "reply_text": "Rendered context text.",
                        "status": "failed",
                        "failure": "TimeoutError: definite pre-send failure",
                        "attempt_count": 1,
                        "updated_at": "2026-07-24T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("remote work for an interrupted claimed attempt"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800008",
    )

    assert result == [
        {
            "parent_post_id": "800008",
            "status": "recovered_interrupted_attempt",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
            "remote_work_repeated": False,
        }
    ]
    recovered = store.get("800008")["context_reply"]
    assert recovered["attempt_count"] == 1
    assert recovered["next_attempt_epoch"] == 8_260
    assert "definite pre-send failure" in recovered["failure"]["error"]
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800008",
    ) == []


def test_interrupted_claim_does_not_compare_distinct_store_attempt_ordinals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800010",
        main_post_confirmed_epoch=8_000,
        quote_id="9" * 64,
        quote_text="Preparation failed before the reply store saw attempt one.",
    )
    store.claim_attempt("800010", started_epoch=8_000)
    store.record_retryable_failure(
        "800010",
        attempt_number=1,
        error="formatter failed before remote transaction",
        failed_epoch=8_100,
    )
    claimed = store.claim_attempt("800010", started_epoch=8_200)
    assert claimed["context_reply"]["attempt_count"] == 2
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": {
                    "800010": {
                        "parent_post_id": "800010",
                        "quote_id": "9" * 64,
                        "reply_text": "Rendered context text.",
                        "status": "failed",
                        "failure": "TimeoutError: first reply-store attempt failed",
                        "attempt_count": 1,
                        "updated_at": "2026-07-24T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating interrupted remote work"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800010",
    )

    assert result[0]["status"] == "recovered_interrupted_attempt"
    recovered = store.get("800010")["context_reply"]
    assert recovered["state"] == "context_reply_failed_retryable"
    assert recovered["attempt_count"] == 2
    assert "first reply-store attempt failed" in recovered["failure"]["error"]


def test_interrupted_claim_reconciles_completed_history_without_reposting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800009",
        main_post_confirmed_epoch=8_000,
        quote_id="8" * 64,
        quote_text="A context reply already confirmed before interruption.",
    )
    store.claim_attempt("800009", started_epoch=8_100)
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": {
                    "800009": {
                        "schema_version": 1,
                        "parent_post_id": "800009",
                        "reply_post_id": "900009",
                        "quote_id": "8" * 64,
                        "reply_text": "Rendered context text.",
                        "reply_epoch": 8_101,
                        "confirmed_at": "2026-07-24T00:00:00Z",
                        "status": "completed",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("reposting an already confirmed context reply"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800009",
    )

    assert result[0]["status"] == "recovered_confirmed_history"
    assert result[0]["remote_work_repeated"] is False
    confirmed = store.get("800009")["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["reply_post_id"] == "900009"


def test_interrupted_claim_accepts_completed_history_with_independent_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800011",
        main_post_confirmed_epoch=8_000,
        quote_id="a" * 64,
        quote_text="A confirmed reply after one preparation-only failure.",
    )
    store.claim_attempt("800011", started_epoch=8_000)
    store.record_retryable_failure(
        "800011",
        attempt_number=1,
        error="preparation failed before reply-store transaction",
        failed_epoch=8_100,
    )
    store.claim_attempt("800011", started_epoch=8_200)
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": {
                    "800011": {
                        "schema_version": 1,
                        "lifecycle_state": "confirmed",
                        "parent_post_id": "800011",
                        "reply_post_id": "900011",
                        "quote_id": "a" * 64,
                        "reply_text": "Rendered context text.",
                        "reply_epoch": 8_201,
                        "started_at": "2026-07-24T00:00:00Z",
                        "attempt_number": 1,
                        "confirmed_at": "2026-07-24T00:00:01Z",
                        "status": "completed",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("reposting a confirmed context reply"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800011",
    )

    assert result[0]["status"] == "recovered_confirmed_history"
    confirmed = store.get("800011")["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["attempt_count"] == 2
    assert confirmed["reply_post_id"] == "900011"


def test_auxiliary_worker_exception_isolated_after_main_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "process_due_historical_context_obligations",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected auxiliary worker fault")
        ),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot.safely_process_due_historical_context_obligations(
        parent_post_id="800012",
    )

    assert result == [
        {
            "parent_post_id": "800012",
            "status": "worker_failed_isolated",
            "error_type": "RuntimeError",
        }
    ]
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert events == [
        (
            "historical_context_outbox",
            {
                "status": "worker_failed_isolated",
                "parent_post_id": "800012",
                "error_type": "RuntimeError",
                "reason": "injected auxiliary worker fault",
                "main_post_success_preserved": True,
            },
        )
    ]


def test_context_outbox_honours_reply_pause_and_write_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800006",
        main_post_confirmed_epoch=8_000,
        quote_id="5" * 64,
        quote_text="A control-aware context attempt.",
    )
    before = store.snapshot()
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: True)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("context attempt while reply controls are paused"),
    )
    assert bot.process_due_historical_context_obligations(
        runtime_state={},
    ) == []
    assert store.snapshot() == before

    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: True)
    assert bot.process_due_historical_context_obligations(
        runtime_state={"x_write_api_cooldown_until_epoch": 10_000},
    ) == []
    assert store.snapshot() == before


def test_context_rate_limit_updates_shared_write_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800007",
        main_post_confirmed_epoch=8_000,
        quote_id="6" * 64,
        quote_text="A rate-limited context attempt.",
    )
    runtime_state: dict = {}
    saved: list[dict] = []
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {
            "status": "failed",
            "error": "X rate limit",
            "error_type": "ApiError",
            "error_service": "x",
            "error_status_code": 429,
            "error_reset_epoch": 9_500,
        },
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda state, **_kwargs: saved.append(copy.deepcopy(state)),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800007",
        runtime_state=runtime_state,
    )

    assert result[0]["context_reply_state"] == "context_reply_failed_retryable"
    assert runtime_state["x_write_api_cooldown_until_epoch"] == 9_560
    assert "429" in runtime_state["x_write_api_cooldown_reason"]
    assert saved


@pytest.mark.parametrize(
    ("parent_post_id", "message", "status", "path", "expected_state", "error_count"),
    [
        (
            "800008",
            "You attempted to reply to a Tweet that is deleted or not visible to you.",
            403,
            None,
            "context_reply_failed_terminal",
            0,
        ),
        (
            "800009",
            "This application is not permitted to perform that operation.",
            403,
            "/2/tweets",
            "context_reply_failed_retryable",
            1,
        ),
        (
            "800010",
            "endpoint not found",
            404,
            "/2/unsupported",
            "context_reply_failed_retryable",
            1,
        ),
    ],
)
def test_context_outbox_uses_endpoint_aware_terminal_classification(
    monkeypatch: pytest.MonkeyPatch,
    parent_post_id: str,
    message: str,
    status: int,
    path: str | None,
    expected_state: str,
    error_count: int,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_post_id,
        main_post_confirmed_epoch=8_000,
        quote_id="7" * 64,
        quote_text="An endpoint-aware context attempt.",
    )
    runtime_state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {
            "status": "failed",
            "error": message,
            "error_type": "ApiError",
            "error_service": "x",
            "error_status_code": status,
            **(
                {
                    "error_request_method": "POST",
                    "error_request_path": path,
                }
                if path is not None
                else {}
            ),
        },
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_post_id,
        runtime_state=runtime_state,
    )

    assert result[0]["context_reply_state"] == expected_state
    assert len(runtime_state["x_write_error_epochs"]) == error_count


def test_bootstrap_refuses_ambiguous_context_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lifecycle_state": "sending",
                "parent_post_id": "830001",
                "quote_id": "2" * 64,
                "reply_text": "Context reply text.",
                "reply_epoch": 8_300,
                "started_at": "2026-07-23T20:00:00Z",
                "attempt_number": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    monkeypatch.setattr(bot, "INITIALISE_REQUESTED", False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setattr(bot, "setup_logging", lambda **_kwargs: bot.log)
    monkeypatch.setattr(bot, "apply_local_config", lambda: None)
    monkeypatch.setattr(bot, "validate_runtime_config_values", lambda _values: [])
    monkeypatch.setattr(
        bot,
        "load_completed_research_quote_hashes",
        lambda: {"3" * 64},
    )
    monkeypatch.setattr(bot, "initialise_quote_image_semantic_veto_shadow", lambda: None)
    monkeypatch.setattr(bot, "validate_production_credentials", lambda: None)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )

    bot.production_bootstrap(configure_file_logging=False)
    assert bot._PRODUCTION_BOOTSTRAPPED is True
    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="interrupted while sending",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.STATE_FILE.exists()


def test_interrupted_outbox_claim_preserves_sending_receipt_as_global_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply_text = "Context — A reply whose remote outcome is not known."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "830002",
        main_post_confirmed_epoch=8_300,
        quote_id="4" * 64,
        quote_text="The quotation attached to the ambiguous context reply.",
    )
    store.claim_attempt("830002", started_epoch=8_301)
    bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lifecycle_state": "sending",
                "parent_post_id": "830002",
                "quote_id": "4" * 64,
                "reply_text": reply_text,
                "reply_epoch": 8_301,
                "started_at": "2026-07-23T20:00:00Z",
                "attempt_number": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_302)

    result = bot.process_due_historical_context_obligations(
        parent_post_id="830002",
    )

    assert result[0]["status"] == "interrupted_attempt_recovery_failed"
    assert (
        store.get("830002")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert marker["reply_to_id"] == "830002"
    assert marker["text_sha256"] == hashlib.sha256(reply_text.encode()).hexdigest()
    assert bot.process_due_historical_context_obligations(
        parent_post_id="830002",
    ) == []


def test_main_acquires_process_lock_before_context_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class StopAfterReconcile(RuntimeError):
        pass

    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: order.append("lock"))

    def stop_after_reconcile() -> None:
        order.append("context_reconcile")
        raise StopAfterReconcile

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_reconcile,
    )

    with pytest.raises(StopAfterReconcile):
        bot.main()

    assert order == ["lock", "context_reconcile"]


@pytest.mark.parametrize(
    "command_name",
    [
        "run_test_cycle",
        "run_test_main_tick",
        "run_test_post_quote",
        "run_test_post_meme",
    ],
)
def test_one_shot_commands_reconcile_ambiguous_context_receipt_after_lock(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    receipt_bytes = json.dumps(
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830003",
            "quote_id": "5" * 64,
            "reply_text": "Context reply with an unresolved remote outcome.",
            "reply_epoch": 8_303,
            "started_at": "2026-07-27T19:00:00Z",
            "attempt_number": 1,
        }
    ).encode("utf-8")
    bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.write_bytes(
        receipt_bytes,
    )
    order: list[str] = []
    real_reconcile = bot.reconcile_runtime_historical_context_state

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "acquire_instance_lock",
        lambda: order.append("lock"),
    )

    def reconcile_after_lock() -> None:
        order.append("context_reconcile")
        real_reconcile()

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        reconcile_after_lock,
    )
    monkeypatch.setattr(
        bot,
        "load_runtime_state",
        lambda: pytest.fail(
            "one-shot command loaded state despite an ambiguous context receipt"
        ),
    )

    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="interrupted while sending",
    ):
        getattr(bot, command_name)()

    assert order == ["lock", "context_reconcile"]
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
    assert not bot.STATE_FILE.exists()


def test_corrupt_context_outbox_latches_only_context_and_quote_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.write_text(
        '{"schema_version":1,"broken":true}\n',
        encoding="utf-8",
    )

    bot.reconcile_runtime_historical_context_state()

    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert bot.run_daily_meme_stage("asset_selection", lambda: "meme") == "meme"
    with pytest.raises(RuntimeError, match="outbox is unavailable"):
        bot.require_historical_context_outbox_writable()


def test_context_runtime_unavailable_does_not_require_outbox_for_main_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableOutbox:
        def verify_writable(self) -> None:
            pytest.fail("context-only outbox should not gate the main post")

        def enqueue(self, *_args, **_kwargs):
            raise OSError("context-only outbox unavailable")

    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        "RuntimeError: source-role policy mismatch",
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        lambda: UnavailableOutbox(),
    )
    bot.require_historical_context_outbox_writable()

    obligation = bot.enqueue_historical_context_obligation(_regular_receipt())

    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"] == {
        "state": "context_reply_not_required",
        "reason": "historical_context_runtime_unavailable",
        "updated_epoch": 1_800_000_000,
    }
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON


@pytest.mark.parametrize(
    "existing_name",
    ["outbox", "outbox_lock", "remote_write_restart_barrier"],
)
def test_installation_initialisation_refuses_existing_durable_state(
    existing_name: str,
) -> None:
    if existing_name == "outbox":
        path = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE
    elif existing_name == "outbox_lock":
        path = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
            f"{bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.lock"
        )
    else:
        path = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
    path.write_text("durable sentinel\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    assert path.read_text(encoding="utf-8") == "durable sentinel\n"


def test_main_post_preflight_stops_before_x_when_outbox_becomes_unwritable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnwritableOutbox:
        def verify_writable(self) -> None:
            raise OSError("injected outbox write failure")

    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        lambda: UnwritableOutbox(),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )

    with pytest.raises(RuntimeError, match="pre-post durability check"):
        bot.post_random_quote(set(), set(), {})

    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_meme_failure_stage_preserves_quote_state_and_ignores_context_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        "820001",
        main_post_confirmed_epoch=8_200,
        quote_id="c" * 64,
        quote_text="A confirmed quote with terminal context.",
    )
    outbox.claim_attempt("820001", started_epoch=8_200)
    outbox.record_terminal_failure(
        "820001",
        attempt_number=1,
        error="context is terminal",
        failed_epoch=8_201,
    )
    outbox_before = outbox.snapshot()
    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        _forbid("daily meme access to historical-context outbox"),
    )

    quote_state = {
        "last_main_post_id": "820001",
        "last_quote_post_epoch": 8_200,
        "next_quote_post_epoch": 18_200,
        "last_regular_image_filename": "t01.jpg",
        "next_meme_post_epoch": 8_300,
        "posted_meme_filenames": [],
    }
    state_before = copy.deepcopy(quote_state)
    quote_history = b'["quote-history-sentinel"]\n'
    image_history = b'["image-history-sentinel"]\n'
    bot.LINES_USED_FILE.write_bytes(quote_history)
    bot.IMAGES_USED_FILE.write_bytes(image_history)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )
    monkeypatch.setattr(
        bot,
        "choose_next_meme",
        lambda _state: (_ for _ in ()).throw(
            RuntimeError("meme asset index unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="meme asset index unavailable"):
        bot.post_next_meme(quote_state)

    assert events == [
        (
            "daily_meme_failure",
            {
                "status": "failed",
                "stage": "meme_eligibility_and_asset_selection",
                "error_type": "RuntimeError",
                "reason": "meme asset index unavailable",
            },
        )
    ]
    assert quote_state == state_before
    assert bot.LINES_USED_FILE.read_bytes() == quote_history
    assert bot.IMAGES_USED_FILE.read_bytes() == image_history
    assert outbox.snapshot() == outbox_before

    events.clear()
    schedule_calls: list[dict] = []
    monkeypatch.setattr(bot, "choose_next_meme", lambda _state: None)
    monkeypatch.setattr(
        bot,
        "schedule_next_meme_post",
        lambda state: schedule_calls.append(copy.deepcopy(state)),
    )
    bot.post_next_meme(quote_state)

    assert schedule_calls == [state_before]
    assert events == []
    assert quote_state == state_before
    assert outbox.snapshot() == outbox_before
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()
