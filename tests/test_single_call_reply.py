from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import single_call_reply as pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FakePassage:
    """Provide the subset of an evidence passage used by the pipeline."""

    evidence_id: str
    passage: str

    def prompt_record(self) -> dict[str, str]:
        """Return one complete local source record."""

        return {
            "evidence_id": self.evidence_id,
            "quote_id": "quote-1",
            "field": "historical_context",
            "passage": self.passage,
            "source_title": "Official archive",
            "source_url": "https://archive.example/source",
            "stable_locator": f"record:{self.evidence_id}",
            "verification_status": "exact",
            "research_confidence": "high",
            "actor": "",
            "action_or_relationship": "",
            "direction_or_polarity": "",
            "date_or_period": "",
            "quantity": "",
        }


class FakeRepository:
    """Expose deterministic local evidence without filesystem fixtures."""

    def __init__(self, count: int = 3) -> None:
        """Create a requested number of distinct passages."""

        values = [
            FakePassage(f"evidence-{index}", f"Trusted passage {index}.")
            for index in range(1, count + 1)
        ]
        self.passages = {value.evidence_id: value for value in values}
        self.last_limits: tuple[int, int] | None = None
        self.last_query: str | None = None

    def resolve_context_quotation(self, _context: dict) -> None:
        """Report no preferred quotation for the synthetic context."""

        return None

    def candidate_passages(
        self,
        query: str,
        *,
        maximum_packets: int,
        maximum_passages: int,
        preferred_quote_id: str | None,
    ) -> list[FakePassage]:
        """Return bounded passages and retain the requested limits."""

        assert preferred_quote_id is None
        self.last_query = query
        self.last_limits = (maximum_packets, maximum_passages)
        return list(self.passages.values())[:maximum_passages]


def context(*, turns: int = 2) -> dict[str, object]:
    """Return one valid canonical-context input."""

    visible = [
        {
            "post_id": f"post-{index}",
            "author_role": "account" if index % 2 else "other_user",
            "text": f"Earlier contribution {index}.",
        }
        for index in range(1, turns)
    ]
    visible.append(
        {
            "post_id": "target",
            "author_role": "user",
            "text": "What principle matters here?",
        }
    )
    return {
        "target_id": "target",
        "thread_id": "post-1" if turns > 1 else "target",
        "root_post_id": "post-1" if turns > 1 else "target",
        "parent_post_id": f"post-{turns - 1}" if turns > 1 else None,
        "lane": "mention",
        "incoming_contribution": "What principle matters here?",
        "quoted_post": None,
        "parent_thread": copy.deepcopy(visible[:-1]),
        "visible_conversation": visible,
        "clarification_request": None,
        "current_date": "2026-09-04",
    }


def raw_decision(
    *,
    decision: str = "reply",
    kind: str = "principle",
    reply: str = "Responsibility matters more than rhetoric.",
    facts: list[str] | None = None,
    reason: str = "useful_reply",
) -> str:
    """Return one compact model output string."""

    return json.dumps(
        {
            "decision": decision,
            "reply_kind": kind,
            "reply": reply,
            "used_fact_ids": facts or [],
            "reason_code": reason,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def response_envelope(text: str) -> dict[str, object]:
    """Wrap output text in the proven Responses API shape."""

    return {
        "id": "resp_test",
        "status": "completed",
        "model": pipeline.MODEL,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 80},
            "output_tokens": 25,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 145,
        },
    }


def enabled_config() -> dict[str, object]:
    """Return the exact enabled production configuration."""

    value = pipeline.default_config()
    value["enabled"] = True
    return value


def test_frozen_prompt_and_schema_hashes() -> None:
    """Pin the exact reviewed prompt bytes and local response contract."""

    assert pipeline.PROMPT_SHA256 == (
        "7bfa91fb2d9b1175560abb33e43f2ced6910d8e63cadd1f8f04935b6dc2f2560"
    )
    assert pipeline.RESPONSE_SCHEMA_SHA256 == (
        "3b1e23015cebe3b75eacde04ebfd4344fa25117f047cdcf83241b0ce709872ce"
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
    assert len(payload["trusted_facts"]) == 2


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
                "data": b"\x89PNG\r\n\x1a\nvalidated-test-bytes",
            }
        ],
    )
    assert result.status == "reply"
    assert len(calls) == 1
    content = calls[0]["request"]["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_text", "input_image"]
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
        raw_decision(kind="direct_factual", facts=["F1"]), payload=payload
    )["used_fact_ids"] == ["F1"]


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


@pytest.mark.parametrize(
    "address",
    [
        "https://example.com/path",
        "www.example.com",
        "example.com",
        "name@example.com",
        "192.0.2.1",
        "2001:db8::1",
        "xn--bcher-kva.example",
        "例子.测试",
        "пример.рф",
        "مثال.إختبار",
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
    assert pipeline.validate_model_output(
        raw_decision(reply=reply), payload=payload
    )["reply"] == reply


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
        recent_account_replies=[
            {"post_id": "later", "text": str(result.reply)}
        ],
    )
    assert calls == 1
    assert draft["validated_draft_hash"] == result.reply.draft_record[
        "validated_draft_hash"
    ]
    assert draft["model_call_count"] == 1


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
