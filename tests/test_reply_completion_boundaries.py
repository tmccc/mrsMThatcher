"""Exercise exact reply completion with local storage and real commit proof."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import datetime
from logging import Logger
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from mrs_bot_receipt_primitives import ReceiptDates
from mrs_bot_reply_delivery import ReplyReceipts
from mrs_bot_reply_drafts import ReplyDrafts
from mrs_bot_reply_receipt_values import ReplyReceiptValues
from mrs_bot_reply_reconciliation import ReplyCompletion
from mrs_bot_state_generation import (
    StateCommitProof, directory_identity, encode_generation, file_identity,
)
from single_call_reply import (
    PipelineResult, ReplyValidationError, STRATEGY_VERSION, ValidatedReply,
    validate_persisted_draft,
)
from tests.helpers.reply_values import UnitReplyEvidenceRepository, unit_confirmed_v4_reply_receipt


class InvalidReceipt(Exception):
    """Represent a rejected local receipt."""


class PersistenceError(Exception):
    """Represent cleanup failure after durable confirmation."""


class UnresolvedSending(Exception):
    """Represent a sending receipt still requiring reconciliation."""


def completion_case(tmp_path: Path):
    """Bind real receipt values, receipt operations and completion to private paths."""
    path = tmp_path / "reply.json"
    state_path = tmp_path / "state.json"
    repository = UnitReplyEvidenceRepository()
    receipt = unit_confirmed_v4_reply_receipt(repository=repository)
    documents = {path: receipt}
    trace = Mock()
    logger = Mock(spec=Logger)
    drafts = ReplyDrafts(
        validate_persisted_draft=validate_persisted_draft,
        evidence_repository=lambda: repository, history=Mock(), generation=Mock(),
        log_event=Mock(), log=logger, strategy_version=STRATEGY_VERSION, model="test",
        result_type=PipelineResult, reply_type=ValidatedReply,
        evidence_unavailable=RuntimeError, validation_error=ReplyValidationError,
    )
    values = ReplyReceiptValues(
        valid_receipt_epoch=lambda epoch: type(epoch) is int and 0 < epoch < 4_102_444_800,
        dates=ReceiptDates(datetime, lambda: 2_000_000_000, "Europe/London", ZoneInfo),
        legacy_draft_is_valid=lambda *_args: False, drafts=drafts,
        now_epoch=lambda: 2_000_000_000, log=logger, invalid_receipt=InvalidReceipt,
    )
    assert values.confirmed_is_valid(receipt)

    def read_json(requested_path):
        assert requested_path == path
        return (requested_path in documents, documents.get(requested_path))

    def retire_source(requested_path, expected_bytes, *, commit_proof, **_kwargs):
        assert requested_path == path
        from mrs_bot_durable_json_io import canonical_atomic_json_bytes
        assert expected_bytes == canonical_atomic_json_bytes(receipt)
        assert documents[path] is receipt
        trace.retire_source(commit_proof)
        del documents[path]

    receipts = ReplyReceipts(
        path=path, read_json=read_json, log=logger, values=values,
        retirement_is_blocking=lambda: False, invalid_receipt=InvalidReceipt,
        namespace_entry_exists=lambda _path: path in documents,
        create_json=lambda requested_path, data: documents.__setitem__(requested_path, data),
        unresolved_sending=UnresolvedSending, bind_confirmed_source=Mock(),
        journal_path=lambda _path: tmp_path / "journal.json", validator_id="test",
        transport_validator=values.sending_is_valid,
        legacy_transport_validator=values.legacy_sending_is_valid,
        transport_journal_error=RuntimeError, replace_bound_source=Mock(),
        mutation_authority=Mock(), current_receipts=lambda: receipts,
        retire_current_source_receipt=retire_source, proved_non_success=LookupError,
        reply_not_allowed=lambda _error: False, rejection_payload=Mock(),
        claim_rejection=Mock(), record_ambiguous=Mock(), persistence_error=PersistenceError,
    )

    def save(state, *, durable=False):
        assert durable is True
        trace.save(state)
        document, data = encode_generation(state, 1, 1_000_000)
        with state_path.open("wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        proof = StateCommitProof(
            state_path, directory_identity(tmp_path), file_identity(state_path),
            hashlib.sha256(data).hexdigest(), 1, 1_000_000,
            receipt_digests=frozenset(document["_confirmed_receipt_commits"]),
        )
        proof.require_current()
        return proof

    def apply(state, applied):
        assert applied is receipt
        trace.apply(state)
        state["replied_to_ids"] = [applied["target_id"]]

    def retire_journal(*, commit_proof, **kwargs):
        assert kwargs["receipt"] is receipt
        trace.retire_journal(commit_proof)

    completion = ReplyCompletion(
        receipt_path=path, persistence_error=PersistenceError, apply_state=apply,
        save_state=save, retire_journal=retire_journal, log=logger, receipts=receipts,
        unresolved_sending_receipt=UnresolvedSending, invalid_receipt=InvalidReceipt,
        verify_lineage=Mock(),
    )
    return completion, receipts, receipt, documents, trace


def test_confirmation_saves_before_retiring_journal_and_exact_receipt(tmp_path):
    completion, receipts, receipt, documents, trace = completion_case(tmp_path)
    state = {}
    assert receipts.load() == ("valid", receipt)
    assert completion.finalise(state, receipt, target_id="100", quote_reply=False) == "999"
    assert [entry[0] for entry in trace.mock_calls] == [
        "apply", "save", "retire_journal", "retire_source",
    ]
    proof = trace.retire_journal.call_args.args[0]
    assert proof is trace.retire_source.call_args.args[0]
    proof.require_receipt(receipt)
    assert state["replied_to_ids"] == ["100"]
    assert documents == {}


def test_save_failure_keeps_journal_and_receipt(tmp_path):
    completion, _receipts, receipt, documents, trace = completion_case(tmp_path)
    failure = OSError("state unavailable")
    completion = replace(completion, save_state=Mock(side_effect=failure))
    with pytest.raises(OSError) as caught:
        completion.finalise({}, receipt, target_id="100", quote_reply=False)
    assert caught.value is failure
    trace.retire_journal.assert_not_called()
    trace.retire_source.assert_not_called()
    assert documents[completion.receipt_path] is receipt


def test_journal_retirement_failure_preserves_source_and_wraps_after_save(tmp_path):
    completion, _receipts, receipt, documents, trace = completion_case(tmp_path)
    failure = OSError("journal unavailable")
    completion = replace(completion, retire_journal=Mock(side_effect=failure))
    with pytest.raises(PersistenceError) as caught:
        completion.finalise({}, receipt, target_id="100", quote_reply=False)
    assert caught.value.__cause__ is failure
    trace.save.assert_called_once()
    trace.retire_source.assert_not_called()
    assert documents[completion.receipt_path] is receipt


def test_confirmed_receipt_replay_commits_and_retires_without_publication(tmp_path):
    completion, receipts, receipt, documents, trace = completion_case(tmp_path)
    state = {}
    assert completion.reconcile(state) is True
    completion.verify_lineage.assert_called_once_with(
        receipt_path=completion.receipt_path, receipt=receipt,
        lane="conversational_reply", post_id="999",
    )
    assert [entry[0] for entry in trace.mock_calls] == [
        "apply", "save", "retire_journal", "retire_source",
    ]
    assert trace.retire_journal.call_args.args[0] is trace.retire_source.call_args.args[0]
    assert documents == {}
    assert receipts.load() == ("absent", None)
    assert completion.reconcile(state) is False
    assert trace.save.call_count == 1
