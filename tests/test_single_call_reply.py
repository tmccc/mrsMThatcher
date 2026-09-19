from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import single_call_reply as pipeline
from reply_evidence import (
    TRUSTED_FACT_AUDIT_CLAIMS,
    EvidencePassage,
    EvidenceRepository,
    retrieval_tokens,
)
from single_call_reply_validation import (
    MAX_REJECTED_REPLY_TEXT_CHARACTERS,
    MAX_VALIDATION_ERROR_CODES,
    rejected_reply_text_fields,
)
from tests.helpers.single_call_fixtures import (
    FakeRepository, context, enabled_config, raw_decision, response_envelope, valid_png, valid_jpeg,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_frozen_prompt_and_schema_hashes() -> None:
    """Pin the exact reviewed prompt bytes and local response contract."""

    assert pipeline.PROMPT_SHA256 == (
        "c1e6145bd90b9811258e91b598ff695ab69900878ed7c03e9210676638377d67"
    )
    assert pipeline.RESPONSE_SCHEMA_SHA256 == (
        "936ea48c371babd74944a227619531139f0386a28ac64130ccfae246b14655b5"
    )
    assert "uniqueItems" in pipeline.RESPONSE_SCHEMA["properties"]["used_fact_ids"]
    assert "uniqueItems" not in pipeline.provider_response_schema()["properties"][
        "used_fact_ids"
    ]


def test_configuration_is_small_exact_and_fail_closed() -> None:
    """Reject alternate models, versions, fields and malformed timeouts."""

    assert pipeline.default_config() == {
        "enabled": False,
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "timeout_seconds": 180,
    }
    assert pipeline.validate_config(pipeline.default_config()) == []
    bad = pipeline.default_config()
    bad["model"] = "another-model"
    bad["shadow"] = True
    assert pipeline.validate_config(bad)


def test_payload_enforces_all_context_and_fact_limits() -> None:
    """Keep subject/target identity while bounding every context collection."""

    repository = FakeRepository(40)
    source = context(turns=15)
    source["visible_conversation"][0]["text"] = "R" * 4_000
    same_author = [
        {"contributor": f"Earlier {index}", "account_reply": f"Reply {index}"}
        for index in range(12)
    ]
    recent = [
        {"post_id": f"reply-{index}", "text": f"Recent reply {index}."}
        for index in range(40)
    ]
    payload, fact_map = pipeline.build_model_payload(
        context=source,
        repository=repository,
        same_author_interactions=same_author,
        recent_account_replies=recent,
    )
    visible = payload["visible_conversation"]
    assert len(visible) <= 12
    assert sum(len(turn["text"]) for turn in visible) <= 12_000
    assert visible[0]["post_id"] == "post-1"
    assert visible[-1]["post_id"] == "target"
    assert [turn["post_id"] for turn in visible] == [
        "post-1",
        *[f"post-{index}" for index in range(5, 15)],
        "target",
    ]
    assert sum(turn["post_id"] == "target" for turn in visible) == 1
    assert len(payload["recent_same_author_account_interactions"]) == 8
    assert len(payload["recent_account_replies"]) == 30
    assert len(payload["trusted_facts"]) == 32
    assert len(fact_map) == 32
    assert repository.last_limits == (8, 32)


def test_history_and_quoted_subject_fields_fail_closed_above_source_bounds() -> None:
    """Reject individually oversized prose before it can enter the payload."""

    source = context(turns=1)
    with pytest.raises(pipeline.ContextValidationError, match="contributor.*too long"):
        pipeline.build_model_payload(
            context=source,
            repository=FakeRepository(),
            same_author_interactions=[
                {
                    "contributor": "x"
                    * (pipeline.MAX_SAME_AUTHOR_CONTRIBUTOR_CHARACTERS + 1),
                    "account_reply": "A prior reply.",
                }
            ],
        )
    with pytest.raises(pipeline.ContextValidationError, match="account reply.*too long"):
        pipeline.build_model_payload(
            context=source,
            repository=FakeRepository(),
            recent_account_replies=[
                "x" * (pipeline.MAX_HISTORY_ACCOUNT_REPLY_CHARACTERS + 1)
            ],
        )

    quoted = context(turns=1)
    quoted["quoted_post"] = {
        "post_id": "quoted",
        "author_role": "other_user",
        "text": "x" * (pipeline.MAX_QUOTED_SUBJECT_TEXT_CHARACTERS + 1),
    }
    quoted["quoted_post_id"] = "quoted"
    quoted["quoted_post_relationship"] = "target_quote"
    with pytest.raises(pipeline.ContextValidationError, match="quoted subject text.*too long"):
        pipeline.build_model_payload(
            context=quoted,
            repository=FakeRepository(),
        )


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("passage", pipeline.MAX_TRUSTED_FACT_PASSAGE_CHARACTERS),
        ("source_title", pipeline.MAX_TRUSTED_FACT_SOURCE_CHARACTERS),
        ("stable_locator", pipeline.MAX_TRUSTED_FACT_LOCATOR_CHARACTERS),
    ],
)
def test_compact_fact_fields_fail_closed_above_source_bounds(
    field: str,
    limit: int,
) -> None:
    """Bound each model-visible fact field independently of record count."""

    record = {
        "evidence_id": "evidence-1",
        "passage": "A trusted passage.",
        "source_title": "Official archive",
        "stable_locator": "record:1",
    }
    record[field] = "x" * (limit + 1)
    with pytest.raises(pipeline.ContextValidationError, match="too long"):
        pipeline.compact_fact_records([record])


def test_fact_source_record_and_final_payload_have_canonical_byte_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound private fact inputs and the complete canonical request payload."""

    oversized_record = {
        "evidence_id": "evidence-1",
        "passage": "A trusted passage.",
        "source_title": "Official archive",
        "stable_locator": "record:1",
        "unused_private_material": "x"
        * pipeline.MAX_TRUSTED_FACT_SOURCE_RECORD_BYTES,
    }
    with pytest.raises(pipeline.ContextValidationError, match="source record.*too large"):
        pipeline.compact_fact_records([oversized_record])

    monkeypatch.setattr(pipeline, "MAX_MODEL_PAYLOAD_BYTES", 100)
    with pytest.raises(pipeline.ContextValidationError, match="model payload.*too large"):
        pipeline.build_model_payload(
            context=context(),
            repository=FakeRepository(),
        )


def test_visible_path_rejects_duplicate_target_and_handles_short_context() -> None:
    """Keep one final target and accept an ordinary two-turn verified path."""

    short = context(turns=2)["visible_conversation"]
    assert pipeline.bound_visible_conversation(
        short, target_post_id="target"
    ) == [
        {"post_id": "post-1", "role": "account", "text": "Earlier contribution 1."},
        {"post_id": "target", "role": "user", "text": "What principle matters here?"},
    ]
    duplicated = [*copy.deepcopy(short), copy.deepcopy(short[-1])]
    with pytest.raises(pipeline.ContextValidationError, match="duplicates"):
        pipeline.bound_visible_conversation(
            duplicated,
            target_post_id="target",
        )


def test_visible_context_accepts_natural_joiners_but_not_other_format_controls() -> None:
    """Permit ZWJ/ZWNJ in contribution text without accepting hidden controls."""

    source = context(turns=1)
    source["incoming_contribution"] = "خانواده\u200cها 👨\u200d👩\u200d👧"
    source["visible_conversation"][0]["text"] = source[
        "incoming_contribution"
    ]
    payload, _mapping = pipeline.build_model_payload(
        context=source,
        repository=FakeRepository(),
    )
    assert payload["visible_conversation"][0]["text"] == source[
        "incoming_contribution"
    ]

    source["visible_conversation"][0]["text"] = "hidden\u2060control"
    with pytest.raises(pipeline.ContextValidationError, match="format"):
        pipeline.build_model_payload(context=source, repository=FakeRepository())


def test_visible_character_trimming_retains_root_target_and_nearest_parents() -> None:
    """Remove older intermediate turns before nearer parent context."""

    source = context(turns=15)["visible_conversation"]
    for turn in source:
        turn["text"] = f"{turn['post_id']} " + ("x" * 1_490)
    visible = pipeline.bound_visible_conversation(
        source,
        target_post_id="target",
    )
    identifiers = [turn["post_id"] for turn in visible]
    assert identifiers[0] == "post-1"
    assert identifiers[-1] == "target"
    assert "post-5" not in identifiers
    assert "post-14" in identifiers
    assert len(visible) <= 12
    assert sum(len(turn["text"]) for turn in visible) <= 12_000


def test_payload_has_one_authoritative_occurrence_of_target_text() -> None:
    """Do not repeat the target contribution outside visible_conversation."""

    repository = FakeRepository(2)
    payload, _fact_map = pipeline.build_model_payload(
        context=context(),
        repository=repository,
        same_author_interactions=[
            {"contributor": "Different history.", "account_reply": "A prior reply."}
        ],
        recent_account_replies=["Another prior reply."],
    )
    canonical = pipeline.canonical_bytes(payload).decode("utf-8")
    assert canonical.count("What principle matters here?") == 1


def test_fact_compaction_deduplicates_without_history_contaminating_retrieval() -> None:
    """Use current context only for stable, non-padded local fact retrieval."""

    records = [
        {
            "evidence_id": "evidence-a",
            "passage": "One   compact passage.",
            "source_title": "Official archive",
            "stable_locator": "record:1",
        },
        {
            "evidence_id": "evidence-b",
            "passage": "One compact passage.",
            "source_title": "Official   archive",
            "stable_locator": "record:1",
        },
        {
            "evidence_id": "evidence-c",
            "passage": "One compact passage.",
            "source_title": "Official archive",
            "stable_locator": "record:2",
        },
    ]
    compact, mapping = pipeline.compact_fact_records(records)
    assert [fact["id"] for fact in compact] == ["F1", "F2"]
    assert len(mapping) == 2

    repository = FakeRepository(2)
    current_context = context()
    current_context["quoted_post"] = {
        "post_id": "quoted-subject",
        "author_role": "other_user",
        "text": "DIRECTLY-QUOTED-SUBJECT",
    }
    current_context["root_post_id"] = "quoted-subject"
    current_context["parent_post_id"] = "quoted-subject"
    current_context["parent_thread"] = [
        copy.deepcopy(current_context["quoted_post"])
    ]
    current_context["visible_conversation"] = [
        copy.deepcopy(current_context["quoted_post"]),
        copy.deepcopy(current_context["visible_conversation"][-1]),
    ]
    payload, _fact_map = pipeline.build_model_payload(
        context=current_context,
        repository=repository,
        same_author_interactions=[
            {
                "contributor": "SAME-AUTHOR-CONTAMINATION",
                "account_reply": "Older reply.",
            }
        ],
        recent_account_replies=["RECENT-REPLY-CONTAMINATION"],
    )
    assert repository.last_query is not None
    assert "SAME-AUTHOR-CONTAMINATION" not in repository.last_query
    assert "RECENT-REPLY-CONTAMINATION" not in repository.last_query
    assert "DIRECTLY-QUOTED-SUBJECT" in repository.last_query
    assert payload["visible_conversation"][0]["text"] == (
        "DIRECTLY-QUOTED-SUBJECT"
    )
    assert len(payload["trusted_facts"]) == 3


def test_trusted_fact_retrieval_excludes_uncertain_and_interpretive_passages() -> None:
    """Do not relabel unresolved research or editorial analysis as authority."""

    def passage(
        identity: str,
        *,
        field: str = "historical_context",
        status: str = "exact",
        confidence: str = "high",
    ) -> EvidencePassage:
        return EvidencePassage(
            evidence_id=identity,
            source_hash=f"source-{identity}",
            quote_id=f"quote-{identity}",
            field=field,
            passage=f"Distinct policy evidence {identity}",
            source_title="Official archive",
            source_url="https://archive.example/source",
            stable_locator=f"record:{identity}",
            verification_status=status,
            research_confidence=confidence,
            trusted_fact_eligible=True,
        )

    candidates = [
        passage("trusted"),
        passage("low", confidence="low"),
        passage("unverified", status="unverified"),
        passage("interpretive", field="intended_argument"),
    ]
    repository = object.__new__(EvidenceRepository)
    repository.packets = {}
    repository.passages = {
        item.evidence_id: item for item in candidates
    }
    repository._passage_tokens = {
        item.evidence_id: retrieval_tokens(item.passage) for item in candidates
    }
    repository._passages_by_quote = {}

    selected = repository.candidate_passages(
        "distinct policy evidence",
        maximum_packets=8,
        maximum_passages=32,
        trusted_only=True,
    )

    assert [item.evidence_id for item in selected] == ["trusted"]


def test_real_trusted_facts_are_field_and_source_audit_bound() -> None:
    """Exclude unaudited packet prose and do not expose source URLs as locators."""

    repository = EvidenceRepository(
        PROJECT_ROOT / "semantic_alignment_research" / "quote_research_full_001",
        factual_evidence_path=PROJECT_ROOT / "reply_factual_evidence.json",
    )
    trusted = [
        passage
        for passage in repository.passages.values()
        if repository.passage_is_trusted_fact(passage)
    ]
    cited_bad_quote_id = (
        "0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5"
    )

    assert trusted
    assert cited_bad_quote_id in repository.packets
    assert any(passage.field == "factual_evidence" for passage in trusted)
    assert all(passage.field != "quote_text" for passage in trusted)
    assert all(
        passage.quote_id != cited_bad_quote_id
        for passage in trusted
    )
    assert all(
        not repository.passage_is_trusted_fact(passage)
        for passage in repository.passages.values()
        if passage.quote_id == cited_bad_quote_id
    )
    assert all(
        not passage.stable_locator.startswith(("http://", "https://"))
        for passage in trusted
    )
    for passage in trusted:
        if passage.field == "factual_evidence":
            continue
        packet = repository.packets[passage.quote_id]
        source = repository._trusted_packet_field_source(
            packet,
            field=passage.field,
        )
        audited_claims = TRUSTED_FACT_AUDIT_CLAIMS[passage.field]
        required_claims = set(audited_claims)
        assert source is not None
        assert required_claims <= set(source["claims_supported"])
        assert passage.stable_locator == (
            str(source.get("stable_locator") or "")
            or str(source.get("public_title") or source.get("source_title") or "")
            or f"retained source {str(source.get('source_id') or '')[:16]}"
        )
        assert all(
            packet["_source_role_audit"]["confidence_after"][claim]
            in {"medium", "high"}
            for claim in audited_claims
        )


def test_audit_source_identity_mutation_invalidates_persisted_fact_binding() -> None:
    """Bind durable facts to the selected audit record, not display text alone."""

    quote_id = "025f0aaeb01b29a6285ae85c4f33c2b9d84c1aad435f0f6036151c39320c3eac"

    def repository_for(packet: dict[str, object]) -> EvidenceRepository:
        repository = object.__new__(EvidenceRepository)
        repository.packets = {quote_id: packet}
        repository.passages = {}
        repository._passage_tokens = {}
        repository._authorised_quote_texts = {}
        repository._authorised_quote_words = {}
        repository._quote_match_texts = {}
        repository._passages_by_quote = {}
        repository._build_indexes()
        return repository

    complete = EvidenceRepository(
        PROJECT_ROOT / "semantic_alignment_research" / "quote_research_full_001",
        factual_evidence_path=PROJECT_ROOT / "reply_factual_evidence.json",
    )
    original_packet = copy.deepcopy(complete.packets[quote_id])
    original = repository_for(original_packet)
    original_passage = next(
        passage
        for passage in original._passages_by_quote[quote_id]
        if passage.field == "verified_text"
    )
    assert original.passage_is_trusted_fact(original_passage)

    compact, fact_map = pipeline.compact_fact_records(
        [original_passage.prompt_record()]
    )
    source_context = context()
    visible = pipeline.bound_visible_conversation(
        source_context["visible_conversation"],
        target_post_id=source_context["target_id"],
    )
    payload = {
        "lane": source_context["lane"],
        "identities": {
            "target_post_id": source_context["target_id"],
            "root_post_id": source_context["root_post_id"],
            "parent_post_id": source_context["parent_post_id"],
        },
        "visible_conversation": visible,
        "trusted_facts": compact,
        "time_context": {"current_date": source_context["current_date"], "target_created_at": None},
    }
    output = {
        "decision": "reply",
        "reply_kind": "principle",
        "reply": original_passage.passage,
        "factual_claims": [{"text": original_passage.passage, "fact_ids": ["F1"]}],
        "used_fact_ids": ["F1"],
        "reason_code": "useful_reply",
    }
    draft = pipeline.create_durable_draft(
        output=output,
        payload=payload,
        fact_map=fact_map,
        images=[],
        target_author_id=source_context["target_author_id"],
    )
    pipeline.validate_persisted_draft(
        draft,
        context=source_context,
        repository=original,
    )

    source = EvidenceRepository._trusted_packet_field_source(
        original_packet,
        field="verified_text",
    )
    assert source is not None
    source_id = source["source_id"]
    for mutation in ("source_id", "source_fingerprint", "policy_version"):
        changed_packet = copy.deepcopy(original_packet)
        matching_sources = [
            item
            for item in changed_packet["_source_role_audit"]["renderable_sources"]
            if item.get("source_id") == source_id
        ]
        assert len(matching_sources) == 1
        if mutation == "source_id":
            matching_sources[0]["source_id"] = "f" * 64
        elif mutation == "source_fingerprint":
            matching_sources[0]["source_fingerprint"] = "e" * 64
        else:
            changed_packet["_source_role_audit"]["policy_version"] += "-changed"
        changed = repository_for(changed_packet)
        changed_passage = next(
            passage
            for passage in changed._passages_by_quote[quote_id]
            if passage.field == "verified_text"
        )

        assert changed_passage.source_title == original_passage.source_title
        assert changed_passage.stable_locator == original_passage.stable_locator
        assert changed_passage.source_hash != original_passage.source_hash
        assert changed_passage.evidence_id != original_passage.evidence_id
        with pytest.raises(ValueError, match="source record changed"):
            pipeline.validate_persisted_draft(
                draft,
                context=source_context,
                repository=changed,
            )


def test_quoted_subject_is_separate_retrieval_input_and_draft_binding() -> None:
    """Keep the verified reply path while binding one labelled quoted branch."""

    repository = FakeRepository(2)
    source = context(turns=4)
    source["quoted_post"] = {
        "post_id": "quoted-900",
        "author_role": "other_user",
        "text": "A separately quoted proposition.",
    }
    source["quoted_post_id"] = "quoted-900"
    source["quoted_post_relationship"] = "target_quote"
    payload, fact_map = pipeline.build_model_payload(
        context=source,
        repository=repository,
    )

    assert [turn["post_id"] for turn in payload["visible_conversation"]] == [
        "post-1",
        "post-2",
        "post-3",
        "target",
    ]
    assert payload["quoted_subject"] == {
        "relationship": "target_quote",
        "post_id": "quoted-900",
        "role": "other_user",
        "text": "A separately quoted proposition.",
    }
    assert repository.last_query is not None
    assert "A separately quoted proposition." in repository.last_query
    canonical = pipeline.canonical_bytes(payload).decode("utf-8")
    assert canonical.count("What principle matters here?") == 1
    assert canonical.count("A separately quoted proposition.") == 1

    output = pipeline.validate_model_output(raw_decision(), payload=payload)
    draft = pipeline.create_durable_draft(
        output=output,
        payload=payload,
        fact_map=fact_map,
        images=[],
        target_author_id="200",
    )
    pipeline.validate_persisted_draft(
        draft,
        context=source,
        repository=repository,
    )
    changed = copy.deepcopy(source)
    changed["quoted_post"]["text"] = "A changed quoted proposition."
    with pytest.raises(ValueError, match="context mismatch"):
        pipeline.validate_persisted_draft(
            draft,
            context=changed,
            repository=repository,
        )
    mismatched_identity = copy.deepcopy(source)
    mismatched_identity["quoted_post_id"] = "quoted-901"
    with pytest.raises(
        pipeline.ContextValidationError,
        match="identities disagree",
    ):
        pipeline.build_model_payload(
            context=mismatched_identity,
            repository=repository,
        )


def test_mixed_image_provenance_is_bound_to_payload_request_and_draft() -> None:
    """Bind target/quote image order without a second text or model request."""

    source = context(turns=2)
    source["quoted_post"] = {
        "post_id": "quoted-900",
        "author_role": "other_user",
        "text": "Quoted image subject.",
    }
    source["quoted_post_relationship"] = "target_quote"
    images = [
        {
            "identity": "target-image",
            "mime_type": "image/png",
            "data": valid_png(),
            "attachment_role": "target_contribution",
            "source_post_id": "target",
        },
        {
            "identity": "quote-image",
            "mime_type": "image/jpeg",
            "data": valid_jpeg(),
            "attachment_role": "quoted_subject",
            "source_post_id": "quoted-900",
        },
    ]
    calls: list[dict[str, object]] = []

    def transport(**kwargs: object) -> dict[str, object]:
        calls.append(copy.deepcopy(kwargs))
        return {"response": response_envelope(raw_decision())}

    result = pipeline.run_reply_pipeline(
        context=source,
        config=enabled_config(),
        repository=FakeRepository(),
        transport=transport,
        supplied_images=images,
    )

    assert result.status == "reply"
    assert len(calls) == 1
    content = calls[0]["request"]["input"][0]["content"]
    assert [item["type"] for item in content] == [
        "input_text",
        "input_image",
        "input_image",
    ]
    model_payload = json.loads(content[0]["text"])
    assert model_payload["supplied_images"] == [
        {
            "attachment_role": "target_contribution",
            "image_index": 1,
            "source_post_id": "target",
        },
        {
            "attachment_role": "quoted_subject",
            "image_index": 2,
            "source_post_id": "quoted-900",
        },
    ]
    assert result.reply is not None
    assert [
        (item["attachment_role"], item["source_post_id"])
        for item in result.reply.draft_record["supplied_images"]
    ] == [
        ("target_contribution", "target"),
        ("quoted_subject", "quoted-900"),
    ]


@pytest.mark.parametrize(
    "images",
    [
        [
            {
                "identity": "wrong-target",
                "mime_type": "image/png",
                "data": valid_png(),
                "attachment_role": "target_contribution",
                "source_post_id": "someone-else",
            }
        ],
        [
            {
                "identity": "quote-first",
                "mime_type": "image/jpeg",
                "data": b"\xff\xd8\xffquote",
                "attachment_role": "quoted_subject",
                "source_post_id": "quoted-900",
            },
            {
                "identity": "target-second",
                "mime_type": "image/png",
                "data": valid_png(),
                "attachment_role": "target_contribution",
                "source_post_id": "target",
            },
        ],
    ],
)
def test_image_provenance_rejects_wrong_subject_or_order(
    images: list[dict[str, object]],
) -> None:
    """Fail before provider work if image provenance is not candidate-bound."""

    source = context(turns=2)
    source["quoted_post"] = {
        "post_id": "quoted-900",
        "author_role": "other_user",
        "text": "Quoted image subject.",
    }
    source["quoted_post_relationship"] = "target_quote"
    calls: list[object] = []
    result = pipeline.run_reply_pipeline(
        context=source,
        config=enabled_config(),
        repository=FakeRepository(),
        transport=lambda **kwargs: calls.append(kwargs),
        supplied_images=images,
    )
    assert result.status == "operational_failure"
    assert result.error_category == "context_validation"
    assert calls == []


def test_text_candidate_builds_one_exact_responses_request() -> None:
    """One text candidate invokes one Responses transport with frozen settings."""

    repository = FakeRepository()
    calls: list[dict[str, object]] = []

    def transport(**kwargs: object) -> dict[str, object]:
        """Capture the sole provider request."""

        calls.append(copy.deepcopy(kwargs))
        return {
            "response": response_envelope(raw_decision()),
            "latency_ms": 42,
            "request_attempt_count": 1,
        }

    result = pipeline.run_reply_pipeline(
        context=context(),
        config=enabled_config(),
        repository=repository,
        transport=transport,
    )
    assert result.status == "reply"
    assert result.model_call_count == 1
    assert len(calls) == 1
    request = calls[0]["request"]
    assert request["model"] == "gpt-5.6-sol"
    assert request["instructions"] == pipeline.SYSTEM_PROMPT
    assert request["reasoning"] == {"effort": "high"}
    assert request["temperature"] == 1
    assert request["max_output_tokens"] == 8192
    assert request["store"] is False
    assert request["prompt_cache_key"] == pipeline.PROMPT_CACHE_KEY
    assert request["prompt_cache_options"] == {
        "mode": "implicit",
        "ttl": "30m",
    }
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["name"] == (
        "single_call_reply_decision"
    )
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"] == (
        pipeline.provider_response_schema()
    )
    assert "tools" not in request
    assert isinstance(request["input"], str)
    assert result.provider_usage["cached_input_tokens"] == 80


def test_image_candidate_is_one_multimodal_request() -> None:
    """Attach validated bytes to the same and only model request."""

    calls: list[dict[str, object]] = []

    def transport(**kwargs: object) -> dict[str, object]:
        """Capture the sole multimodal request."""

        calls.append(kwargs)
        return {"response": response_envelope(raw_decision())}

    result = pipeline.run_reply_pipeline(
        context=context(),
        config=enabled_config(),
        repository=FakeRepository(),
        transport=transport,
        supplied_images=[
            {
                "identity": "media-1",
                "mime_type": "image/png",
                "data": valid_png(),
                "attachment_role": "target_contribution",
                "source_post_id": "target",
            }
        ],
    )
    assert result.status == "reply"
    assert len(calls) == 1
    content = calls[0]["request"]["input"][0]["content"]
    assert [item["type"] for item in content] == [
        "input_text",
        "input_image",
    ]
    supplied_image_context = json.loads(content[0]["text"])["supplied_images"]
    assert supplied_image_context == [
        {
            "attachment_role": "target_contribution",
            "image_index": 1,
            "source_post_id": "target",
        }
    ]
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_direct_factual_requires_one_supplied_unique_fact() -> None:
    """Enforce grounding IDs locally without a repair call."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_model_output(
            raw_decision(kind="direct_factual"), payload=payload
        )
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_model_output(
            raw_decision(kind="direct_factual", facts=["F99"]), payload=payload
        )
    assert pipeline.validate_model_output(
        raw_decision(
            kind="direct_factual",
            reply="Trusted passage 1.",
            facts=["F1"],
        ),
        payload=payload,
    )["used_fact_ids"] == ["F1"]


def test_duplicate_fact_ids_are_local_validation_not_provider_schema_failure() -> None:
    """Classify unsupported provider-side uniqueness as a local mechanical veto."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    with pytest.raises(pipeline.ReplyValidationError) as duplicate:
        pipeline.validate_model_output(
            raw_decision(
                kind="direct_factual",
                reply="Trusted passage 1.",
                facts=["F1", "F1"],
            ),
            payload=payload,
        )
    assert duplicate.value.errors == ("duplicate_used_fact_ids",)
    assert duplicate.value.category == "local_validation"

    with pytest.raises(pipeline.ReplyValidationError) as malformed:
        pipeline.validate_model_output(
            raw_decision(facts=["not-a-schema-fact-id"]),
            payload=payload,
        )
    assert "invalid_used_fact_ids" in malformed.value.errors
    assert malformed.value.category == "schema_validation"


def test_same_author_reply_remains_part_of_local_duplicate_validation() -> None:
    """Avoid payload duplication without weakening recent-prose validation."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(),
        repository=FakeRepository(),
        same_author_interactions=[
            {
                "contributor": "An earlier contribution.",
                "account_reply": "Responsibility matters more than rhetoric.",
            }
        ],
    )
    with pytest.raises(pipeline.ReplyValidationError) as raised:
        pipeline.validate_model_output(raw_decision(), payload=payload)
    assert "exact_duplicate_reply" in raised.value.errors


def test_account_quoted_subject_remains_part_of_local_duplicate_validation() -> None:
    """Exclude quote prose from history fields without weakening validation."""

    source = context(turns=1)
    source["quoted_post"] = {
        "post_id": "quoted-account-reply",
        "author_role": "account",
        "text": "Responsibility matters more than rhetoric.",
    }
    source["quoted_post_id"] = "quoted-account-reply"
    source["quoted_post_relationship"] = "target_quote"
    payload, _mapping = pipeline.build_model_payload(
        context=source,
        repository=FakeRepository(),
    )

    with pytest.raises(pipeline.ReplyValidationError) as raised:
        pipeline.validate_model_output(raw_decision(), payload=payload)
    assert "exact_duplicate_reply" in raised.value.errors


@pytest.mark.parametrize(
    "address",
    [
        "https://example.com/path",
        "www.example.com",
        "example.com",
        "name@example.com",
        "user@品牌。中国",
        "user＠品牌。中国",
        "192.0.2.1",
        "2001:db8::1",
        "xn--bcher-kva.example",
        "bücher.de",
        "例子.测试",
        "例子。测试",
        "例子。中国",
        "example。com",
        "ｅｘａｍｐｌｅ．ｃｏｍ",
        "请看 例子.测试，获取详情。",
        "пример.рф",
        "Смотрите пример.рф, пожалуйста.",
        "почта。рф/path",
        "網站。台灣",
        "مثال.إختبار",
        "مثال。موقع",
        "品牌.online",
        "café.london",
        "Visit “品牌。中国” now.",
        "Website: 品牌。中国",
        "The website is 品牌。中国.",
        "We saw “新聞。香港” yesterday.",
        "Use 新聞。香港, please.",
        "We saw 新聞。香港.",
        "The address is 新聞。香港, which works.",
        "The URL is 新聞。香港, which works.",
        "The link is 新聞。香港, which works.",
        "The hostname is 新聞。香港, which works.",
        "网址是 品牌。中国，可查看。",
        "网站是 品牌。中国 可访问。",
        "域名是 品牌。中国，欢迎访问。",
        "URL: 品牌。中国",
        "Link: 品牌。中国",
        "Go to 品牌。中国 now.",
        "新聞。香港./path",
        "新聞。香港。/path",
        "新聞。香港.?q=1",
        "新聞。香港.:443",
        "网址是品牌。中国，可查看。",
        "网站是品牌。中国，可访问。",
        "域名是品牌。中国，欢迎访问。",
        "请访问品牌。中国，获取详情。",
        "請訪問品牌。中國，了解詳情。",
        "访问品牌。中国即可。",
        "Read abc。中国 now.",
        "See abc。中国 for details.",
        "Use abc。中国, please.",
        "Find abc。中国 online.",
    ],
)
def test_real_links_domains_email_and_network_addresses_are_rejected(
    address: str,
) -> None:
    """Reject each prohibited address class mechanically."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    with pytest.raises(pipeline.ReplyValidationError) as raised:
        pipeline.validate_model_output(
            raw_decision(reply=f"Read {address} for details."), payload=payload
        )
    assert "reply_contains_link_or_address" in raised.value.errors


@pytest.mark.parametrize(
    "reply",
    [
        "这场争论的重点是责任，而不是口号。原则若不能落实为行动，就只是装饰。",
        "责任比口号重要.原则必须化为行动.",
        "責任は重要.原則は行動を導く.",
        "المبدأ الواضح خير من الشعار الغامض.",
        "責任を伴わない約束は、ただの飾りです。",
        "Ответственность важнее лозунгов. Принцип должен вести к действию.",
    ],
)
def test_foreign_language_prose_is_not_rejected(reply: str) -> None:
    """Accept natural non-Latin prose, including CJK full-stop punctuation."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    assert_prose_has_no_mechanical_errors(reply, payload)


@pytest.mark.parametrize(
    "reply",
    [
        "这是第一点。中国，当然也不例外。",
        "原则很清楚。公司，应承担责任。",
        "这是版本2。公司，应承担责任。",
        "这是A。中国，也不例外。",
        "我认为 这是第一点。中国 也不例外。",
        "请访问这一原则。公司，应承担责任。",
        "访问权很重要。公司，应当尊重它。",
        "访问自由很重要。中国，也不例外。",
        "网站监管很重要。中国，也有规则。",
        "域名制度很重要。公司，应承担责任。",
        "访问公共服务很重要。政府，应保障公平。",
        "See principle。中国，也不例外。",
        "网址是 公共资源。中国，应加强监管。",
    ],
)
def test_cjk_sentence_stops_before_idn_words_remain_prose(reply: str) -> None:
    """Do not reinterpret ordinary CJK clauses as internationalised hosts."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    assert pipeline.contains_link_or_address(reply) is False
    assert_prose_has_no_mechanical_errors(reply, payload)


@pytest.mark.parametrize(
    "address",
    [
        "網站。台灣",
        "网址。中国",
        "郵件。公司",
        "책。한국",
        "“新聞。香港”",
        "(新聞。香港)",
        "新聞。香港,",
        "新聞。香港，",
        "新聞。香港/path",
        "新聞。香港:443",
        "新聞.香港",
        "“新聞.香港”",
        "新聞.香港/path",
    ],
)
def test_bare_ambiguous_cjk_hostname_form_is_rejected(address: str) -> None:
    """Bias a whole two-label U+3002 token toward explicit address safety."""

    assert pipeline.contains_link_or_address(address) is True


@pytest.mark.parametrize(
    "reply",
    [
        "A practical example, e.g. thrift, remains useful.",
        "「責任が大切です。原則は行動を導きます。」",
    ],
)
def test_reply_accepts_abbreviations_and_unicode_closers(
    reply: str,
) -> None:
    """Keep ordinary punctuation valid without imposing a sentence count."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    assert_prose_has_no_mechanical_errors(reply, payload)


@pytest.mark.parametrize(
    "reply",
    [
        "Mrs. Thatcher was right. We agree.",
        "Mr. Smith agrees. We proceed.",
        "The U.K. Government should act. Responsibility matters.",
        "At 3 p.m. London responded. We noticed.",
        "For example, e.g. Thatcher’s reforms mattered. We agree.",
        "That is, i.e. Thatcher’s point stands. We agree.",
        "The U.K. Labour Party should listen. Responsibility matters.",
        "The U.S. Federal Reserve acted. We agree.",
        "The U.S. Senate acted. We agree.",
        "The E.U. Council acted. We agree.",
        "The U.N. Security Council acted. We agree.",
        "The meeting starts at 3 p.m. London time. We agree.",
        "The deadline is 5 p.m. BST. We agree.",
        "The vote is at 7:30 a.m. Westminster time. We agree.",
        "Meet at 3 p.m. London time. We agree.",
        "The U.S. “Inflation Reduction Act” passed. We agree.",
        "The U.K. (London especially) needs reform. We agree.",
        "Dr. “Thatcher” spoke. We agreed.",
        "U.K.–based policy matters. We agree.",
        "The value is ２．５. We agree.",
        "J. Smith spoke. We agreed.",
        "A. Smith spoke. We agreed.",
        "At 3 p.m. (London time) we left. We agreed.",
        "The U.K. «Government policy» matters. We agree.",
        "Dr. «Thatcher» spoke. We agreed.",
        "The Govt. Department acted. We agreed.",
    ],
)
def test_reply_accepts_capitalised_abbreviation_continuations(
    reply: str,
) -> None:
    """Recognise common title, entity and clause-initial time continuations."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    assert_prose_has_no_mechanical_errors(reply, payload)


@pytest.mark.parametrize(
    "reply",
    [
        "We met at 3 p.m. It mattered. We left.",
        "He lives in the U.K. It matters. We agree.",
        "A. It works. It is fine.",
        "Prof. She agrees. We proceed.",
        "I live in the U.K. “It is cold.” It is wet.",
        "I live in the U.K. (It is cold.) It is wet.",
        "I live in the U.K. 2025 was cold. It changed.",
        "I live in the U.K.“It is cold.” It is wet.",
        "At 3 p.m. It mattered. We left.",
        "By 5 p.m. We stopped. They left.",
        "Around 7 a.m. However, rain fell. We stayed.",
        "We met at 3 p.m. London called. We left.",
        "It ended at 5 p.m. Parliament adjourned. We left.",
        "A. “Boris agreed.” We left.",
        "Prof. “Boris agreed.” We left.",
        "For example, e.g. “Boris agreed.” We left.",
        "The U.K. It matters. We agree.",
        "The U.S. This matters. We agree.",
        "That is, i.e. This matters. We agree.",
        "Option A. Responsibility matters. We agree.",
        "Choose A. Responsibility matters. We agree.",
        "The answer is A. Responsibility matters. We agree.",
        "The answer is no. 10 people agree. We proceed.",
        "I said no. 10 colleagues agreed. We left.",
        "Πρώτη ερώτηση; Δεύτερη; Τρίτη;",
        "One‽ Two‽ Three‽",
        "First․ Second․ Third․",
        "First︙ Second︙ Third︙",
        "ראשון׃ שני׃ שלישי׃",
        "དང་པོ། གཉིས་པ། གསུམ་པ།",
    ],
)
def test_reply_accepts_unicode_or_abbreviated_three_sentences(
    reply: str,
) -> None:
    """Sentence count does not reject otherwise valid short prose."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    assert_prose_has_no_mechanical_errors(reply, payload)


@pytest.mark.parametrize("separator", ["\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"])
def test_unicode_line_separators_are_rejected(separator: str) -> None:
    """Treat Unicode line and paragraph separators as prohibited line breaks."""

    payload, _mapping = pipeline.build_model_payload(
        context=context(), repository=FakeRepository()
    )
    with pytest.raises(pipeline.ReplyValidationError) as raised:
        pipeline.validate_model_output(
            raw_decision(reply=f"First{separator}Second"), payload=payload
        )
    assert "reply_contains_line_break" in raised.value.errors


def test_no_reply_is_editorial_but_invalid_output_is_operational() -> None:
    """Keep deliberate silence separate from provider/local failure."""

    outputs = iter(
        [
            raw_decision(
                decision="no_reply",
                kind="no_reply",
                reply="",
                reason="completed_exchange",
            ),
            '{"decision":"reply"}',
        ]
    )

    def transport(**_kwargs: object) -> dict[str, object]:
        """Return the next deterministic response."""

        return {"response": response_envelope(next(outputs))}

    first = pipeline.run_reply_pipeline(
        context=context(),
        config=enabled_config(),
        repository=FakeRepository(),
        transport=transport,
    )
    second = pipeline.run_reply_pipeline(
        context=context(),
        config=enabled_config(),
        repository=FakeRepository(),
        transport=transport,
    )
    assert (first.status, first.decision, first.local_validation_status) == (
        "no_reply",
        "no_reply",
        "passed",
    )
    assert second.status == "operational_failure"
    assert second.decision is None
    assert second.error_category == "schema_validation"
    assert first.validation_error_codes == ()
    assert "response_fields_mismatch" in second.validation_error_codes


@pytest.mark.parametrize(
    ("output", "category", "codes"),
    [
        *[
            (
                raw_decision(decision=decision),
                "schema_validation",
                ("invalid_decision",),
            )
            for decision in ([], ["reply"], {}, {"reply": True}, None, "invalid")
        ],
        (
            raw_decision(reply="PRIVATE candidate @name\n#topic"),
            "local_validation",
            (
                "control_or_format_character", "reply_contains_hashtag",
                "reply_contains_line_break", "reply_contains_link_or_address",
                "reply_contains_mention",
            ),
        ),
        (
            raw_decision(
                kind="direct_factual", reply="PRIVATE candidate @name",
                facts=["not-a-schema-fact-id"],
            ),
            "schema_validation",
            (
                "direct_factual_missing_fact_id", "invalid_used_fact_ids",
                "reply_contains_link_or_address", "reply_contains_mention",
            ),
        ),
        (
            raw_decision(
                kind="direct_factual", reply="Trusted passage 1.",
                facts=["F1", "F1"],
            ),
            "local_validation",
            ("duplicate_used_fact_ids",),
        ),
        (
            raw_decision(reply="First point. Second point. " * 15),
            "local_validation",
            ("invalid_reply_length_or_whitespace",),
        ),
        (
            raw_decision(kind="direct_factual", reply="I am an automated account."),
            "local_validation",
            ("direct_factual_missing_fact_id",),
        ),
    ],
)
def test_validation_rules_and_rejected_reply_reach_telemetry_without_reasoning(
    output: str, category: str, codes: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain rejected prose and rules without making a reusable posting draft."""

    response = response_envelope(output)
    response["output"][0]["summary"] = [{"text": "PRIVATE reasoning"}]
    transport = Mock(return_value={"response": response, "latency_ms": 37})
    create_draft = Mock(side_effect=AssertionError("rejected text is not a draft"))
    monkeypatch.setattr(pipeline, "create_durable_draft", create_draft)
    result = pipeline.run_reply_pipeline(
        context=context(), config=enabled_config(), repository=FakeRepository(),
        transport=transport,
    )

    assert result.status == "operational_failure"
    assert result.reason == "model_response_validation_failed"
    assert result.error_category == category
    assert result.validation_error_codes == codes
    assert result.local_validation_status == "failed"
    assert result.model_call_count == result.provider_request_attempt_count == 1
    assert result.reply is None and result.decision is None
    assert result.provider_response_id == "resp_test"
    assert result.provider_latency_ms == 37
    assert result.provider_usage["total_tokens"] == 145
    telemetry = pipeline.decision_telemetry(result)
    assert telemetry["validation_error_codes"] == list(codes)
    assert telemetry["error_category"] == category
    assert telemetry["failure_reason"] == "model_response_validation_failed"
    proposed_reply = json.loads(output)["reply"]
    assert result.rejected_reply_text == proposed_reply
    assert telemetry["rejected_reply_text"] == proposed_reply
    assert telemetry["rejected_reply_text_status"] == "available"
    assert telemetry["rejected_reply_text_character_count"] == len(proposed_reply)
    assert "PRIVATE reasoning" not in json.dumps(telemetry)
    assert "Trusted passage 2." not in json.dumps(telemetry)
    transport.assert_called_once()
    create_draft.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [
        "PRIVATE raw response, not JSON",
        '{"reply":"PRIVATE first","reply":"PRIVATE second"}',
        '{"reply":"PRIVATE reply","used_fact_ids":NaN}',
        '{"decision":"reply"}',
        '["PRIVATE reply"]',
        '{"reply":42}',
        raw_decision(reply="\ud800"),
        json.dumps({"reply": "PRIVATE reply", "padding": "x" * pipeline.MAX_RAW_OUTPUT_CHARACTERS}),
    ],
)
def test_rejected_reply_is_unavailable_without_a_safe_strict_json_string(output: str) -> None:
    """Keep absent, malformed, oversized and unencodable replies out of logs."""
    result = pipeline.run_reply_pipeline(
        context=context(), config=enabled_config(), repository=FakeRepository(),
        transport=lambda **_kwargs: {"response": response_envelope(output)},
    )
    assert result.status == "operational_failure"
    assert result.reply is None and result.decision is None
    telemetry = pipeline.decision_telemetry(result)
    assert telemetry["rejected_reply_text"] is None
    assert telemetry["rejected_reply_text_status"] == "unavailable"
    assert telemetry["rejected_reply_text_character_count"] is None
    assert "PRIVATE" not in json.dumps(telemetry)


def test_rejected_reply_text_is_exact_and_bounded_without_envelope_fields() -> None:
    """Preserve whitespace and Unicode while truncating only the reply field."""
    reply = "  Café\n\t責任🙂 " + "x" * MAX_REJECTED_REPLY_TEXT_CHARACTERS + "  "
    output = json.loads(raw_decision(reply=reply))
    output["reasoning"] = "PRIVATE extra output field"
    result = pipeline.run_reply_pipeline(
        context=context(), config=enabled_config(), repository=FakeRepository(),
        transport=lambda **_kwargs: {"response": response_envelope(json.dumps(output))},
    )
    telemetry = pipeline.decision_telemetry(result)
    assert result.reply is None
    assert result.rejected_reply_text == reply[:MAX_REJECTED_REPLY_TEXT_CHARACTERS]
    assert telemetry["rejected_reply_text"] == result.rejected_reply_text
    assert telemetry["rejected_reply_text_status"] == "truncated"
    assert telemetry["rejected_reply_text_character_count"] == len(reply)
    assert "PRIVATE extra output field" not in json.dumps(telemetry)


@pytest.mark.parametrize("value", [None, 42, True, {}, ["reply"], "\ud800"])
def test_rejected_reply_text_fields_rejects_nontext_and_invalid_unicode(value: object) -> None:
    """Treat only valid Unicode strings as available diagnostic text."""
    assert rejected_reply_text_fields(value, character_count=5000) == {
        "rejected_reply_text": None,
        "rejected_reply_text_status": "unavailable",
        "rejected_reply_text_character_count": None,
    }


@pytest.mark.parametrize("count", [None, True, -1, 1, "9000", [], {}])
def test_rejected_reply_text_fields_ignores_malformed_original_counts(count: object) -> None:
    """Derive the original length when a supplied count cannot describe it."""
    assert rejected_reply_text_fields("  é\n", character_count=count) == {
        "rejected_reply_text": "  é\n",
        "rejected_reply_text_status": "available",
        "rejected_reply_text_character_count": 4,
    }


def test_rejected_reply_text_fields_preserves_empty_and_previously_truncated_text() -> None:
    """Distinguish empty available text from absent text and retain lost length."""
    assert rejected_reply_text_fields("") == {
        "rejected_reply_text": "",
        "rejected_reply_text_status": "available",
        "rejected_reply_text_character_count": 0,
    }
    assert rejected_reply_text_fields("  é\n", character_count=5000) == {
        "rejected_reply_text": "  é\n",
        "rejected_reply_text_status": "truncated",
        "rejected_reply_text_character_count": 5000,
    }


@pytest.mark.parametrize(
    ("status", "category"),
    [("reply", None), ("no_reply", None), ("operational_failure", "provider_schema"),
     ("operational_failure", "context_validation"), ("operational_failure", "draft_validation"),
     ("operational_failure", "local_validation"), ("operational_failure", "schema_validation")],
)
def test_other_decisions_do_not_emit_rejected_reply_diagnostics(status: str, category: str | None) -> None:
    """Restrict diagnostic prose to rejected schema or mechanical decisions."""
    result = pipeline.PipelineResult(
        status=status, reason="fixture", error_category=category,
        rejected_reply_text="PRIVATE candidate", rejected_reply_text_character_count=17,
    )
    telemetry = pipeline.decision_telemetry(result)
    assert not any(key.startswith("rejected_reply_text") for key in telemetry)
    assert "PRIVATE" not in json.dumps(telemetry)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PRIVATE diagnostic", []),
        ({"reply_contains_mention": "PRIVATE diagnostic"}, []),
        (None, []),
        (
            ["reply_contains_mention", "PRIVATE diagnostic", None, True,
             "reply_contains_mention", {"secret": "PRIVATE diagnostic"}],
            ["reply_contains_mention"],
        ),
        (
            ["reply_contains_mention"] * MAX_VALIDATION_ERROR_CODES
            + ["reply_contains_hashtag"],
            ["reply_contains_mention"],
        ),
    ],
)
def test_decision_telemetry_bounds_and_redacts_untrusted_validation_codes(
    value: object, expected: list[str],
) -> None:
    """Never copy arbitrary exception prose into the diagnostic field."""

    result = pipeline.PipelineResult(
        status="operational_failure", reason="model_response_validation_failed",
        error_category="local_validation", validation_error_codes=value,
    )
    telemetry = pipeline.decision_telemetry(result)
    assert telemetry["validation_error_codes"] == expected
    assert "PRIVATE" not in json.dumps(telemetry)


def test_nonvalidation_defect_is_not_converted_into_validation_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Let implementation defects escape the deliberately narrow validator catch."""

    failure = RuntimeError("validator implementation defect")

    def broken_validator(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise failure

    monkeypatch.setattr(pipeline, "validate_model_output", broken_validator)
    with pytest.raises(RuntimeError) as raised:
        pipeline.run_reply_pipeline(
            context=context(), config=enabled_config(), repository=FakeRepository(),
            transport=lambda **_kwargs: {"response": response_envelope(raw_decision())},
        )
    assert raised.value is failure


def test_transport_control_interrupt_is_not_reclassified_as_provider_failure() -> None:
    """Let the bot's live pause interrupt cross the module boundary unchanged."""

    class ControlInterrupt(RuntimeError):
        propagate_from_single_call_pipeline = True

    def transport(**_kwargs: object) -> dict[str, object]:
        """Raise the synthetic global control interruption."""

        raise ControlInterrupt("paused")

    with pytest.raises(ControlInterrupt, match="paused"):
        pipeline.run_reply_pipeline(
            context=context(),
            config=enabled_config(),
            repository=FakeRepository(),
            transport=transport,
        )


def test_validated_draft_recovers_without_transport() -> None:
    """A hash-bound current draft remains reusable with zero new provider calls."""

    repository = FakeRepository()
    calls = 0

    def transport(**_kwargs: object) -> dict[str, object]:
        """Count the sole creation-time provider call."""

        nonlocal calls
        calls += 1
        return {"response": response_envelope(raw_decision())}

    source_context = context()
    result = pipeline.run_reply_pipeline(
        context=source_context,
        config=enabled_config(),
        repository=repository,
        transport=transport,
    )
    assert result.reply is not None
    draft = pipeline.validate_persisted_draft(
        result.reply.draft_record,
        context=source_context,
        repository=repository,
    )
    assert calls == 1
    assert draft["validated_draft_hash"] == result.reply.draft_record[
        "validated_draft_hash"
    ]
    assert draft["model_call_count"] == 1
    with pytest.raises(
        pipeline.ReplyValidationError,
        match="exact_duplicate_reply",
    ):
        pipeline.validate_persisted_draft(
            result.reply.draft_record,
            context=source_context,
            repository=repository,
            recent_account_replies=[
                {"post_id": "later", "text": str(result.reply)}
            ],
        )

    mismatched_context = copy.deepcopy(source_context)
    mismatched_context["target_author_id"] = "201"
    with pytest.raises(ValueError, match="context mismatch"):
        pipeline.validate_persisted_draft(
            result.reply.draft_record,
            context=mismatched_context,
            repository=repository,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing_id", "wrong_role", "incomplete_message"],
)
def test_provider_envelope_requires_bound_completed_assistant_message(
    mutation: str,
) -> None:
    """Reject structurally invalid Responses envelopes before output parsing."""

    envelope = response_envelope(raw_decision())
    message = envelope["output"][1]
    if mutation == "missing_id":
        envelope.pop("id")
    elif mutation == "wrong_role":
        message["role"] = "user"
    else:
        message["status"] = "in_progress"

    with pytest.raises(pipeline.ModelResponseError):
        pipeline.parse_openai_response(envelope)


def test_disabled_pipeline_stops_before_payload_and_transport() -> None:
    """Disabled mode performs neither fact retrieval nor provider preparation."""

    called = False

    def transport(**_kwargs: object) -> dict[str, object]:
        """Fail if disabled orchestration crosses the transport boundary."""

        nonlocal called
        called = True
        raise AssertionError("transport called")

    result = pipeline.run_reply_pipeline(
        context={},
        config=pipeline.default_config(),
        repository=object(),
        transport=transport,
    )
    assert result.status == "disabled"
    assert result.model_call_count == 0
    assert called is False


def test_production_import_closure_excludes_retired_conversational_modules() -> None:
    """Production-local imports cannot reach either retired strategy module."""

    pending = ["mrsMThatcher2"]
    visited: set[str] = set()
    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        source_path = PROJECT_ROOT / f"{module_name.replace('.', '/')}.py"
        if not source_path.exists():
            package_init = PROJECT_ROOT / module_name.replace(".", "/") / "__init__.py"
            if not package_init.exists():
                continue
            source_path = package_init
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                candidates.append(node.module)
            for candidate in candidates:
                root = candidate.split(".", 1)[0]
                if (PROJECT_ROOT / f"{root}.py").exists() or (
                    PROJECT_ROOT / root / "__init__.py"
                ).exists():
                    pending.append(root)

    assert "reply_strategy" not in visited
    assert "tested_reply_pipeline" not in visited


def assert_prose_has_no_mechanical_errors(reply: str, payload: dict) -> None:
    """Test punctuation mechanics only, without claiming factual classification."""
    pipeline.validate_model_output(raw_decision(reply=reply), payload=payload)
