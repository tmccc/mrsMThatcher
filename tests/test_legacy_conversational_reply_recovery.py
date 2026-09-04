from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest

from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.helpers.protocol_activation import create_test_protocol_activation


_IMPORT_TEMPORARY = tempfile.TemporaryDirectory(
    prefix="mrsMThatcher-legacy-reply-recovery-import-"
)
_IMPORT_BASE = Path(_IMPORT_TEMPORARY.name)
_IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(_IMPORT_BASE),
    "MRS_LOG_FILE": str(_IMPORT_BASE / "test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "OPENAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "OPENAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
_ORIGINAL_ENV = {name: os.environ.get(name) for name in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

import mrsMThatcher2 as bot  # noqa: E402
import remote_write_transport_journal as transport_module  # noqa: E402

for _name, _value in _ORIGINAL_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "legacy_conversational_reply_receipts.json"
)
LEGACY_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]
CASE_IDS = [str(case["case_id"]) for case in LEGACY_CASES]


def _forbid(operation: str):
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"legacy receipt recovery attempted forbidden {operation}")

    return forbidden


@pytest.fixture(autouse=True)
def isolated_recovery_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_module.reset_consumed_authorities_for_tests()
    paths = {
        "BASE_DIR": tmp_path,
        "STATE_FILE": tmp_path / "bot_state.json",
        "REGULAR_POST_RECEIPT_FILE": tmp_path / "regular_post_receipt.json",
        "MEME_POST_RECEIPT_FILE": tmp_path / "meme_post_receipt.json",
        "CONFIRMED_REPLY_RECEIPT_FILE": tmp_path / "confirmed_reply_receipt.json",
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE": (
            tmp_path / "historical_context_reply_receipt.json"
        ),
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE": tmp_path / "context_history.json",
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE": tmp_path / "context_outbox.json",
        "AMBIGUOUS_POST_OUTCOME_FILE": tmp_path / "ambiguous_post_outcome.json",
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE": (
            tmp_path / "ambiguous_post_outcome.restart_barrier.json"
        ),
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE": (
            tmp_path / REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME
        ),
        "CONTROL_FILE": tmp_path / "control.json",
    }
    for name, value in paths.items():
        monkeypatch.setattr(bot, name, value)
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "x_request", _forbid("X request"))
    monkeypatch.setattr(bot.requests, "request", _forbid("network request"))
    monkeypatch.setattr(bot, "create_post", _forbid("X post"))


def _legacy_case(case_id: str) -> dict:
    return copy.deepcopy(
        next(case for case in LEGACY_CASES if case["case_id"] == case_id)
    )


def _confirmed_receipt(case: dict, receipt_schema_version: int = 4) -> dict:
    sending = copy.deepcopy(case["sending_receipt"])
    confirmation_epoch = int(case["confirmation_epoch"])
    sending["schema_version"] = receipt_schema_version
    if receipt_schema_version == 4:
        confirmed = {
            **sending,
            "lifecycle_state": "confirmed",
            "reply_post_id": str(case["reply_post_id"]),
            "confirmation_epoch": confirmation_epoch,
            "reply_epoch": confirmation_epoch,
            "daily_reply_date": bot.epoch_date_str(confirmation_epoch),
            "source_receipt_sha256": str(case["source_receipt_sha256"]),
        }
        return confirmed
    sending.pop("attempt_epoch", None)
    sending.pop("source_receipt_sha256", None)
    sending["reply_epoch"] = confirmation_epoch
    sending["daily_reply_date"] = bot.epoch_date_str(confirmation_epoch)
    sending["reply_post_id"] = str(case["reply_post_id"])
    if receipt_schema_version == 3:
        sending["lifecycle_state"] = "confirmed"
    else:
        assert receipt_schema_version == 2
        sending.pop("lifecycle_state", None)
    return sending


def _persist_receipt(receipt: dict) -> None:
    bot.atomic_write_json(
        bot.CONFIRMED_REPLY_RECEIPT_FILE,
        receipt,
        durable=True,
    )


def _seed_confirmed_legacy_transport(case: dict) -> Path:
    sending = copy.deepcopy(case["sending_receipt"])
    _persist_receipt(sending)
    payload = {
        "text": str(sending["reply_text"]),
        "reply": {"in_reply_to_tweet_id": str(sending["target_id"])},
    }
    source_binding = bot.bind_transport_source(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        expected_receipt=sending,
        expected_receipt_bytes=bot.canonical_atomic_json_bytes(sending),
        lane="conversational_reply",
        payload=payload,
        validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=bot._legacy_conversational_transport_source_semantic_validator,
    )
    authority = bot.begin_transport_transaction(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        source_binding=source_binding,
    )
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    authority = bot.arm_transport_transaction(
        journal_path,
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused legacy conversational journal arming"
        ),
    )
    bot.consume_transport_authority(
        journal_path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
        expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
    )
    bot.confirm_transport_transaction(
        journal_path,
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused legacy conversational journal confirmation"
        ),
        post_id=str(case["reply_post_id"]),
        confirmation_epoch=int(case["confirmation_epoch"]),
    )
    return journal_path


@pytest.mark.parametrize("case_id", CASE_IDS)
@pytest.mark.parametrize("receipt_schema_version", [2, 3, 4])
def test_legacy_confirmed_receipts_reconcile_locally_without_outbound_authority(
    case_id: str,
    receipt_schema_version: int,
) -> None:
    case = _legacy_case(case_id)
    receipt = _confirmed_receipt(case, receipt_schema_version)
    _persist_receipt(receipt)

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False
    assert bot.load_confirmed_reply_receipt() == ("valid", receipt)

    state = bot.default_state()
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert state["replied_to_ids"] == [str(receipt["target_id"])]
    assert state["own_auto_reply_ids"] == [str(receipt["reply_post_id"])]
    assert state["ai_reply_history"][-1]["strategy_version"] == receipt[
        "ai_reply_draft"
    ]["strategy_version"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_legacy_schema_v4_receipt_verifies_exact_source_bytes_and_hash(
    case_id: str,
) -> None:
    case = _legacy_case(case_id)
    sending = case["sending_receipt"]
    expected_source_bytes = bot.canonical_atomic_json_bytes(sending)
    assert hashlib.sha256(expected_source_bytes).hexdigest() == case[
        "source_receipt_sha256"
    ]

    confirmed = _confirmed_receipt(case)
    assert bot._legacy_confirmed_reply_receipt_is_semantically_valid(confirmed)
    assert bot.conversational_sending_receipt_from_confirmed(confirmed) == sending

    changed_hash = copy.deepcopy(confirmed)
    changed_hash["source_receipt_sha256"] = "0" * 64
    assert not bot._legacy_confirmed_reply_receipt_is_semantically_valid(
        changed_hash
    )

    missing_hash = copy.deepcopy(confirmed)
    missing_hash.pop("source_receipt_sha256")
    assert not bot._legacy_confirmed_reply_receipt_is_semantically_valid(
        missing_hash
    )


def test_malformed_legacy_draft_fails_closed_without_escaping_validation() -> None:
    case = _legacy_case("single_sol_schema_1")
    malformed = copy.deepcopy(case["sending_receipt"])
    draft = malformed["ai_reply_draft"]
    draft["trusted_fact_ids"] = [{}]
    unsigned = {
        key: value
        for key, value in draft.items()
        if key != "validated_draft_hash"
    }
    draft["validated_draft_hash"] = bot._legacy_reply_value_sha256(unsigned)

    assert not bot._legacy_sending_reply_receipt_is_semantically_valid(
        malformed
    )


def test_tested_pipeline_direct_answer_repair_variant_remains_recoverable() -> None:
    case = _legacy_case("tested_reply_pipeline")
    sending = case["sending_receipt"]
    draft = sending["ai_reply_draft"]
    draft.update(
        {
            "direct_answer_repair_attempted": True,
            "direct_answer_repair_outcome": "approved",
            "mode": "direct_factual_answer",
            "reply_requirement": "supported_factual",
            "original_local_rejection_reason": "direct_answer_incomplete",
            "original_proposed_reply": "An incomplete first answer.",
        }
    )
    unsigned = {
        key: value for key, value in draft.items() if key != "approval_hash"
    }
    draft["approval_hash"] = bot._legacy_reply_value_sha256(unsigned)
    case["source_receipt_sha256"] = hashlib.sha256(
        bot.canonical_atomic_json_bytes(sending)
    ).hexdigest()

    assert bot._legacy_sending_reply_receipt_is_semantically_valid(sending)
    assert bot._legacy_confirmed_reply_receipt_is_semantically_valid(
        _confirmed_receipt(case)
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_legacy_sending_receipt_is_only_a_barrier_without_confirmed_journal(
    case_id: str,
) -> None:
    case = _legacy_case(case_id)
    sending = case["sending_receipt"]
    source_bytes = bot.canonical_atomic_json_bytes(sending)
    _persist_receipt(sending)
    payload = {
        "text": str(sending["reply_text"]),
        "reply": {"in_reply_to_tweet_id": str(sending["target_id"])},
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False
    assert bot.transport_source_semantic_validator(
        "conversational_reply",
        sending,
        payload,
    ) is False
    assert bot.load_confirmed_reply_receipt() == ("legacy_sending", sending)
    assert bot.unresolved_conversational_reply_receipt_is_blocking() is True
    with pytest.raises(bot.UnresolvedSendingReplyReceipt):
        bot.reconcile_confirmed_reply_receipt(bot.default_state())

    result = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        bot.default_state(),
    )

    assert result["conversational_reply"] is False
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == source_bytes
    assert not bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    ).exists()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_exact_confirmed_journal_promotes_legacy_sending_then_reconciles(
    case_id: str,
) -> None:
    case = _legacy_case(case_id)
    journal_path = _seed_confirmed_legacy_transport(case)
    state = bot.default_state()

    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        state,
    )

    assert recovered == {
        "historical_context": False,
        "conversational_reply": True,
        "regular": False,
        "meme": False,
    }
    assert state["replied_to_ids"] == [
        str(case["sending_receipt"]["target_id"])
    ]
    assert state["own_auto_reply_ids"] == [str(case["reply_post_id"])]
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not journal_path.exists()


def test_confirmed_legacy_journal_cannot_authorise_a_different_legacy_source() -> None:
    first = _legacy_case(CASE_IDS[0])
    second = _legacy_case(CASE_IDS[1])
    journal_path = _seed_confirmed_legacy_transport(first)
    _persist_receipt(second["sending_receipt"])
    source_before = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    journal_before = journal_path.read_bytes()
    state = bot.default_state()
    state_before = copy.deepcopy(state)

    with pytest.raises(bot.TransportJournalError):
        bot.reconcile_confirmed_transactions_before_global_barrier(
            set(),
            set(),
            state,
        )

    assert state == state_before
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == source_before
    assert journal_path.read_bytes() == journal_before


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_legacy_draft_is_never_reusable_as_a_current_pending_draft(
    case_id: str,
) -> None:
    case = _legacy_case(case_id)
    sending = case["sending_receipt"]

    with pytest.raises((KeyError, RuntimeError, TypeError, ValueError)):
        bot.validate_current_ai_reply_draft(
            sending["ai_reply_draft"],
            context=sending["reply_context"],
        )
