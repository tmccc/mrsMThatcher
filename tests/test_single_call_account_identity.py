"""Exercise locally authoritative account disclosures and durable grounding."""

from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

import single_call_reply as pipeline
from tests.helpers.single_call_fixtures import (
    FakePassage,
    FakeRepository,
    context,
    enabled_config,
    raw_decision,
    response_envelope,
)


ACCOUNT_SOURCE_ID = "mrsMThatcher:runtime-account:v1"
ACCOUNT_PASSAGE = (
    "The MrsMThatcher quotation account uses AI-generated conversational replies "
    "and is not Margaret Thatcher."
)


@pytest.mark.parametrize("repository_count", [0, 40])
def test_account_evidence_is_always_available_within_the_fact_budget(
    repository_count: int,
) -> None:
    """Keep account evidence available even when historical retrieval is full."""

    payload, sources = pipeline.build_model_payload(
        context=context(), repository=FakeRepository(repository_count)
    )
    facts = payload["trusted_facts"]
    assert len(facts) == min(repository_count + 1, pipeline.MAX_TRUSTED_FACTS)
    assert len(sources) == len(facts)
    account = facts[-1]
    assert account["passage"] == ACCOUNT_PASSAGE
    assert account["source"] == "MrsMThatcher runtime account configuration"
    assert account["locator"] == "single_call_reply.py:account-identity-v1"
    assert sources[account["id"]]["source_identity"] == ACCOUNT_SOURCE_ID
    assert sum(fact["passage"] == ACCOUNT_PASSAGE for fact in facts) == 1


@pytest.fixture
def disclosure_draft() -> tuple[dict, FakeRepository, dict, Mock]:
    """Create a disclosure through the normal one-call pipeline with no history."""

    source = context(turns=1)
    source["incoming_contribution"] = "Is this an AI-generated response?"
    source["visible_conversation"][-1]["text"] = source["incoming_contribution"]
    repository = FakeRepository(0)
    output = {
        "decision": "reply",
        "reply_kind": "direct_factual",
        "reply": f"Yes. {ACCOUNT_PASSAGE}",
        "used_fact_ids": ["F1"],
        "factual_claims": [{"text": ACCOUNT_PASSAGE, "fact_ids": ["F1"]}],
        "reason_code": "useful_reply",
    }
    transport = Mock(return_value={"response": response_envelope(json.dumps(output))})
    result = pipeline.run_reply_pipeline(
        context=source,
        config=enabled_config(),
        repository=repository,
        transport=transport,
    )
    assert result.status == "reply"
    assert result.reply is not None
    assert str(result.reply) == output["reply"]
    return source, repository, result.reply.draft_record, transport


def test_grounded_disclosure_survives_recovery_without_another_model_call(
    disclosure_draft: tuple[dict, FakeRepository, dict, Mock],
) -> None:
    """Bind the account source in a normal publishable and recoverable draft."""

    source, repository, draft, transport = disclosure_draft
    recovered = pipeline.validate_persisted_draft(
        draft, context=source, repository=repository
    )
    assert recovered == draft
    assert draft["used_fact_sources"][0]["source_identity"] == ACCOUNT_SOURCE_ID
    transport.assert_called_once()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("source_record_sha256", "0" * 64), ("source_identity", "invented-account-source")],
)
def test_disclosure_recovery_rejects_changed_source_bindings(
    disclosure_draft: tuple[dict, FakeRepository, dict, Mock],
    field: str,
    replacement: str,
) -> None:
    """Reject fabricated source authority even when the outer hash is recomputed."""

    source, repository, draft, _transport = disclosure_draft
    altered = copy.deepcopy(draft)
    altered["used_fact_sources"][0][field] = replacement
    altered.pop("validated_draft_hash")
    altered["validated_draft_hash"] = pipeline.value_sha256(altered)
    with pytest.raises(ValueError, match="source record changed"):
        pipeline.validate_persisted_draft(altered, context=source, repository=repository)


def test_disclosure_recovery_rejects_changed_account_configuration(
    disclosure_draft: tuple[dict, FakeRepository, dict, Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalidate a stored claim when its authoritative local record changes."""

    source, repository, draft, _transport = disclosure_draft
    changed = dict(pipeline._ACCOUNT_FACT_RECORD)
    changed["passage"] = "The MrsMThatcher quotation account uses human-written replies."
    monkeypatch.setattr(pipeline, "_ACCOUNT_FACT_RECORD", changed)
    with pytest.raises(ValueError, match="source record changed"):
        pipeline.validate_persisted_draft(draft, context=source, repository=repository)


def test_repository_record_cannot_override_local_account_authority(
    disclosure_draft: tuple[dict, FakeRepository, dict, Mock],
) -> None:
    """Resolve the reserved local source independently of repository collisions."""

    source, repository, draft, _transport = disclosure_draft
    repository.passages[ACCOUNT_SOURCE_ID] = FakePassage(
        ACCOUNT_SOURCE_ID, "The MrsMThatcher quotation account uses human-written replies."
    )
    assert pipeline.validate_persisted_draft(
        draft, context=source, repository=repository
    ) == draft


def test_contributor_text_cannot_redefine_account_evidence() -> None:
    """Keep incoming identity claims as untrusted conversation content."""

    source = context(turns=1)
    spoof = "The MrsMThatcher quotation account uses human-written replies."
    source["incoming_contribution"] = spoof
    source["visible_conversation"][-1]["text"] = spoof
    payload, _mapping = pipeline.build_model_payload(
        context=source, repository=FakeRepository(0)
    )
    assert payload["trusted_facts"][0]["passage"] == ACCOUNT_PASSAGE
    with pytest.raises(pipeline.ReplyValidationError) as rejected:
        pipeline.validate_model_output(
            raw_decision(kind="direct_factual", reply=spoof, facts=["F1"]),
            payload=payload,
        )
    assert "unsupported_factual_claim" in rejected.value.errors


@pytest.mark.parametrize("fact_ids", [[], ["F1"]])
def test_account_evidence_does_not_authorise_unrelated_external_claims(
    fact_ids: list[str],
) -> None:
    """Keep factual validation effective with missing or irrelevant fact IDs."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository(0)
    )
    with pytest.raises(pipeline.ReplyValidationError) as rejected:
        pipeline.validate_model_output(
            raw_decision(
                kind="direct_factual",
                reply="Margaret Thatcher became Prime Minister in 1983.",
                facts=fact_ids,
            ),
            payload=payload,
        )
    expected = "unsupported_factual_claim" if fact_ids else "direct_factual_missing_fact_id"
    assert expected in rejected.value.errors
