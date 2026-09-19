"""Supply deterministic evidence, model responses and HTTP doubles for reply tests."""

from __future__ import annotations

import copy
import json
import io
from PIL import Image
from dataclasses import dataclass

import single_call_reply as pipeline


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
        trusted_only: bool = False,
    ) -> list[FakePassage]:
        """Return bounded passages and retain the requested limits."""

        assert preferred_quote_id is None
        assert trusted_only is True
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
        "target_author_id": "200",
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
            "factual_claims": ([{"text": reply, "fact_ids": facts}] if facts else []),
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
                "role": "assistant",
                "status": "completed",
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


class FakeHttpResponse:
    """Expose the small requests.Response surface used by reply transports."""

    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        body: object = None,
    ) -> None:
        """Create a response with fresh metadata and a controllable JSON body."""
        self.status_code = status_code
        self.headers = headers or {}
        self._body = {} if body is None else body
        self.closed = False

    def json(self) -> object:
        """Return a defensive copy of the configured response body."""
        return copy.deepcopy(self._body)

    def iter_content(self, *, chunk_size: int):
        """Yield deterministic image bytes for media-download tests."""
        del chunk_size
        yield valid_png()

    def close(self) -> None:
        """Record that the response was closed."""
        self.closed = True


def valid_png() -> bytes:
    """Encode a complete small image instead of a signature-only placeholder."""
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    return stream.getvalue()


def valid_jpeg() -> bytes:
    """Encode a complete small JPEG for multimodal request fixtures."""
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="JPEG")
    return stream.getvalue()
