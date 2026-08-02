from __future__ import annotations

from pathlib import Path

import pytest

import exact_receipt_retirement as receipt_retirement
import mrsMThatcher2 as bot
import remote_media_upload_receipt as media
import remote_write_transport_journal as transport
from transaction_mutation_authority import (
    TransactionMutationAuthorityError,
    issue_transaction_mutation_authority,
    require_transaction_mutation_authority,
)


def _test_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="focused test mutation",
    )


@pytest.mark.parametrize(
    ("call", "match"),
    (
        (
            lambda: receipt_retirement.prepare_exact_receipt_retirement(
                Path("missing"), b"receipt"
            ),
            "preparation requires verified",
        ),
        (
            lambda: receipt_retirement.retire_exact_receipt(
                Path("missing"), b"receipt"
            ),
            "retirement requires verified",
        ),
        (
            lambda: receipt_retirement.resume_interrupted_receipt_retirement(
                Path("missing")
            ),
            "resume requires verified",
        ),
        (
            lambda: transport.replace_bound_source_receipt(None, b"receipt"),
            "replacement requires verified",
        ),
        (
            lambda: transport.arm_transport_transaction(Path("missing"), None),
            "arming requires verified",
        ),
        (
            lambda: transport.confirm_transport_transaction(
                Path("missing"), None, post_id="1", confirmation_epoch=1
            ),
            "confirmation requires verified",
        ),
        (
            lambda: transport.abort_untransmitted_transport_transaction(
                source_binding=None
            ),
            "abort requires verified",
        ),
        (
            lambda: transport.retire_confirmed_transport_transaction(
                receipt_path=Path("missing"),
                expected_confirmed_receipt={},
                lane="quote_image",
                post_id="1",
            ),
            "retirement requires verified",
        ),
        (
            lambda: media.confirm_media_upload(
                Path("missing"), None, media_id="1"
            ),
            "confirmation requires verified",
        ),
        (
            lambda: media.abort_untransmitted_media_upload(
                Path("missing"), None
            ),
            "abort requires verified",
        ),
        (
            lambda: media.retire_confirmed_media_upload(Path("missing"), None),
            "retirement requires verified",
        ),
        (
            lambda: media.resume_interrupted_confirmed_media_retirement(
                Path("missing"),
                transport_journal_path=Path("journal"),
                transport_fence_path=Path("fence"),
                source_receipt_path=Path("source"),
            ),
            "resume requires verified",
        ),
    ),
)
def test_destructive_low_level_entry_points_fail_before_inspection_without_authority(
    call,
    match: str,
) -> None:
    with pytest.raises(TransactionMutationAuthorityError, match=match):
        call()


def test_authority_rechecks_bound_verifier_on_every_use() -> None:
    calls: list[str] = []
    authority = issue_transaction_mutation_authority(
        calls.append,
        operation="test issue",
    )

    require_transaction_mutation_authority(authority, operation="first mutation")
    require_transaction_mutation_authority(authority, operation="second mutation")

    assert calls == [
        "test issue authority issuance",
        "first mutation",
        "second mutation",
    ]


def test_bot_issues_authority_from_exact_instance_lock_verifier(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", calls.append)

    authority = bot.transaction_mutation_authority("source receipt retirement")
    require_transaction_mutation_authority(
        authority,
        operation="low-level receipt mutation",
    )

    assert calls == [
        "source receipt retirement authority issuance",
        "low-level receipt mutation",
    ]


def test_bot_source_retirement_passes_verified_authority(monkeypatch, tmp_path) -> None:
    receipt = tmp_path / "regular_post_receipt.json"
    receipt.write_bytes(b"receipt")
    verifier_calls: list[str] = []
    observed = []
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", verifier_calls.append)

    def fake_retire(path, expected, *, mutation_authority, **_kwargs):
        require_transaction_mutation_authority(
            mutation_authority,
            operation="fake low-level retirement",
        )
        observed.append((path, expected))

    monkeypatch.setattr(bot, "retire_or_resume_exact_receipt", fake_retire)

    bot.retire_current_source_receipt(receipt, b"receipt")

    assert observed == [(receipt, b"receipt")]
    assert verifier_calls == [
        "source receipt exact retirement authority issuance",
        "fake low-level retirement",
    ]


def test_historical_store_refuses_mutation_without_authority_provider(tmp_path) -> None:
    from historical_context_formatter import HistoricalContextReplyStore

    store = HistoricalContextReplyStore(
        tmp_path / "history.json",
        tmp_path / "receipt.json",
    )
    store.receipt_path.write_bytes(b"receipt")

    with pytest.raises(RuntimeError, match="mutation authority provider"):
        store._retire_exact_receipt(b"receipt")


def test_explicit_test_verifier_authorises_low_level_mutation(tmp_path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"receipt")
    receipt.chmod(0o600)
    receipt_retirement.initialise_retirement_ledger(
        receipt,
        mutation_authority=_test_authority(),
    )

    result = receipt_retirement.retire_exact_receipt(
        receipt,
        b"receipt",
        mutation_authority=_test_authority(),
    )

    assert result.completed is True
    assert not receipt.exists()
