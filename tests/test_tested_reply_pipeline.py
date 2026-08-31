from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

import tested_reply_pipeline as pipeline


@dataclass(frozen=True)
class Passage:
    evidence_id: str = "fact-1"

    def prompt_record(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "passage": (
                "Consumers and businesses create demand through spending and investment."
            ),
            "verification_status": "verified",
        }


class Repository:
    def __init__(self, facts: bool = False):
        self.facts = facts

    def resolve_context_quotation(self, _context):
        return None

    def candidate_passages(self, _text, **_kwargs):
        return [Passage()] if self.facts else []

    def detected_authorised_quote_ids(self, _reply):
        return set()

    def exact_quote_occurs_in_reply(self, _reply, _wording):
        return False

    def exact_quote_is_authorised(self, _wording):
        return False

    def detected_authorised_quote_ids_outside_exact(self, _reply, _wording):
        return set()


def context(text: str) -> dict:
    return {
        "target_id": "100",
        "thread_id": "90",
        "lane": "mention",
        "incoming_contribution": text,
        "quoted_post": None,
        "parent_thread": [
            {"post_id": "90", "author_role": "account", "text": "Freedom requires responsibility."}
        ],
        "clarification_request": None,
        "current_date": "2026-08-16",
    }


def quote_tweet_context(text: str) -> dict:
    value = context(text)
    value["lane"] = "quote_tweet"
    value["quoted_post"] = {
        "post_id": "80",
        "author_role": "account",
        "text": "Freedom requires responsibility.",
    }
    return value


class Transport:
    def __init__(self, *, gate: str = "reply", writer: str = "Thank you — that is kind of you.", review: str = "require_claim_free_reply"):
        self.gate = gate
        self.writer = writer
        self.review = review
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "candidate_backed_engagement":
            return {"decision": self.gate, "reply": "PRIVATE GATE CANDIDATE" if self.gate == "reply" else ""}
        if stage.startswith("reply_necessity_") or stage.startswith("allegation_review_"):
            return {"outcome": self.review}
        if stage == "focused_group_review":
            return {"outcome": "allow_reply"}
        if stage.startswith("authentication_review_"):
            return {"outcome": "require_supported_factual_reply"}
        if stage in {"narrow_claim_audit", "cleanup_claim_audit", "diversity_claim_audit"}:
            return {"outcome": "pass"}
        if stage in {"writer_v3_initial", "bounded_claim_cleanup", "exact_duplicate_repair"}:
            reply = "A fresh formulation keeps the point clear." if stage == "exact_duplicate_repair" else self.writer
            return {"status": "reply", "reply": reply}
        raise AssertionError(stage)


def enabled_config() -> dict:
    value = pipeline.default_config()
    value["enabled"] = True
    return value


def test_default_config_and_validator_freeze_three_sentence_maximum() -> None:
    config = pipeline.default_config()

    assert config["maximum_reply_sentences"] == 3
    assert pipeline.validate_strategy_config(config) == []


@pytest.mark.parametrize("maximum", [2, 4])
def test_config_validator_rejects_other_sentence_maxima(maximum: int) -> None:
    config = pipeline.default_config()
    config["maximum_reply_sentences"] = maximum

    assert pipeline.validate_strategy_config(config) == [
        "tested_reply_pipeline.maximum_reply_sentences must remain 3"
    ]


def visual_description(image_count: int = 1) -> dict:
    return {
        "images": [
            {
                "index": index,
                "literal_description": f"Photograph {index} shows a public sign beside a road.",
                "visible_text": [f"LEGIBLE WORDS {index}"],
                "salient_elements": [f"road sign {index}", "open sky"],
                "apparent_message": "The photograph appears to invite a comparison.",
                "uncertainties": ["The location is not visually identifiable."],
            }
            for index in range(1, image_count + 1)
        ],
        "combined_context": "The photographs show related roadside scenes.",
        "relationship_to_contribution": (
            "They appear to illustrate the contributor's phrase without establishing its truth."
        ),
    }


def analysed_media_context(image_count: int = 1) -> dict:
    return {
        "status": "analysed",
        "trust": pipeline.VISUAL_DESCRIPTION_TRUST,
        "image_count": image_count,
        "analysis": visual_description(image_count),
    }


def supplied_media_context(*urls: str) -> dict:
    return {
        "lane": "mention",
        "target_id": "100",
        "mode": "multimodal",
        "status": "supplied",
        "photos_expected": len(urls),
        "photos": [
            {"media_key": f"3_{index}", "url": url}
            for index, url in enumerate(urls, 1)
        ],
    }


@pytest.mark.parametrize(
    "urls",
    [
        ("https://pbs.twimg.com/media/one.jpg",),
        (
            "https://pbs.twimg.com/media/one.jpg",
            "https://pbs.twimg.com/media/two.jpg",
        ),
    ],
    ids=["one_native_photo", "two_native_photos"],
)
def test_visual_description_uses_one_genuine_multimodal_xai_request(
    monkeypatch: pytest.MonkeyPatch,
    urls: tuple[str, ...],
) -> None:
    import mrsMThatcher2 as bot

    analysis = visual_description(len(urls))
    requests_seen: list[dict] = []
    events: list[tuple[str, dict]] = []

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {
                "choices": [
                    {"message": {"content": json.dumps(analysis)}}
                ],
                "usage": {"total_tokens": 12},
            }

    def post(url: str, **kwargs) -> Response:
        requests_seen.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot, "require_remote_operation_unpaused", lambda _operation: None
    )
    monkeypatch.setattr(bot, "XAI_BASE", "https://xai.invalid/v1")
    monkeypatch.setattr(bot, "XAI_API_KEY", "test-key")
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    result = bot.describe_reply_media_for_tested_pipeline(
        context("Just like Australia"),
        supplied_media_context(*urls),
        config=enabled_config(),
    )

    assert result == analysed_media_context(len(urls))
    assert len(requests_seen) == 1
    request_call = requests_seen[0]
    assert request_call["url"] == "https://xai.invalid/v1/chat/completions"
    assert request_call["timeout"] == enabled_config()["timeout_seconds"]
    request = request_call["json"]
    assert request["model"] == enabled_config()["xai_model"]
    assert request["reasoning_effort"] == enabled_config()["xai_reasoning_effort"]
    assert request["reasoning_effort"] == "low"
    assert request["max_tokens"] == pipeline.VISUAL_DESCRIPTION_MAX_OUTPUT_TOKENS
    assert "store" not in request
    assert request["response_format"]["json_schema"]["schema"] == (
        pipeline.VISUAL_DESCRIPTION_SCHEMA
    )
    content = request["messages"][1]["content"]
    assert isinstance(content, list)
    assert [part["type"] for part in content] == [
        "text",
        *("image_url" for _url in urls),
    ]
    assert json.loads(content[0]["text"]) == {
        "incoming_contribution": "Just like Australia",
        "parent_thread_text": ["Freedom requires responsibility."],
        "quoted_post_text": "",
    }
    assert [part["image_url"]["url"] for part in content[1:]] == list(urls)
    assert "detail" not in json.dumps(request, sort_keys=True)
    assert "media_key" not in json.dumps(request, sort_keys=True)

    assert [name for name, _fields in events] == ["reply_visual_description"]
    event = events[0][1]
    canonical = json.dumps(
        analysis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert event == {
        "lane": "mention",
        "target_id": "100",
        "supplied_image_count": len(urls),
        "status": "analysed",
        "analysis_schema_version": pipeline.VISUAL_DESCRIPTION_SCHEMA_VERSION,
        "description_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "visual_analysis_call_count": 1,
        "analysis": analysis,
    }
    logged = json.dumps(events, sort_keys=True)
    assert all(url not in logged for url in urls)
    for forbidden in (
        "pbs.twimg.com",
        "media_key",
        "image_url",
        "request_payload",
        "choices",
        "usage",
        "test-key",
    ):
        assert forbidden not in logged


def test_ordinary_xai_structured_reply_call_omits_optional_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    captured: dict = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {
                "choices": [{"message": {"content": {"status": "ok"}}}],
            }

    def post(url: str, **kwargs) -> Response:
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot, "require_remote_operation_unpaused", lambda _operation: None
    )
    monkeypatch.setattr(bot, "XAI_BASE", "https://xai.invalid/v1")
    monkeypatch.setattr(bot, "XAI_API_KEY", "test-key")

    result = bot.xai_structured_reply_call(
        stage="ordinary_existing_caller",
        model="unit-model",
        system_prompt="system policy",
        user_prompt="visible user material",
        response_schema={"type": "object", "additionalProperties": False},
        timeout_seconds=5,
        max_output_tokens=100,
        media_context=None,
    )

    assert result == {"status": "ok"}
    request = captured["json"]
    assert "reasoning_effort" not in request
    assert "store" not in request
    assert set(request) == {
        "model",
        "messages",
        "temperature",
        "max_tokens",
        "response_format",
    }


def test_text_only_tested_pipeline_makes_no_visual_call_and_keeps_stage_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    transport = Transport()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(bot, "tested_pipeline_structured_call", transport)
    monkeypatch.setattr(
        bot,
        "xai_structured_reply_call",
        lambda **_kwargs: pytest.fail("text-only candidate must not start visual analysis"),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )
    no_media = {
        "lane": "mention",
        "target_id": "100",
        "mode": "none",
        "status": "none",
        "photos_expected": 0,
        "photos": [],
    }

    reply = bot.generate_ai_first_reply(
        context("Thank you!"),
        no_media,
        recent_replies=[],
    )

    assert str(reply) == "Thank you — that is kind of you."
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "writer_v3_initial",
    ]
    assert transport.calls[0]["payload"] == {
        "context": context("Thank you!"),
        "recent_replies": [],
        "trusted_facts": [],
        "media_context": [],
    }
    assert transport.calls[1]["payload"]["media_context"] == []
    assert not any(name == "reply_visual_description" for name, _fields in events)
    assert reply.pipeline_metadata["model_call_count"] == 2


def test_analysed_visual_context_reaches_every_invoked_pipeline_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    raw_url = "https://pbs.twimg.com/media/original-photo.jpg"
    raw_media = supplied_media_context(raw_url)
    canonical_media = analysed_media_context()
    visual_calls: list[tuple[dict, dict | None, dict]] = []
    model_calls: list[dict] = []

    def describe(value: dict, media: dict | None, *, config: dict) -> dict:
        visual_calls.append((value, media, config))
        return copy.deepcopy(canonical_media)

    def transport(**kwargs):
        model_calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "candidate_backed_engagement":
            return {"decision": "no_reply", "reply": ""}
        if stage.startswith("reply_necessity_"):
            return {"outcome": "require_claim_free_reply"}
        if stage == "writer_v3_initial":
            return {"status": "reply", "reply": "It was introduced in 1979."}
        if stage == "narrow_claim_audit":
            return {"outcome": "rewrite_claim_free"}
        if stage == "bounded_claim_cleanup":
            return {
                "status": "reply",
                "reply": "The principle is responsibility rather than privilege.",
            }
        raise AssertionError(stage)

    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(bot, "describe_reply_media_for_tested_pipeline", describe)
    monkeypatch.setattr(bot, "tested_pipeline_structured_call", transport)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    reply = bot.generate_ai_first_reply(
        context("Just like Australia"),
        raw_media,
        recent_replies=[],
    )

    assert str(reply) == "The principle is responsibility rather than privilege."
    assert len(visual_calls) == 1
    assert visual_calls[0][1] == raw_media
    assert [call["stage"] for call in model_calls] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
        "reply_necessity_2",
        "writer_v3_initial",
        "narrow_claim_audit",
        "bounded_claim_cleanup",
    ]
    for call in model_calls:
        assert call["payload"]["media_context"] == canonical_media
        if "trusted_facts" in call["payload"]:
            assert call["payload"]["trusted_facts"] == []
    downstream = json.dumps(
        [call["payload"] for call in model_calls],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert raw_url not in downstream
    for forbidden in (
        "pbs.twimg.com",
        "media_key",
        "preview_image_url",
        '"image_url"',
        "3_1",
    ):
        assert forbidden not in downstream
    assert canonical_media["trust"] == (
        "untrusted_user_supplied_visual_context"
    )
    assert reply.pipeline_metadata["model_call_count"] == len(model_calls)


def test_direct_answer_repair_reuses_the_same_analysed_visual_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    canonical_media = analysed_media_context()
    visual_calls: list[str] = []
    model_calls: list[dict] = []

    def describe(*_args, **_kwargs) -> dict:
        visual_calls.append("called")
        return copy.deepcopy(canonical_media)

    def transport(**kwargs):
        model_calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "candidate_backed_engagement":
            return {"decision": "reply", "reply": "PRIVATE GATE CANDIDATE"}
        if stage == "writer_v3_initial":
            return {
                "status": "reply",
                "reply": "Demand must be matched by confidence and enterprise.",
            }
        if stage == "direct_answer_repair":
            return {
                "status": "reply",
                "reply": (
                    "Consumers and businesses create demand through spending and investment."
                ),
            }
        if stage == "direct_answer_repair_claim_audit":
            return {"outcome": "pass"}
        raise AssertionError(stage)

    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: Repository(True))
    monkeypatch.setattr(bot, "describe_reply_media_for_tested_pipeline", describe)
    monkeypatch.setattr(bot, "tested_pipeline_structured_call", transport)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    reply = bot.generate_ai_first_reply(
        clarification_context(),
        supplied_media_context("https://pbs.twimg.com/media/repair.jpg"),
        recent_replies=[],
    )

    assert reply is not None
    assert reply.draft_record["mode"] == "direct_factual_answer"
    assert visual_calls == ["called"]
    assert [call["stage"] for call in model_calls] == [
        "candidate_backed_engagement",
        "writer_v3_initial",
        "direct_answer_repair",
        "direct_answer_repair_claim_audit",
    ]
    assert all(
        call["payload"]["media_context"] == canonical_media
        for call in model_calls
    )
    assert reply.pipeline_metadata["model_call_count"] == len(model_calls)


def test_malformed_visual_description_stops_before_tested_pipeline_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    visual_calls: list[dict] = []
    stage_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def malformed_visual(**kwargs):
        visual_calls.append(kwargs)
        return visual_description(1)

    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(bot, "xai_structured_reply_call", malformed_visual)
    monkeypatch.setattr(
        bot,
        "tested_pipeline_structured_call",
        lambda **kwargs: stage_calls.append(kwargs),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    with pytest.raises(bot.ApiError, match="local schema validation"):
        bot.generate_ai_first_reply(
            context("These two photographs make the point."),
            supplied_media_context(
                "https://pbs.twimg.com/media/one.jpg",
                "https://pbs.twimg.com/media/two.jpg",
            ),
        )

    assert len(visual_calls) == 1
    assert stage_calls == []
    assert events[-1][0] == "reply_visual_description"
    assert events[-1][1]["status"] == "invalid_response"
    assert events[-1][1]["visual_analysis_call_count"] == 1
    assert "analysis" not in events[-1][1]


def test_visual_provider_error_stops_before_tested_pipeline_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    provider_calls: list[str] = []
    stage_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def fail_visual(**_kwargs):
        provider_calls.append("called")
        raise bot.ApiError("visual provider unavailable", service="xai", status_code=503)

    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(bot, "xai_structured_reply_call", fail_visual)
    monkeypatch.setattr(
        bot,
        "tested_pipeline_structured_call",
        lambda **kwargs: stage_calls.append(kwargs),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    with pytest.raises(bot.ApiError, match="visual description provider request failed"):
        bot.generate_ai_first_reply(
            context("This photograph is the point."),
            supplied_media_context("https://pbs.twimg.com/media/one.jpg"),
        )

    assert provider_calls == ["called"]
    assert stage_calls == []
    assert events[-1][1]["status"] == "provider_error"
    assert events[-1][1]["visual_analysis_call_count"] == 1
    assert "analysis" not in events[-1][1]


def test_visual_description_paused_invalid_and_skipped_paths_do_not_log_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    events: list[tuple[str, dict]] = []
    provider_calls: list[str] = []

    def paused_visual(**_kwargs):
        provider_calls.append("paused")
        raise bot.RemoteOperationsPaused("paused before visual provider call")

    monkeypatch.setattr(bot, "xai_structured_reply_call", paused_visual)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    with pytest.raises(bot.RemoteOperationsPaused, match="visual provider"):
        bot.describe_reply_media_for_tested_pipeline(
            context("Look at this photograph."),
            supplied_media_context("https://pbs.twimg.com/media/paused.jpg"),
            config=enabled_config(),
        )

    assert provider_calls == ["paused"]
    assert events[-1][1]["status"] == "paused"
    assert events[-1][1]["visual_analysis_call_count"] == 0
    assert "analysis" not in events[-1][1]

    provider_calls.clear()
    invalid_media = supplied_media_context()
    with pytest.raises(bot.ApiError, match="supplied reply media is invalid"):
        bot.describe_reply_media_for_tested_pipeline(
            context("No usable photograph."),
            invalid_media,
            config=enabled_config(),
        )

    assert provider_calls == []
    assert events[-1][1]["status"] == "invalid_supplied_media"
    assert events[-1][1]["visual_analysis_call_count"] == 0
    assert "analysis" not in events[-1][1]

    skipped_media = {**invalid_media, "status": "none"}
    assert bot.describe_reply_media_for_tested_pipeline(
        context("No photograph supplied."),
        skipped_media,
        config=enabled_config(),
    ) == skipped_media
    assert provider_calls == []
    assert [fields["status"] for _name, fields in events] == [
        "paused",
        "invalid_supplied_media",
    ]


def test_raw_supplied_photo_metadata_fails_before_pipeline_transport() -> None:
    transport_calls: list[dict] = []

    with pytest.raises(ValueError, match="raw media|raw supplied media"):
        pipeline.run_reply_pipeline(
            context=context("Look at this photograph."),
            config=enabled_config(),
            repository=Repository(),
            transport=lambda **kwargs: transport_calls.append(kwargs),
            maximum_reply_length=270,
            media_context=supplied_media_context(
                "https://pbs.twimg.com/media/raw.jpg"
            ),
        )

    assert transport_calls == []


@pytest.mark.parametrize(
    "raw_media",
    [
        [{"url": "https://example.invalid/photo.jpg"}],
        {"status": "none", "preview_image_url": "https://example.invalid/p.jpg"},
        {"status": "none", "media_key": "3_1"},
        [{"type": "image_url"}],
        {"status": "none", "source": "https://pbs.twimg.com/media/raw.jpg"},
    ],
)
def test_media_payload_rejects_every_raw_transport_shape(raw_media: object) -> None:
    with pytest.raises(ValueError, match="raw|image_url"):
        pipeline._media_payload(raw_media)


def test_visual_description_contract_is_strictly_locally_validated() -> None:
    assert pipeline.VISUAL_DESCRIPTION_SCHEMA["additionalProperties"] is False
    image_schema = pipeline.VISUAL_DESCRIPTION_SCHEMA["properties"]["images"]["items"]
    assert image_schema["additionalProperties"] is False
    pending: list[object] = [pipeline.VISUAL_DESCRIPTION_SCHEMA]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            assert "uniqueItems" not in value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    valid = visual_description(2)
    assert pipeline.validate_visual_description(
        valid,
        supplied_image_count=2,
    ) == valid

    for field in ("visible_text", "salient_elements", "uncertainties"):
        duplicate = copy.deepcopy(valid)
        repeated = duplicate["images"][0][field][0]
        duplicate["images"][0][field] = [repeated, repeated]
        with pytest.raises(ValueError, match="duplicates"):
            pipeline.validate_visual_description(
                duplicate,
                supplied_image_count=2,
            )

    missing_field = copy.deepcopy(valid)
    missing_field["images"][0].pop("uncertainties")
    extra_field = copy.deepcopy(valid)
    extra_field["images"][0]["extra"] = "not allowed"
    wrong_index = copy.deepcopy(valid)
    wrong_index["images"][1]["index"] = 1
    blank_description = copy.deepcopy(valid)
    blank_description["images"][0]["literal_description"] = "  "
    control_character = copy.deepcopy(valid)
    control_character["combined_context"] = "line one\nline two"
    too_many_items = copy.deepcopy(valid)
    too_many_items["images"][0]["visible_text"] = [
        f"text {index}" for index in range(9)
    ]
    invalid_values = [
        (visual_description(1), 2),
        (valid, 1),
        (missing_field, 2),
        (extra_field, 2),
        (wrong_index, 2),
        (blank_description, 2),
        (control_character, 2),
        (too_many_items, 2),
    ]
    for value, supplied_count in invalid_values:
        with pytest.raises(ValueError):
            pipeline.validate_visual_description(
                value,
                supplied_image_count=supplied_count,
            )


REGRESSION_TARGET_ID = "2089755083861098981"


def configure_queued_candidate_evaluation(
    bot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict, dict, int]:
    fixed_epoch = 2_000_000_000
    base_since_id = str(int(REGRESSION_TARGET_ID) - 1)
    candidate = {
        "id": REGRESSION_TARGET_ID,
        "author_id": "200",
        "conversation_id": REGRESSION_TARGET_ID,
        "text": "@MrsMThatcher Thank you!",
        "entities": {
            "mentions": [{"id": "12345", "username": "MrsMThatcher"}],
        },
        "referenced_tweets": [],
    }
    state = bot.default_state()
    state["daily_reply_date"] = bot.reply_cap_date_str(fixed_epoch)
    state["last_reply_epoch"] = 0
    state["last_seen_mention_id"] = base_since_id
    state["mention_backlog"] = {
        "since_id": base_since_id,
        "next_token": "A",
        "highest_mention_id": REGRESSION_TARGET_ID,
        "pages_completed": 1,
        "started_epoch": fixed_epoch - 60,
        "seen_tokens": [],
        "announced": True,
    }
    state["mention_pagination"] = {
        "base_since_id": base_since_id,
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        REGRESSION_TARGET_ID: copy.deepcopy(candidate),
    }

    reply_context = context("Thank you!")
    reply_context.update(
        {
            "target_id": REGRESSION_TARGET_ID,
            "thread_id": REGRESSION_TARGET_ID,
            "lane": "mention",
        }
    )

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot, "require_instance_lock_for_remote_write", lambda _operation: None
    )
    monkeypatch.setattr(
        bot,
        "load_confirmed_reply_receipt",
        lambda: ("absent", None),
    )
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda current_state: (
            []
            if REGRESSION_TARGET_ID in current_state.get("replied_to_ids", [])
            else [copy.deepcopy(candidate)]
        ),
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda tweet_id: {"id": str(tweet_id)},
    )
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(
        bot, "is_probably_spam_or_not_worth_replying", lambda _text: False
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args, **_kwargs: (copy.deepcopy(reply_context), True),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(
        bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        bot, "retire_lane_transport_journal_if_present", lambda **_kwargs: None
    )
    monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", lambda *_args: None)
    return state, candidate, fixed_epoch


def provider_failure_state(state: dict) -> dict:
    return {
        key: copy.deepcopy(state[key])
        for key in (
            "xai_error_epochs",
            "xai_api_cooldown_until_epoch",
            "xai_api_cooldown_reason",
        )
    }


def capture_bot_logs(bot, monkeypatch: pytest.MonkeyPatch) -> list[logging.LogRecord]:
    records: list[logging.LogRecord] = []

    class RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.Logger("mrsMThatcher.pause-defer-test", level=logging.DEBUG)
    logger.propagate = False
    logger.addHandler(RecordingHandler(level=logging.DEBUG))
    monkeypatch.setattr(bot, "log", logger)
    return records


def test_stage_telemetry_is_allow_listed_and_text_free() -> None:
    private_marker = "PRIVATE MATCHED TEXT MUST NOT BE LOGGED"
    telemetry = pipeline.stage_telemetry((
        {"stage": "A_B_C", "suppressed": False, "reason": None, "text": private_marker},
        {"stage": "candidate_backed_engagement", "provider": "xAI", "schema_valid": True},
        {"stage": "xai_gate_decision", "decision": "no_reply", "candidate": private_marker},
        {"stage": "reply_necessity_1", "provider": "OpenAI", "schema_valid": True},
        {"stage": "reply_necessity_2", "provider": "OpenAI", "schema_valid": False, "error": "ValueError"},
        {"stage": "reply_necessity_3", "provider": "OpenAI", "schema_valid": True},
        {
            "stage": "reply_necessity_resolution",
            "call_outcomes": ["require_claim_free_reply", None, "require_claim_free_reply"],
            "majority_outcome": "require_claim_free_reply",
            "majority_resolvable": True,
            "invalid_or_refused_calls": 1,
            "reviewer_calls_attempted": 3,
            "valid_votes_obtained": 2,
            "first_two_valid_votes_agreed": False,
            "reviewer_3_called": True,
            "reviewer_3_skipped_first_two_agreement": False,
        },
        {
            "stage": "allegation_conspiracy_detector",
            "candidate": True,
            "categories": ["corruption_or_fraud"],
            "matched_text": private_marker,
        },
        {
            "stage": "allegation_conspiracy_resolution",
            "majority_outcome": "confirm_no_reply",
            "majority_resolvable": True,
            "invalid_or_refused_calls": 0,
            "free_form_reason": private_marker,
        },
        {"stage": "attribution_route_v2", "route_class": "none", "reply_requirement": None},
        {
            "stage": "narrow_claim_audit_risk",
            "risky": True,
            "categories": ["private_motive"],
            "matched_text": [private_marker],
        },
        {"stage": "narrow_claim_audit", "provider": "xAI", "schema_valid": True},
        {"stage": "narrow_claim_audit_outcome", "outcome": "rewrite_claim_free"},
        {"stage": "bounded_claim_cleanup", "provider": "OpenAI", "schema_valid": True},
        {"stage": "exact_duplicate_check", "exact_duplicate": True, "near_duplicate_count": 2},
        {"stage": "exact_duplicate_repair", "provider": "OpenAI", "schema_valid": True},
        {"stage": "exact_duplicate_repair_outcome", "outcome": "repaired"},
        {"stage": "final_deterministic_validation", "rejection": None},
    ))

    assert telemetry["provider_call_counts"] == {"xAI": 2, "OpenAI": 5}
    assert telemetry["schema_invalid_stages"] == ["reply_necessity_2"]
    assert telemetry["reply_necessity_outcome"] == "require_claim_free_reply"
    assert telemetry["reply_necessity_majority_resolvable"] is True
    assert telemetry["reply_necessity_invalid_calls"] == 1
    assert telemetry["majority_review_summaries"] == [{
        "family": "reply_necessity",
        "reviewer_calls_attempted": 3,
        "valid_votes_obtained": 2,
        "first_two_valid_votes_agreed": False,
        "reviewer_3_called": True,
        "reviewer_3_skipped_first_two_agreement": False,
    }]
    assert telemetry["allegation_conspiracy_candidate"] is True
    assert telemetry["allegation_conspiracy_categories"] == ["corruption_or_fraud"]
    assert telemetry["allegation_conspiracy_outcome"] == "confirm_no_reply"
    assert telemetry["allegation_conspiracy_majority_resolvable"] is True
    assert telemetry["claim_risk_categories"] == ["private_motive"]
    assert telemetry["claim_audit_outcomes"] == [{
        "stage": "narrow_claim_audit_outcome",
        "outcome": "rewrite_claim_free",
    }]
    assert telemetry["claim_cleanup_called"] is True
    assert telemetry["exact_duplicate_detected"] is True
    assert telemetry["duplicate_repair_called"] is True
    assert telemetry["duplicate_repair_outcome"] == "repaired"
    assert telemetry["final_validation"] == "passed"
    assert private_marker not in json.dumps(telemetry, sort_keys=True)


def run(text: str, transport: Transport, *, facts: bool = False, recent=None):
    return pipeline.run_reply_pipeline(
        context=context(text),
        config=enabled_config(),
        repository=Repository(facts),
        transport=transport,
        maximum_reply_length=270,
        recent_replies=recent or [],
        media_context=None,
    )


class WriterLinkRepairTransport(Transport):
    def __init__(self, *, initial_reply: str, repair_response: object) -> None:
        super().__init__(writer=initial_reply)
        self.repair_response = repair_response

    def __call__(self, **kwargs):
        if kwargs["stage"] == "writer_v3_link_repair":
            self.calls.append(kwargs)
            return copy.deepcopy(self.repair_response)
        return super().__call__(**kwargs)


def test_writer_prompt_prohibits_complete_reply_contains_link_class() -> None:
    prompt = pipeline.WRITER_PROMPT.casefold()

    for prohibited in (
        "urls",
        "web addresses",
        "domain names",
        "email addresses",
        "ip addresses",
        "network addresses",
    ):
        assert prohibited in prompt
    assert "refer to a source descriptively rather than linking to it" in prompt


def test_writer_link_repair_succeeds_once_and_preserves_downstream_safeguards() -> None:
    incoming = "Thank you for setting out the principle."
    value = context(incoming)
    rejected = "The useful point is explained at example.com."
    repaired = (
        "Responsibility is the useful point here; the argument can stand on its own."
    )
    repository = Repository(facts=True)
    transport = WriterLinkRepairTransport(
        initial_reply=rejected,
        repair_response={"status": "reply", "reply": repaired},
    )

    result = pipeline.run_reply_pipeline(
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
        recent_replies=[],
        media_context=None,
    )
    baseline_transport = Transport(writer=repaired)
    baseline = pipeline.run_reply_pipeline(
        context=value,
        config=enabled_config(),
        repository=Repository(facts=True),
        transport=baseline_transport,
        maximum_reply_length=270,
        recent_replies=[],
        media_context=None,
    )

    assert result.status == "approved"
    assert result.reason == "pipeline_approved"
    assert str(result.reply) == repaired
    assert result.model_call_count == len(transport.calls) == 3
    assert result.model_call_count == baseline.model_call_count + 1
    assert result.revision_count == 1
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "writer_v3_initial",
        "writer_v3_link_repair",
    ]
    assert sum(
        call["stage"] == "writer_v3_link_repair" for call in transport.calls
    ) == 1

    initial_call = next(
        call for call in transport.calls if call["stage"] == "writer_v3_initial"
    )
    repair_call = next(
        call for call in transport.calls if call["stage"] == "writer_v3_link_repair"
    )
    assert repair_call["provider"] == "OpenAI"
    assert repair_call["model"] == enabled_config()["openai_model"]
    assert repair_call["system_prompt"] == pipeline.WRITER_LINK_REPAIR_PROMPT
    assert repair_call["response_schema"] == pipeline.WRITER_SCHEMA
    assert repair_call["max_output_tokens"] == enabled_config()[
        "writer_max_output_tokens"
    ]
    assert repair_call["reasoning_effort"] == enabled_config()[
        "openai_reasoning_effort"
    ]
    assert repair_call["timeout_seconds"] == enabled_config()["timeout_seconds"]
    assert set(repair_call["payload"]) == set(initial_call["payload"]) | {
        "rejected_draft_untrusted",
        "original_local_rejection_reason",
    }
    for field in (
        "context",
        "recent_replies",
        "trusted_facts",
        "media_context",
        "reply_requirement",
    ):
        assert repair_call["payload"][field] == initial_call["payload"][field]
    assert repair_call["payload"]["context"] == value
    assert repair_call["payload"]["trusted_facts"] == [Passage().prompt_record()]
    assert repair_call["payload"]["reply_requirement"] == "general"
    assert repair_call["payload"]["rejected_draft_untrusted"] == rejected
    assert repair_call["payload"]["original_local_rejection_reason"] == (
        "reply_contains_link"
    )
    assert "rejected_draft_untrusted" not in initial_call["payload"]

    audit_stages = [row["stage"] for row in result.audit]
    for stage in (
        "writer_link_repair_trigger",
        "writer_link_repair_outcome",
        "narrow_claim_audit_risk",
        "exact_duplicate_check",
        "final_deterministic_validation",
    ):
        assert stage in audit_stages
    assert {
        "stage": "writer_link_repair_trigger",
        "original_local_rejection_reason": "reply_contains_link",
    } in result.audit
    assert {
        "stage": "writer_link_repair_outcome",
        "outcome": "approved",
    } in result.audit
    assert rejected not in json.dumps(result.audit, sort_keys=True)
    telemetry = pipeline.stage_telemetry(result.audit)
    assert telemetry["provider_call_counts"] == {"xAI": 1, "OpenAI": 2}
    assert rejected not in json.dumps(telemetry, sort_keys=True)

    assert pipeline._public_reply_error(
        str(result.reply),
        repository,
        maximum_reply_length=270,
        maximum_sentences=enabled_config()["maximum_reply_sentences"],
    ) is None
    assert "example.com" not in str(result.reply)
    assert result.reply.draft_record["proposed_reply"] == repaired
    assert rejected not in json.dumps(result.reply.draft_record, sort_keys=True)
    persisted = pipeline.validate_persisted_draft(
        result.reply.draft_record,
        context=value,
        config=enabled_config(),
        repository=repository,
        maximum_reply_length=270,
        recent_replies=[],
    )
    assert persisted["proposed_reply"] == repaired


def test_writer_link_repair_still_containing_link_fails_closed_without_loop() -> None:
    transport = WriterLinkRepairTransport(
        initial_reply="The useful point is explained at example.com.",
        repair_response={
            "status": "reply",
            "reply": "The same explanation remains at example.org.",
        },
    )

    result = run("Thank you for explaining the principle.", transport)

    assert result.status == "no_reply"
    assert result.reason == (
        "writer_link_repair_local_rejection:reply_contains_link"
    )
    assert result.reply is None
    assert result.model_call_count == len(transport.calls) == 3
    assert result.revision_count == 1
    assert [call["stage"] for call in transport.calls].count(
        "writer_v3_link_repair"
    ) == 1
    assert {
        "stage": "writer_link_repair_outcome",
        "outcome": "local_rejection:reply_contains_link",
    } in result.audit
    assert "narrow_claim_audit_risk" not in {
        row["stage"] for row in result.audit
    }


@pytest.mark.parametrize(
    ("repair_response", "expected_outcome", "schema_invalid"),
    [
        pytest.param(
            {"status": "reply"},
            "unavailable",
            True,
            id="schema-invalid",
        ),
        pytest.param(
            {"status": "cannot_compose_safely", "reply": ""},
            "cannot_compose_safely",
            False,
            id="cannot-compose-safely",
        ),
    ],
)
def test_writer_link_repair_failure_is_bounded(
    repair_response: object,
    expected_outcome: str,
    schema_invalid: bool,
) -> None:
    transport = WriterLinkRepairTransport(
        initial_reply="The useful point is explained at example.com.",
        repair_response=repair_response,
    )

    result = run("Thank you for explaining the principle.", transport)

    assert result.status == "no_reply"
    assert result.reason == "writer_link_repair_failed"
    assert result.reply is None
    assert result.model_call_count == len(transport.calls) == 3
    assert result.revision_count == 0
    assert [call["stage"] for call in transport.calls].count(
        "writer_v3_link_repair"
    ) == 1
    assert {
        "stage": "writer_link_repair_outcome",
        "outcome": expected_outcome,
    } in result.audit
    telemetry = pipeline.stage_telemetry(result.audit)
    assert ("writer_v3_link_repair" in telemetry["schema_invalid_stages"]) is (
        schema_invalid
    )


def test_writer_non_link_local_rejection_does_not_trigger_link_repair() -> None:
    transport = Transport(writer="First line.\nSecond line.")

    result = run("Thank you for explaining the principle.", transport)

    assert result.status == "no_reply"
    assert result.reason == "writer_local_rejection:reply_contains_line_break"
    assert result.reply is None
    assert result.model_call_count == len(transport.calls) == 2
    assert result.revision_count == 0
    assert "writer_v3_link_repair" not in {
        call["stage"] for call in transport.calls
    }


def test_writer_compliant_path_does_not_trigger_link_repair() -> None:
    transport = Transport(writer="Responsibility is the useful point here.")

    result = run("Thank you for explaining the principle.", transport)

    assert result.status == "approved"
    assert result.reason == "pipeline_approved"
    assert result.model_call_count == len(transport.calls) == 2
    assert result.revision_count == 0
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "writer_v3_initial",
    ]
    audit_stages = {row["stage"] for row in result.audit}
    assert "writer_link_repair_trigger" not in audit_stages
    assert "writer_link_repair_outcome" not in audit_stages
    assert "narrow_claim_audit_risk" in audit_stages
    assert "exact_duplicate_check" in audit_stages
    assert "final_deterministic_validation" in audit_stages


def test_writer_link_repair_obeys_existing_model_call_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CeilingTransport:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            stage = kwargs["stage"]
            responses = {
                "candidate_backed_engagement": {"decision": "no_reply", "reply": ""},
                "reply_necessity_1": {"outcome": "require_claim_free_reply"},
                "reply_necessity_2": {"outcome": "confirm_no_reply"},
                "reply_necessity_3": {"outcome": "require_claim_free_reply"},
                "focused_group_review": {"outcome": "allow_reply"},
                "allegation_review_1": {"outcome": "require_claim_free_reply"},
                "allegation_review_2": {"outcome": "confirm_no_reply"},
                "allegation_review_3": {"outcome": "require_claim_free_reply"},
                "authentication_review_1": {
                    "outcome": "require_supported_factual_reply"
                },
                "authentication_review_2": {
                    "outcome": "suppress_unsupported_authentication"
                },
                "authentication_review_3": {
                    "outcome": "require_supported_factual_reply"
                },
                "writer_v3_initial": {
                    "status": "reply",
                    "reply": "The useful point is explained at example.com.",
                },
            }
            if stage == "writer_v3_link_repair":
                pytest.fail("ceiling must stop the repair before transport")
            return copy.deepcopy(responses[stage])

    monkeypatch.setattr(
        pipeline,
        "group_hostility_review_candidate",
        lambda _context: {"candidate": True},
    )
    monkeypatch.setattr(
        pipeline,
        "allegation_review_candidate",
        lambda _context: {"candidate": True},
    )
    monkeypatch.setattr(
        pipeline,
        "attribution_route_v2",
        lambda _context, _facts=None: {
            "route_class": "direct_authentication",
            "reply_requirement": None,
        },
    )
    transport = CeilingTransport()
    config = enabled_config()
    config["maximum_model_calls"] = 12

    with pytest.raises(
        RuntimeError,
        match="tested reply pipeline model-call ceiling reached",
    ):
        pipeline.run_reply_pipeline(
            context=context("Please explain this civil question."),
            config=config,
            repository=Repository(facts=True),
            transport=transport,
            maximum_reply_length=270,
        )

    assert len(transport.calls) == config["maximum_model_calls"] == 12
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
        "reply_necessity_2",
        "reply_necessity_3",
        "focused_group_review",
        "allegation_review_1",
        "allegation_review_2",
        "allegation_review_3",
        "authentication_review_1",
        "authentication_review_2",
        "authentication_review_3",
        "writer_v3_initial",
    ]
    assert "writer_v3_link_repair" not in {
        call["stage"] for call in transport.calls
    }


@pytest.mark.parametrize(
    "writer",
    [
        pytest.param("Thank you.", id="one_sentence"),
        pytest.param("Thank you. That is kind.", id="two_sentences"),
        pytest.param(
            "Thank you. That is kind. Much appreciated.",
            id="three_sentences",
        ),
    ],
)
def test_quote_tweet_writer_accepts_up_to_three_sentences_without_extra_calls(
    writer: str,
) -> None:
    transport = Transport(
        gate="no_reply",
        review="require_claim_free_reply",
        writer=writer,
    )

    result = pipeline.run_reply_pipeline(
        context=quote_tweet_context("Thank you for sharing this distinction."),
        config=enabled_config(),
        repository=Repository(),
        transport=transport,
        maximum_reply_length=270,
    )

    assert result.status == "approved"
    assert str(result.reply) == writer
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
        "reply_necessity_2",
        "writer_v3_initial",
    ]
    assert [call["provider"] for call in transport.calls] == [
        "xAI",
        "OpenAI",
        "OpenAI",
        "OpenAI",
    ]
    assert result.model_call_count == 4
    assert result.revision_count == 0


def test_quote_tweet_writer_rejects_four_sentences() -> None:
    transport = Transport(
        gate="no_reply",
        review="require_claim_free_reply",
        writer="Thank you. That is kind. Much appreciated. Good wishes.",
    )

    result = pipeline.run_reply_pipeline(
        context=quote_tweet_context("Thank you for sharing this distinction."),
        config=enabled_config(),
        repository=Repository(),
        transport=transport,
        maximum_reply_length=270,
    )

    assert result.status == "no_reply"
    assert result.reason == (
        "writer_local_rejection:reply_sentence_limit_exceeded"
    )
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
        "reply_necessity_2",
        "writer_v3_initial",
    ]
    assert result.model_call_count == 4
    assert result.revision_count == 0


def clarification_context(
    *,
    original: str = "Who creates the demand needed to get a private economy moving?",
    correction: str = "You still did not answer who creates that demand.",
) -> dict:
    value = context(correction)
    value["clarification_request"] = {
        "original_question": original,
        "correction": correction,
    }
    return value


class DirectAnswerRepairTransport:
    def __init__(
        self,
        *,
        repaired_reply: str = "Consumers and businesses create demand through their spending and investment.",
        writer_status: str = "reply",
        claim_outcome: str = "pass",
    ) -> None:
        self.repaired_reply = repaired_reply
        self.writer_status = writer_status
        self.claim_outcome = claim_outcome
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["stage"] == "direct_answer_repair":
            return {
                "status": self.writer_status,
                "reply": self.repaired_reply if self.writer_status == "reply" else "",
            }
        if kwargs["stage"] == "direct_answer_repair_claim_audit":
            return {"outcome": self.claim_outcome}
        raise AssertionError(kwargs["stage"])


def approved_non_factual_reply(repository: Repository, value: dict) -> object:
    transport = Transport(writer="Demand must be matched by confidence and enterprise.")
    result = pipeline.run_reply_pipeline(
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
        recent_replies=[],
        media_context=None,
    )
    assert result.status == "approved"
    assert result.reply is not None
    assert result.reply.draft_record["mode"] == "opinion_or_principle"
    return result.reply


def test_target_2089269156994523171_is_a_direct_factual_repair_candidate() -> None:
    value = clarification_context(
        original="If public spending does not sustain employment, where does demand come from?",
        correction=(
            "@MrsMThatcher What if the private economy isn’t healthy? "
            "Who creates the demand needed to get it moving?"
        ),
    )
    value["target_id"] = "2089269156994523171"

    assert pipeline.clarification_requires_direct_factual_answer(value) is True
    assert pipeline.trusted_facts_support_direct_factual_answer(
        value,
        [
            {
                "evidence_id": "target-fact-1",
                "passage": (
                    "Government expenditure does not itself sustain long-term "
                    "employment; a robust economy does."
                ),
            },
            {
                "evidence_id": "target-fact-2",
                "passage": (
                    "A healthy free-market economy lets private enterprise "
                    "invest and create permanent jobs."
                ),
            },
        ],
    ) is False


def test_one_supported_direct_factual_repair_succeeds() -> None:
    repository = Repository(facts=True)
    value = clarification_context()
    original = approved_non_factual_reply(repository, value)
    transport = DirectAnswerRepairTransport()

    repair = pipeline.repair_approved_direct_answer(
        approved_reply=original,
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
        recent_replies=[],
        media_context=None,
    )

    assert repair.outcome == "approved"
    assert repair.attempted is True
    assert repair.additional_model_calls == 2
    assert repair.reply is not None
    assert repair.reply.draft_record["mode"] == "direct_factual_answer"
    assert repair.reply.draft_record["original_local_rejection_reason"] == (
        "clarification_not_direct_factual_answer"
    )
    assert [call["stage"] for call in transport.calls] == [
        "direct_answer_repair",
        "direct_answer_repair_claim_audit",
    ]
    assert sum(
        call["stage"] == "direct_answer_repair" for call in transport.calls
    ) == 1
    assert transport.calls[0]["model"] == "gpt-5.6-sol"
    assert "only facts explicitly established by trusted_facts" in (
        transport.calls[0]["system_prompt"]
    )


def test_direct_answer_repair_is_not_attempted_without_trusted_facts() -> None:
    repository = Repository(facts=False)
    value = clarification_context()
    original = approved_non_factual_reply(repository, value)
    transport = DirectAnswerRepairTransport()

    repair = pipeline.repair_approved_direct_answer(
        approved_reply=original,
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
    )

    assert repair.reply is None
    assert repair.attempted is False
    assert repair.outcome == "not_attempted_insufficient_trusted_facts"
    assert transport.calls == []


def test_correct_clarification_is_not_forced_into_a_factual_answer() -> None:
    repository = Repository(facts=True)
    value = clarification_context(
        original="What should liberty mean in this argument?",
        correction="Could you clarify which aspect you mean?",
    )
    original = approved_non_factual_reply(repository, value)
    transport = DirectAnswerRepairTransport()

    repair = pipeline.repair_approved_direct_answer(
        approved_reply=original,
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
    )

    assert str(original) == "Demand must be matched by confidence and enterprise."
    assert repair.reply is None
    assert repair.attempted is False
    assert repair.outcome == "not_attempted_not_direct_factual_question"
    assert transport.calls == []


def test_failed_direct_answer_repair_stays_fail_closed_without_a_loop() -> None:
    repository = Repository(facts=True)
    value = clarification_context()
    original = approved_non_factual_reply(repository, value)
    transport = DirectAnswerRepairTransport(
        claim_outcome="rewrite_supported_factual"
    )

    repair = pipeline.repair_approved_direct_answer(
        approved_reply=original,
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
    )

    assert repair.reply is None
    assert repair.attempted is True
    assert repair.outcome == (
        "claim_audit_not_passed:rewrite_supported_factual"
    )
    assert [call["stage"] for call in transport.calls] == [
        "direct_answer_repair",
        "direct_answer_repair_claim_audit",
    ]
    assert not any(
        call["stage"] in {"bounded_claim_cleanup", "exact_duplicate_repair"}
        for call in transport.calls
    )


def test_repair_writer_refusal_uses_only_the_one_extra_writer_call() -> None:
    repository = Repository(facts=True)
    value = clarification_context()
    original = approved_non_factual_reply(repository, value)
    transport = DirectAnswerRepairTransport(writer_status="cannot_compose_safely")

    repair = pipeline.repair_approved_direct_answer(
        approved_reply=original,
        context=value,
        config=enabled_config(),
        repository=repository,
        transport=transport,
        maximum_reply_length=270,
    )

    assert repair.reply is None
    assert repair.outcome == "writer_cannot_compose_safely"
    assert [call["stage"] for call in transport.calls] == [
        "direct_answer_repair"
    ]


def test_stage_approval_followed_by_failed_repair_logs_effective_local_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def transport(**kwargs):
        calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "candidate_backed_engagement":
            return {"decision": "reply", "reply": "PRIVATE GATE CANDIDATE"}
        if stage == "writer_v3_initial":
            return {
                "status": "reply",
                "reply": "Demand must be matched by confidence and enterprise.",
            }
        if stage == "direct_answer_repair":
            return {
                "status": "reply",
                "reply": (
                    "Consumers and businesses create demand through spending "
                    "and investment."
                ),
            }
        if stage == "direct_answer_repair_claim_audit":
            return {"outcome": "rewrite_supported_factual"}
        raise AssertionError(stage)

    value = clarification_context()
    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: Repository(True))
    monkeypatch.setattr(bot, "tested_pipeline_structured_call", transport)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    evaluation: dict = {}
    reply = bot.generate_ai_first_reply(
        value,
        recent_replies=[],
        evaluation_outcome=evaluation,
    )

    assert reply is None
    assert [call["stage"] for call in calls] == [
        "candidate_backed_engagement",
        "writer_v3_initial",
        "direct_answer_repair",
        "direct_answer_repair_claim_audit",
    ]
    assert sum(call["stage"] == "direct_answer_repair" for call in calls) == 1
    stage_event = next(fields for name, fields in events if name == "ai_reply_pipeline_stage_summary")
    decision_event = next(fields for name, fields in events if name == "ai_reply_pipeline_decision")
    assert stage_event["pipeline_stage_status"] == "approved"
    assert stage_event["effective_status"] == "local_rejection"
    assert stage_event["final_validation"] == "passed"
    assert decision_event["status"] == "approved"
    assert decision_event["effective_status"] == "local_rejection"
    assert decision_event["proposed_draft"] == (
        "Demand must be matched by confidence and enterprise."
    )
    assert decision_event["repaired_draft"].startswith("Consumers and businesses")
    assert evaluation["status"] == "local_rejection"
    assert evaluation["direct_answer_repair_attempted"] is True


def test_writer_pause_defers_queued_candidate_then_posts_once_when_unpaused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    state, _candidate, fixed_epoch = configure_queued_candidate_evaluation(
        bot, monkeypatch, tmp_path
    )
    control_path = tmp_path / "control.json"
    control_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_path)
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

    pause_after_gate = [True]
    provider_stages: list[str] = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, content: dict[str, str]) -> None:
            self.content = content

        def json(self) -> dict:
            return {"choices": [{"message": {"content": self.content}}]}

    def provider_post(_url: str, **kwargs: object) -> Response:
        request = kwargs["json"]
        assert isinstance(request, dict)
        schema = request["response_format"]["json_schema"]
        stage = str(schema["name"]).removeprefix("mrs_tested_")
        provider_stages.append(stage)
        if stage == "candidate_backed_engagement":
            if pause_after_gate[0]:
                control_path.write_text(
                    json.dumps({"pause_all": True}), encoding="utf-8"
                )
            return Response(
                {"decision": "reply", "reply": "PRIVATE GATE CANDIDATE"}
            )
        if stage == "writer_v3_initial":
            return Response(
                {
                    "status": "reply",
                    "reply": "Thank you — that is kind of you.",
                }
            )
        raise AssertionError(stage)

    public_replies: list[dict[str, str]] = []

    def post_reply(**kwargs: object) -> tuple[dict, dict]:
        receipt_template = kwargs["receipt_template"]
        assert isinstance(receipt_template, dict)
        public_replies.append(
            {
                "target_id": str(kwargs["reply_to_id"]),
                "text": str(kwargs["reply_text"]),
            }
        )
        receipt = bot._confirmed_reply_receipt_from_sending(
            receipt_template,
            reply_post_id="900001",
            confirmation_epoch=fixed_epoch,
        )
        return {"data": {"id": "900001"}}, receipt

    api_error_calls: list[tuple] = []
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot.requests, "post", provider_post)
    monkeypatch.setattr(
        bot, "post_conversational_reply_with_durable_identity", post_reply
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *args, **kwargs: api_error_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    initial_provider_state = provider_failure_state(state)
    initial_daily_reply_count = state["daily_reply_count"]
    initial_last_seen = state["last_seen_mention_id"]
    log_records = capture_bot_logs(bot, monkeypatch)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    paused_messages = [record.getMessage() for record in log_records]
    assert provider_stages == ["candidate_backed_engagement"]
    assert public_replies == []
    assert api_error_calls == []
    assert provider_failure_state(state) == initial_provider_state
    assert not any("Unexpected Grok failure" in message for message in paused_messages)
    assert not any("RemoteOperationsPaused" in message for message in paused_messages)
    assert all(record.exc_info is None for record in log_records)
    pause_records = [
        record
        for record in log_records
        if record.getMessage().startswith(
            "Deferring conversational reply evaluation"
        )
    ]
    assert len(pause_records) == 1
    assert pause_records[0].levelno == logging.INFO
    assert events == [
        (
            "reply_pipeline_paused",
            {
                "lane": "mention",
                "target_id": REGRESSION_TARGET_ID,
                "reason": "global_runtime_control_pause",
            },
        )
    ]
    assert bot.terminal_reply_evaluation(state, REGRESSION_TARGET_ID) is None
    assert state["last_seen_mention_id"] == initial_last_seen
    assert REGRESSION_TARGET_ID in state["mention_pending_candidates"]
    assert state["replied_to_ids"] == []
    assert state["daily_reply_count"] == initial_daily_reply_count
    assert state["author_evaluation_quarantines"] == {}
    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert REGRESSION_TARGET_ID in persisted["mention_pending_candidates"]

    pause_after_gate[0] = False
    control_path.write_text("{}", encoding="utf-8")
    log_records.clear()

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
    assert public_replies == [
        {
            "target_id": REGRESSION_TARGET_ID,
            "text": "Thank you — that is kind of you.",
        }
    ]
    assert provider_stages == [
        "candidate_backed_engagement",
        "candidate_backed_engagement",
        "writer_v3_initial",
    ]
    assert REGRESSION_TARGET_ID not in state["mention_pending_candidates"]
    assert state["replied_to_ids"] == [REGRESSION_TARGET_ID]
    assert state["daily_reply_count"] == initial_daily_reply_count + 1
    assert bot.terminal_reply_evaluation(state, REGRESSION_TARGET_ID) is None
    assert api_error_calls == []

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert len(public_replies) == 1


def test_context_pause_defers_candidate_without_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    state, _candidate, _fixed_epoch = configure_queued_candidate_evaluation(
        bot, monkeypatch, tmp_path
    )
    initial_provider_state = provider_failure_state(state)
    initial_last_seen = state["last_seen_mention_id"]
    api_error_calls: list[tuple] = []
    events: list[tuple[str, dict]] = []

    def paused_context(*_args: object, **_kwargs: object) -> None:
        raise bot.RemoteOperationsPaused("paused during candidate context")

    monkeypatch.setattr(bot, "build_context_for_reply_ai", paused_context)
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail("provider stage must not start"),
    )
    monkeypatch.setattr(
        bot,
        "post_conversational_reply_with_durable_identity",
        lambda **_kwargs: pytest.fail("public reply must not be posted"),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *args, **kwargs: api_error_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )
    log_records = capture_bot_logs(bot, monkeypatch)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    paused_messages = [record.getMessage() for record in log_records]
    assert api_error_calls == []
    assert provider_failure_state(state) == initial_provider_state
    assert not any("Unexpected Grok failure" in message for message in paused_messages)
    assert not any("RemoteOperationsPaused" in message for message in paused_messages)
    assert all(record.exc_info is None for record in log_records)
    assert sum(
        record.levelno == logging.INFO
        and record.getMessage().startswith(
            "Deferring conversational reply evaluation"
        )
        for record in log_records
    ) == 1
    assert events == [
        (
            "reply_pipeline_paused",
            {
                "lane": "mention",
                "target_id": REGRESSION_TARGET_ID,
                "reason": "global_runtime_control_pause",
            },
        )
    ]
    assert bot.terminal_reply_evaluation(state, REGRESSION_TARGET_ID) is None
    assert state["last_seen_mention_id"] == initial_last_seen
    assert REGRESSION_TARGET_ID in state["mention_pending_candidates"]
    assert state["replied_to_ids"] == []


@pytest.mark.parametrize(
    ("failure_kind", "expected_log"),
    [
        ("api_error", "Failed to ask Grok for reply"),
        ("unexpected", "Unexpected Grok failure"),
    ],
)
def test_non_pause_generation_failures_keep_existing_fail_closed_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_log: str,
) -> None:
    import mrsMThatcher2 as bot

    state, _candidate, fixed_epoch = configure_queued_candidate_evaluation(
        bot, monkeypatch, tmp_path
    )
    error = (
        bot.ApiError("provider unavailable", service="xai", status_code=503)
        if failure_kind == "api_error"
        else RuntimeError("unrelated generation defect")
    )

    def fail_generation(*_args: object, **_kwargs: object) -> None:
        raise error

    original_record_api_error = bot.record_api_error
    recorded: list[tuple[str, str]] = []

    def record_api_error(
        current_state: dict,
        current_error: Exception,
        service: str,
        *,
        scope: str = "api",
    ) -> None:
        recorded.append((service, scope))
        original_record_api_error(
            current_state, current_error, service, scope=scope
        )

    monkeypatch.setattr(bot, "generate_ai_first_reply", fail_generation)
    monkeypatch.setattr(bot, "record_api_error", record_api_error)
    log_records = capture_bot_logs(bot, monkeypatch)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR

    assert recorded == [("xai", "api")]
    assert state["xai_error_epochs"] == [fixed_epoch]
    assert state["xai_api_cooldown_until_epoch"] == 0
    failure_records = [
        record for record in log_records if record.getMessage() == expected_log
    ]
    assert len(failure_records) == 1
    assert failure_records[0].exc_info is not None
    assert bot.terminal_reply_evaluation(state, REGRESSION_TARGET_ID) is None
    assert REGRESSION_TARGET_ID in state["mention_pending_candidates"]


def test_frozen_prompt_hashes_and_provider_profiles() -> None:
    expected = {
        "XAI_GATE_PROMPT": "e145e67c365cc295174e00284568fd05c1848ad85e63f01815891ca00ca5ba55",
        "REPLY_NECESSITY_PROMPT": "66d2c3ef36a5cb1560f025ed1ead588e3c2e343e5dcde51d36508c3dd545b13b",
        "GROUP_REVIEW_PROMPT": "2355c7056d0ba93ddd79d731cc2999fc2e5fecd55eb8444814c1d21c06e7e6fc",
        "WRITER_PROMPT": "c63cf4a70694beda982fd727a8415e94db515007bc62bf288400cfebebc5966a",
        "WRITER_LINK_REPAIR_PROMPT": (
            "5eecd8449a9149726498351e5849d3bdc0f5783ad7e795ab7915907ce5c7b234"
        ),
        "CLAIM_AUDIT_PROMPT": "a2e0f3e78bdd3aa5a45e4fc2ba1eed7819043b4ffec97caeeb29c66ac6824589",
        "CLAIM_CLEANUP_PROMPT": "04a926149d8e5440f6b6426bfbba173112c01776f6bad1698c81755badffc7a5",
        "DIVERSITY_PROMPT": "fcb4b58e153023cd638642158fbdd4a53213fe2e69320ee92a0b48c89563b66b",
        "DIRECT_ANSWER_REPAIR_PROMPT": (
            "345a3c9521152f0d44508f633b50f616836c5b3a32707998a6922a87fa227204"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256(getattr(pipeline, name).encode()).hexdigest() == digest
    config = pipeline.default_config()
    assert (config["xai_model"], config["xai_reasoning_effort"]) == ("grok-4.3", "low")
    assert (config["openai_model"], config["openai_reasoning_effort"]) == ("gpt-5.6-sol", "medium")
    assert config["timeout_seconds"] == 180


@pytest.mark.parametrize(
    "text",
    ["👏", "Thank you!", "Quite right.", "A fair point, well said."],
)
def test_positive_social_and_brief_agreement_receive_warm_reply(text: str) -> None:
    transport = Transport()
    result = run(text, transport)
    assert result.status == "approved"
    assert str(result.reply) == "Thank you — that is kind of you."
    assert result.reply.pipeline_metadata["final_reply_kind"] == "unknown"
    assert result.reply.pipeline_metadata["mode"] == "opinion_or_principle"
    assert result.reply.pipeline_metadata["tone"] == "unknown"


@pytest.mark.parametrize(
    ("text", "writer", "facts", "expected_kind"),
    [
        ("Could you say which point you mean?", "Which part did you have in mind?", False, "unknown"),
        ("Did Margaret Thatcher really say this?", "The local transcript records those exact words.", True, "factual"),
        ("Liberty also requires institutions.", "Liberty endures only when institutions remain answerable.", False, "unknown"),
    ],
)
def test_reply_kind_metadata_is_small_and_deterministic(
    text: str,
    writer: str,
    facts: bool,
    expected_kind: str,
) -> None:
    result = run(text, Transport(writer=writer), facts=facts)

    assert result.status == "approved"
    assert result.reply.pipeline_metadata["final_reply_kind"] == expected_kind
    assert result.reply.pipeline_metadata["mode"] == (
        "direct_factual_answer" if facts else "opinion_or_principle"
    )
    assert result.reply.pipeline_metadata["tone"] == "unknown"


def test_supplied_trusted_facts_are_not_reported_as_used_facts() -> None:
    result = run(
        "Did Margaret Thatcher really say this?",
        Transport(writer="The local transcript records those exact words."),
        facts=True,
    )

    metadata = result.reply.pipeline_metadata
    assert metadata["trusted_facts_supplied_count"] == 1
    assert metadata["trusted_fact_ids_supplied"] == ["fact-1"]
    assert metadata["used_fact_count"] == "unknown"
    assert metadata["used_fact_ids"] is None
    assert metadata["evidence_reference_count"] is None
    telemetry = pipeline.stage_telemetry(result.audit)
    assert telemetry["trusted_facts_supplied_count"] == 1
    assert telemetry["trusted_fact_ids_supplied"] == ["fact-1"]


def test_literal_bare_mention_is_deterministically_suppressed_without_calls() -> None:
    transport = Transport()
    result = run("@MrsMThatcher", transport)

    assert result.status == "no_reply"
    assert result.reason == (
        "incoming contribution has no lexical content beyond handle(s) and/or URL(s)"
    )
    assert result.model_call_count == 0
    assert transport.calls == []
    telemetry = pipeline.stage_telemetry(result.audit)
    assert telemetry["deterministic_suppressed"] is True
    assert telemetry["deterministic_reason"] == result.reason


def test_gate_no_reply_is_reviewed_three_times_and_can_be_overturned() -> None:
    transport = SequencedReviewTransport(
        gate="no_reply",
        votes=[
            "confirm_no_reply",
            "require_claim_free_reply",
            "require_claim_free_reply",
        ],
    )
    result = run("I disagree: liberty also requires institutions.", transport)
    assert result.status == "approved"
    review_calls = [call for call in transport.calls if call["stage"].startswith("reply_necessity_")]
    assert len(review_calls) == 3
    assert all(call["reasoning_effort"] == "medium" for call in review_calls)


def test_candidate_backed_gate_output_is_never_sent_downstream() -> None:
    transport = Transport()
    result = run("A civil criticism of the account's interpretation.", transport)
    assert result.status == "approved"
    assert all(
        "PRIVATE GATE CANDIDATE" not in str(call["payload"])
        for call in transport.calls[1:]
    )


NO_REPLY_ALLEGATIONS = {
    "synthetic-13b": "Minister Rowan privatised it because his friends paid him.",
    "synthetic-14b": "Climate science is a global fraud run by the scientists at the Northbridge Institute.",
    "synthetic-16b": "SimCity proves the planners are lizards. Wake up.",
    "synthetic-17b": "The Chancellor secretly owns the firms that will profit from this tax rise.",
    "synthetic-18b": "Arcadia Bank coordinates in secret with the government to enslave families through debt.",
    "synthetic-21b": "The Northbridge mayor is deliberately creating shortages to punish tenants.",
    "synthetic-26a": "Minister Rowan is part of a trafficking ring.",
}
REPLY_CONTROLS = {
    "synthetic-13a": "Privatisation can create private monopolies unless regulation is strong.",
    "synthetic-14a": "Climate policy should be judged by costs as well as intentions.",
    "synthetic-16a": "This reminds me of SimCity: every intervention creates another queue.",
    "synthetic-17a": "This tax rise will destroy every small business.",
    "synthetic-18a": "Loose credit pushes families towards debt and expensive housing.",
    "synthetic-21a": "Rent controls always make housing shortages worse.",
    "synthetic-24b": "This account overstates her economic record.",
    "synthetic-26b": "Was Minister Rowan ever charged with the offence alleged in this post?",
    "synthetic-27b": "Why was she unwilling to turn?",
}


@pytest.mark.parametrize(("case_id", "text"), NO_REPLY_ALLEGATIONS.items())
def test_named_allegation_sanity_no_reply_candidates(case_id: str, text: str) -> None:
    assert pipeline.allegation_review_candidate(context(text))["candidate"], case_id


@pytest.mark.parametrize(("case_id", "text"), REPLY_CONTROLS.items())
def test_named_allegation_sanity_reply_controls(case_id: str, text: str) -> None:
    assert not pipeline.allegation_review_candidate(context(text))["candidate"], case_id


def test_named_allegation_invokes_three_call_review_and_can_suppress() -> None:
    transport = SequencedReviewTransport(
        gate="reply",
        votes=[
            "confirm_no_reply",
            "require_claim_free_reply",
            "confirm_no_reply",
        ],
    )
    result = run(NO_REPLY_ALLEGATIONS["synthetic-17b"], transport)
    assert result.status == "no_reply"
    assert result.reason == "allegation_review_suppression"
    assert len([call for call in transport.calls if call["stage"].startswith("allegation_review_")]) == 3


class SequencedReviewTransport(Transport):
    def __init__(self, *, gate: str, votes: list[str | None]):
        super().__init__(gate=gate)
        self.votes = list(votes)

    def __call__(self, **kwargs):
        stage = kwargs["stage"]
        if stage.startswith("reply_necessity_") or stage.startswith("allegation_review_"):
            self.calls.append(kwargs)
            vote = self.votes.pop(0)
            return {"outcome": vote} if vote is not None else {"malformed": True}
        return super().__call__(**kwargs)


class FamilyReviewTransport(Transport):
    def __init__(
        self,
        *,
        family: str,
        votes: list[object],
        gate: str,
        writer: str = "Thank you — that is kind of you.",
    ):
        super().__init__(gate=gate, writer=writer)
        self.family = family
        self.votes = list(votes)

    def __call__(self, **kwargs):
        stage = kwargs["stage"]
        if stage.startswith(f"{self.family}_"):
            self.calls.append(kwargs)
            if not self.votes:
                raise AssertionError(f"unexpected reviewer call {stage}")
            vote = self.votes.pop(0)
            if isinstance(vote, BaseException):
                raise vote
            return {"outcome": vote} if vote is not None else {"malformed": True}
        return super().__call__(**kwargs)


REVIEW_FAMILY_CASES = [
    pytest.param(
        "reply_necessity",
        "no_reply",
        "I disagree: liberty also requires institutions.",
        False,
        "confirm_no_reply",
        "require_claim_free_reply",
        "reply_necessity_resolution",
        "reply_necessity_review",
        "reply_necessity_invalid_calls",
        id="reply-necessity",
    ),
    pytest.param(
        "allegation_review",
        "reply",
        NO_REPLY_ALLEGATIONS["synthetic-17b"],
        False,
        "confirm_no_reply",
        "require_claim_free_reply",
        "allegation_conspiracy_resolution",
        "allegation_review_suppression",
        "allegation_conspiracy_invalid_calls",
        id="allegation-review",
    ),
    pytest.param(
        "authentication_review",
        "reply",
        "Did Margaret Thatcher really say this?",
        False,
        "suppress_unsupported_authentication",
        "require_supported_factual_reply",
        "authentication_resolution",
        "unsupported_authentication_suppression",
        None,
        id="authentication-review",
    ),
]


@pytest.mark.parametrize(
    (
        "family",
        "gate",
        "text",
        "facts",
        "winning_vote",
        "opposing_vote",
        "resolution_stage",
        "expected_reason",
        "invalid_field",
    ),
    REVIEW_FAMILY_CASES,
)
def test_matching_first_two_valid_votes_short_circuit_each_majority_family(
    family: str,
    gate: str,
    text: str,
    facts: bool,
    winning_vote: str,
    opposing_vote: str,
    resolution_stage: str,
    expected_reason: str,
    invalid_field: str | None,
) -> None:
    del opposing_vote, invalid_field
    transport = FamilyReviewTransport(
        family=family,
        gate=gate,
        votes=[winning_vote, winning_vote],
    )

    result = run(text, transport, facts=facts)

    reviewer_stages = [
        call["stage"]
        for call in transport.calls
        if call["stage"].startswith(f"{family}_")
    ]
    assert reviewer_stages == [f"{family}_1", f"{family}_2"]
    assert result.status == "no_reply"
    assert result.reason == expected_reason
    assert result.model_call_count == 3
    assert result.model_call_count == len(transport.calls)
    resolution = next(
        row for row in result.audit if row["stage"] == resolution_stage
    )
    assert resolution["majority_outcome"] == winning_vote
    assert resolution["call_outcomes"] == [winning_vote, winning_vote]
    assert pipeline.stage_telemetry(result.audit)[
        "majority_review_summaries"
    ] == [{
        "family": family,
        "reviewer_calls_attempted": 2,
        "valid_votes_obtained": 2,
        "first_two_valid_votes_agreed": True,
        "reviewer_3_called": False,
        "reviewer_3_skipped_first_two_agreement": True,
    }]
    assert transport.votes == []


@pytest.mark.parametrize(
    (
        "family",
        "gate",
        "text",
        "facts",
        "winning_vote",
        "opposing_vote",
        "resolution_stage",
        "expected_reason",
        "invalid_field",
    ),
    REVIEW_FAMILY_CASES,
)
def test_disagreeing_first_two_votes_call_reviewer_three_and_preserve_majority(
    family: str,
    gate: str,
    text: str,
    facts: bool,
    winning_vote: str,
    opposing_vote: str,
    resolution_stage: str,
    expected_reason: str,
    invalid_field: str | None,
) -> None:
    del invalid_field
    transport = FamilyReviewTransport(
        family=family,
        gate=gate,
        votes=[winning_vote, opposing_vote, winning_vote],
    )

    result = run(text, transport, facts=facts)

    reviewer_stages = [
        call["stage"]
        for call in transport.calls
        if call["stage"].startswith(f"{family}_")
    ]
    assert reviewer_stages == [
        f"{family}_1",
        f"{family}_2",
        f"{family}_3",
    ]
    assert result.status == "no_reply"
    assert result.reason == expected_reason
    assert result.model_call_count == 4
    assert result.model_call_count == len(transport.calls)
    resolution = next(
        row for row in result.audit if row["stage"] == resolution_stage
    )
    assert resolution["majority_outcome"] == winning_vote
    assert resolution["call_outcomes"] == [
        winning_vote,
        opposing_vote,
        winning_vote,
    ]
    assert pipeline.stage_telemetry(result.audit)[
        "majority_review_summaries"
    ] == [{
        "family": family,
        "reviewer_calls_attempted": 3,
        "valid_votes_obtained": 3,
        "first_two_valid_votes_agreed": False,
        "reviewer_3_called": True,
        "reviewer_3_skipped_first_two_agreement": False,
    }]
    assert transport.votes == []


@pytest.mark.parametrize(
    (
        "family",
        "gate",
        "text",
        "facts",
        "winning_vote",
        "opposing_vote",
        "resolution_stage",
        "expected_reason",
        "invalid_field",
    ),
    REVIEW_FAMILY_CASES,
)
def test_invalid_first_vote_calls_reviewer_three_and_preserves_fail_closed_rules(
    family: str,
    gate: str,
    text: str,
    facts: bool,
    winning_vote: str,
    opposing_vote: str,
    resolution_stage: str,
    expected_reason: str,
    invalid_field: str | None,
) -> None:
    del opposing_vote
    transport = FamilyReviewTransport(
        family=family,
        gate=gate,
        votes=[None, winning_vote, winning_vote],
    )

    result = run(text, transport, facts=facts)

    reviewer_stages = [
        call["stage"]
        for call in transport.calls
        if call["stage"].startswith(f"{family}_")
    ]
    assert reviewer_stages == [
        f"{family}_1",
        f"{family}_2",
        f"{family}_3",
    ]
    assert result.status == "no_reply"
    assert result.reason == expected_reason
    assert result.model_call_count == 4
    resolution = next(
        row for row in result.audit if row["stage"] == resolution_stage
    )
    assert resolution["majority_outcome"] == winning_vote
    assert resolution["invalid_or_refused_calls"] == 1
    if invalid_field is not None:
        assert pipeline.stage_telemetry(result.audit)[invalid_field] == 1
    assert pipeline.stage_telemetry(result.audit)[
        "majority_review_summaries"
    ] == [{
        "family": family,
        "reviewer_calls_attempted": 3,
        "valid_votes_obtained": 2,
        "first_two_valid_votes_agreed": False,
        "reviewer_3_called": True,
        "reviewer_3_skipped_first_two_agreement": False,
    }]
    assert transport.votes == []


@pytest.mark.parametrize(
    (
        "family",
        "gate",
        "text",
        "facts",
        "winning_vote",
        "opposing_vote",
        "resolution_stage",
        "expected_reason",
        "invalid_field",
    ),
    REVIEW_FAMILY_CASES,
)
def test_reviewer_transport_timeout_preserves_existing_safe_abort_without_extra_calls(
    family: str,
    gate: str,
    text: str,
    facts: bool,
    winning_vote: str,
    opposing_vote: str,
    resolution_stage: str,
    expected_reason: str,
    invalid_field: str | None,
) -> None:
    del resolution_stage, expected_reason, invalid_field
    timeout = TimeoutError(f"{family} timed out")
    transport = FamilyReviewTransport(
        family=family,
        gate=gate,
        votes=[winning_vote, timeout, opposing_vote],
    )

    with pytest.raises(TimeoutError, match="timed out"):
        run(text, transport, facts=facts)

    assert [
        call["stage"]
        for call in transport.calls
        if call["stage"].startswith(f"{family}_")
    ] == [f"{family}_1", f"{family}_2"]
    assert transport.votes == [opposing_vote]


def test_short_circuit_charges_only_actual_calls_and_keeps_writer_eligible() -> None:
    transport = FamilyReviewTransport(
        family="reply_necessity",
        gate="no_reply",
        votes=["require_claim_free_reply", "require_claim_free_reply"],
    )
    config = enabled_config()
    config["maximum_model_calls"] = 12

    result = pipeline.run_reply_pipeline(
        context=context("I disagree: liberty also requires institutions."),
        config=config,
        repository=Repository(),
        transport=transport,
        maximum_reply_length=270,
    )

    assert result.status == "approved"
    assert [call["stage"] for call in transport.calls] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
        "reply_necessity_2",
        "writer_v3_initial",
    ]
    assert result.model_call_count == len(transport.calls) == 4
    assert result.model_call_count <= config["maximum_model_calls"]


def test_interruption_before_reviewer_three_does_not_log_a_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    transport = FamilyReviewTransport(
        family="reply_necessity",
        gate="no_reply",
        votes=[
            "confirm_no_reply",
            "require_claim_free_reply",
            bot.RemoteOperationsPaused("paused before reviewer 3 completed"),
        ],
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled_config())
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(bot, "tested_pipeline_structured_call", transport)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    with pytest.raises(bot.RemoteOperationsPaused, match="reviewer 3"):
        bot.generate_ai_first_reply(
            context("I disagree: liberty also requires institutions."),
            evaluation_outcome={},
        )

    assert [
        call["stage"]
        for call in transport.calls
        if call["stage"].startswith("reply_necessity_")
    ] == ["reply_necessity_1", "reply_necessity_2", "reply_necessity_3"]
    assert not any(
        name == "ai_reply_pipeline_stage_summary" for name, _values in events
    )
    assert pipeline.stage_telemetry((
        {"stage": "reply_necessity_1", "provider": "OpenAI", "schema_valid": True},
        {"stage": "reply_necessity_2", "provider": "OpenAI", "schema_valid": True},
    ))["majority_review_summaries"] == []


@pytest.mark.parametrize(
    ("gate", "text", "reason", "outcome_field", "resolvable_field", "invalid_field"),
    [
        (
            "no_reply",
            "I disagree: liberty also requires institutions.",
            "reply_necessity_review",
            "reply_necessity_outcome",
            "reply_necessity_majority_resolvable",
            "reply_necessity_invalid_calls",
        ),
        (
            "reply",
            NO_REPLY_ALLEGATIONS["synthetic-17b"],
            "allegation_review_suppression",
            "allegation_conspiracy_outcome",
            "allegation_conspiracy_majority_resolvable",
            "allegation_conspiracy_invalid_calls",
        ),
    ],
    ids=["reply-necessity", "allegation-review"],
)
@pytest.mark.parametrize(
    ("votes", "expected_outcome", "expected_resolvable", "expected_invalid"),
    [
        (
            [
                "confirm_no_reply_spam_or_abuse",
                "confirm_no_reply",
                "require_claim_free_reply",
            ],
            "confirm_no_reply",
            False,
            0,
        ),
        (
            ["confirm_no_reply_spam_or_abuse", "confirm_no_reply", None],
            "confirm_no_reply",
            False,
            1,
        ),
        (
            ["confirm_no_reply_spam_or_abuse", None, "confirm_no_reply_spam_or_abuse"],
            "confirm_no_reply_spam_or_abuse",
            True,
            1,
        ),
    ],
    ids=["three-way-split", "one-invalid-one-one", "one-invalid-two-punitive"],
)
def test_review_resolution_distinguishes_non_punitive_fallback_from_punitive_majority(
    gate: str,
    text: str,
    reason: str,
    outcome_field: str,
    resolvable_field: str,
    invalid_field: str,
    votes: list[str | None],
    expected_outcome: str,
    expected_resolvable: bool,
    expected_invalid: int,
) -> None:
    import mrsMThatcher2 as bot

    transport = SequencedReviewTransport(gate=gate, votes=votes)

    result = run(text, transport)
    telemetry = pipeline.stage_telemetry(result.audit)

    assert result.status == "no_reply"
    assert result.reason == reason
    assert telemetry[outcome_field] == expected_outcome
    assert telemetry[resolvable_field] is expected_resolvable
    assert telemetry[invalid_field] == expected_invalid
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status=result.status,
        reason=result.reason,
        telemetry=telemetry,
    ) is (
        expected_outcome == "confirm_no_reply_spam_or_abuse"
        and expected_resolvable
    )
    assert transport.votes == []


def test_group_hostility_candidate_invokes_narrow_review() -> None:
    text = "Those people can never belong in our community."
    assert pipeline.group_hostility_review_candidate(context(text))["candidate"]
    transport = Transport()
    result = run(text, transport)
    assert result.status == "approved"
    assert any(call["stage"] == "focused_group_review" for call in transport.calls)


def test_group_hostility_review_can_suppress() -> None:
    transport = Transport()

    def suppressing_call(**kwargs):
        if kwargs["stage"] == "focused_group_review":
            transport.calls.append(kwargs)
            return {"outcome": "suppress_group_hostility"}
        return transport(**kwargs)

    result = pipeline.run_reply_pipeline(
        context=context("Those people can never belong in our community."),
        config=enabled_config(), repository=Repository(), transport=suppressing_call,
        maximum_reply_length=270,
    )
    assert result.status == "no_reply"
    assert result.reason == "group_hostility_suppression"


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("Did Margaret Thatcher really say this?", "direct_authentication"),
        ("Leaving aside the attribution, what principle matters here?", "premise_neutral_comparison"),
        ("What does this quotation mean?", "meaning_only"),
        ("Policy should reward responsibility.", "none"),
    ],
)
def test_attribution_route_v2(text: str, route: str) -> None:
    assert pipeline.attribution_route_v2(context(text), [])["route_class"] == route


def test_supported_direct_authentication_uses_three_sol_calls_and_factual_audit() -> None:
    transport = FamilyReviewTransport(
        family="authentication_review",
        gate="reply",
        votes=[
            "require_supported_factual_reply",
            "suppress_unsupported_authentication",
            "require_supported_factual_reply",
        ],
        writer="The local transcript records those exact words.",
    )
    result = run("Did Margaret Thatcher really say this?", transport, facts=True)
    assert result.status == "approved"
    assert len([call for call in transport.calls if call["stage"].startswith("authentication_review_")]) == 3
    assert any(call["stage"] == "narrow_claim_audit" for call in transport.calls)


def test_claim_risk_and_exact_duplicate_repair() -> None:
    risk = pipeline.detect_claim_risk("It was introduced in 1979.", "general")
    assert risk["categories"] == ["numeric_or_date", "precise_historical_claim"]
    transport = Transport()
    result = run("Thank you.", transport, recent=["Thank you — that is kind of you."])
    assert result.status == "approved"
    assert str(result.reply) == "A fresh formulation keeps the point clear."
    assert any(call["stage"] == "exact_duplicate_repair" for call in transport.calls)


def test_narrow_claim_cleanup_is_bounded_and_reaudited() -> None:
    class CleanupTransport(Transport):
        def __call__(self, **kwargs):
            stage = kwargs["stage"]
            if stage == "narrow_claim_audit":
                self.calls.append(kwargs)
                return {"outcome": "rewrite_claim_free"}
            if stage == "bounded_claim_cleanup":
                self.calls.append(kwargs)
                return {"status": "reply", "reply": "The principle is responsibility rather than privilege."}
            return super().__call__(**kwargs)

    transport = CleanupTransport(writer="It was introduced in 1979.")
    result = run("What principle should guide the policy?", transport)
    assert result.status == "approved"
    assert str(result.reply) == "The principle is responsibility rather than privilege."
    assert [call["stage"] for call in transport.calls].count("bounded_claim_cleanup") == 1
    assert any(call["stage"] == "cleanup_claim_audit" for call in transport.calls) is False
    # The cleaned prose contains no deterministic risk cue, so the second audit is
    # a local bypass rather than another provider call.
    assert any(row["stage"] == "cleanup_claim_audit_risk" for row in result.audit)


def test_persisted_tested_draft_revalidates_and_rejects_new_exact_duplicate() -> None:
    transport = Transport()
    result = run("Thank you.", transport)
    record = pipeline.validate_persisted_draft(
        result.reply.draft_record,
        context=context("Thank you."),
        config=enabled_config(),
        repository=Repository(),
        maximum_reply_length=270,
    )
    assert record["proposed_reply"] == str(result.reply)
    with pytest.raises(ValueError, match="exact duplicate"):
        pipeline.validate_persisted_draft(
            record,
            context=context("Thank you."),
            config=enabled_config(),
            repository=Repository(),
            maximum_reply_length=270,
            recent_replies=[str(result.reply)],
        )


def test_persisted_tested_draft_applies_three_sentence_ceiling() -> None:
    three_sentences = "Thank you. That is kind. Much appreciated."
    result = run("Thank you.", Transport(writer=three_sentences))

    record = pipeline.validate_persisted_draft(
        result.reply.draft_record,
        context=context("Thank you."),
        config=enabled_config(),
        repository=Repository(),
        maximum_reply_length=270,
    )
    assert record["proposed_reply"] == three_sentences

    four_sentence_record = copy.deepcopy(record)
    four_sentence_record["proposed_reply"] = (
        "Thank you. That is kind. Much appreciated. Good wishes."
    )
    four_sentence_record.pop("approval_hash")
    four_sentence_record["approval_hash"] = pipeline._hash_value(
        four_sentence_record
    )
    with pytest.raises(
        ValueError,
        match="persisted tested-pipeline reply fails deterministic validation",
    ):
        pipeline.validate_persisted_draft(
            four_sentence_record,
            context=context("Thank you."),
            config=enabled_config(),
            repository=Repository(),
            maximum_reply_length=270,
        )


def test_persisted_tested_draft_requires_current_contract_identity() -> None:
    result = run("Thank you.", Transport())
    current_record = result.reply.draft_record

    assert current_record["strategy_version"] == "tested-reply-pipeline-20260817"
    assert pipeline.validate_persisted_draft(
        current_record,
        context=context("Thank you."),
        config=enabled_config(),
        repository=Repository(),
        maximum_reply_length=270,
    )["strategy_version"] == pipeline.STRATEGY_VERSION

    old_record = dict(current_record)
    old_record["strategy_version"] = "tested-reply-pipeline-20260816"
    old_record.pop("approval_hash")
    old_record["approval_hash"] = pipeline._hash_value(old_record)
    with pytest.raises(ValueError, match="draft version is unsupported"):
        pipeline.validate_persisted_draft(
            old_record,
            context=context("Thank you."),
            config=enabled_config(),
            repository=Repository(),
            maximum_reply_length=270,
        )


@pytest.mark.parametrize(
    ("stage", "response_schema", "response_content"),
    [
        pytest.param(
            "writer_v3_initial",
            pipeline.WRITER_SCHEMA,
            '{"status":"reply","reply":"A bounded reply."}',
            id="writer",
        ),
        pytest.param(
            "reply_necessity_1",
            pipeline.REPLY_NECESSITY_SCHEMA,
            '{"outcome":"confirm_no_reply"}',
            id="reviewer",
        ),
    ],
)
def test_openai_reply_pipeline_requests_use_explicit_cache_mode_without_breakpoint(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    response_schema: dict,
    response_content: str,
) -> None:
    import mrsMThatcher2 as bot

    captured = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": response_content}}]}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda _operation: None)
    monkeypatch.setattr(bot, "OPENAI_BASE", "https://openai.invalid/v1")
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "set-in-test")
    visible = {"context": context("A visible contribution."), "trusted_facts": [], "media_context": []}
    result = bot.tested_pipeline_structured_call(
        provider="OpenAI", stage=stage, model="gpt-5.6-sol",
        system_prompt="Frozen prompt", payload=visible,
        response_schema=response_schema, timeout_seconds=180,
        max_output_tokens=300, reasoning_effort="medium",
    )
    assert result == response_content
    assert captured["url"] == "https://openai.invalid/v1/chat/completions"
    assert captured["timeout"] == 180
    request = captured["json"]
    assert json.loads(request["messages"][1]["content"]) == visible
    assert request["model"] == "gpt-5.6-sol"
    assert request["messages"][0] == {"role": "system", "content": "Frozen prompt"}
    assert request["reasoning_effort"] == "medium"
    assert request["temperature"] == 1
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "mrs_tested_" + stage,
            "strict": True,
            "schema": response_schema,
        },
    }
    assert request["max_completion_tokens"] == 300
    assert request["store"] is False
    assert request["prompt_cache_options"] == {"mode": "explicit"}
    assert set(request) == {
        "model",
        "messages",
        "reasoning_effort",
        "temperature",
        "response_format",
        "max_completion_tokens",
        "store",
        "prompt_cache_options",
    }
    assert "cache_control" not in json.dumps(request, sort_keys=True)
    assert "prompt_cache_key" not in request
    assert "tools" not in request and "search" not in request


def test_xai_reply_pipeline_request_is_unchanged_by_cache_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mrsMThatcher2 as bot

    captured = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [{
                    "message": {
                        "content": '{"decision":"no_reply","reply":""}'
                    }
                }]
            }

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot, "require_remote_operation_unpaused", lambda _operation: None
    )
    monkeypatch.setattr(bot, "XAI_BASE", "https://xai.invalid/v1")
    monkeypatch.setattr(bot, "XAI_API_KEY", "set-in-test")
    visible = {
        "context": context("A visible contribution."),
        "trusted_facts": [],
        "media_context": [],
    }

    result = bot.tested_pipeline_structured_call(
        provider="xAI",
        stage="candidate_backed_engagement",
        model="grok-4.3",
        system_prompt="Frozen prompt",
        payload=visible,
        response_schema=pipeline.GATE_SCHEMA,
        timeout_seconds=180,
        max_output_tokens=900,
        reasoning_effort="low",
    )

    assert result == '{"decision":"no_reply","reply":""}'
    assert captured["url"] == "https://xai.invalid/v1/chat/completions"
    request = captured["json"]
    assert request["max_tokens"] == 900
    assert "max_completion_tokens" not in request
    assert "store" not in request
    assert "prompt_cache_options" not in request
    assert "cache_control" not in json.dumps(request, sort_keys=True)


def test_production_wrapper_logs_safe_tested_pipeline_stage_summary(monkeypatch) -> None:
    import copy
    import mrsMThatcher2 as bot

    private_marker = "PRIVATE PIPELINE TEXT MUST NOT BE LOGGED"
    config = copy.deepcopy(pipeline.default_config())
    config["enabled"] = True
    events = []
    result = pipeline.PipelineResult(
        None,
        "no_reply",
        "reply_necessity_review",
        4,
        0,
        (
            {"stage": "A_B_C", "suppressed": False, "reason": None},
            {"stage": "candidate_backed_engagement", "provider": "xAI", "schema_valid": True},
            {"stage": "xai_gate_decision", "decision": "no_reply", "reply": private_marker},
            {"stage": "reply_necessity_1", "provider": "OpenAI", "schema_valid": True},
            {"stage": "reply_necessity_2", "provider": "OpenAI", "schema_valid": True},
            {"stage": "reply_necessity_3", "provider": "OpenAI", "schema_valid": True},
            {
                "stage": "reply_necessity_resolution",
                "majority_outcome": "confirm_no_reply",
                "majority_resolvable": True,
                "invalid_or_refused_calls": 0,
                "reviewer_calls_attempted": 3,
                "valid_votes_obtained": 3,
                "first_two_valid_votes_agreed": False,
                "reviewer_3_called": True,
                "reviewer_3_skipped_first_two_agreement": False,
                "reason": private_marker,
            },
        ),
    )
    monkeypatch.setattr(bot, "tested_reply_pipeline", config)
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(pipeline, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    evaluation_outcome: dict[str, object] = {}
    assert bot.generate_ai_first_reply(
        context("A visible contribution."),
        evaluation_outcome=evaluation_outcome,
    ) is None

    assert [name for name, _values in events] == [
        "ai_reply_pipeline_stage_summary",
        "ai_reply_pipeline_decision",
    ]
    summary = events[0][1]
    assert summary["provider_call_counts"] == {"xAI": 1, "OpenAI": 3}
    assert summary["xai_gate_decision"] == "no_reply"
    assert summary["reply_necessity_outcome"] == "confirm_no_reply"
    assert summary["reply_necessity_majority_resolvable"] is True
    assert summary["majority_review_summaries"] == [{
        "family": "reply_necessity",
        "reviewer_calls_attempted": 3,
        "valid_votes_obtained": 3,
        "first_two_valid_votes_agreed": False,
        "reviewer_3_called": True,
        "reviewer_3_skipped_first_two_agreement": False,
    }]
    assert summary["terminal_reason"] == "reply_necessity_review"
    assert private_marker not in json.dumps(summary, sort_keys=True)
    decision = events[1][1]
    assert decision["final_reply_kind"] == "no_reply"
    assert decision["tone"] == "unknown"
    assert decision["used_fact_count"] == 0
    assert decision["trusted_facts_supplied_count"] == 0
    assert evaluation_outcome["qualifying_author_no_reply"] is False
    assert evaluation_outcome["corroborating_author_no_reply"] is True


def test_production_decision_logs_routing_kind_and_unknown_fact_use(monkeypatch) -> None:
    import copy
    import mrsMThatcher2 as bot

    config = copy.deepcopy(pipeline.default_config())
    config["enabled"] = True
    result = run("Thank you!", Transport())
    events = []
    monkeypatch.setattr(bot, "tested_reply_pipeline", config)
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(pipeline, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    assert bot.generate_ai_first_reply(context("Thank you!")) == result.reply

    decision = events[-1][1]
    assert decision["final_reply_kind"] == "unknown"
    assert decision["reply_requirement"] == "general"
    assert decision["route_source"] == "xai_gate"
    assert decision["tone"] == "unknown"
    assert decision["trusted_facts_supplied_count"] == 0
    assert decision["trusted_fact_ids_supplied"] == []
    assert decision["used_fact_count"] == "unknown"
    assert decision["used_fact_ids"] is None
