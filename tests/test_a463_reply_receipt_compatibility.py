"""Recover frozen a4639e7 replies without authorising another model or X call."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers.legacy_reply_fixtures import (
    _confirmed_receipt,
    bot,
    isolated_recovery_paths,
)
from tests.test_legacy_conversational_reply_recovery import (
    _persist_receipt,
    _seed_confirmed_legacy_transport,
)


FIXTURE = Path(__file__).parent / "fixtures/a4639e7_single_call_reply_receipts.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(autouse=True)
def no_current_repository_needed(monkeypatch):
    """Keep rejection at the frozen prompt boundary independent of local corpus."""
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: object())


@pytest.fixture(params=CASES, ids=lambda value: value["case_id"])
def case(request):
    """Return the exact pre-change receipt with independent mutable ownership."""
    return copy.deepcopy(request.param)


def _rehash(draft: dict) -> None:
    """Rehash deliberate corruptions to test semantic checks independently."""
    draft["validated_draft_hash"] = bot._legacy_reply_value_sha256(
        {key: value for key, value in draft.items() if key != "validated_draft_hash"}
    )


def test_frozen_schema4_receipts_are_valid_only_for_recovery(case):
    sending = case["sending_receipt"]
    assert hashlib.sha256(bot.canonical_atomic_json_bytes(sending)).hexdigest() == case["source_receipt_sha256"]
    assert bot._legacy_sending_reply_receipt_is_semantically_valid(sending)
    assert bot._legacy_confirmed_reply_receipt_is_semantically_valid(_confirmed_receipt(case))
    assert not bot.sending_reply_receipt_is_semantically_valid(sending)
    with pytest.raises(ValueError, match="contract mismatch"):
        bot.validate_current_ai_reply_draft(sending["ai_reply_draft"], context=sending["reply_context"])


def test_obsolete_unsent_frozen_draft_is_discarded_for_regeneration(case):
    sending = case["sending_receipt"]
    target, lane = sending["target_id"], sending["candidate_source"]
    key = bot.pending_ai_reply_draft_key(target, lane)
    state = bot.default_state()
    state_before = copy.deepcopy(state)
    state_before.pop("pending_ai_reply_drafts", None)
    state["pending_ai_reply_drafts"] = {key: copy.deepcopy(sending["ai_reply_draft"])}
    journal = bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not journal.exists()

    result = bot.recover_pending_ai_reply(
        state, target, lane, context=sending["reply_context"],
    )

    assert result.status == "draft_discarded"
    assert result.reason == "obsolete_or_invalid_persisted_draft"
    assert result.model_call_count == 0
    assert result.reply is None
    assert state == state_before
    assert bot.recover_pending_ai_reply(state, target, lane, context=sending["reply_context"]) is None
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not journal.exists()
    assert not bot.STATE_FILE.exists()


def test_started_frozen_receipt_remains_a_barrier_without_confirmation(case):
    sending = case["sending_receipt"]
    _persist_receipt(sending)
    before = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    assert bot.load_confirmed_reply_receipt() == ("legacy_sending", sending)
    assert not bot.transport_source_semantic_validator(
        "conversational_reply", sending,
        {"text": sending["reply_text"], "reply": {"in_reply_to_tweet_id": sending["target_id"]}},
    )
    with pytest.raises(bot.UnresolvedSendingReplyReceipt):
        bot.reconcile_confirmed_reply_receipt(bot.default_state())
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == before


def test_confirmed_frozen_receipt_reconciles_and_remains_deduplicated_after_reload(case):
    _persist_receipt(_confirmed_receipt(case))
    state = bot.default_state()
    assert bot.reconcile_confirmed_reply_receipt(state)
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    reloaded = bot.load_state()
    assert reloaded["replied_to_ids"] == [case["sending_receipt"]["target_id"]]
    assert reloaded["own_auto_reply_ids"] == [case["reply_post_id"]]
    assert not bot.reconcile_confirmed_reply_receipt(reloaded)


@pytest.mark.parametrize("corruption", [
    "unknown_prompt", "unknown_schema", "wrong_model", "wrong_reasoning", "multiple_calls",
    "claim_span", "claim_ids", "claim_extra_field", "time_context", "context_time",
    "context_author", "context_text", "bad_image_role", "draft_hash",
])
def test_frozen_schema4_rejects_tampering_even_when_draft_rehashed(corruption):
    case = copy.deepcopy(CASES[1])
    sending = case["sending_receipt"]
    draft = sending["ai_reply_draft"]
    if corruption == "unknown_prompt":
        draft["prompt_sha256"] = "f" * 64
    elif corruption == "unknown_schema":
        draft["response_schema_sha256"] = "f" * 64
    elif corruption == "wrong_model":
        draft["model"] = "unrecognised-model"
    elif corruption == "wrong_reasoning":
        draft["reasoning_effort"] = "low"
    elif corruption == "multiple_calls":
        draft["model_call_count"] = 2
    elif corruption == "claim_span":
        draft["factual_claims"][0]["text"] = "An absent claim."
    elif corruption == "claim_ids":
        draft["factual_claims"][0]["fact_ids"] = ["F2"]
    elif corruption == "claim_extra_field":
        draft["factual_claims"][0]["extra"] = True
    elif corruption == "time_context":
        draft["time_context"]["current_date"] = "2026-07-21"
    elif corruption == "context_time":
        sending["reply_context"]["target_created_at"] = "2026-07-20T12:00:00"
    elif corruption == "context_author":
        sending["reply_context"]["target_author_id"] = "999"
    elif corruption == "context_text":
        sending["reply_context"]["visible_conversation"][0]["text"] = "Changed contribution."
    elif corruption == "bad_image_role":
        draft["supplied_images"] = [{"identity": "old image", "sha256": "a" * 64, "mime_type": "image/png", "byte_count": 128, "attachment_role": "quoted_subject", "source_post_id": "999"}]
    _rehash(draft)
    if corruption == "draft_hash":
        draft["validated_draft_hash"] = "0" * 64
    assert not bot._legacy_sending_reply_receipt_is_semantically_valid(sending)


def test_frozen_schema4_confirmed_receipt_requires_exact_source_hash(case):
    receipt = _confirmed_receipt(case)
    receipt["source_receipt_sha256"] = "0" * 64
    assert not bot._legacy_confirmed_reply_receipt_is_semantically_valid(receipt)


def test_frozen_confirmed_journal_cannot_promote_different_receipt(case):
    journal = _seed_confirmed_legacy_transport(case)
    other = copy.deepcopy(CASES[1 if case["case_id"] == "social" else 0]["sending_receipt"])
    _persist_receipt(other)
    source_before = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    journal_before = journal.read_bytes()
    with pytest.raises(bot.TransportJournalError):
        bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), bot.default_state())
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == source_before
    assert journal.read_bytes() == journal_before


def test_frozen_confirmation_keeps_evidence_until_state_commit(case, monkeypatch):
    journal = _seed_confirmed_legacy_transport(case)
    journal_before = journal.read_bytes()

    def fail_save(*args, **kwargs):
        raise OSError("injected state publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(bot, "save_state", fail_save)
        with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), bot.default_state())
    assert journal.read_bytes() == journal_before
    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "valid"
    assert receipt["source_receipt_sha256"] == case["source_receipt_sha256"]
    assert bot._legacy_confirmed_reply_receipt_is_semantically_valid(receipt)
    assert bot.reconcile_confirmed_reply_receipt(bot.load_state())
    assert not journal.exists()
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


def test_frozen_confirmed_journal_recovers_once_in_fresh_process(case, tmp_path):
    journal = _seed_confirmed_legacy_transport(case)
    env = dict(os.environ, MRS_TEST_MODE="1", MRS_BASE_DIR=str(tmp_path), MRS_LOG_FILE=str(tmp_path / "child.log"))
    code = '''
import json, socket
import mrsMThatcher2 as bot
def forbidden(*args, **kwargs):
    raise AssertionError("frozen recovery attempted outbound access")
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
bot.x_request = bot.create_post = bot.requests.request = forbidden
bot._PRODUCTION_BOOTSTRAPPED = True
bot.STATE_BACKUP_COUNT = 0
state = bot.load_state()
result = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), state)
assert result["conversational_reply"]
reloaded = bot.load_state()
assert not bot.reconcile_confirmed_reply_receipt(reloaded)
print(json.dumps({"replied": reloaded["replied_to_ids"], "own": reloaded["own_auto_reply_ids"]}))
'''
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result == {"replied": [case["sending_receipt"]["target_id"]], "own": [case["reply_post_id"]]}
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not journal.exists()
