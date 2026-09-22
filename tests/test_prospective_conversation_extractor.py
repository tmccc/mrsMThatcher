from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from tools import extract_prospective_conversations as extractor
import mrs_log_digest as digest
from mrs_bot_reply_model_transport import ReplyModelTransport
from mrs_provider_request_records import (
    InvalidRequestRecord,
    read_provider_request_record,
    record_provider_request,
)


BOUNDARY = "2026-08-24T15:08:39Z"
DEFAULT_UNTIL = "2026-08-27T18:00:00Z"


def snowflake_id(value: str | datetime, sequence: int = 0) -> str:
    instant = (
        extractor.parse_aware_timestamp(value, option="test snowflake")
        if isinstance(value, str)
        else value.astimezone(timezone.utc)
    )
    milliseconds = int(instant.timestamp() * 1000)
    return str(
        ((milliseconds - extractor.X_SNOWFLAKE_EPOCH_MS) << 22)
        | (sequence & ((1 << 22) - 1))
    )


def local_minute_as_utc(local_prefix: str) -> str:
    value = datetime.strptime(local_prefix, "%Y-%m-%d %H:%M").replace(
        tzinfo=ZoneInfo("Europe/London")
    )
    return str(extractor.format_utc(value.astimezone(timezone.utc)))


def log_line(
    local_time: str,
    message: str,
    *,
    source: str = "mrsMThatcher2.test",
    level: str = "INFO",
) -> str:
    return f"{local_time} {level} {source}:1 - {message}\n"


def event_line(local_time: str, event: str, **fields: object) -> str:
    payload = json.dumps(
        {"event": event, **fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return log_line(local_time, f"EVENT {payload}")


def reply_media_context_line(
    local_time: str,
    target_id: str,
    *,
    lane: str = "mention",
    photo_count: int = 2,
    status: str = "supplied",
) -> str:
    unavailable = " unavailable" if status == "unavailable" else ""
    count_field = "photos_expected" if status == "unavailable" else "photos"
    return log_line(
        local_time,
        f"Reply media context{unavailable} lane={lane} target_id={target_id} "
        f"{count_field}={photo_count} mode=multimodal status={status}",
        level="WARNING" if status == "unavailable" else "INFO",
    )


def reply_visual_description_line(
    local_time: str,
    target_id: str,
    *,
    lane: str = "mention",
    status: str = "analysed",
    supplied_image_count: object = 2,
    description_sha256: object | None = None,
    visual_analysis_call_count: object | None = None,
    **extra: object,
) -> str:
    if description_sha256 is None:
        description_sha256 = "a" * 64 if status == "analysed" else ""
    if visual_analysis_call_count is None:
        visual_analysis_call_count = (
            1 if status in {"analysed", "provider_error", "invalid_response"} else 0
        )
    return event_line(
        local_time,
        "reply_visual_description",
        lane=lane,
        target_id=target_id,
        supplied_image_count=supplied_image_count,
        status=status,
        analysis_schema_version=1,
        description_sha256=description_sha256,
        visual_analysis_call_count=visual_analysis_call_count,
        **extra,
    )


def account_root_posted(
    post_id: str,
    *,
    created_at: str,
    local_time: str = "2026-08-25 00:00:00",
    public_text: str | None = "A confirmed account root.",
    quote_text: str | None = None,
    image_summary: str | None = None,
    lane: str = "quote_image",
) -> str:
    if public_text:
        visible_text = public_text
        visible_source = "public_text"
    elif quote_text:
        visible_text = quote_text
        visible_source = "image_quote_text"
    elif image_summary:
        visible_text = image_summary
        visible_source = "image_summary"
    else:
        visible_text = None
        visible_source = "unavailable"
    return event_line(
        local_time,
        "account_root_posted",
        event_version=1,
        lane=lane,
        post_id=post_id,
        root_post_id=post_id,
        conversation_id=post_id,
        public_text=public_text,
        visible_text=visible_text,
        visible_text_source=visible_source,
        quote_id="a" * 64,
        quote_text=quote_text,
        image_summary=image_summary,
        post_created_at=created_at,
        publication_authority="confirmed_transport",
    )


def historical_context_posted(
    parent_id: str,
    reply_id: str,
    *,
    created_at: str,
    local_time: str = "2026-08-25 00:00:01",
    reply_text: str | None = "Context — a confirmed historical note.",
) -> str:
    return event_line(
        local_time,
        "historical_context_reply_posted",
        event_version=1,
        lane="historical_context_reply",
        parent_post_id=parent_id,
        reply_post_id=reply_id,
        root_post_id=parent_id,
        conversation_id=parent_id,
        reply_text=reply_text,
        quote_id="a" * 64,
        reply_created_at=created_at,
        publication_authority="confirmed_transport",
    )


def legacy_account_pair(
    root_id: str,
    context_id: str,
    *,
    local_prefix: str = "2026-08-25 00:08",
    quote_id: str = "e" * 64,
) -> str:
    """Literal, identity-redacted form of the audited retained sequence."""
    attempt_id = "b" * 64
    root_transaction = "c" * 64
    context_transaction = "d" * 64
    root_text = "A source-faithful visible quotation."
    context_text = "Context — verified historical context."
    return "".join(
        [
            log_line(
                f"{local_prefix}:00",
                "Wrote main-post sending receipt lane=quote_image "
                f"attempt_id={attempt_id} path=<private-path>",
                level="WARNING",
            ),
            log_line(
                f"{local_prefix}:01",
                "Promoted main-post receipt to attempting lane=quote_image "
                f"attempt_id={attempt_id} path=<private-path>",
                level="WARNING",
            ),
            create_attempt(
                root_id,
                root_transaction,
                lane="quote_image",
                text=root_text,
                local_time=f"{local_prefix}:02",
            ),
            generic_success(root_id, local_time=f"{local_prefix}:03"),
            log_line(
                f"{local_prefix}:04",
                "Promoted main-post attempt to confirmed pending-schedule receipt "
                f"lane=quote_image attempt_id={attempt_id} post_id={root_id} "
                "path=<private-path>",
                level="WARNING",
            ),
            log_line(
                f"{local_prefix}:05",
                "Finalised confirmed pending-schedule receipt "
                f"lane=quote_image post_id={root_id} path=<private-path>",
                level="WARNING",
            ),
            event_line(
                f"{local_prefix}:06",
                "main_post_posted",
                lane="quote_image",
                post_id=root_id,
                quote_hash=quote_id,
                image_basename="redacted-image.jpg",
            ),
            create_attempt(
                root_id,
                context_transaction,
                lane="historical_context_reply",
                text=context_text,
                local_time=f"{local_prefix}:07",
            ),
            generic_success(context_id, local_time=f"{local_prefix}:08"),
            event_line(
                f"{local_prefix}:09",
                "historical_context_reply",
                status="completed",
                parent_post_id=root_id,
                quote_id=quote_id,
                reply_preview=context_text,
            ),
            event_line(
                f"{local_prefix}:10",
                "historical_context_obligation",
                status="completed",
                context_reply_state="context_reply_confirmed",
                parent_post_id=root_id,
                attempt_number=1,
            ),
        ]
    )


def legacy_main_publication(
    root_id: str,
    *,
    lane: str = "quote_image",
    text: str = "A source-faithful visible quotation.",
    local_prefix: str = "2026-08-25 00:18",
    quote_id: str = "a" * 64,
) -> str:
    attempt_id = "6" * 64
    transaction_id = "7" * 64
    return "".join(
        [
            log_line(
                f"{local_prefix}:00",
                f"Wrote main-post sending receipt lane={lane} "
                f"attempt_id={attempt_id} path=<private-path>",
                level="WARNING",
            ),
            log_line(
                f"{local_prefix}:01",
                f"Promoted main-post receipt to attempting lane={lane} "
                f"attempt_id={attempt_id} path=<private-path>",
                level="WARNING",
            ),
            create_attempt(
                root_id,
                transaction_id,
                lane=lane,
                text=text,
                local_time=f"{local_prefix}:02",
            ),
            generic_success(root_id, local_time=f"{local_prefix}:03"),
            log_line(
                f"{local_prefix}:04",
                "Promoted main-post attempt to confirmed pending-schedule receipt "
                f"lane={lane} attempt_id={attempt_id} post_id={root_id} "
                "path=<private-path>",
                level="WARNING",
            ),
            log_line(
                f"{local_prefix}:05",
                "Finalised confirmed pending-schedule receipt "
                f"lane={lane} post_id={root_id} path=<private-path>",
                level="WARNING",
            ),
            event_line(
                f"{local_prefix}:06",
                "main_post_posted",
                lane=lane,
                post_id=root_id,
                quote_hash=quote_id,
                image_basename="redacted-image.jpg",
            ),
        ]
    )


def branch_turn(
    post_id: str,
    parent_post_id: str | None,
    author_role: str,
    author_key: str,
    minute: int,
    *,
    text: str | None = "Substantive branch turn.",
    correction_cues: list[str] | None = None,
    lane: str = "mention",
    clarification: bool = False,
) -> dict[str, object]:
    return {
        "account_turn_asked_for_clarification": clarification,
        "author_key": author_key,
        "author_role": author_role,
        "correction_cues": list(correction_cues or []),
        "created_at": f"2026-08-25T00:{minute:02d}:00Z",
        "lane": lane,
        "parent_post_id": parent_post_id,
        "pipeline_stage_summaries": [],
        "post_id": post_id,
        "reconstruction_confidence": "high",
        "source_provenance": [],
        "substantive": bool(text),
        "text": text,
        "text_source": "public_text" if text else "unavailable",
        "warnings": [],
    }


def branch_conversation(
    turns: list[dict[str, object]], *, key: str
) -> dict[str, object]:
    return {
        "activity_status": "quiescent",
        "completeness": "complete",
        "conversation_key": key,
        "prospective_status": "eligible",
        "reconstruction_confidence": "high",
        "root_post_id": "root",
        "start_time": "2026-08-25T00:00:00Z",
        "turns": turns,
        "warnings": [],
    }


def first_exchange(
    *,
    author_id: str = "raw-user-123",
    root_id: str = "100",
    reply_id: str = "101",
    local_prefix: str = "2026-08-24 16:10",
) -> str:
    minute = local_prefix
    return "".join(
        [
            log_line(f"{minute}:00", f"Considering mention id={root_id} author_id={author_id} text='A meaningful first question?'"),
            log_line(f"{minute}:01", f"Built AI reply context for mention {root_id}. chain_items=0 immediate_parent=None quoted=False"),
            event_line(
                f"{minute}:02",
                "ai_reply_pipeline_stage_summary",
                lane="mention",
                target_id=root_id,
                status="approved",
                reply_requirement="answer_question",
                route_source="tested_pipeline",
                trusted_facts_supplied_count=1,
                trusted_fact_ids_supplied=["fact-a"],
                claim_cleanup_called=False,
            ),
            log_line(f"{minute}:03", f"Generated reply to mention {root_id}: 'A published account answer.'"),
            event_line(
                f"{minute}:04",
                "reply_posted",
                lane="mention",
                target_id=root_id,
                author_id=author_id,
                reply_post_id=reply_id,
                target_created_at=local_minute_as_utc(local_prefix),
            ),
        ]
    )


def continuation(
    *,
    author_id: str = "raw-user-123",
    post_id: str = "102",
    parent_id: str = "101",
    text: str = "That is not what I said; my point is different.",
    local_time: str = "2026-08-24 16:20",
) -> str:
    minute = local_time
    return "".join(
        [
            log_line(f"{minute}:00", f"Considering mention id={post_id} author_id={author_id} text={text!r}"),
            log_line(f"{minute}:01", f"Built AI reply context for mention {post_id}. chain_items=2 immediate_parent={parent_id} quoted=False"),
        ]
    )


def publish_reply(
    target_id: str,
    reply_id: str,
    *,
    text: str = "A second published account answer.",
    author_id: str = "raw-user-123",
    local_time: str = "2026-08-24 16:21",
) -> str:
    minute = local_time
    return "".join(
        [
            log_line(f"{minute}:00", f"Generated reply to mention {target_id}: {text!r}"),
            event_line(
                f"{minute}:01",
                "reply_posted",
                lane="mention",
                target_id=target_id,
                author_id=author_id,
                reply_post_id=reply_id,
            ),
        ]
    )


def create_attempt(
    target_id: str,
    transaction_id: str,
    *,
    text: str = "A durable attempted reply.",
    lane: str = "conversational_reply",
    local_time: str = "2026-08-24 16:12:00",
) -> str:
    reply_to = (
        target_id
        if lane in {"conversational_reply", "historical_context_reply"}
        else "None"
    )
    return log_line(
        local_time,
        "Creating X post with durable transport journal. "
        f"lane={lane} transaction_id={transaction_id} reply_to_id={reply_to} "
        f"media_count=0 made_with_ai=False text={text!r}",
    )


def generic_success(
    reply_id: str,
    *,
    local_time: str = "2026-08-24 16:12:01",
) -> str:
    return log_line(
        local_time,
        f"Created X post successfully. response={{'data': {{'id': '{reply_id}'}}}}",
    )


def receipt_promotion(
    target_id: str,
    reply_id: str,
    *,
    local_time: str = "2026-08-24 16:12:02",
) -> str:
    return log_line(
        local_time,
        "Promoted conversational reply receipt to confirmed "
        f"source=mention target_id={target_id} reply_post_id={reply_id} "
        "path=/private/confirmed_reply_receipt.json",
        level="WARNING",
    )


def rewrite_private_json(path: Path, value: object, *, mode: int = 0o400) -> None:
    os.chmod(path, 0o600)
    path.write_bytes(extractor.canonical_json_bytes(value))
    os.chmod(path, mode)


def rewrite_batch_posts_and_hashes(batch: Path, posts: list[dict[str, object]]) -> None:
    canonical = batch / "canonical-posts.jsonl"
    os.chmod(canonical, 0o600)
    canonical.write_bytes(extractor.jsonl_bytes(posts))
    os.chmod(canonical, 0o400)
    manifest_path = batch / "manifest.json"
    manifest = extractor._strict_read_json(manifest_path)
    assert isinstance(manifest, dict)
    hashes = dict(manifest["output_file_hashes"])
    hashes["canonical-posts.jsonl"] = extractor.sha256_file(canonical)
    manifest["output_file_hashes"] = hashes
    manifest["canonical_snapshot_sha256"] = extractor._snapshot_hash(
        hashes["canonical-posts.jsonl"],
        hashes["conversations.jsonl"],
        hashes["review-candidates.jsonl"],
    )
    rewrite_private_json(manifest_path, manifest)


def rewrite_batch_candidates_and_hashes(
    batch: Path, candidates: list[dict[str, object]]
) -> None:
    candidate_path = batch / "review-candidates.jsonl"
    os.chmod(candidate_path, 0o600)
    candidate_path.write_bytes(extractor.jsonl_bytes(candidates))
    os.chmod(candidate_path, 0o400)
    manifest_path = batch / "manifest.json"
    manifest = extractor._strict_read_json(manifest_path)
    assert isinstance(manifest, dict)
    hashes = dict(manifest["output_file_hashes"])
    hashes["review-candidates.jsonl"] = extractor.sha256_file(candidate_path)
    manifest["output_file_hashes"] = hashes
    manifest["canonical_snapshot_sha256"] = extractor._snapshot_hash(
        hashes["canonical-posts.jsonl"],
        hashes["conversations.jsonl"],
        hashes["review-candidates.jsonl"],
    )
    rewrite_private_json(manifest_path, manifest)


def rewrite_batch_conversations_and_hashes(
    batch: Path, conversations: list[dict[str, object]]
) -> None:
    conversation_path = batch / "conversations.jsonl"
    os.chmod(conversation_path, 0o600)
    conversation_path.write_bytes(extractor.jsonl_bytes(conversations))
    os.chmod(conversation_path, 0o400)
    manifest_path = batch / "manifest.json"
    manifest = extractor._strict_read_json(manifest_path)
    assert isinstance(manifest, dict)
    hashes = dict(manifest["output_file_hashes"])
    hashes["conversations.jsonl"] = extractor.sha256_file(conversation_path)
    manifest["output_file_hashes"] = hashes
    manifest["canonical_snapshot_sha256"] = extractor._snapshot_hash(
        hashes["canonical-posts.jsonl"],
        hashes["conversations.jsonl"],
        hashes["review-candidates.jsonl"],
    )
    rewrite_private_json(manifest_path, manifest)


def set_batch_creation_time(batch: Path, value: str) -> None:
    manifest_path = batch / "manifest.json"
    manifest = extractor._strict_read_json(manifest_path)
    assert isinstance(manifest, dict)
    manifest["creation_timestamp"] = value
    rewrite_private_json(manifest_path, manifest)


def append_independent_root(
    active: Path,
    *,
    created_at: str,
    local_time: str,
    author: str,
) -> str:
    post_id = snowflake_id(created_at)
    with active.open("a", encoding="utf-8") as handle:
        handle.write(
            log_line(
                local_time,
                f"Considering mention id={post_id} author_id={author} text='Independent substantive root'",
            )
        )
        handle.write(
            log_line(
                str(datetime.strptime(local_time, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=1)),
                f"Built AI reply context for mention {post_id}. chain_items=0 immediate_parent=None quoted=False",
            )
        )
    return post_id


def write_active(project: Path, text: str) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    path = project / "mrsMThatcher.log"
    path.write_text(text, encoding="utf-8")
    return path


def run_scan(
    project: Path,
    output: Path,
    *,
    until: str = DEFAULT_UNTIL,
    quiescence_hours: float = 48,
) -> dict[str, object]:
    return extractor.run_scan(
        project_dir=project,
        output_root=output,
        prospective_start=BOUNDARY,
        until=until,
        quiescence_hours=quiescence_hours,
    )


def current_batch(output: Path) -> Path:
    return output / os.readlink(output / "current")


def rows(output: Path, name: str) -> list[dict[str, object]]:
    return extractor._load_jsonl(current_batch(output) / name)


def test_exact_provider_request_reaches_batch_and_frozen_review_pack(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    body_value = {
        "instructions": "private multilingual Ω 😀\n\\nliteral " + "z" * 5000 + " END",
        "input": [{"type": "input_image", "image_url": "data:image/png;base64,AAEC/w=="}],
    }
    observed_by_provider: list[bytes] = []

    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ConnectionError(RequestException):
        pass

    response = SimpleNamespace(
        status_code=200,
        headers={},
        json=lambda: {"id": "fixture-provider-response"},
        close=lambda: None,
    )

    def fake_post(*_args: object, **kwargs: object) -> object:
        observed_by_provider.append(bytes(kwargs["data"]))
        return response

    ticks = iter((1.0, 1.01))
    transport = ReplyModelTransport(
        log=Mock(),
        model="fixture-model",
        reasoning_effort="low",
        monotonic=lambda: next(ticks),
        require_remote_operation_unpaused=lambda _operation: None,
        report_bot_health_progress=lambda _boundary: None,
        requests=SimpleNamespace(
            post=fake_post,
            RequestException=RequestException,
            Timeout=Timeout,
            ConnectTimeout=ConnectTimeout,
            ConnectionError=ConnectionError,
        ),
        base_url="https://fixture.invalid/v1",
        api_key="fixture-secret-not-recorded",
        sleep=lambda _seconds: None,
        now_epoch=lambda: 0,
        error_type=RuntimeError,
        request_record_directory=project / "ai-request-records",
    )
    transport_result = transport.call(
        request=body_value,
        timeout_seconds=180,
        lane="mention",
        target_id="100",
    )
    call_id = str(transport_result["call_id"])
    record = read_provider_request_record(
        project / "ai-request-records" / f"{call_id}.json.gz"
    )
    recorded_body = str(record["request_body_utf8"]).encode("utf-8")
    body = observed_by_provider[0]
    digest_rows, _coverage = digest.provider_request_export(
        project / "ai-request-records",
        [{
            "kind": "single_call_reply_decision",
            "time": "2026-09-22 12:00:00",
            "call_id": call_id,
            "lane": "mention",
            "target_id": "100",
            "model_call_count": 1,
            "provider_request_attempt_count": 1,
        }],
        window_start=datetime(2026, 9, 22, 11, 59),
        window_end=datetime(2026, 9, 22, 12, 1),
    )
    digest_body = str(digest_rows[0]["request_body_utf8"]).encode("utf-8")
    write_active(
        project,
        first_exchange()
        + continuation()
        + event_line(
            "2026-08-24 16:20:05",
            "single_call_reply_posting_outcome",
            call_id=call_id,
            lane="mention",
            target_id="100",
            status="confirmed",
            reply_post_id="900",
        ),
    )
    run_scan(project, output, until="2099-01-01T00:00:00Z")
    batch_rows = rows(output, "provider-requests.jsonl")
    assert len(batch_rows) == 1
    batch_body = str(batch_rows[0]["request_body_utf8"]).encode("utf-8")
    assert batch_body == body
    assert batch_rows[0]["association_status"] == "associated_target"
    posts = rows(output, "canonical-posts.jsonl")
    assert posts[0]["provider_call_ids"] == [call_id]

    extractor.freeze_review_pack(
        output_root=output,
        pack_name="exact-input",
        since=BOUNDARY,
        until="2099-01-01T00:00:00Z",
    )
    shutil.rmtree(project / "ai-request-records")
    pack_rows = extractor._load_jsonl(
        output / "review-packs" / "exact-input" / "provider-requests.jsonl"
    )
    pack_body = str(pack_rows[0]["request_body_utf8"]).encode("utf-8")
    expected = hashlib.sha256(body).hexdigest()
    recovered_bodies = (
        body,
        recorded_body,
        digest_body,
        batch_body,
        pack_body,
    )
    assert all(recovered == body for recovered in recovered_bodies)
    assert {hashlib.sha256(recovered).hexdigest() for recovered in recovered_bodies} == {
        expected
    }
    assert b"fixture-secret-not-recorded" not in json.dumps(record).encode("utf-8")
    assert extractor.validate_output_root(output)["valid"] is True


def test_provider_usage_survives_projection_and_aggregates_by_call_identity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    complete_target = snowflake_id("2026-08-24T15:30:00Z")
    absent_target = snowflake_id("2026-08-24T15:31:00Z")
    zero_target = snowflake_id("2026-08-24T15:32:00Z")
    complete_call = "11111111-2222-4333-8444-555555555555"
    absent_call = "22222222-3333-4444-8555-666666666666"
    zero_call = "33333333-4444-4555-8666-777777777777"
    usage = {
        "call_id": complete_call,
        "lane": "mention",
        "target_id": complete_target,
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "provider_response_id": "resp_usage_complete",
        "provider_latency_ms": 1234,
        "request_attempt_count": 2,
        "request_attempt_count_status": "available",
        "input_tokens": 101,
        "cached_input_tokens": 22,
        "cache_write_input_tokens": 3,
        "output_tokens": 17,
        "reasoning_tokens": 9,
        "total_tokens": 118,
    }
    write_active(
        project,
        log_line("2026-08-24 15:00:00", "Pre-boundary coverage")
        + event_line(
            "2026-08-24 16:30:00",
            "single_call_reply_provider_usage",
            **usage,
        )
        + event_line(
            "2026-08-24 16:30:01",
            "single_call_reply_provider_usage",
            **usage,
        )
        + event_line(
            "2026-08-24 16:31:00",
            "single_call_reply_provider_usage",
            call_id=absent_call,
            lane="mention",
            target_id=absent_target,
            model="gpt-5.6-sol",
            provider_response_id="resp_usage_absent",
            provider_latency_ms=4321,
            request_attempt_count=1,
        )
        + event_line(
            "2026-08-24 16:32:00",
            "single_call_reply_provider_usage",
            call_id=zero_call,
            lane="mention",
            target_id=zero_target,
            model="gpt-5.6-sol",
            provider_response_id="resp_usage_zero",
            provider_latency_ms=0,
            request_attempt_count=0,
            request_attempt_count_status="available",
            input_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
        ),
    )

    run_scan(project, output)

    posts = {row["post_id"]: row for row in rows(output, "canonical-posts.jsonl")}
    complete_events = [
        row
        for row in posts[complete_target]["pipeline_stage_summaries"]
        if row["event_kind"] == "single_call_reply_provider_usage"
    ]
    assert len(complete_events) == 2
    for projected in complete_events:
        for field, value in usage.items():
            if field not in {"lane", "target_id"}:
                assert projected[field] == value

    absent_event = posts[absent_target]["pipeline_stage_summaries"][0]
    assert absent_event["call_id"] == absent_call
    assert absent_event["provider_response_id"] == "resp_usage_absent"
    assert absent_event["provider_latency_ms"] == 4321
    assert absent_event["request_attempt_count"] == 1
    assert "request_attempt_count_status" not in absent_event
    assert not set(extractor.PROVIDER_USAGE_TOKEN_FIELDS) & set(absent_event)

    zero_event = posts[zero_target]["pipeline_stage_summaries"][0]
    assert zero_event["provider_latency_ms"] == 0
    assert zero_event["request_attempt_count"] == 0
    assert {
        field: zero_event[field]
        for field in extractor.PROVIDER_USAGE_TOKEN_FIELDS
    } == {field: 0 for field in extractor.PROVIDER_USAGE_TOKEN_FIELDS}

    conversations = rows(output, "conversations.jsonl")
    projected_by_post = {
        turn["post_id"]: turn["pipeline_stage_summaries"]
        for conversation in conversations
        for turn in conversation["turns"]
    }
    assert projected_by_post[complete_target] == posts[complete_target][
        "pipeline_stage_summaries"
    ]

    expected_summary = {
        "deduplication_identity": (
            "call_id, else provider_response_id, else structured event fingerprint"
        ),
        "duplicate_event_count": 1,
        "included_usage_event_count": 3,
        "usage_event_count_lacking_token_data": 1,
        "usage_event_count_with_conflicting_token_data": 0,
        "token_reported_event_counts": {
            field: 2 for field in extractor.PROVIDER_USAGE_TOKEN_FIELDS
        },
        "token_totals": {
            "input_tokens": 101,
            "cached_input_tokens": 22,
            "cache_write_input_tokens": 3,
            "output_tokens": 17,
            "reasoning_tokens": 9,
            "total_tokens": 118,
        },
    }
    batch = current_batch(output)
    status = extractor._strict_read_json(batch / "status.json")
    manifest = extractor._strict_read_json(batch / "manifest.json")
    report = (batch / "extraction-report.md").read_text(encoding="utf-8")
    assert status["provider_usage_summary"] == expected_summary
    assert manifest["provider_usage_summary"] == expected_summary
    assert "Provider usage events included after correlation: `3`" in report
    assert "Provider usage events lacking token data: `1`" in report
    request_rows = rows(output, "provider-requests.jsonl")
    assert {row["capture_status"] for row in request_rows} == {
        "missing_expected_record"
    }
    assert all(
        row["capture_status"] != "historical_not_recorded"
        for row in request_rows
    )
    assert extractor.validate_output_root(output)["valid"] is True


def test_historical_unrecorded_request_and_usage_remain_unavailable(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    target_id = snowflake_id("2026-08-24T15:40:00Z")
    write_active(
        project,
        log_line("2026-08-24 15:00:00", "Pre-boundary coverage")
        + event_line(
            "2026-08-24 16:40:00",
            "single_call_reply_decision",
            lane="mention",
            target_id=target_id,
            model_call_count=1,
            decision="no_reply",
            reply_kind="no_reply",
            reason_code="completed_exchange",
        ),
    )

    run_scan(project, output)

    request_rows = rows(output, "provider-requests.jsonl")
    assert len(request_rows) == 1
    request = request_rows[0]
    assert request["capture_status"] == "historical_not_recorded"
    assert request["call_id"] is None
    assert not {
        "request_body_utf8",
        "request_body_sha256",
        "request_body_byte_length",
    } & set(request)
    status = extractor._strict_read_json(current_batch(output) / "status.json")
    usage = status["provider_usage_summary"]
    assert usage["included_usage_event_count"] == 0
    assert usage["usage_event_count_lacking_token_data"] == 0
    assert usage["token_totals"] == {
        field: None for field in extractor.PROVIDER_USAGE_TOKEN_FIELDS
    }
    assert extractor.validate_output_root(output)["valid"] is True


@pytest.mark.parametrize("prior_429", [False, True])
def test_real_transport_timeout_event_survives_parser_and_normaliser(
    prior_429: bool,
) -> None:
    """Actual ambiguous transport evidence keeps its target and attempt number."""

    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ReadTimeout(Timeout):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ConnectionError(RequestException):
        pass

    class ProviderError(RuntimeError):
        def __init__(self, message: str, **fields: object) -> None:
            super().__init__(message)
            self.status_code = fields.get("status_code")
            self.reset_epoch = fields.get("reset_epoch")

    timeout = ReadTimeout("synthetic ambiguous timeout")
    responses: list[object] = []
    if prior_429:
        responses.append(SimpleNamespace(
            status_code=429,
            headers={"Retry-After": "0"},
            close=lambda: None,
        ))
    responses.append(timeout)
    emitted: list[tuple[str, dict[str, object]]] = []
    owner = ReplyModelTransport(
        log=Mock(),
        model="fixture-model",
        reasoning_effort="low",
        monotonic=lambda: 1.0,
        require_remote_operation_unpaused=lambda _operation: None,
        report_bot_health_progress=lambda _boundary: None,
        requests=SimpleNamespace(
            post=Mock(side_effect=responses),
            RequestException=RequestException,
            Timeout=Timeout,
            ConnectTimeout=ConnectTimeout,
            ConnectionError=ConnectionError,
        ),
        base_url="https://fixture.invalid/v1",
        api_key="fixture-secret",
        sleep=lambda _seconds: None,
        now_epoch=lambda: 100,
        error_type=ProviderError,
        log_event=lambda name, **fields: emitted.append((name, fields)),
    )

    with pytest.raises(ProviderError) as caught:
        owner.call(
            request={"model": "fixture-model"},
            timeout_seconds=20,
            lane="mention",
            target_id="900",
        )

    expected_attempt = 2 if prior_429 else 1
    assert caught.value.__cause__ is timeout
    assert caught.value.request_attempt_count == expected_attempt
    assert caught.value.status_code == (429 if prior_429 else None)
    lines = "".join(
        event_line(
            f"2026-09-04 12:00:0{index}", name, **fields,
        )
        for index, (name, fields) in enumerate(emitted)
    )
    records, warnings = extractor.parse_log_records(lines.encode())
    statistics: dict[str, object] = {}
    posts = extractor.normalise_canonical_posts(
        records, [], b"t" * 32, parser_statistics=statistics,
    )

    outcomes = [
        summary
        for summary in posts[0]["pipeline_stage_summaries"]
        if summary["event_kind"] == "provider_request_attempt_outcome"
    ]
    ambiguous = next(
        summary for summary in outcomes
        if summary.get("outcome") == "ambiguous_transport_outcome"
    )
    assert warnings == []
    assert statistics["registered_event_missing_target_count"] == 0
    assert ambiguous["call_id"] == caught.value.call_id
    assert ambiguous["attempt_number"] == expected_attempt
    assert posts[0]["lane"] == "mention"
    assert posts[0]["post_id"] == "900"
    if prior_429:
        assert [summary["attempt_number"] for summary in outcomes] == [1, 2]
        assert outcomes[0]["provider_status_code"] == 429


def test_invalid_deflate_capture_isolated_in_conversation_export(
    tmp_path: Path,
) -> None:
    """One invalid DEFLATE member is reported without hiding a valid capture."""

    directory = tmp_path / "ai-request-records"
    valid_id = "11111111-2222-4333-8444-555555555555"
    corrupt_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    valid_body = b'{"model":"fixture","input":"complete"}'
    record_provider_request(
        directory,
        request={"model": "fixture", "input": "complete"},
        request_body=valid_body,
        call_id=valid_id,
        lane="mention",
        target_post_id="100",
        endpoint_path="/responses",
        timeout_seconds=20,
        captured_at="2026-09-04T12:00:00Z",
    )
    corrupt = directory / f"{corrupt_id}.json.gz"
    corrupt.write_bytes(
        bytes.fromhex("1f8b08000000000002ff") + b"\x07" + b"\x00" * 8
    )
    corrupt.chmod(0o600)
    observed = datetime(2026, 9, 4, 12, tzinfo=timezone.utc).timestamp()
    os.utime(corrupt, (observed, observed))

    with pytest.raises(InvalidRequestRecord) as caught:
        read_provider_request_record(corrupt)
    assert isinstance(caught.value.__cause__, zlib.error)

    capture_rows = extractor.load_provider_request_captures(
        directory,
        cutoff=extractor.parse_aware_timestamp(
            "2026-09-05T00:00:00Z", option="test cutoff",
        ),
    )
    by_id = {row["call_id"]: row for row in capture_rows}
    assert by_id[valid_id]["capture_status"] == "complete"
    assert by_id[valid_id]["request_body_utf8"].encode("utf-8") == valid_body
    assert by_id[corrupt_id]["capture_status"] == "corrupt_or_hash_mismatch"
    assert "corrupt provider request record" in by_id[corrupt_id]["error"]


def state_bytes(output: Path) -> bytes:
    return (output / "state" / "extractor-state.json").read_bytes()


def mark_state_as_registered_v6(output: Path) -> None:
    state_path = output / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    state.update(
        {
            "schema_version": 6,
            "extractor_version": "prospective-conversation-extractor-v6",
            "parser_version": "prospective-conversation-log-parser-v6",
        }
    )
    for entry in state.get("source_file_cache", {}).values():
        if isinstance(entry, dict):
            entry["parser_version"] = "prospective-conversation-log-parser-v6"
    rewrite_private_json(state_path, state, mode=0o600)


def current_target(output: Path) -> str:
    return os.readlink(output / "current")


def all_private_output_bytes(output: Path) -> bytes:
    values: list[bytes] = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "pseudonym-key":
            values.append(path.read_bytes())
    return b"\n".join(values)


def test_exact_log_discovery_range_ordering_gaps_and_exclusions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    valid = ["mrsMThatcher.log", "mrsMThatcher.log.1", "mrsMThatcher.log.3", "mrsMThatcher.log.100"]
    for name in valid:
        (project / name).write_text("", encoding="utf-8")
    for name in (
        "mrsMThatcher.log.0",
        "mrsMThatcher.log.101",
        "mrsMThatcher.log.tmp",
        "mrsMThatcher-test.log",
        "smoke-mrsMThatcher.log",
        "unrelated.log.1",
        "mrsMThatcher.log.2.copy",
    ):
        (project / name).write_text("", encoding="utf-8")
    (project / "mrsMThatcher.log.2").mkdir()
    (project / "real-source").write_text("", encoding="utf-8")
    (project / "mrsMThatcher.log.4").symlink_to(project / "real-source")

    selected, warnings = extractor.discover_log_sources(project)

    assert [path.name for path in selected] == [
        "mrsMThatcher.log.100",
        "mrsMThatcher.log.3",
        "mrsMThatcher.log.1",
        "mrsMThatcher.log",
    ]
    assert {warning.kind for warning in warnings} == {
        "source_non_regular_rejected",
        "source_symlink_rejected",
    }


def test_all_rotations_one_through_one_hundred_are_recognised_numerically(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for number in range(1, 101):
        (project / f"mrsMThatcher.log.{number}").write_text("", encoding="utf-8")
    (project / "mrsMThatcher.log").write_text("", encoding="utf-8")

    selected, warnings = extractor.discover_log_sources(project)

    assert not warnings
    assert [path.name for path in selected] == [
        *(f"mrsMThatcher.log.{number}" for number in range(100, 0, -1)),
        "mrsMThatcher.log",
    ]


def test_complete_line_only_read_and_incomplete_tail_metadata(tmp_path: Path) -> None:
    path = tmp_path / "mrsMThatcher.log"
    path.write_bytes(b"first\nsecond\npartial")

    source = extractor.read_source_prefix(path)

    assert source.complete_data == b"first\nsecond\n"
    assert source.complete_line_count == 2
    assert source.incomplete_trailing_line_ignored is True
    assert source.size == len(b"first\nsecond\npartial")
    assert source.content_sha256 == hashlib.sha256(b"first\nsecond\npartial").hexdigest()


def test_active_growth_after_initial_fstat_is_deferred(tmp_path: Path) -> None:
    path = tmp_path / "mrsMThatcher.log"
    path.write_bytes(b"before\n")

    def append_after_open(_descriptor: int, _info: os.stat_result) -> None:
        with path.open("ab") as handle:
            handle.write(b"after\n")

    source = extractor.read_source_prefix(path, after_open=append_after_open)

    assert source.complete_data == b"before\n"
    assert path.read_bytes() == b"before\nafter\n"


def test_rotation_rename_during_open_read_returns_original_inode_and_requests_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mrsMThatcher.log"
    path.write_bytes(b"original inode\n")

    def rotate_after_open(_descriptor: int, _info: os.stat_result) -> None:
        path.replace(tmp_path / "mrsMThatcher.log.1")
        path.write_bytes(b"new active\n")

    source = extractor.read_source_prefix(path, after_open=rotate_after_open)

    assert source.complete_data == b"original inode\n"
    assert source.inventory_retry_required is True


def test_truncation_during_bounded_read_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "mrsMThatcher.log"
    path.write_bytes(b"some bytes\n")

    def truncate_after_open(_descriptor: int, _info: os.stat_result) -> None:
        path.write_bytes(b"")

    with pytest.raises(extractor.SourceChanged, match="truncated"):
        extractor.read_source_prefix(path, after_open=truncate_after_open)


def test_source_inventory_retries_once_after_replacement_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    path = write_active(project, first_exchange())
    real_read = extractor.read_source_prefix
    calls = 0

    def one_retry(candidate: Path) -> extractor.SourceRead:
        nonlocal calls
        calls += 1
        result = real_read(candidate)
        if calls == 1:
            return replace(result, inventory_retry_required=True)
        return result

    monkeypatch.setattr(extractor, "read_source_prefix", one_retry)
    inventory = extractor.collect_source_inventory(project)

    assert path.name == inventory.files[0].basename
    assert inventory.retry_count == 1
    assert calls == 2


def test_parser_preserves_multiline_record_and_uses_london_to_utc() -> None:
    data = (
        b"2026-08-24 16:10:00 INFO source.name:7 - first\n"
        b"continuation\n"
    )

    records, warnings = extractor.parse_log_records(data)

    assert not warnings
    assert len(records) == 1
    assert records[0].timestamp == "2026-08-24T15:10:00Z"
    assert records[0].message == "first\ncontinuation\n"


def test_pre_boundary_creation_observed_after_boundary_is_not_prospective(
    tmp_path: Path,
) -> None:
    root_id = snowflake_id("2026-08-24T14:00:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line(
            "2026-08-24 20:00:00",
            f"Considering mention id={root_id} author_id=delayed-user text='A delayed question'",
        )
        + log_line(
            "2026-08-24 20:00:01",
            f"Built AI reply context for mention {root_id}. chain_items=0 immediate_parent=None quoted=False",
        ),
    )

    run_scan(project, output)

    post = rows(output, "canonical-posts.jsonl")[0]
    conversation = rows(output, "conversations.jsonl")[0]
    assert post["created_at"] == "2026-08-24T14:00:00Z"
    assert post["creation_time_source"] == "x_snowflake"
    assert post["first_observed_at"] == "2026-08-24T19:00:00Z"
    assert conversation["prospective_status"] == "pre_boundary"
    assert conversation["start_time_source"] == "confirmed_root_turn"


def test_delayed_backlog_and_genuinely_new_post_use_snowflake_creation_time(
    tmp_path: Path,
) -> None:
    old_id = snowflake_id("2026-08-24T10:00:00Z")
    new_id = snowflake_id("2026-08-24T15:30:00Z", 1)
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        "".join(
            [
                log_line("2026-08-24 20:00:00", f"Considering mention id={old_id} author_id=a text='Old backlog item'"),
                log_line("2026-08-24 20:00:01", f"Built AI reply context for mention {old_id}. chain_items=0 immediate_parent=None quoted=False"),
                log_line("2026-08-24 20:01:00", f"Considering mention id={new_id} author_id=b text='New post-boundary item'"),
                log_line("2026-08-24 20:01:01", f"Built AI reply context for mention {new_id}. chain_items=0 immediate_parent=None quoted=False"),
            ]
        ),
    )

    run_scan(project, output)
    by_root = {
        row["root_post_id"]: row for row in rows(output, "conversations.jsonl")
    }

    assert by_root[old_id]["prospective_status"] == "pre_boundary"
    assert by_root[new_id]["prospective_status"] == "eligible"


def test_pre_boundary_root_and_missing_numeric_root_date_later_continuations(
    tmp_path: Path,
) -> None:
    root_id = snowflake_id("2026-08-24T14:30:00Z")
    continuation_id = snowflake_id("2026-08-24T16:00:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        event_line(
            "2026-08-24 17:01:00",
            "ai_reply_pipeline_decision",
            target_id=continuation_id,
            root_post_id=root_id,
            parent_post_id=snowflake_id("2026-08-24T15:45:00Z"),
            incoming_text="A post-boundary continuation",
        ),
    )

    run_scan(project, output)
    conversation = rows(output, "conversations.jsonl")[0]

    assert conversation["root_post_id"] == root_id
    assert conversation["start_time"] == "2026-08-24T14:30:00Z"
    assert conversation["start_time_source"] == "root_post_id_snowflake"
    assert conversation["prospective_status"] == "pre_boundary"


def test_missing_undateable_root_stays_start_unknown(tmp_path: Path) -> None:
    target_id = snowflake_id("2026-08-24T16:00:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        event_line(
            "2026-08-24 17:01:00",
            "ai_reply_pipeline_decision",
            target_id=target_id,
            root_post_id="opaque-root",
            parent_post_id="opaque-parent",
            incoming_text="Continuation with no dateable root",
        ),
    )

    run_scan(project, output)
    conversation = rows(output, "conversations.jsonl")[0]

    assert conversation["start_time"] is None
    assert conversation["start_time_source"] == "unavailable"
    assert conversation["prospective_status"] == "start_unknown"


def test_single_call_decision_is_retained_without_stage_summary() -> None:
    records, _ = extractor.parse_log_records(
        (
            log_line(
                "2026-09-04 12:00:00",
                "Considering mention id=900 author_id=700 "
                "text='A current contribution'",
            )
            + log_line(
                "2026-09-04 12:00:01",
                "Built single-call reply context target_id=900 turns=3 "
                "root_id=800 parent_id=850",
            )
            + event_line(
                "2026-09-04 12:00:02",
                "single_call_reply_decision",
                target_id="900",
                lane="mention",
                strategy_version="single-sol-reply-20260904",
                model="gpt-5.6-sol",
                decision="no_reply",
                reply_kind="no_reply",
                reason_code="completed_exchange",
                outcome_type="editorial",
                local_validation_status="passed",
                model_call_count=1,
                visible_turn_count=3,
                trusted_fact_count=2,
            )
        ).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], b"s" * 32)[0]

    assert post["post_id"] == "900"
    assert post["root_post_id"] == "800"
    assert post["parent_post_id"] == "850"
    assert post["tested_pipeline_stage_summaries"] == []
    assert post["pipeline_stage_summaries"] == [
        {
            "event_id": records[2].record_fingerprint,
            "event_kind": "single_call_reply_decision",
            "observed_at": records[2].timestamp,
            "model_call_count": 1,
            "decision": "no_reply",
            "reply_kind": "no_reply",
            "reason_code": "completed_exchange",
            "outcome_type": "editorial",
            "local_validation_status": "passed",
            "visible_turn_count": 3,
            "trusted_fact_count": 2,
            "model": "gpt-5.6-sol",
            "strategy_version": "single-sol-reply-20260904",
        }
    ]


def test_conflicting_explicit_creation_time_preserves_snowflake_and_malformed_falls_back() -> None:
    key = b"t" * 32
    snowflake = snowflake_id("2026-08-24T15:00:00Z")
    records, _ = extractor.parse_log_records(
        (
            event_line(
                "2026-08-24 16:20:00",
                "ai_reply_pipeline_decision",
                target_id=snowflake,
                target_created_at="2026-08-24T15:10:00Z",
            )
            + event_line(
                "2026-08-24 16:21:00",
                "ai_reply_pipeline_decision",
                target_id=snowflake_id("2026-08-24T15:05:00Z", 1),
                target_created_at="not-a-time",
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], key)

    by_id = {post["post_id"]: post for post in posts}
    assert by_id[snowflake]["created_at"] == "2026-08-24T15:00:00Z"
    assert by_id[snowflake]["creation_time_source"] == "x_snowflake"
    assert by_id[snowflake]["creation_time_conflict"] is True
    fallback = by_id[snowflake_id("2026-08-24T15:05:00Z", 1)]
    assert fallback["created_at"] == "2026-08-24T15:05:00Z"
    assert fallback["creation_time_source"] == "x_snowflake"
    assert "invalid_explicit_target_creation_time" in fallback["warnings"]


def test_equal_structured_creation_times_merge_without_conflict() -> None:
    records, _ = extractor.parse_log_records(
        (
            event_line(
                "2026-08-24 16:20:00",
                "ai_reply_pipeline_decision",
                target_id="opaque-equal",
                root_post_id="opaque-equal",
                target_created_at="2026-08-24T15:10:00Z",
            )
            + event_line(
                "2026-08-24 16:21:00",
                "ai_reply_pipeline_stage_summary",
                target_id="opaque-equal",
                root_post_id="opaque-equal",
                target_created_at="2026-08-24T15:10:00Z",
            )
        ).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], b"e" * 32)[0]

    assert post["created_at"] == "2026-08-24T15:10:00Z"
    assert post["creation_time_source"] == "structured_event"
    assert post["creation_time_conflict"] is False
    assert post["creation_time_conflicts"] == []
    structured = [
        value
        for value in post["creation_time_provenance"]
        if value["source"] == "structured_event"
    ]
    assert len(structured) == 2


def test_structured_times_across_boundary_do_not_use_last_event_wins() -> None:
    records, _ = extractor.parse_log_records(
        (
            event_line(
                "2026-08-24 16:20:00",
                "ai_reply_pipeline_decision",
                target_id="opaque-conflict",
                root_post_id="opaque-conflict",
                incoming_text="A substantive contribution",
                target_created_at="2026-08-24T15:00:00Z",
            )
            + event_line(
                "2026-08-24 16:21:00",
                "ai_reply_pipeline_stage_summary",
                target_id="opaque-conflict",
                root_post_id="opaque-conflict",
                target_created_at="2026-08-24T15:10:00Z",
            )
        ).encode()
    )
    posts = extractor.normalise_canonical_posts(records, [], b"f" * 32)
    conversations, _ = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert posts[0]["created_at"] is None
    assert posts[0]["creation_time_source"] == "unavailable"
    assert posts[0]["creation_time_conflict"] is True
    assert conversations[0]["prospective_status"] == "start_unknown"


def test_snowflake_resolves_structured_creation_conflict_deterministically() -> None:
    post_id = snowflake_id("2026-08-24T15:05:00Z")
    records, _ = extractor.parse_log_records(
        (
            event_line(
                "2026-08-24 16:20:00",
                "ai_reply_pipeline_decision",
                target_id=post_id,
                root_post_id=post_id,
                incoming_text="A substantive contribution",
                target_created_at="2026-08-24T15:00:00Z",
            )
            + event_line(
                "2026-08-24 16:21:00",
                "ai_reply_pipeline_stage_summary",
                target_id=post_id,
                root_post_id=post_id,
                target_created_at="2026-08-24T15:10:00Z",
            )
        ).encode()
    )
    posts = extractor.normalise_canonical_posts(records, [], b"g" * 32)
    conversations, _ = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert posts[0]["created_at"] == "2026-08-24T15:05:00Z"
    assert posts[0]["creation_time_source"] == "x_snowflake"
    assert posts[0]["creation_time_conflict"] is True
    assert conversations[0]["prospective_status"] == "pre_boundary"


def test_reversing_conflicting_structured_events_has_identical_outcome() -> None:
    def extract(first: str, second: str) -> tuple[object, object, object]:
        records, _ = extractor.parse_log_records(
            (
                event_line(
                    "2026-08-24 16:20:00",
                    "ai_reply_pipeline_decision",
                    target_id="opaque-order",
                    root_post_id="opaque-order",
                    incoming_text="A substantive contribution",
                    target_created_at=first,
                )
                + event_line(
                    "2026-08-24 16:21:00",
                    "ai_reply_pipeline_stage_summary",
                    target_id="opaque-order",
                    root_post_id="opaque-order",
                    target_created_at=second,
                )
            ).encode()
        )
        posts = extractor.normalise_canonical_posts(records, [], b"h" * 32)
        conversations, _ = extractor.build_conversations(
            posts,
            boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
            cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
            quiescence_hours=48,
        )
        return (
            posts[0]["created_at"],
            posts[0]["creation_time_source"],
            conversations[0]["prospective_status"],
        )

    before_then_after = extract(
        "2026-08-24T15:00:00Z", "2026-08-24T15:10:00Z"
    )
    after_then_before = extract(
        "2026-08-24T15:10:00Z", "2026-08-24T15:00:00Z"
    )

    assert before_then_after == after_then_before == (
        None,
        "unavailable",
        "start_unknown",
    )


def test_creation_conflict_survives_incremental_scan_and_snapshot_reload(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(
        project,
        event_line(
            "2026-08-24 16:20:00",
            "ai_reply_pipeline_decision",
            target_id="opaque-persist",
            root_post_id="opaque-persist",
            incoming_text="A substantive contribution",
            target_created_at="2026-08-24T15:00:00Z",
        )
        + event_line(
            "2026-08-24 16:21:00",
            "ai_reply_pipeline_stage_summary",
            target_id="opaque-persist",
            root_post_id="opaque-persist",
            target_created_at="2026-08-24T15:10:00Z",
        ),
    )
    run_scan(project, output)
    first = next(
        post
        for post in rows(output, "canonical-posts.jsonl")
        if post["post_id"] == "opaque-persist"
    )
    first_details = first["creation_time_conflicts"]
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )

    result = run_scan(project, output, until="2026-08-28T19:00:00Z")
    persisted = next(
        post
        for post in rows(output, "canonical-posts.jsonl")
        if post["post_id"] == "opaque-persist"
    )

    assert result["status"] == "published"
    assert persisted["creation_time_conflict"] is True
    assert persisted["creation_time_conflicts"] == first_details
    assert persisted["created_at"] is None


def test_validation_rejects_inconsistent_creation_conflict_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        event_line(
            "2026-08-24 16:20:00",
            "ai_reply_pipeline_decision",
            target_id="opaque-invalid-conflict",
            root_post_id="opaque-invalid-conflict",
            incoming_text="A substantive contribution",
            target_created_at="2026-08-24T15:00:00Z",
        )
        + event_line(
            "2026-08-24 16:21:00",
            "ai_reply_pipeline_stage_summary",
            target_id="opaque-invalid-conflict",
            root_post_id="opaque-invalid-conflict",
            target_created_at="2026-08-24T15:10:00Z",
        ),
    )
    run_scan(project, output)
    posts = rows(output, "canonical-posts.jsonl")
    posts[0]["creation_time_conflict"] = False
    rewrite_batch_posts_and_hashes(current_batch(output), posts)

    validation = extractor.validate_output_root(output)

    assert validation["valid"] is False
    assert any(
        "conflict details exist without a conflict" in error
        for error in validation["errors"]
    )


def test_future_snowflake_is_unavailable_and_observations_track_first_and_last() -> None:
    key = b"u" * 32
    future_id = snowflake_id("2026-08-25T00:00:00Z")
    records, _ = extractor.parse_log_records(
        (
            log_line("2026-08-24 16:10:00", f"Considering mention id={future_id} author_id=u text='First observation'")
            + log_line("2026-08-24 17:10:00", f"Considering mention id={future_id} author_id=u text='First observation'")
        ).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], key)[0]

    assert post["created_at"] is None
    assert post["creation_time_source"] == "unavailable"
    assert post["first_observed_at"] == "2026-08-24T15:10:00Z"
    assert post["last_observed_at"] == "2026-08-24T16:10:00Z"
    assert "x_snowflake_materially_after_first_observation" in post["warnings"]


def test_account_reply_creation_time_comes_from_reply_id_not_confirmation_log(
    tmp_path: Path,
) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line("2026-08-24 21:00:00", f"Considering mention id={target_id} author_id=u text='Question?'"),
    )
    active = project / "mrsMThatcher.log"
    with active.open("a", encoding="utf-8") as handle:
        handle.write(
            event_line(
                "2026-08-24 21:00:01",
                "reply_posted",
                target_id=target_id,
                reply_post_id=reply_id,
            )
        )

    run_scan(project, output)
    account = next(
        row
        for row in rows(output, "canonical-posts.jsonl")
        if row["author_role"] == "account"
    )

    assert account["created_at"] == "2026-08-24T15:12:00Z"
    assert account["creation_time_source"] == "x_snowflake"
    assert account["first_observed_at"] == "2026-08-24T20:00:01Z"


def test_first_scan_initialises_state_second_unchanged_scan_has_no_duplicate_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())

    first = run_scan(project, output)
    first_batches = sorted((output / "batches").iterdir())
    real_retention = extractor.apply_batch_retention
    retention_calls = 0

    def counted_retention(*args: object, **kwargs: object) -> extractor.RetentionResult:
        nonlocal retention_calls
        retention_calls += 1
        return real_retention(*args, **kwargs)

    monkeypatch.setattr(extractor, "apply_batch_retention", counted_retention)
    second = run_scan(project, output)

    assert first["status"] == "published"
    assert second["status"] == "no_change"
    assert retention_calls == 1
    assert sorted((output / "batches").iterdir()) == first_batches
    status = extractor.get_status(output)
    assert status["initialised"] is True
    assert status["prospective_boundary"] == BOUNDARY
    assert stat.S_IMODE((output / "state" / "pseudonym-key").stat().st_mode) == 0o600


def test_append_creates_one_snapshot_duplicate_event_deduplicates_and_late_turn_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    original_batch = current_target(output)
    duplicate = event_line(
        "2026-08-24 16:10:04",
        "reply_posted",
        lane="mention",
        target_id="100",
        author_id="raw-user-123",
        reply_post_id="101",
    )
    with active.open("a", encoding="utf-8") as handle:
        handle.write(duplicate)
        handle.write(continuation())

    real_retention = extractor.apply_batch_retention
    retention_calls = 0

    def counted_retention(*args: object, **kwargs: object) -> extractor.RetentionResult:
        nonlocal retention_calls
        retention_calls += 1
        return real_retention(*args, **kwargs)

    monkeypatch.setattr(extractor, "apply_batch_retention", counted_retention)
    result = run_scan(project, output)
    posts = rows(output, "canonical-posts.jsonl")
    conversations = rows(output, "conversations.jsonl")

    assert result["status"] == "published"
    assert retention_calls == 1
    assert current_target(output) != original_batch
    assert [row["post_id"] for row in posts].count("101") == 1
    assert len(conversations) == 1
    assert conversations[0]["user_turn_count"] == 2


def test_failed_conversational_attempt_cannot_consume_unrelated_generic_success() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    unrelated_id = snowflake_id("2026-08-24T15:12:00Z")
    records, _ = extractor.parse_log_records(
        (
            log_line("2026-08-24 16:10:00", f"Considering mention id={target_id} author_id=u text='Question?'")
            + create_attempt(target_id, "a" * 64)
            + log_line("2026-08-24 16:12:01", "Failed to post generated reply")
            + create_attempt("ignored", "b" * 64, lane="quote_image", local_time="2026-08-24 16:13:00")
            + generic_success(unrelated_id, local_time="2026-08-24 16:13:01")
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"p" * 32)

    assert all(post["author_role"] != "account" for post in posts)
    target = next(post for post in posts if post["post_id"] == target_id)
    assert target["send_attempts"][0]["last_observed_status"] == "failed"


def test_generic_success_without_confirmation_never_publishes_account_turn() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "c" * 64)
            + generic_success(reply_id)
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"q" * 32)

    assert [post for post in posts if post["author_role"] == "account"] == []
    assert posts[0]["send_attempts"][0]["last_observed_status"] == "remote_success_observed"


def test_receipt_promotion_publishes_with_attempt_text_and_authority() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "d" * 64, text="Confirmed attempt text")
            + generic_success(reply_id)
            + receipt_promotion(target_id, reply_id)
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"r" * 32)
    account = next(post for post in posts if post["author_role"] == "account")
    target = next(post for post in posts if post["post_id"] == target_id)

    assert account["text"] == "Confirmed attempt text"
    assert account["publication_authority"] == "confirmed_receipt_promotion"
    assert account["publication_evidence"][0]["event_kind"] == "confirmed_receipt_promotion"
    assert target["send_attempts"][0]["last_observed_status"] == "confirmed"


def test_structured_confirmation_deduplicates_with_receipt_evidence() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    records, _ = extractor.parse_log_records(
        (
            log_line("2026-08-24 16:10:00", f"Generated reply to mention {target_id}: 'Final text'")
            + receipt_promotion(target_id, reply_id)
            + event_line(
                "2026-08-24 16:13:00",
                "reply_posted",
                target_id=target_id,
                reply_post_id=reply_id,
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"s" * 32)
    accounts = [post for post in posts if post["author_role"] == "account"]

    assert len(accounts) == 1
    assert accounts[0]["text"] == "Final text"
    assert accounts[0]["publication_authority"] == "structured_confirmation"
    assert {value["event_kind"] for value in accounts[0]["publication_evidence"]} == {
        "confirmed_receipt_promotion",
        "reply_posted",
    }


def test_generated_text_survives_scan_and_rotation_until_confirmation(
    tmp_path: Path,
) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(
        project,
        log_line("2026-08-24 16:10:00", f"Considering mention id={target_id} author_id=u text='Question?'")
        + log_line("2026-08-24 16:10:01", f"Built AI reply context for mention {target_id}. chain_items=0 immediate_parent=None quoted=False")
        + log_line("2026-08-24 16:11:00", f"Generated reply to mention {target_id}: 'Cross-scan final text'")
        + create_attempt(target_id, "e" * 64, text="Cross-scan final text"),
    )
    run_scan(project, output)
    active.replace(project / "mrsMThatcher.log.1")
    write_active(project, receipt_promotion(target_id, reply_id))

    run_scan(project, output, until="2026-08-28T18:00:00Z")
    account = next(
        post
        for post in rows(output, "canonical-posts.jsonl")
        if post["author_role"] == "account"
    )

    assert account["text"] == "Cross-scan final text"
    assert account["publication_authority"] == "confirmed_receipt_promotion"


def test_confirmation_without_recoverable_text_is_partial_and_never_invents_text() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    records, _ = extractor.parse_log_records(
        receipt_promotion(target_id, reply_id).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"v" * 32)
    account = next(post for post in posts if post["author_role"] == "account")

    assert account["text"] is None
    assert account["reconstruction_confidence"] == "medium"
    assert "confirmed_account_reply_text_unavailable" in account["warnings"]


def test_confirmation_with_multiple_unbound_attempts_does_not_guess_reply_text() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:15:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "2" * 64, text="First possible draft")
            + create_attempt(
                target_id,
                "3" * 64,
                text="Second possible draft",
                local_time="2026-08-24 16:14:00",
            )
            + receipt_promotion(
                target_id, reply_id, local_time="2026-08-24 16:15:00"
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"m" * 32)
    account = next(post for post in posts if post["author_role"] == "account")

    assert account["text"] is None
    assert "ambiguous_confirmed_reply_attempt_text" in account["warnings"]
    assert "confirmed_reply_multiple_eligible_attempts" in account["warnings"]
    assert "confirmed_account_reply_text_unavailable" in account["warnings"]


@pytest.mark.parametrize("terminal_status", ["failed", "retired"])
def test_unrelated_confirmation_never_reuses_terminal_attempt_text(
    terminal_status: str,
) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:15:00Z")
    terminal_line = (
        log_line("2026-08-24 16:12:01", "Failed to post generated reply")
        if terminal_status == "failed"
        else log_line(
            "2026-08-24 16:12:01",
            "Removed conversational reply sending receipt disposition=discarded "
            f"source=mention target_id={target_id}",
        )
    )
    records, _ = extractor.parse_log_records(
        (
            create_attempt(
                target_id,
                "4" * 64,
                text=f"Do not reuse {terminal_status} draft",
            )
            + terminal_line
            + receipt_promotion(
                target_id, reply_id, local_time="2026-08-24 16:15:00"
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"n" * 32)
    account = next(post for post in posts if post["author_role"] == "account")
    target = next(post for post in posts if post["post_id"] == target_id)

    assert account["text"] is None
    assert "confirmed_reply_no_eligible_attempt_text" in account["warnings"]
    assert (
        "confirmed_reply_failed_or_retired_attempt_text_not_reused"
        in account["warnings"]
    )
    assert target["send_attempts"][0]["last_observed_status"] == terminal_status


def test_single_started_attempt_may_supply_confirmation_text() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:15:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "5" * 64, text="Sole active draft")
            + receipt_promotion(
                target_id, reply_id, local_time="2026-08-24 16:15:00"
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"o" * 32)
    account = next(post for post in posts if post["author_role"] == "account")

    assert account["text"] == "Sole active draft"


def test_known_remote_id_mismatch_never_supplies_confirmation_text() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    observed_reply_id = snowflake_id("2026-08-24T15:14:00Z")
    confirmed_reply_id = snowflake_id("2026-08-24T15:15:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "6" * 64, text="Sole remote-success draft")
            + generic_success(observed_reply_id)
            + receipt_promotion(
                target_id,
                confirmed_reply_id,
                local_time="2026-08-24 16:15:00",
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"p" * 32)
    account = next(post for post in posts if post["author_role"] == "account")
    target = next(post for post in posts if post["post_id"] == target_id)
    attempt = target["send_attempts"][0]

    assert account["text"] is None
    assert account["parent_post_id"] == target_id
    assert account["publication_authority"] == "confirmed_receipt_promotion"
    assert "confirmed_reply_known_remote_id_mismatch" in account["warnings"]
    assert "confirmed_reply_no_eligible_attempt_text" in account["warnings"]
    assert "confirmed_account_reply_text_unavailable" in account["warnings"]
    assert attempt["remote_post_id_observed"] == observed_reply_id
    assert attempt["last_observed_status"] == "remote_success_observed"
    assert attempt["status_observed_at"] == "2026-08-24T15:12:01Z"


@pytest.mark.parametrize("terminal_status", ["failed", "retired"])
def test_exact_reply_id_recovers_text_from_later_terminal_attempt_status(
    terminal_status: str,
) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:15:00Z")
    initial_records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "7" * 64, text="Exactly identified draft")
            + generic_success(reply_id)
        ).encode()
    )
    prior = extractor.normalise_canonical_posts(initial_records, [], b"q" * 32)
    target = next(post for post in prior if post["post_id"] == target_id)
    target["send_attempts"][0]["last_observed_status"] = terminal_status
    confirmation_records, _ = extractor.parse_log_records(
        receipt_promotion(
            target_id, reply_id, local_time="2026-08-24 16:15:00"
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(
        confirmation_records, prior, b"q" * 32
    )
    account = next(post for post in posts if post["author_role"] == "account")
    target = next(post for post in posts if post["post_id"] == target_id)

    assert account["text"] == "Exactly identified draft"
    assert target["send_attempts"][0]["last_observed_status"] == "confirmed"


def test_failed_attempt_plus_active_attempt_uses_only_active_text() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:15:00Z")
    records, _ = extractor.parse_log_records(
        (
            create_attempt(target_id, "8" * 64, text="Failed old draft")
            + log_line("2026-08-24 16:12:01", "Failed to post generated reply")
            + create_attempt(
                target_id,
                "9" * 64,
                text="Later active draft",
                local_time="2026-08-24 16:14:00",
            )
            + receipt_promotion(
                target_id, reply_id, local_time="2026-08-24 16:15:00"
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"r" * 32)
    account = next(post for post in posts if post["author_role"] == "account")
    target = next(post for post in posts if post["post_id"] == target_id)
    statuses = {
        attempt["transaction_id"]: attempt["last_observed_status"]
        for attempt in target["send_attempts"]
    }

    assert account["text"] == "Later active draft"
    assert statuses["8" * 64] == "failed"
    assert statuses["9" * 64] == "confirmed"


def test_failed_draft_is_not_an_account_turn_or_review_candidate(tmp_path: Path) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line("2026-08-24 16:10:00", f"Considering mention id={target_id} author_id=u text='Question?'")
        + log_line("2026-08-24 16:10:01", f"Built AI reply context for mention {target_id}. chain_items=0 immediate_parent=None quoted=False")
        + create_attempt(target_id, "f" * 64)
        + log_line("2026-08-24 16:12:01", "Unexpected failure posting generated reply"),
    )

    run_scan(project, output)
    conversation = rows(output, "conversations.jsonl")[0]

    assert conversation["account_turn_count"] == 0
    assert rows(output, "review-candidates.jsonl") == []


def test_validation_rejects_published_account_without_authoritative_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    batch = current_batch(output)
    posts = rows(output, "canonical-posts.jsonl")
    account = next(post for post in posts if post["author_role"] == "account")
    account["publication_authority"] = None
    account["publication_evidence"] = []
    rewrite_batch_posts_and_hashes(batch, posts)

    problems = extractor._validate_batch_directory(
        batch,
        require_immutable=False,
        expected_boundary=BOUNDARY,
    )

    assert any("lacks authoritative publication" in value for value in problems)


def test_structured_event_registry_ignores_generic_and_target_like_unknown_events() -> None:
    records, _ = extractor.parse_log_records(
        (
            event_line("2026-08-24 16:10:00", "unrelated_event", id="123")
            + event_line(
                "2026-08-24 16:11:00",
                "other_event",
                id="456",
                author_id="raw-user",
                incoming_text="Should not become a post",
            )
        ).encode()
    )
    statistics: dict[str, int] = {}

    posts = extractor.normalise_canonical_posts(
        records, [], b"w" * 32, parser_statistics=statistics
    )

    assert posts == []
    assert statistics["ignored_structured_event_count"] == 2
    assert statistics["ignored_target_like_event_count"] == 2


def test_reply_media_context_and_visual_event_attach_only_safe_metadata() -> None:
    records, _ = extractor.parse_log_records(
        (
            log_line(
                "2026-08-24 16:10:00",
                "Considering mention id=900 author_id=raw-user "
                "text='What is shown here?'",
            )
            + reply_media_context_line("2026-08-24 16:10:01", "900")
            + reply_visual_description_line(
                "2026-08-24 16:10:02", "900", description_sha256="b" * 64
            )
        ).encode()
    )
    statistics: dict[str, object] = {}

    posts = extractor.normalise_canonical_posts(
        records, [], b"v" * 32, parser_statistics=statistics
    )
    post = next(row for row in posts if row["post_id"] == "900")

    assert post["reply_media_context_observations"] == [
        {
            "lane": "mention",
            "mode": "multimodal",
            "observed_at": "2026-08-24T15:10:01Z",
            "photo_count": 2,
            "record_fingerprint": records[1].record_fingerprint,
            "status": "supplied",
        }
    ]
    attempt = post["reply_visual_description_attempts"][0]
    assert set(attempt) == {
        "analysis_schema_version",
        "description_sha256",
        "lane",
        "observed_at",
        "record_fingerprint",
        "status",
        "supplied_image_count",
        "visual_analysis_call_count",
    }
    assert attempt["description_sha256"] == "b" * 64
    assert post["reply_visual_context_summary"] == {
        "analysis_attempt_count": 1,
        "analysis_history_complete": True,
        "analysis_observation_status": "analysed",
        "analysis_schema_versions": [1],
        "collection_history_complete": True,
        "distinct_successful_description_count": 1,
        "latest_analysis_status": "analysed",
        "latest_collection_status": "supplied",
        "native_photo_count_max": 2,
        "omitted_media_observation_count": 0,
        "omitted_visual_event_count": 0,
        "successful_analysis_count": 1,
        "successful_description_sha256s": ["b" * 64],
        "visual_event_count": 1,
    }
    assert statistics["ignored_structured_event_count"] == 0
    assert statistics["ignored_target_like_event_count"] == 0
    assert "image_url" not in json.dumps(post)


def test_reply_media_context_unavailable_and_pre_feature_supplied_are_distinct() -> None:
    records, _ = extractor.parse_log_records(
        (
            log_line(
                "2026-08-24 16:10:00",
                "Considering mention id=901 author_id=u text='Unavailable media'",
            )
            + reply_media_context_line(
                "2026-08-24 16:10:01", "901", status="unavailable"
            )
            + log_line(
                "2026-08-24 16:11:00",
                "Considering mention id=902 author_id=u text='Historical photo'",
            )
            + reply_media_context_line("2026-08-24 16:11:01", "902")
        ).encode()
    )

    posts = {
        row["post_id"]: row
        for row in extractor.normalise_canonical_posts(records, [], b"v" * 32)
    }

    assert posts["901"]["reply_media_context_observations"][0]["status"] == (
        "unavailable"
    )
    assert posts["901"]["reply_visual_context_summary"][
        "latest_collection_status"
    ] == "unavailable"
    assert posts["901"]["reply_visual_context_summary"][
        "analysis_observation_status"
    ] == "not_applicable"
    assert posts["902"]["reply_visual_context_summary"][
        "analysis_observation_status"
    ] == "not_observed"
    assert posts["902"]["reply_visual_description_attempts"] == []


def test_reply_visual_attempt_history_preserves_failures_successes_and_hashes() -> None:
    first_hash = "1" * 64
    second_hash = "2" * 64
    records, _ = extractor.parse_log_records(
        (
            reply_media_context_line("2026-08-24 16:10:00", "903")
            + reply_visual_description_line(
                "2026-08-24 16:10:01", "903", status="provider_error"
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:02",
                "903",
                description_sha256=first_hash,
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:03",
                "903",
                description_sha256=first_hash,
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:04",
                "903",
                description_sha256=second_hash,
            )
        ).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], b"v" * 32)[0]
    summary = post["reply_visual_context_summary"]

    assert [
        attempt["status"] for attempt in post["reply_visual_description_attempts"]
    ] == ["provider_error", "analysed", "analysed", "analysed"]
    assert summary["analysis_observation_status"] == "analysed"
    assert summary["latest_analysis_status"] == "analysed"
    assert summary["analysis_attempt_count"] == 4
    assert summary["visual_event_count"] == 4
    assert summary["successful_analysis_count"] == 3
    assert summary["distinct_successful_description_count"] == 2
    assert summary["successful_description_sha256s"] == [first_hash, second_hash]


@pytest.mark.parametrize(
    ("status", "call_count"),
    [
        ("provider_error", 1),
        ("invalid_response", 1),
        ("invalid_supplied_media", 0),
        ("paused", 0),
    ],
)
def test_reply_visual_failed_statuses_are_retained_without_success(
    status: str, call_count: int
) -> None:
    supplied_count = 0 if status == "invalid_supplied_media" else 1
    records, _ = extractor.parse_log_records(
        reply_visual_description_line(
            "2026-08-24 16:10:00",
            "904",
            status=status,
            supplied_image_count=supplied_count,
            visual_analysis_call_count=call_count,
        ).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], b"v" * 32)[0]
    summary = post["reply_visual_context_summary"]

    assert post["reply_visual_description_attempts"][0]["status"] == status
    assert post["reply_visual_description_attempts"][0][
        "description_sha256"
    ] is None
    assert summary["analysis_observation_status"] == (
        "attempted_not_analysed" if call_count else "not_attempted"
    )
    assert summary["visual_event_count"] == 1
    assert summary["analysis_attempt_count"] == call_count
    assert summary["latest_analysis_status"] == status
    assert summary["successful_analysis_count"] == 0
    rendered = extractor._review_visual_context_line(summary)
    if call_count:
        assert "1 analysis call" in rendered
        assert "no successful description" in rendered
    else:
        expected = (
            "analysis paused; no analysis call attempted"
            if status == "paused"
            else "supplied media invalid; no analysis call attempted"
        )
        assert expected in rendered
        assert "successful description" not in rendered


def test_reply_visual_malformed_events_do_not_fabricate_metadata_or_leak() -> None:
    secret_url = "https://private.invalid/photo.jpg"
    records, _ = extractor.parse_log_records(
        (
            reply_media_context_line("2026-08-24 16:10:00", "905", photo_count=1)
            + reply_visual_description_line(
                "2026-08-24 16:10:01",
                "905",
                supplied_image_count=True,
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:02",
                "905",
                description_sha256="A" * 64,
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:03",
                "905",
                image_url=secret_url,
            )
            + reply_visual_description_line(
                "2026-08-24 16:10:04",
                "905",
                analysis=["not", "an", "object"],
            )
        ).encode()
    )
    statistics: dict[str, object] = {}

    post = extractor.normalise_canonical_posts(
        records, [], b"v" * 32, parser_statistics=statistics
    )[0]
    rendered = json.dumps(post)

    assert post["reply_visual_description_attempts"] == []
    assert post["reply_visual_context_summary"][
        "analysis_observation_status"
    ] == "not_observed"
    assert statistics["ambiguous_registered_event_count"] == 4
    assert statistics["ignored_structured_event_count"] == 0
    assert statistics["ignored_target_like_event_count"] == 0
    assert "malformed_reply_visual_description_event" in post["warnings"]
    assert secret_url not in rendered


def test_reply_visual_observation_bounds_and_duplicate_records_are_deterministic() -> None:
    media_lines = [
        reply_media_context_line(
            f"2026-08-24 16:10:{index:02d}", "906", photo_count=1
        )
        for index in range(18)
    ]
    visual_lines = [
        reply_visual_description_line(
            f"2026-08-24 16:11:{index:02d}",
            "906",
            supplied_image_count=1,
            description_sha256="3" * 64,
        )
        for index in range(18)
    ]
    duplicated = media_lines[-1] + visual_lines[-1]
    records, _ = extractor.parse_log_records(
        ("".join(media_lines + visual_lines) + duplicated).encode()
    )

    post = extractor.normalise_canonical_posts(records, [], b"v" * 32)[0]

    assert len(post["reply_media_context_observations"]) == 16
    assert post["reply_media_context_other_count"] == 2
    assert len(post["reply_visual_description_attempts"]) == 16
    assert post["reply_visual_description_other_count"] == 2
    assert post["reply_media_context_observations"][0]["observed_at"] == (
        "2026-08-24T15:10:02Z"
    )
    assert post["reply_visual_description_attempts"][0]["observed_at"] == (
        "2026-08-24T15:11:02Z"
    )
    assert post["reply_visual_context_summary"]["analysis_attempt_count"] == 16
    assert post["reply_visual_context_summary"]["visual_event_count"] == 16
    assert post["reply_visual_context_summary"][
        "omitted_visual_event_count"
    ] == 2
    assert post["reply_visual_context_summary"][
        "analysis_history_complete"
    ] is False
    assert post["reply_visual_context_summary"][
        "omitted_media_observation_count"
    ] == 2
    assert post["reply_visual_context_summary"][
        "collection_history_complete"
    ] is False
    assert post["reply_visual_context_summary"][
        "distinct_successful_description_count"
    ] == 1
    rendered = extractor._review_visual_context_line(
        post["reply_visual_context_summary"]
    )
    assert "2 older media observations were omitted" in rendered


def test_reply_visual_bounded_history_qualifies_omitted_successes() -> None:
    omitted_success_lines = [
        reply_visual_description_line(
            "2026-08-24 16:11:00",
            "bounded-a",
            description_sha256="5" * 64,
        ),
        *[
            reply_visual_description_line(
                f"2026-08-24 16:11:{index:02d}",
                "bounded-a",
                status="provider_error",
            )
            for index in range(1, 17)
        ],
    ]
    retained_success_lines = [
        reply_visual_description_line(
            "2026-08-24 16:12:00",
            "bounded-b",
            status="provider_error",
        ),
        reply_visual_description_line(
            "2026-08-24 16:12:01",
            "bounded-b",
            description_sha256="6" * 64,
        ),
        *[
            reply_visual_description_line(
                f"2026-08-24 16:12:{index:02d}",
                "bounded-b",
                status="provider_error",
            )
            for index in range(2, 17)
        ],
    ]
    records, _ = extractor.parse_log_records(
        "".join(omitted_success_lines + retained_success_lines).encode()
    )

    posts = {
        post["post_id"]: post
        for post in extractor.normalise_canonical_posts(records, [], b"v" * 32)
    }
    omitted = posts["bounded-a"]["reply_visual_context_summary"]
    retained = posts["bounded-b"]["reply_visual_context_summary"]

    assert len(posts["bounded-a"]["reply_visual_description_attempts"]) == 16
    assert omitted["omitted_visual_event_count"] == 1
    assert omitted["analysis_history_complete"] is False
    assert omitted["visual_event_count"] == 16
    assert omitted["analysis_attempt_count"] == 16
    assert omitted["successful_analysis_count"] == 0
    assert omitted["analysis_observation_status"] == "history_incomplete"
    omitted_line = extractor._review_visual_context_line(omitted)
    assert "retained visual history is incomplete" in omitted_line
    assert "1 older visual event was omitted" in omitted_line
    assert (
        "no successful description is present among the retained events"
        in omitted_line
    )

    assert retained["omitted_visual_event_count"] == 1
    assert retained["analysis_history_complete"] is False
    assert retained["successful_analysis_count"] == 1
    assert retained["analysis_observation_status"] == "analysed"
    retained_line = extractor._review_visual_context_line(retained)
    assert "1 retained successful description" in retained_line
    assert "retained visual history is incomplete" in retained_line


def test_reply_media_and_visual_events_touch_canonical_observation_times() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    records, _ = extractor.parse_log_records(
        (
            reply_media_context_line("2026-08-24 16:10:01", target_id)
            + reply_visual_description_line(
                "2026-08-24 16:10:02",
                target_id,
                description_sha256="7" * 64,
            )
        ).encode()
    )

    posts = extractor.normalise_canonical_posts(records, [], b"v" * 32)
    post = posts[0]
    conversations, _ = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert post["first_observed_at"] == "2026-08-24T15:10:01Z"
    assert post["last_observed_at"] == "2026-08-24T15:10:02Z"
    assert {
        item["record_fingerprint"] for item in post["source_provenance"]
    } == {record.record_fingerprint for record in records}
    assert len(conversations) == 1
    assert conversations[0]["turns"][0]["post_id"] == target_id


def test_v6_validation_rejects_corrupt_canonical_visual_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        first_exchange()
        + reply_media_context_line("2026-08-24 16:10:05", "100")
        + reply_visual_description_line(
            "2026-08-24 16:10:06", "100", description_sha256="8" * 64
        )
        + continuation(),
    )
    run_scan(project, output)
    batch = current_batch(output)
    original_posts = rows(output, "canonical-posts.jsonl")

    cases = (
        ("media_not_list", "reply media context observations are not a list"),
        ("visual_not_list", "reply visual event history is not a list"),
        ("missing_summary", "reply visual metadata fields are missing"),
        ("duplicate_media_fingerprint", "fingerprints are duplicated"),
        ("invalid_visual_fingerprint", "visual event fingerprint is invalid"),
        ("invalid_status_call", "visual event contract is invalid"),
        ("invalid_hash", "visual event contract is invalid"),
        ("negative_media_overflow", "media context overflow count is invalid"),
        ("boolean_visual_overflow", "visual event overflow count is invalid"),
        ("incorrect_overflow", "media context overflow count is inconsistent"),
        ("summary_mismatch", "visual context summary is inconsistent"),
    )
    for case, expected_problem in cases:
        posts = json.loads(json.dumps(original_posts))
        post = next(row for row in posts if row["post_id"] == "100")
        if case == "media_not_list":
            post["reply_media_context_observations"] = {}
        elif case == "visual_not_list":
            post["reply_visual_description_attempts"] = {}
        elif case == "missing_summary":
            post.pop("reply_visual_context_summary")
        elif case == "duplicate_media_fingerprint":
            post["reply_media_context_observations"].append(
                dict(post["reply_media_context_observations"][0])
            )
        elif case == "invalid_visual_fingerprint":
            post["reply_visual_description_attempts"][0][
                "record_fingerprint"
            ] = "A" * 64
        elif case == "invalid_status_call":
            event = post["reply_visual_description_attempts"][0]
            event["status"] = "paused"
            event["description_sha256"] = None
            event["visual_analysis_call_count"] = 1
        elif case == "invalid_hash":
            post["reply_visual_description_attempts"][0][
                "description_sha256"
            ] = "A" * 64
        elif case == "negative_media_overflow":
            post["reply_media_context_other_count"] = -1
        elif case == "boolean_visual_overflow":
            post["reply_visual_description_other_count"] = True
        elif case == "incorrect_overflow":
            post["reply_media_context_other_count"] = 1
        elif case == "summary_mismatch":
            post["reply_visual_context_summary"]["analysis_attempt_count"] = 0
        rewrite_batch_posts_and_hashes(batch, posts)

        problems = extractor._validate_batch_directory(
            batch,
            require_immutable=False,
            expected_boundary=BOUNDARY,
        )

        assert any(expected_problem in problem for problem in problems), (
            case,
            problems,
        )


def test_v6_validation_rejects_conversation_visual_metadata_mismatch(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        first_exchange()
        + reply_media_context_line("2026-08-24 16:10:05", "100")
        + reply_visual_description_line("2026-08-24 16:10:06", "100")
        + continuation(),
    )
    run_scan(project, output)
    batch = current_batch(output)
    conversations = rows(output, "conversations.jsonl")
    turn = next(
        turn
        for turn in conversations[0]["turns"]
        if turn["post_id"] == "100"
    )
    turn["reply_media_context_observations"][0]["photo_count"] = 1
    turn["reply_visual_context_summary"] = (
        extractor._derive_reply_visual_context_summary(turn)
    )
    rewrite_batch_conversations_and_hashes(batch, conversations)

    problems = extractor._validate_batch_directory(
        batch,
        require_immutable=False,
        expected_boundary=BOUNDARY,
    )

    assert any(
        "conversation turn visual metadata disagrees with canonical post"
        in problem
        for problem in problems
    )


def test_v6_validation_rejects_candidate_visual_metadata_corruption(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        first_exchange()
        + reply_media_context_line("2026-08-24 16:10:05", "100")
        + reply_visual_description_line("2026-08-24 16:10:06", "100")
        + continuation(),
    )
    run_scan(project, output)
    batch = current_batch(output)
    original_candidates = rows(output, "review-candidates.jsonl")
    assert original_candidates

    for case, expected_problem in (
        ("missing_summaries", "review candidate schema is incomplete"),
        ("inconsistent_summary", "visual summaries are inconsistent"),
        ("duplicate_summary", "visual summary post IDs are duplicated"),
        (
            "path_turn_mismatch",
            "path turn visual metadata disagrees with canonical post",
        ),
    ):
        candidates = json.loads(json.dumps(original_candidates))
        candidate = candidates[0]
        if case == "missing_summaries":
            candidate.pop("reply_visual_context_summaries")
        elif case == "inconsistent_summary":
            candidate["reply_visual_context_summaries"][0][
                "analysis_attempt_count"
            ] = 0
        elif case == "duplicate_summary":
            candidate["reply_visual_context_summaries"].append(
                dict(candidate["reply_visual_context_summaries"][0])
            )
        elif case == "path_turn_mismatch":
            turn = next(
                turn
                for turn in candidate["path_turns"]
                if turn["post_id"] == "100"
            )
            turn["reply_media_context_observations"][0]["photo_count"] = 1
            turn["reply_visual_context_summary"] = (
                extractor._derive_reply_visual_context_summary(turn)
            )
            candidate["reply_visual_context_summaries"] = (
                extractor._reply_visual_context_summaries(
                    candidate["path_turns"]
                )
            )
        rewrite_batch_candidates_and_hashes(batch, candidates)

        problems = extractor._validate_batch_directory(
            batch,
            require_immutable=False,
            expected_boundary=BOUNDARY,
        )

        assert any(expected_problem in problem for problem in problems), (
            case,
            problems,
        )


def test_reply_media_context_omitted_counts_stay_exact_across_reparsed_appends(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    coverage = log_line("2026-08-24 16:00:00", "Boundary coverage")
    observations = [
        reply_media_context_line(
            f"2026-08-24 16:10:{index:02d}", "907", photo_count=1
        )
        for index in range(18)
    ]
    path = write_active(project, coverage + "".join(observations))
    run_scan(project, output)
    assert rows(output, "canonical-posts.jsonl")[0][
        "reply_media_context_other_count"
    ] == 2

    new_observation = reply_media_context_line(
        "2026-08-24 16:12:00", "907", photo_count=1
    )
    path.write_text(
        coverage + "".join(observations) + new_observation,
        encoding="utf-8",
    )
    run_scan(project, output)
    assert rows(output, "canonical-posts.jsonl")[0][
        "reply_media_context_other_count"
    ] == 3

    path.write_text(
        coverage + "".join(observations) + new_observation + observations[0],
        encoding="utf-8",
    )
    run_scan(project, output)
    post = rows(output, "canonical-posts.jsonl")[0]
    assert len(post["reply_media_context_observations"]) == 16
    assert post["reply_media_context_other_count"] == 3


def test_reply_visual_metadata_propagates_without_changing_candidate_selection(
    tmp_path: Path,
) -> None:
    raw_author = "raw-user-visual-private"
    base = (
        first_exchange(author_id=raw_author)
        + continuation(author_id=raw_author)
        + publish_reply("102", "103", author_id=raw_author)
        + continuation(
            author_id=raw_author,
            post_id="104",
            parent_id="103",
            text="One more substantive continuation.",
            local_time="2026-08-24 16:30",
        )
    )
    secret_url = "https://private.invalid/source-photo.jpg"
    secret_description = "PRIVATE VISUAL DESCRIPTION"
    retained_analysis = {
        "images": [
            {
                "index": index,
                "literal_description": (
                    secret_description if index == 1 else "A second private description"
                ),
                "visible_text": [f"PRIVATE OCR {index}"],
                "salient_elements": [f"private element {index}"],
                "apparent_message": "A private apparent message",
                "uncertainties": ["A private uncertainty"],
            }
            for index in (1, 2)
        ],
        "combined_context": "PRIVATE COMBINED VISUAL CONTEXT",
        "relationship_to_contribution": "PRIVATE VISUAL RELATIONSHIP",
    }
    retained_analysis_hash = hashlib.sha256(
        json.dumps(
            retained_analysis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with_metadata = (
        first_exchange(author_id=raw_author)
        + reply_media_context_line("2026-08-24 16:10:05", "100")
        + reply_visual_description_line(
            "2026-08-24 16:10:06",
            "100",
            description_sha256=retained_analysis_hash,
            analysis=retained_analysis,
        )
        + reply_visual_description_line(
            "2026-08-24 16:10:07",
            "100",
            image_url=secret_url,
            visual_description=secret_description,
        )
        + continuation(author_id=raw_author)
        + reply_media_context_line(
            "2026-08-24 16:20:02", "102", photo_count=2
        )
        + publish_reply("102", "103", author_id=raw_author)
        + continuation(
            author_id=raw_author,
            post_id="104",
            parent_id="103",
            text="One more substantive continuation.",
            local_time="2026-08-24 16:30",
        )
        + reply_media_context_line(
            "2026-08-24 16:30:02", "104", photo_count=1
        )
        + reply_visual_description_line(
            "2026-08-24 16:30:03",
            "104",
            status="provider_error",
            supplied_image_count=1,
        )
    )
    base_project = tmp_path / "base-project"
    visual_project = tmp_path / "visual-project"
    base_output = tmp_path / "base-output"
    visual_output = tmp_path / "visual-output"
    write_active(base_project, base)
    write_active(visual_project, with_metadata)
    run_scan(base_project, base_output)
    run_scan(visual_project, visual_output)

    base_candidates = rows(base_output, "review-candidates.jsonl")
    visual_candidates = rows(visual_output, "review-candidates.jsonl")
    assert len(base_candidates) == len(visual_candidates) == 1
    assert base_candidates[0]["review_reason_codes"] == visual_candidates[0][
        "review_reason_codes"
    ]
    summaries = visual_candidates[0]["reply_visual_context_summaries"]
    assert [summary["post_id"] for summary in summaries] == ["100", "102", "104"]
    assert [summary["analysis_observation_status"] for summary in summaries] == [
        "analysed",
        "not_observed",
        "attempted_not_analysed",
    ]
    conversation = rows(visual_output, "conversations.jsonl")[0]
    summary_by_post = {
        turn["post_id"]: turn["reply_visual_context_summary"]
        for turn in conversation["turns"]
        if turn["post_id"] in {"100", "102", "104"}
    }
    assert summary_by_post["100"]["successful_description_sha256s"] == [
        retained_analysis_hash
    ]

    extractor.freeze_review_pack(
        output_root=visual_output,
        pack_name="visual-context",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    markdown = (
        visual_output / "review-packs" / "visual-context" / "review-pack.md"
    ).read_text(encoding="utf-8")
    canonical_material = json.dumps(
        {
            "posts": rows(visual_output, "canonical-posts.jsonl"),
            "conversations": rows(visual_output, "conversations.jsonl"),
            "candidates": visual_candidates,
        }
    )

    assert (
        "Visual context: 2 native photos; collection supplied; analysis analysed; "
        "1 analysis call; 1 successful description; 1 distinct hash; hash "
        f"{retained_analysis_hash[:12]}."
        in markdown
    )
    assert "no visual-analysis event observed" in markdown
    assert (
        "analysis provider_error; 1 analysis call; no successful description"
        in markdown
    )
    assert retained_analysis_hash not in markdown
    for forbidden in (
        secret_url,
        secret_description,
        "PRIVATE OCR",
        "PRIVATE COMBINED VISUAL CONTEXT",
        "PRIVATE VISUAL RELATIONSHIP",
        raw_author,
    ):
        assert forbidden not in markdown
        assert forbidden not in canonical_material


def test_review_pack_qualifies_incomplete_and_not_attempted_visual_history(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    root_visual_events = reply_visual_description_line(
        "2026-08-24 16:10:06",
        "100",
        description_sha256="9" * 64,
    ) + "".join(
        reply_visual_description_line(
            f"2026-08-24 16:10:{second:02d}",
            "100",
            status="provider_error",
        )
        for second in range(7, 23)
    )
    write_active(
        project,
        first_exchange()
        + reply_media_context_line("2026-08-24 16:10:05", "100")
        + root_visual_events
        + continuation()
        + reply_media_context_line("2026-08-24 16:20:02", "102", photo_count=1)
        + reply_visual_description_line(
            "2026-08-24 16:20:03",
            "102",
            status="paused",
            supplied_image_count=1,
        ),
    )
    run_scan(project, output)

    extractor.freeze_review_pack(
        output_root=output,
        pack_name="bounded-visual-context",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    markdown = (
        output
        / "review-packs"
        / "bounded-visual-context"
        / "review-pack.md"
    ).read_text(encoding="utf-8")

    assert "retained visual history is incomplete" in markdown
    assert "1 older visual event was omitted" in markdown
    assert (
        "no successful description is present among the retained events"
        in markdown
    )
    assert "analysis paused; no analysis call attempted" in markdown
    assert "analysis paused; 1 analysis call" not in markdown


def test_ignored_structured_event_counts_are_aggregated_in_source_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        first_exchange()
        + "".join(
            event_line(
                f"2026-08-24 16:{30 + index:02d}:00",
                "unrelated_event",
                id=str(index),
            )
            for index in range(5)
        ),
    )

    run_scan(project, output)
    source_manifest = extractor._strict_read_json(
        current_batch(output) / "source-manifest.json"
    )
    assert isinstance(source_manifest, dict)

    assert source_manifest["structured_event_statistics"][
        "ignored_structured_event_count"
    ] == 5
    assert source_manifest["structured_event_statistics"][
        "ignored_target_like_event_count"
    ] == 5
    assert source_manifest["source_warnings"] == []


def test_registered_event_fields_fail_closed_on_missing_or_conflicting_target() -> None:
    target_a = snowflake_id("2026-08-24T15:10:00Z")
    target_b = snowflake_id("2026-08-24T15:11:00Z")
    records, _ = extractor.parse_log_records(
        (
            event_line(
                "2026-08-24 16:10:00",
                "mention_reply_posted",
                mention_id=target_a,
                target_id=target_b,
                reply_post_id=snowflake_id("2026-08-24T15:12:00Z"),
            )
            + event_line(
                "2026-08-24 16:11:00",
                "ai_reply_pipeline_decision",
                id="generic-only",
            )
        ).encode()
    )
    statistics: dict[str, int] = {}

    posts = extractor.normalise_canonical_posts(
        records, [], b"x" * 32, parser_statistics=statistics
    )

    assert posts == []
    assert statistics["ambiguous_registered_event_count"] == 1
    assert statistics["registered_event_missing_target_count"] == 1


def test_every_registered_contract_rejects_unregistered_generic_id_field() -> None:
    lines = "".join(
        event_line(
            f"2026-08-24 16:{index:02d}:00",
            kind,
            id=str(10_000 + index),
        )
        for index, kind in enumerate(
            extractor.STRUCTURED_CONVERSATION_EVENT_FIELDS, start=1
        )
    )
    records, _ = extractor.parse_log_records(lines.encode())

    posts = extractor.normalise_canonical_posts(records, [], b"y" * 32)

    assert posts == []


def test_rotation_with_same_content_is_cached_and_does_not_change_snapshot(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    before = current_target(output)
    active.replace(project / "mrsMThatcher.log.1")
    (project / "mrsMThatcher.log").write_text("", encoding="utf-8")

    result = run_scan(project, output)

    assert result["status"] == "no_change"
    assert current_target(output) == before


def test_retention_keeps_current_recent_and_newest_daily_and_prunes_expired(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    cutoffs = [
        "2026-08-27T19:00:00Z",
        "2026-08-27T20:00:00Z",
        "2026-08-27T21:00:00Z",
        "2026-08-27T22:00:00Z",
        "2026-08-27T23:00:00Z",
    ]
    for index, cutoff in enumerate(cutoffs, start=1):
        append_independent_root(
            active,
            created_at=f"2026-08-24T{15 + index:02d}:30:00Z",
            local_time=f"2026-08-24 {16 + index:02d}:30:00",
            author=f"user-{index}",
        )
        run_scan(project, output, until=cutoff)
    batches = sorted((output / "batches").iterdir(), key=lambda path: path.name)
    assert len(batches) == 6
    current = current_batch(output).name
    now = extractor.parse_aware_timestamp("2026-12-01T12:00:00Z", option="test")
    assigned = {
        batches[0].name: "2026-11-30T10:00:00Z",
        batches[1].name: "2026-11-25T08:00:00Z",
        batches[2].name: "2026-11-25T20:00:00Z",
        batches[3].name: "2026-10-15T12:00:00Z",
        batches[4].name: "2026-08-01T12:00:00Z",
        batches[5].name: "2026-07-01T12:00:00Z",
    }
    for batch in batches:
        set_batch_creation_time(batch, assigned[batch.name])

    result = extractor.apply_batch_retention(
        output, now=now, expected_boundary=BOUNDARY
    )
    retained = {path.name for path in (output / "batches").iterdir()}

    assert current in retained
    assert batches[0].name in retained
    assert batches[2].name in retained
    assert batches[3].name in retained
    assert batches[1].name not in retained
    assert batches[4].name not in retained
    assert set(result.pruned_batch_ids) == {batches[1].name, batches[4].name}
    assert result.retained_automatic_bytes < extractor.MAX_AUTOMATIC_BATCH_BYTES


def test_retention_does_not_load_protected_current_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    protected = current_batch(output)

    def reject_jsonl(_path: Path) -> list[dict[str, object]]:
        raise AssertionError("protected current JSONL was loaded")

    monkeypatch.setattr(extractor, "_load_jsonl", reject_jsonl)
    result = extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-08-28T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert protected.exists()
    assert result.retention_lightweight_protected_batch_count == 1
    assert result.retention_full_validation_batch_count == 0


def test_retention_does_not_load_review_referenced_batch_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange() + continuation())
    run_scan(project, output)
    referenced = current_batch(output)
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="lightweight-reference",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    set_batch_creation_time(referenced, "2026-01-01T00:00:00Z")

    def reject_jsonl(_path: Path) -> list[dict[str, object]]:
        raise AssertionError("review-referenced JSONL was loaded")

    monkeypatch.setattr(extractor, "_load_jsonl", reject_jsonl)
    result = extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert referenced.exists()
    assert result.retention_full_validation_batch_count == 0


def test_retention_does_not_load_recent_historical_batch_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    recent = current_batch(output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    set_batch_creation_time(recent, "2026-11-30T23:00:00Z")
    set_batch_creation_time(current_batch(output), "2026-11-30T23:30:00Z")

    def reject_jsonl(_path: Path) -> list[dict[str, object]]:
        raise AssertionError("recent protected JSONL was loaded")

    monkeypatch.setattr(extractor, "_load_jsonl", reject_jsonl)
    result = extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert recent.exists()
    assert result.retention_full_validation_batch_count == 0


def test_retention_fully_validates_each_deletion_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    candidate = current_batch(output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    set_batch_creation_time(candidate, "2026-01-01T00:00:00Z")
    validated: list[str] = []
    real_validate = extractor._validate_batch_directory

    def record_validation(batch: Path, **kwargs: object) -> list[str]:
        validated.append(batch.name)
        return real_validate(batch, **kwargs)

    monkeypatch.setattr(extractor, "_validate_batch_directory", record_validation)
    result = extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert validated == [candidate.name]
    assert result.retention_full_validation_batch_count == 1
    assert not candidate.exists()


def test_corrupt_later_deletion_candidate_prevents_all_pruning(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange() + continuation())
    run_scan(project, output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="middle",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    append_independent_root(
        active,
        created_at="2026-08-24T19:00:00Z",
        local_time="2026-08-24 20:00:00",
        author="current",
    )
    run_scan(project, output, until="2026-08-28T20:00:00Z")
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="retention-fail-closed",
        since=BOUNDARY,
        until="2026-08-29T00:00:00Z",
        include_open=True,
    )
    batches = sorted((output / "batches").iterdir(), key=lambda path: path.name)
    for index, batch in enumerate(batches, start=1):
        set_batch_creation_time(batch, f"2026-01-0{index}T00:00:00Z")
    corrupt = batches[1] / "canonical-posts.jsonl"
    os.chmod(corrupt, 0o600)
    corrupt.write_bytes(corrupt.read_bytes() + b"{}\n")
    os.chmod(corrupt, 0o400)
    before_batches = {path.name for path in batches}
    before_current = current_target(output)
    pack = output / "review-packs" / "retention-fail-closed"
    before_pack = {path.name: path.read_bytes() for path in pack.iterdir()}

    with pytest.raises(extractor.ExtractorError, match="deletion candidate"):
        extractor.apply_batch_retention(
            output,
            now=extractor.parse_aware_timestamp(
                "2026-12-01T00:00:00Z", option="test"
            ),
            expected_boundary=BOUNDARY,
        )

    assert {path.name for path in (output / "batches").iterdir()} == before_batches
    assert current_target(output) == before_current
    assert {path.name: path.read_bytes() for path in pack.iterdir()} == before_pack


def test_review_pack_reference_protects_old_batch_and_pack_bytes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange() + continuation())
    run_scan(project, output)
    referenced = current_batch(output)
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="reference-protection",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
        frozen_at=extractor.parse_aware_timestamp(
            "2026-08-28T00:00:00Z", option="test"
        ),
    )
    pack = output / "review-packs" / "reference-protection"
    before_pack = {
        path.name: path.read_bytes() for path in pack.iterdir() if path.is_file()
    }
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    set_batch_creation_time(referenced, "2026-01-01T00:00:00Z")
    set_batch_creation_time(current_batch(output), "2026-01-02T00:00:00Z")

    extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert referenced.exists()
    assert before_pack == {
        path.name: path.read_bytes() for path in pack.iterdir() if path.is_file()
    }


def test_invalid_review_pack_and_malformed_batch_fail_closed_without_deletion(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange() + continuation())
    run_scan(project, output)
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="corrupt-pack",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    old = sorted((output / "batches").iterdir())[0]
    set_batch_creation_time(old, "2026-01-01T00:00:00Z")
    pack_manifest = output / "review-packs" / "corrupt-pack" / "manifest.json"
    os.chmod(pack_manifest, 0o600)
    pack_manifest.write_text("{}\n", encoding="utf-8")
    os.chmod(pack_manifest, 0o400)
    outside = tmp_path / "outside"
    outside.mkdir()
    malicious = output / "batches" / "20260101T000000Z-aaaaaaaaaaaa"
    malicious.symlink_to(outside, target_is_directory=True)

    with pytest.raises(extractor.ExtractorError, match="review-pack provenance"):
        extractor.apply_batch_retention(
            output,
            now=extractor.parse_aware_timestamp(
                "2026-12-01T00:00:00Z", option="test"
            ),
            expected_boundary=BOUNDARY,
        )

    assert old.exists()
    assert malicious.is_symlink()
    assert outside.exists()


def test_unchanged_scan_applies_retention_and_compacts_source_cache(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    old = sorted((output / "batches").iterdir())[0]
    set_batch_creation_time(old, "2025-01-01T00:00:00Z")
    active.replace(project / "mrsMThatcher.log.1")
    write_active(project, "")
    run_scan(project, output, until="2026-08-28T20:00:00Z")
    state = extractor.read_extractor_state(output, missing_ok=False)
    assert state is not None
    assert len(state["source_file_cache"]) == 2
    (project / "mrsMThatcher.log.1").unlink()

    result = run_scan(project, output, until="2026-08-28T21:00:00Z")
    state = extractor.read_extractor_state(output, missing_ok=False)
    assert state is not None

    assert result["status"] == "no_change"
    assert not old.exists()
    assert len(state["source_file_cache"]) == 1
    assert all(
        entry["parser_version"] == extractor.PARSER_VERSION
        for entry in state["source_file_cache"].values()
    )


def test_budget_retention_removes_oldest_additional_unreferenced_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    for index in range(2):
        append_independent_root(
            active,
            created_at=f"2026-08-24T{18 + index:02d}:00:00Z",
            local_time=f"2026-08-24 {19 + index:02d}:00:00",
            author=f"budget-{index}",
        )
        run_scan(project, output, until=f"2026-08-28T{19 + index:02d}:00:00Z")
    batches = sorted((output / "batches").iterdir(), key=lambda path: path.name)
    for index, batch in enumerate(batches):
        set_batch_creation_time(batch, f"2026-11-30T2{index}:00:00Z")
    sizes = {batch.name: 100 for batch in batches}
    real_tree_bytes = extractor._path_tree_bytes

    def synthetic_size(path: Path) -> int:
        if path.parent == output / "batches" and path.name in sizes:
            return sizes[path.name]
        return real_tree_bytes(path)

    monkeypatch.setattr(extractor, "MAX_AUTOMATIC_BATCH_BYTES", 250)
    monkeypatch.setattr(extractor, "_path_tree_bytes", synthetic_size)
    result = extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    retained = {path.name for path in (output / "batches").iterdir()}
    assert batches[0].name not in retained
    assert batches[1].name in retained
    assert batches[2].name in retained
    assert result.pruned_batch_ids == (batches[0].name,)
    assert result.retained_automatic_bytes == 200


def test_budget_retention_preserves_large_current_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    old = current_batch(output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    current = current_batch(output)
    set_batch_creation_time(old, "2026-11-30T22:00:00Z")
    set_batch_creation_time(current, "2026-11-30T23:00:00Z")
    real_tree_bytes = extractor._path_tree_bytes

    def synthetic_size(path: Path) -> int:
        if path == old:
            return 20
        if path == current:
            return 95
        return real_tree_bytes(path)

    monkeypatch.setattr(extractor, "MAX_AUTOMATIC_BATCH_BYTES", 100)
    monkeypatch.setattr(extractor, "_path_tree_bytes", synthetic_size)
    extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert current.exists()
    assert current_target(output) == f"batches/{current.name}"
    assert not old.exists()


def test_budget_retention_preserves_review_reference_and_pack_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange() + continuation())
    run_scan(project, output)
    referenced = current_batch(output)
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="budget-reference",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    for index in range(2):
        append_independent_root(
            active,
            created_at=f"2026-08-24T{18 + index:02d}:00:00Z",
            local_time=f"2026-08-24 {19 + index:02d}:00:00",
            author=f"later-{index}",
        )
        run_scan(project, output, until=f"2026-08-28T{19 + index:02d}:00:00Z")
    batches = sorted((output / "batches").iterdir(), key=lambda path: path.name)
    middle = batches[1]
    current = current_batch(output)
    for index, batch in enumerate(batches):
        set_batch_creation_time(batch, f"2026-11-30T2{index}:00:00Z")
    pack = output / "review-packs" / "budget-reference"
    before_pack = {path.name: path.read_bytes() for path in pack.iterdir()}
    real_tree_bytes = extractor._path_tree_bytes

    def synthetic_size(path: Path) -> int:
        if path.parent == output / "batches":
            return 100
        return real_tree_bytes(path)

    monkeypatch.setattr(extractor, "MAX_AUTOMATIC_BATCH_BYTES", 200)
    monkeypatch.setattr(extractor, "_path_tree_bytes", synthetic_size)
    extractor.apply_batch_retention(
        output,
        now=extractor.parse_aware_timestamp("2026-12-01T00:00:00Z", option="test"),
        expected_boundary=BOUNDARY,
    )

    assert referenced.exists()
    assert current.exists()
    assert not middle.exists()
    assert {path.name: path.read_bytes() for path in pack.iterdir()} == before_pack


def test_changed_scan_fails_when_protected_plus_projected_exceeds_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    before_current = current_target(output)
    before_batches = {path.name for path in (output / "batches").iterdir()}
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    real_tree_bytes = extractor._path_tree_bytes

    def synthetic_size(path: Path) -> int:
        if path.parent == output / "batches":
            return 100
        return real_tree_bytes(path)

    monkeypatch.setattr(extractor, "MAX_AUTOMATIC_BATCH_BYTES", 100)
    monkeypatch.setattr(extractor, "_path_tree_bytes", synthetic_size)
    with pytest.raises(extractor.ExtractorError, match="protected automatic"):
        run_scan(project, output, until="2026-08-28T19:00:00Z")

    assert current_target(output) == before_current
    assert {path.name for path in (output / "batches").iterdir()} == before_batches
    assert not any(
        path.name.startswith(".tmp-") for path in (output / "batches").iterdir()
    )
    status = extractor.get_status(output)
    assert "exceed budget" in str(status["last_retention_error"])
    assert status["automatic_batch_budget_bytes"] == 100


def test_unchanged_scan_prunes_to_byte_budget_without_new_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    old = current_batch(output)
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    run_scan(project, output, until="2026-08-28T19:00:00Z")
    current = current_batch(output)
    set_batch_creation_time(old, "2026-11-30T22:00:00Z")
    set_batch_creation_time(current, "2026-11-30T23:00:00Z")
    real_tree_bytes = extractor._path_tree_bytes

    def synthetic_size(path: Path) -> int:
        if path.parent == output / "batches":
            return 60
        return real_tree_bytes(path)

    monkeypatch.setattr(extractor, "MAX_AUTOMATIC_BATCH_BYTES", 100)
    monkeypatch.setattr(extractor, "_path_tree_bytes", synthetic_size)
    result = run_scan(project, output, until="2026-08-28T20:00:00Z")

    assert result["status"] == "no_change"
    assert current_batch(output) == current
    assert not old.exists()
    assert [path.name for path in (output / "batches").iterdir()] == [current.name]


def test_disk_preflight_refuses_before_temporary_batch_is_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    before_current = current_target(output)
    before_batches = {path.name for path in (output / "batches").iterdir()}
    append_independent_root(
        active,
        created_at="2026-08-24T18:00:00Z",
        local_time="2026-08-24 19:00:00",
        author="later",
    )
    usage_type = type(shutil.disk_usage(output))
    monkeypatch.setattr(
        extractor.shutil,
        "disk_usage",
        lambda _path: usage_type(10_000, 9_999, 1),
    )

    with pytest.raises(extractor.ExtractorError, match="minimum_free_bytes=10737418240"):
        run_scan(project, output, until="2026-08-28T19:00:00Z")

    assert current_target(output) == before_current
    assert {path.name for path in (output / "batches").iterdir()} == before_batches
    assert not any(
        path.name.startswith(".tmp-") for path in (output / "batches").iterdir()
    )
    status = extractor.get_status(output)
    assert status["minimum_free_bytes"] == 10 * 1024 * 1024 * 1024
    assert "insufficient free space" in str(status["last_retention_error"])


def test_status_and_validation_account_retained_storage_accurately(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)

    status = extractor.get_status(output)
    validation = extractor.validate_output_root(output)

    assert status["retained_batch_count"] == 1
    assert status["retained_automatic_bytes"] > 0
    assert status["total_extractor_bytes"] >= status["retained_automatic_bytes"]
    assert validation["valid"] is True
    assert validation["retained_automatic_bytes"] == status["retained_automatic_bytes"]
    assert validation["total_extractor_bytes"] == status["total_extractor_bytes"]


def test_malformed_batch_symlink_blocks_retention_and_is_never_followed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")
    malicious = output / "batches" / "20260101T000000Z-bbbbbbbbbbbb"
    malicious.symlink_to(outside, target_is_directory=True)

    with pytest.raises(extractor.ExtractorError, match="invalid automatic batch"):
        extractor.apply_batch_retention(
            output,
            now=extractor.parse_aware_timestamp(
                "2026-12-01T00:00:00Z", option="test"
            ),
            expected_boundary=BOUNDARY,
        )

    assert malicious.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_send_attempt_history_is_bounded_per_target() -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    lines = "".join(
        create_attempt(
            target_id,
            f"{index:064x}",
            local_time=f"2026-08-24 16:{10 + index:02d}:00",
        )
        for index in range(1, 8)
    )
    records, _ = extractor.parse_log_records(lines.encode())

    post = extractor.normalise_canonical_posts(records, [], b"z" * 32)[0]

    assert len(post["send_attempts"]) == extractor.MAX_SEND_ATTEMPTS_PER_TARGET
    assert [attempt["transaction_id"] for attempt in post["send_attempts"]] == [
        f"{index:064x}" for index in range(3, 8)
    ]


def test_prospective_version_7_and_registered_v6_predecessor_are_exact() -> None:
    assert extractor.SCHEMA_VERSION == 7
    assert extractor.EXTRACTOR_VERSION == "prospective-conversation-extractor-v7"
    assert extractor.PARSER_VERSION == "prospective-conversation-log-parser-v7"
    assert extractor.REGISTERED_REBUILD_SOURCE == (
        6,
        "prospective-conversation-extractor-v6",
        "prospective-conversation-log-parser-v6",
    )
    assert "reply_visual_description" in (
        extractor.STRUCTURED_CONVERSATION_EVENT_FIELDS
    )
    assert "reply_visual_description" not in extractor.PIPELINE_EVENT_KINDS


def test_state_version_mismatch_fails_before_prior_canonical_data_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    state_path = output / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    state["extractor_version"] = "older-extractor"
    rewrite_private_json(state_path, state, mode=0o600)
    monkeypatch.setattr(
        extractor,
        "_load_prior_posts",
        lambda *_args, **_kwargs: pytest.fail("prior canonical data was loaded"),
    )

    with pytest.raises(extractor.ExtractorError, match="version mismatch"):
        run_scan(project, output)


def test_parser_version_mismatch_forces_source_reparse(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_active(project, first_exchange())
    inventory = extractor.collect_source_inventory(project)
    digest = inventory.files[0].content_sha256
    prior_state = {
        "source_file_cache": {
            digest: {
                "earliest_record_timestamp": "2026-08-24T15:10:00Z",
                "latest_record_timestamp": "2026-08-24T15:10:04Z",
                "parser_version": "old-parser",
                "processed_cutoff": DEFAULT_UNTIL,
            }
        }
    }

    parsed = extractor.parse_incremental_sources(
        inventory,
        prior_state,
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
    )

    assert parsed.parsed_source_hash_count == 1
    assert parsed.source_cache[digest]["parser_version"] == extractor.PARSER_VERSION


def test_validation_rejects_mixed_parser_versions_in_canonical_snapshot(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    batch = current_batch(output)
    posts = rows(output, "canonical-posts.jsonl")
    posts[0]["derivation_parser_version"] = "different-parser"
    rewrite_batch_posts_and_hashes(batch, posts)

    problems = extractor._validate_batch_directory(
        batch,
        require_immutable=False,
        expected_boundary=BOUNDARY,
    )

    assert any("canonical post parser mismatch" in value for value in problems)


def test_registered_rebuild_v6_to_v7_preserves_key_pseudonyms_and_old_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    write_active(
        project,
        log_line("2026-08-24 15:00:00", "Unrelated pre-boundary coverage record")
        + first_exchange(author_id="stable-rebuild-user"),
    )
    run_scan(project, old_root)
    mark_state_as_registered_v6(old_root)
    old_key = (old_root / "state" / "pseudonym-key").read_bytes()
    old_author = next(
        post["author_key"]
        for post in rows(old_root, "canonical-posts.jsonl")
        if post["author_role"] == "user"
    )
    before = {
        str(path.relative_to(old_root)): path.read_bytes()
        for path in old_root.rglob("*")
        if path.is_file()
    }

    result = extractor.rebuild_to_new_root(
        project_dir=project,
        source_output_root=old_root,
        new_output_root=new_root,
        until=DEFAULT_UNTIL,
    )
    new_author = next(
        post["author_key"]
        for post in rows(new_root, "canonical-posts.jsonl")
        if post["author_role"] == "user"
    )

    assert result["status"] == "rebuilt"
    assert (new_root / "state" / "pseudonym-key").read_bytes() == old_key
    assert new_author == old_author
    assert extractor.validate_output_root(new_root)["valid"] is True
    assert before == {
        str(path.relative_to(old_root)): path.read_bytes()
        for path in old_root.rglob("*")
        if path.is_file()
    }


def test_registered_v6_to_v7_rebuild_reparses_single_call_semantics(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    write_active(
        project,
        log_line("2026-08-24 15:00:00", "Pre-boundary coverage")
        + log_line(
            "2026-09-04 12:00:00",
            "Considering mention id=900 author_id=700 text='Current contribution'",
        )
        + log_line(
            "2026-09-04 12:00:01",
            "Built single-call reply context target_id=900 turns=3 "
            "root_id=800 parent_id=850",
        )
        + event_line(
            "2026-09-04 12:00:02",
            "single_call_reply_decision",
            target_id="900",
            lane="mention",
            model_call_count=1,
            decision="no_reply",
            reply_kind="no_reply",
            reason_code="completed_exchange",
        ),
    )
    run_scan(project, old_root, until="2026-09-05T00:00:00Z")
    source_posts = rows(old_root, "canonical-posts.jsonl")
    source_post = next(post for post in source_posts if post["post_id"] == "900")
    source_post["root_post_id"] = None
    source_post["parent_post_id"] = None
    source_post["pipeline_stage_summaries"] = []
    rewrite_batch_posts_and_hashes(current_batch(old_root), source_posts)
    mark_state_as_registered_v6(old_root)

    result = extractor.rebuild_to_new_root(
        project_dir=project,
        source_output_root=old_root,
        new_output_root=new_root,
        until="2026-09-05T00:00:00Z",
    )
    rebuilt = next(
        post for post in rows(new_root, "canonical-posts.jsonl")
        if post["post_id"] == "900"
    )
    source_manifest = extractor._strict_read_json(
        current_batch(new_root) / "source-manifest.json"
    )
    assert isinstance(source_manifest, dict)

    assert result["status"] == "rebuilt"
    assert source_manifest["parsed_source_hash_count"] == 1
    assert rebuilt["schema_version"] == 7
    assert rebuilt["derivation_parser_version"] == (
        "prospective-conversation-log-parser-v7"
    )
    assert rebuilt["root_post_id"] == "800"
    assert rebuilt["parent_post_id"] == "850"
    assert rebuilt["pipeline_stage_summaries"][0]["event_kind"] == (
        "single_call_reply_decision"
    )
    unchanged_source = next(
        post for post in rows(old_root, "canonical-posts.jsonl")
        if post["post_id"] == "900"
    )
    assert unchanged_source["root_post_id"] is None
    assert unchanged_source["pipeline_stage_summaries"] == []


def test_registered_rebuild_refuses_existing_destination_and_insufficient_boundary_coverage(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    existing = tmp_path / "existing"
    existing.mkdir()
    write_active(project, first_exchange())
    run_scan(project, old_root)
    mark_state_as_registered_v6(old_root)

    with pytest.raises(extractor.ExtractorError, match="must not already exist"):
        extractor.rebuild_to_new_root(
            project_dir=project,
            source_output_root=old_root,
            new_output_root=existing,
            until=DEFAULT_UNTIL,
        )
    missing_coverage_root = tmp_path / "missing-coverage"
    with pytest.raises(extractor.ExtractorError, match="do not span"):
        extractor.rebuild_to_new_root(
            project_dir=project,
            source_output_root=old_root,
            new_output_root=missing_coverage_root,
            until=DEFAULT_UNTIL,
        )
    assert not missing_coverage_root.exists()


def test_identical_text_with_distinct_post_ids_remains_distinct(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    text = "".join(
        [
            log_line("2026-08-24 16:10:00", "Considering mention id=200 author_id=user-a text='Same text'"),
            log_line("2026-08-24 16:10:01", "Built AI reply context for mention 200. chain_items=0 immediate_parent=None quoted=False"),
            log_line("2026-08-24 16:11:00", "Considering mention id=201 author_id=user-b text='Same text'"),
            log_line("2026-08-24 16:11:01", "Built AI reply context for mention 201. chain_items=0 immediate_parent=None quoted=False"),
        ]
    )
    write_active(project, text)

    run_scan(project, output)

    posts = rows(output, "canonical-posts.jsonl")
    assert {row["post_id"] for row in posts} == {"200", "201"}
    assert len(rows(output, "conversations.jsonl")) == 2


def test_failed_state_publication_preserves_old_expired_current_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    before_state = state_bytes(output)
    before_current = current_target(output)
    old_current = current_batch(output)
    set_batch_creation_time(old_current, "2026-01-01T00:00:00Z")
    before_batches = sorted(path.name for path in (output / "batches").iterdir())
    scan_start = extractor.parse_aware_timestamp(
        "2026-12-01T12:00:00Z", option="test"
    )
    old_created = extractor.parse_aware_timestamp(
        str(extractor._strict_read_json(old_current / "manifest.json")["creation_timestamp"]),
        option="test",
    )
    assert old_created < scan_start - extractor.DAILY_BATCH_RETENTION
    with active.open("a", encoding="utf-8") as handle:
        handle.write(continuation())

    real_retention = extractor.apply_batch_retention
    retention_calls = 0
    real_metadata_reader = extractor._read_retention_batch_metadata
    metadata_reads = 0

    def counted_retention(*args: object, **kwargs: object) -> extractor.RetentionResult:
        nonlocal retention_calls
        retention_calls += 1
        return real_retention(*args, **kwargs)

    def counted_metadata(*args: object, **kwargs: object) -> extractor.RetentionBatchMetadata:
        nonlocal metadata_reads
        metadata_reads += 1
        return real_metadata_reader(*args, **kwargs)

    def fail_state(_root: Path, _value: object) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(extractor, "apply_batch_retention", counted_retention)
    monkeypatch.setattr(extractor, "_read_retention_batch_metadata", counted_metadata)
    monkeypatch.setattr(extractor, "_atomic_write_state", fail_state)
    with pytest.raises(OSError, match="injected"):
        extractor.run_scan(
            project_dir=project,
            output_root=output,
            prospective_start=BOUNDARY,
            until="2026-12-01T12:00:00Z",
            quiescence_hours=48,
            scan_start=scan_start,
        )

    assert state_bytes(output) == before_state
    assert current_target(output) == before_current
    assert old_current.exists()
    assert (output / "current").is_symlink()
    assert (output / "current").resolve() == old_current.resolve()
    assert sorted(path.name for path in (output / "batches").iterdir()) == before_batches
    assert retention_calls == 1
    assert metadata_reads == 1
    assert extractor.validate_output_root(output)["valid"] is True


def test_explicit_conversation_id_groups_posts() -> None:
    key = b"a" * 32
    records, _ = extractor.parse_log_records(
        (
            event_line("2026-08-24 16:10:00", "ai_reply_pipeline_decision", target_id="301", conversation_id="conv-x", incoming_text="First meaningful turn", target_created_at="2026-08-24T15:10:00Z")
            + event_line("2026-08-24 16:11:00", "ai_reply_pipeline_decision", target_id="302", conversation_id="conv-x", incoming_text="Second meaningful turn", target_created_at="2026-08-24T15:11:00Z")
        ).encode()
    )
    posts = extractor.normalise_canonical_posts(records, [], key)

    conversations, _candidates = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert len(conversations) == 1
    assert conversations[0]["conversation_id"] == "conv-x"


def test_root_and_parent_grouping_siblings_but_different_roots_stay_separate() -> None:
    key = b"b" * 32
    data = "".join(
        [
            event_line("2026-08-24 16:10:00", "ai_reply_pipeline_decision", target_id="400", root_post_id="400", incoming_text="Root one", target_created_at="2026-08-24T15:10:00Z"),
            event_line("2026-08-24 16:11:00", "ai_reply_pipeline_decision", target_id="401", root_post_id="400", parent_post_id="400", incoming_text="Branch one", target_created_at="2026-08-24T15:11:00Z"),
            event_line("2026-08-24 16:12:00", "ai_reply_pipeline_decision", target_id="402", root_post_id="400", parent_post_id="400", incoming_text="Branch two", target_created_at="2026-08-24T15:12:00Z"),
            event_line("2026-08-24 16:13:00", "ai_reply_pipeline_decision", target_id="403", root_post_id="403", incoming_text="Separate root", target_created_at="2026-08-24T15:13:00Z"),
        ]
    )
    records, _ = extractor.parse_log_records(data.encode())
    posts = extractor.normalise_canonical_posts(records, [], key)
    conversations, _ = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert sorted(row["user_turn_count"] for row in conversations) == [1, 3]
    assert {row["root_post_id"] for row in conversations} == {"400", "403"}


def test_author_identity_alone_never_joins_and_ambiguous_orphan_is_low_partial(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        "".join(
            [
                log_line("2026-08-24 16:10:00", "Considering mention id=500 author_id=same-user text='First unrelated post'"),
                log_line("2026-08-24 16:11:00", "Considering mention id=501 author_id=same-user text='Second unrelated post'"),
            ]
        ),
    )

    run_scan(project, output)
    conversations = rows(output, "conversations.jsonl")

    assert len(conversations) == 2
    assert all(row["prospective_status"] == "start_unknown" for row in conversations)
    assert all(row["completeness"] == "partial" for row in conversations)
    assert all(row["reconstruction_confidence"] == "low" for row in conversations)


def test_same_author_with_explicit_different_roots_remains_separate() -> None:
    key = b"c" * 32
    data = "".join(
        [
            event_line(
                "2026-08-24 16:10:00",
                "ai_reply_pipeline_decision",
                target_id="550",
                root_post_id="550",
                author_id="same-author",
                incoming_text="First root",
                target_created_at="2026-08-24T15:10:00Z",
            ),
            event_line(
                "2026-08-24 16:11:00",
                "ai_reply_pipeline_decision",
                target_id="551",
                root_post_id="551",
                author_id="same-author",
                incoming_text="Second root",
                target_created_at="2026-08-24T15:11:00Z",
            ),
        ]
    )
    records, _ = extractor.parse_log_records(data.encode())
    posts = extractor.normalise_canonical_posts(records, [], key)
    conversations, _ = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )

    assert len(conversations) == 2
    assert len({row["author_key"] for row in conversations}) == 1


def test_pre_boundary_conversation_and_continuation_remain_pre_boundary_and_pack_excludes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    text = "".join(
        [
            first_exchange(root_id="600", reply_id="601", local_prefix="2026-08-24 16:07"),
            continuation(post_id="602", parent_id="601", local_time="2026-08-24 16:20"),
        ]
    )
    write_active(project, text)
    run_scan(project, output)

    conversations = rows(output, "conversations.jsonl")
    assert conversations[0]["prospective_status"] == "pre_boundary"
    result = extractor.freeze_review_pack(
        output_root=output,
        pack_name="pre-boundary-excluded",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    assert result["conversation_count"] == 0


def test_unknown_start_excluded_boundary_mismatch_and_naive_timestamp_fail_closed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line("2026-08-24 16:10:00", "Considering mention id=700 author_id=user text='Meaningful but parent unknown'"),
    )
    run_scan(project, output)

    assert rows(output, "conversations.jsonl")[0]["prospective_status"] == "start_unknown"
    assert rows(output, "review-candidates.jsonl") == []
    with pytest.raises(extractor.ExtractorError, match="boundary mismatch"):
        extractor.run_scan(
            project_dir=project,
            output_root=output,
            prospective_start="2026-08-25T00:00:00Z",
            until=DEFAULT_UNTIL,
        )
    with pytest.raises(extractor.ExtractorError, match="timezone"):
        extractor.run_scan(
            project_dir=project,
            output_root=tmp_path / "other-output",
            prospective_start="2026-08-24T15:08:39",
            until=DEFAULT_UNTIL,
        )


def test_open_exactly_quiescent_and_late_turn_reopens(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    first_cutoff = "2026-08-26T15:10:04Z"
    run_scan(project, output, until=first_cutoff)
    assert rows(output, "conversations.jsonl")[0]["activity_status"] == "quiescent"
    with active.open("a", encoding="utf-8") as handle:
        handle.write(
            continuation(
                post_id="103",
                parent_id="101",
                local_time="2026-08-26 16:11",
            )
        )
    run_scan(project, output, until="2026-08-26T16:30:00Z")
    assert rows(output, "conversations.jsonl")[0]["activity_status"] == "open"


@pytest.mark.parametrize(
    ("text", "substantive", "reason"),
    [
        ("   ", False, "whitespace_only"),
        ("@somebody", False, "bare_handle"),
        ("https://example.test/path", False, "bare_url"),
        ("!", False, "single_punctuation_mark"),
        ("🙂", False, "isolated_symbol_or_emoji"),
        ("Thanks", False, "routine_one_word_greeting_or_thanks"),
        ("Wrong.", True, "meaningful_text_retained"),
        ("Why?", True, "meaningful_text_retained"),
        ("I am grieving.", True, "meaningful_text_retained"),
        ("A vital distinction.", True, "meaningful_text_retained"),
    ],
)
def test_conservative_substantive_detection(
    text: str, substantive: bool, reason: str
) -> None:
    result = extractor.substantive_result(text)
    assert (result.substantive, result.reason) == (substantive, reason)


def test_review_reasons_same_chain_third_turn_correction_and_multiple_replies(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        "".join(
            [
                first_exchange(),
                continuation(),
                publish_reply("102", "103"),
            ]
        ),
    )

    run_scan(project, output)
    candidate = rows(output, "review-candidates.jsonl")[0]
    reasons = set(candidate["review_reason_codes"])

    assert {
        "same_author_path_continuation",
        "multiple_account_replies_on_path",
        "third_or_later_substantive_path_turn",
        "explicit_correction_cue",
    } <= reasons
    assert "not what i said" in candidate["correction_cues"]
    assert not reasons & {
        "defect",
        "bad_reply",
        "proposition_substitution",
        "repair_required",
        "false_concession",
    }


def test_post_clarification_and_sibling_branch_activity_are_descriptive() -> None:
    conversation = {
        "account_turn_count": 2,
        "activity_status": "quiescent",
        "author_key": "user-a",
        "completeness": "complete",
        "conversation_key": "conversation-a",
        "last_activity_time": "2026-08-25T00:00:00Z",
        "prospective_status": "eligible",
        "reconstruction_confidence": "high",
        "start_time": "2026-08-24T16:00:00Z",
        "substantive_turn_count": 5,
        "user_turn_count": 3,
        "warnings": [],
        "turns": [
            {"post_id": "root", "parent_post_id": None, "author_role": "user", "author_key": "user-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": []},
            {"post_id": "u1", "parent_post_id": "root", "author_role": "user", "author_key": "user-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": []},
            {"post_id": "a1", "parent_post_id": "u1", "author_role": "account", "author_key": "account-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": [], "account_turn_asked_for_clarification": True},
            {"post_id": "u2", "parent_post_id": "root", "author_role": "user", "author_key": "user-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": []},
            {"post_id": "a2", "parent_post_id": "u2", "author_role": "account", "author_key": "account-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": []},
            {"post_id": "u3", "parent_post_id": "a1", "author_role": "user", "author_key": "user-a", "substantive": True, "correction_cues": [], "pipeline_stage_summaries": []},
        ],
    }

    candidates = extractor.build_review_candidates(conversation)
    clarification_candidate = next(
        row for row in candidates if row["branch_tip_post_id"] == "u3"
    )
    assert "post_clarification_continuation" in clarification_candidate[
        "review_reason_codes"
    ]
    assert "sibling_branch_context" in clarification_candidate[
        "review_reason_codes"
    ]


def test_routine_exchange_is_not_over_selected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        "".join(
            [
                log_line("2026-08-24 16:10:00", "Considering mention id=800 author_id=user text='Thanks'"),
                log_line("2026-08-24 16:10:01", "Built AI reply context for mention 800. chain_items=0 immediate_parent=None quoted=False"),
            ]
        ),
    )
    run_scan(project, output)
    assert rows(output, "review-candidates.jsonl") == []


def test_stable_hmac_pseudonyms_no_raw_ids_and_key_not_copied(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    raw_id = "extremely-raw-contributor-987654"
    write_active(
        project,
        first_exchange(author_id=raw_id) + continuation(author_id=raw_id),
    )
    run_scan(project, output)
    first_author = rows(output, "canonical-posts.jsonl")[0]["author_key"]
    run_scan(project, output)
    second_author = rows(output, "canonical-posts.jsonl")[0]["author_key"]

    assert first_author == second_author
    assert raw_id.encode() not in all_private_output_bytes(output)
    assert not (current_batch(output) / "pseudonym-key").exists()
    key = (output / "state" / "pseudonym-key").read_bytes()
    assert key not in all_private_output_bytes(output)
    extractor.freeze_review_pack(
        output_root=output,
        pack_name="privacy-pack",
        since=BOUNDARY,
        until="2026-08-28T00:00:00Z",
        include_open=True,
    )
    assert raw_id.encode() not in all_private_output_bytes(output)


def test_different_keys_produce_different_author_keys(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_active(project, first_exchange(author_id="same-raw-user"))
    output_a = tmp_path / "output-a"
    output_b = tmp_path / "output-b"
    run_scan(project, output_a)
    run_scan(project, output_b)

    author_a = next(
        row["author_key"]
        for row in rows(output_a, "canonical-posts.jsonl")
        if row["author_role"] == "user"
    )
    author_b = next(
        row["author_key"]
        for row in rows(output_b, "canonical-posts.jsonl")
        if row["author_role"] == "user"
    )
    assert author_a != author_b


def test_lock_contention_returns_success_result_without_state_change(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    before = state_bytes(output)
    descriptor = os.open(output / "state" / "extractor.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_scan(project, output)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert result["status"] == "lock_held"
    assert state_bytes(output) == before


def test_current_points_only_to_complete_immutable_batch(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    batch = current_batch(output)

    assert set(path.name for path in batch.iterdir()) == set(extractor.BATCH_FILES)
    assert stat.S_IMODE(batch.stat().st_mode) == 0o500
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in batch.iterdir())
    assert not any(path.name.startswith(".tmp-") for path in (output / "batches").iterdir())


def test_status_is_read_only_and_valid_when_uninitialised(tmp_path: Path) -> None:
    output = tmp_path / "does-not-exist"
    before = set(tmp_path.iterdir())

    status = extractor.get_status(output)

    assert status["initialised"] is False
    assert status["schema_version"] == 7
    assert status["last_retention_error"] is None
    assert status["automatic_batch_budget_bytes"] == 10 * 1024 * 1024 * 1024
    assert status["minimum_free_bytes"] == 10 * 1024 * 1024 * 1024
    assert set(tmp_path.iterdir()) == before


def test_retention_failure_is_visible_and_next_success_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    real_retention = extractor.apply_batch_retention

    def fail_retention(*_args: object, **_kwargs: object) -> extractor.RetentionResult:
        raise extractor.ExtractorError("injected retention inventory failure")

    monkeypatch.setattr(extractor, "apply_batch_retention", fail_retention)
    with pytest.raises(extractor.ExtractorError, match="injected retention"):
        run_scan(project, output)

    failed_status = extractor.get_status(output)
    assert failed_status["last_retention_error"] == (
        "injected retention inventory failure"
    )
    assert "automatic_batch_retention_failed" in failed_status["warnings"]

    monkeypatch.setattr(extractor, "apply_batch_retention", real_retention)
    result = run_scan(project, output)
    recovered_status = extractor.get_status(output)

    assert result["status"] == "no_change"
    assert recovered_status["last_retention_error"] is None
    assert "automatic_batch_retention_failed" not in recovered_status["warnings"]


def test_stale_storage_telemetry_is_advisory_and_status_is_read_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    state_path = output / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    for field in (
        "protected_automatic_bytes",
        "retained_automatic_bytes",
        "retained_batch_count",
        "review_pack_bytes",
        "total_extractor_bytes",
    ):
        state[field] = -1
    rewrite_private_json(state_path, state, mode=0o600)
    before_state = state_path.read_bytes()

    status = extractor.get_status(output)
    after_state = state_path.read_bytes()
    validation = extractor.validate_output_root(output)

    assert after_state == before_state
    assert status["retained_batch_count"] == 1
    assert status["retained_automatic_bytes"] > 0
    assert status["protected_automatic_bytes"] > 0
    assert "stored_storage_telemetry_stale" in status["warnings"]
    assert validation["valid"] is True
    assert "stored_storage_telemetry_stale" in validation["warnings"]


def test_validate_detects_manifest_hash_corruption(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    assert extractor.validate_output_root(output)["valid"] is True
    manifest = current_batch(output) / "manifest.json"
    os.chmod(manifest, 0o600)
    manifest.write_bytes(manifest.read_bytes() + b" ")
    os.chmod(manifest, 0o400)

    validation = extractor.validate_output_root(output)

    assert validation["valid"] is False
    assert any(
        "hash mismatch" in error
        or "invalid strict JSON" in error
        or "canonical JSON" in error
        for error in validation["errors"]
    )


def test_extractor_code_provenance_uses_the_script_repository(tmp_path: Path) -> None:
    repository = tmp_path / "extractor-repository"
    script = repository / "tools" / "extract_prospective_conversations.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"print('exact extractor source')\n")
    git_dir = repository / ".git"
    git_dir.mkdir()
    commit = "a" * 40
    (git_dir / "HEAD").write_text(commit + "\n", encoding="ascii")

    provenance = extractor._extractor_code_provenance(script)

    assert provenance == {
        "extractor_repository_commit_sha": commit,
        "extractor_script_path": "tools/extract_prospective_conversations.py",
        "extractor_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
    }


def test_batch_manifest_records_extractor_code_not_project_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    project_git = project / ".git"
    project_git.mkdir()
    (project_git / "HEAD").write_text("b" * 40 + "\n", encoding="ascii")
    expected = {
        "extractor_repository_commit_sha": "a" * 40,
        "extractor_script_path": "tools/extract_prospective_conversations.py",
        "extractor_script_sha256": "c" * 64,
    }
    monkeypatch.setattr(
        extractor,
        "_extractor_code_provenance",
        lambda: dict(expected),
    )

    run_scan(project, output)
    manifest = extractor._strict_read_json(current_batch(output) / "manifest.json")

    assert isinstance(manifest, dict)
    assert {
        field: manifest[field] for field in expected
    } == expected
    assert manifest["repository_commit_sha"] == expected[
        "extractor_repository_commit_sha"
    ]
    assert manifest["repository_commit_sha"] != "b" * 40
    assert extractor.validate_output_root(output)["valid"] is True


def test_batch_manifest_matches_the_loaded_extractor_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())

    run_scan(project, output)
    manifest = extractor._strict_read_json(current_batch(output) / "manifest.json")
    script = Path(extractor.__file__).resolve(strict=True)

    assert isinstance(manifest, dict)
    assert manifest["extractor_script_sha256"] == extractor.sha256_file(script)
    assert manifest["extractor_repository_commit_sha"] == (
        extractor._read_repository_commit(script.parent.parent)
    )
    assert manifest["repository_commit_sha"] == manifest[
        "extractor_repository_commit_sha"
    ]


def test_validation_rejects_invalid_extractor_code_provenance(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())
    run_scan(project, output)
    manifest_path = current_batch(output) / "manifest.json"
    manifest = extractor._strict_read_json(manifest_path)
    assert isinstance(manifest, dict)
    manifest["extractor_script_sha256"] = "not-a-sha256"
    manifest["repository_commit_sha"] = "f" * 40
    rewrite_private_json(manifest_path, manifest)

    validation = extractor.validate_output_root(output)

    assert validation["valid"] is False
    assert any(
        "extractor script hash is invalid" in error
        for error in validation["errors"]
    )
    assert any(
        "repository commit alias is inconsistent" in error
        for error in validation["errors"]
    )


def test_freeze_pack_filters_open_by_default_is_immutable_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange() + continuation())
    run_scan(project, output, until="2026-08-24T16:30:00Z")

    closed = extractor.freeze_review_pack(
        output_root=output,
        pack_name="closed-only",
        since=BOUNDARY,
        until="2026-08-25T00:00:00Z",
    )
    included = extractor.freeze_review_pack(
        output_root=output,
        pack_name="with-open",
        since=BOUNDARY,
        until="2026-08-25T00:00:00Z",
        include_open=True,
    )
    repeated = extractor.freeze_review_pack(
        output_root=output,
        pack_name="with-open-repeat",
        since=BOUNDARY,
        until="2026-08-25T00:00:00Z",
        include_open=True,
    )

    assert closed["conversation_count"] == 0
    assert included["conversation_count"] == 1
    assert repeated["conversation_count"] == 1
    pack = output / "review-packs" / "with-open"
    repeated_pack = output / "review-packs" / "with-open-repeat"
    assert (pack / "conversations.jsonl").read_bytes() == (
        repeated_pack / "conversations.jsonl"
    ).read_bytes()
    assert (pack / "review-candidates.jsonl").read_bytes() == (
        repeated_pack / "review-candidates.jsonl"
    ).read_bytes()
    assert stat.S_IMODE(pack.stat().st_mode) == 0o500
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in pack.iterdir())
    with pytest.raises(extractor.ExtractorError, match="already exists"):
        extractor.freeze_review_pack(
            output_root=output,
            pack_name="with-open",
            since=BOUNDARY,
            until="2026-08-25T00:00:00Z",
            include_open=True,
        )


def test_review_pack_has_its_own_creation_time_and_preserves_source_time(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange() + continuation())
    source_created = extractor.parse_aware_timestamp(
        "2026-08-25T10:00:00Z", option="test"
    )
    extractor.run_scan(
        project_dir=project,
        output_root=output,
        prospective_start=BOUNDARY,
        until=DEFAULT_UNTIL,
        quiescence_hours=48,
        scan_start=source_created,
    )
    first_time = extractor.parse_aware_timestamp(
        "2026-08-26T12:00:00Z", option="test"
    )
    second_time = extractor.parse_aware_timestamp(
        "2026-08-26T13:00:00Z", option="test"
    )
    for name, frozen_at in (("pack-one", first_time), ("pack-two", second_time)):
        extractor.freeze_review_pack(
            output_root=output,
            pack_name=name,
            since=BOUNDARY,
            until="2026-08-28T00:00:00Z",
            include_open=True,
            frozen_at=frozen_at,
        )
    pack_one = output / "review-packs" / "pack-one"
    pack_two = output / "review-packs" / "pack-two"
    first_manifest = extractor._strict_read_json(pack_one / "manifest.json")
    second_manifest = extractor._strict_read_json(pack_two / "manifest.json")
    assert isinstance(first_manifest, dict) and isinstance(second_manifest, dict)

    assert first_manifest["creation_timestamp"] == "2026-08-26T12:00:00Z"
    assert second_manifest["creation_timestamp"] == "2026-08-26T13:00:00Z"
    assert first_manifest["source_batch_creation_timestamp"] == "2026-08-25T10:00:00Z"
    assert second_manifest["source_batch_creation_timestamp"] == "2026-08-25T10:00:00Z"
    assert (pack_one / "conversations.jsonl").read_bytes() == (
        pack_two / "conversations.jsonl"
    ).read_bytes()
    assert (pack_one / "review-candidates.jsonl").read_bytes() == (
        pack_two / "review-candidates.jsonl"
    ).read_bytes()
    assert extractor.validate_output_root(output)["valid"] is True


@pytest.mark.parametrize(
    "text",
    [
        "Which unemployment measure and period are you using?",
        "Which unemployment series should anchor the comparison?",
        "What source are you relying on?",
        "Which law do you mean?",
        "Could you clarify which period you mean?",
    ],
)
def test_evidential_clarification_forms_are_detected(text: str) -> None:
    assert extractor._looks_like_clarification_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Which party will win?",
        "What happened next?",
        "Which policy is best?",
        "What do you think?",
    ],
)
def test_general_questions_are_not_misclassified_as_clarification(text: str) -> None:
    assert extractor._looks_like_clarification_request(text) is False


def test_continuation_after_evidential_clarification_is_descriptive_candidate(
    tmp_path: Path,
) -> None:
    target_id = snowflake_id("2026-08-24T15:10:00Z")
    reply_id = snowflake_id("2026-08-24T15:12:00Z")
    continuation_id = snowflake_id("2026-08-24T15:20:00Z")
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line("2026-08-24 16:10:00", f"Considering mention id={target_id} author_id=u text='The rate changed.'")
        + log_line("2026-08-24 16:10:01", f"Built AI reply context for mention {target_id}. chain_items=0 immediate_parent=None quoted=False")
        + create_attempt(
            target_id,
            "1" * 64,
            text="Which unemployment measure and period are you using?",
        )
        + generic_success(reply_id)
        + receipt_promotion(target_id, reply_id)
        + log_line("2026-08-24 16:20:00", f"Considering mention id={continuation_id} author_id=u text='The claimant count from July.'")
        + log_line("2026-08-24 16:20:01", f"Built AI reply context for mention {continuation_id}. chain_items=2 immediate_parent={reply_id} quoted=False"),
    )

    run_scan(project, output)
    candidate = rows(output, "review-candidates.jsonl")[0]

    assert "post_clarification_continuation" in candidate["review_reason_codes"]
    assert not set(candidate["review_reason_codes"]) & {
        "defect",
        "repair_required",
        "bad_reply",
    }


def test_digest_runtime_config_and_production_log_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange(author_id="private-user-id"))
    protected = [
        project / ".mrs_log_digest_state.json",
        project / "mrsMThatcher.env",
        project / "bot_state.json",
        project / "mrsMThatcher.control.json",
        project / "mrsMThatcher.local.json",
    ]
    for path in protected:
        path.write_text(f"sentinel:{path.name}\n", encoding="utf-8")
    before = {path: (path.stat(), path.read_bytes()) for path in protected}
    original_open = extractor.os.open
    source_open_flags: list[int] = []

    def guarded_open(path: object, flags: int, mode: int = 0o777) -> int:
        candidate = Path(path)
        if candidate in protected:
            raise AssertionError(f"protected production file accessed: {candidate.name}")
        if candidate == active:
            source_open_flags.append(flags)
        return original_open(path, flags, mode)

    monkeypatch.setattr(extractor.os, "open", guarded_open)
    run_scan(project, output)

    assert source_open_flags
    assert all(flags & os.O_ACCMODE == os.O_RDONLY for flags in source_open_flags)
    for path, (metadata, content) in before.items():
        after = path.stat()
        assert path.read_bytes() == content
        assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )


def test_source_has_no_network_provider_or_digest_imports() -> None:
    source = Path(extractor.__file__).read_text(encoding="utf-8")
    lowered = source.casefold()
    prohibited = (
        "import requests",
        "import httpx",
        "urllib.request",
        "import socket",
        "openai",
        "anthropic",
        "xai",
        "twitter",
        "mrs_log_digest",
        "mrsMThatcher.env",
    )
    assert not any(value.casefold() in lowered for value in prohibited)


def test_cli_until_is_deterministic_and_validate_status_freeze_work(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange() + continuation())
    script = Path(extractor.__file__)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(script.parents[1])
    scan = subprocess.run(
        [
            sys.executable,
            str(script),
            "scan",
            "--project-dir",
            str(project),
            "--output-root",
            str(output),
            "--prospective-start",
            BOUNDARY,
            "--until",
            DEFAULT_UNTIL,
            "--quiescence-hours",
            "48",
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    status = subprocess.run(
        [sys.executable, str(script), "status", "--output-root", str(output)],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    validation = subprocess.run(
        [sys.executable, str(script), "validate", "--output-root", str(output)],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(scan.stdout)["status"] == "published"
    assert json.loads(status.stdout)["initialised"] is True
    assert json.loads(validation.stdout)["valid"] is True
    assert current_batch(output).name.startswith("20260827T180000Z-")


def test_v3_account_root_context_and_user_descendants_share_one_graph() -> None:
    root_id = "2092026402438627782"
    context_id = "2092026406586753468"
    user_root_reply = snowflake_id("2026-08-25T02:00:00Z")
    user_context_reply = snowflake_id("2026-08-25T02:01:00Z")
    text = "".join(
        [
            account_root_posted(
                root_id,
                created_at="2026-08-24T23:08:57Z",
                public_text=None,
                quote_text="Visible words embedded in the quote image.",
            ),
            historical_context_posted(
                root_id,
                context_id,
                created_at="2026-08-24T23:08:58Z",
            ),
            log_line(
                "2026-08-25 01:10:00",
                f"Considering mention id={context_id} author_id=redacted-self "
                "text='A later truncated mention observation'",
            ),
            log_line(
                "2026-08-25 03:00:00",
                f"Considering mention id={user_root_reply} author_id=user-a "
                "text='A reply to the root.'",
            ),
            log_line(
                "2026-08-25 03:00:01",
                f"Built AI reply context for mention {user_root_reply}. "
                f"chain_items=1 immediate_parent={root_id} quoted=False",
            ),
            log_line(
                "2026-08-25 03:01:00",
                f"Considering mention id={user_context_reply} author_id=user-b "
                "text='A reply to the historical context.'",
            ),
            log_line(
                "2026-08-25 03:01:01",
                f"Built AI reply context for mention {user_context_reply}. "
                f"chain_items=2 immediate_parent={context_id} quoted=False",
            ),
        ]
    )
    records, _warnings = extractor.parse_log_records(text.encode())
    posts = extractor.normalise_canonical_posts(records, [], b"v" * 32)
    by_id = {post["post_id"]: post for post in posts}

    root = by_id[root_id]
    context = by_id[context_id]
    assert root["author_role"] == "account"
    assert root["publication_status"] == "published"
    assert root["parent_post_id"] is None
    assert root["parent_observation_status"] == "confirmed_none"
    assert root["root_post_id"] == root_id == root["conversation_id"]
    assert root["text"] == "Visible words embedded in the quote image."
    assert root["text_source"] == "image_quote_text"
    assert root["public_text"] is None
    assert root["visible_media_text"] == root["text"]
    assert context["author_role"] == "account"
    assert context["author_key"] == root["author_key"]
    assert context["parent_post_id"] == root_id
    assert context["root_post_id"] == root_id == context["conversation_id"]
    assert context["text"] == "Context — a confirmed historical note."
    assert context["text_source"] == "historical_context_reply"
    assert context["self_observation_count"] == 1
    assert "A later truncated mention" not in context["text"]

    conversations, _candidates = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )
    assert len(conversations) == 1
    assert conversations[0]["root_post_id"] == root_id
    assert {turn["post_id"] for turn in conversations[0]["turns"]} == {
        root_id,
        context_id,
        user_root_reply,
        user_context_reply,
    }


def test_mention_before_authoritative_event_is_promoted_without_user_identity() -> None:
    root_id = "2091994786873909414"
    text = "".join(
        [
            log_line(
                "2026-08-24 22:00:00",
                f"Considering mention id={root_id} author_id=raw-self-id "
                "text='Poll text must not win.'",
            ),
            account_root_posted(
                root_id,
                created_at="2026-08-24T21:03:20Z",
                local_time="2026-08-24 22:00:01",
                public_text="Authoritative public root text.",
            ),
        ]
    )
    records, _warnings = extractor.parse_log_records(text.encode())
    posts = extractor.normalise_canonical_posts(records, [], b"w" * 32)

    assert len(posts) == 1
    assert posts[0]["author_role"] == "account"
    assert str(posts[0]["author_key"]).startswith("account-")
    assert posts[0]["text"] == "Authoritative public root text."
    assert posts[0]["self_observation_count"] == 1


def test_prefix_timing_and_generic_success_do_not_establish_account_posts() -> None:
    first_id = "2092161227854090348"
    adjacent_id = str(int(first_id) + (1 << 22))
    unbound_success_id = str(int(adjacent_id) + (1 << 22))
    text = "".join(
        [
            log_line(
                "2026-08-25 10:00:00",
                f"Considering mention id={first_id} author_id=user-a "
                "text='Context — prefix alone proves nothing'",
            ),
            log_line(
                "2026-08-25 10:00:00",
                f"Built AI reply context for mention {first_id}. "
                "chain_items=0 immediate_parent=None quoted=False",
            ),
            log_line(
                "2026-08-25 10:00:01",
                f"Considering mention id={adjacent_id} author_id=user-b "
                "text='Adjacent in time, unrelated in graph.'",
            ),
            log_line(
                "2026-08-25 10:00:01",
                f"Built AI reply context for mention {adjacent_id}. "
                "chain_items=0 immediate_parent=None quoted=False",
            ),
            generic_success(unbound_success_id, local_time="2026-08-25 10:00:02"),
        ]
    )
    records, _warnings = extractor.parse_log_records(text.encode())
    posts = extractor.normalise_canonical_posts(records, [], b"x" * 32)
    by_id = {post["post_id"]: post for post in posts}

    assert set(by_id) == {first_id, adjacent_id}
    assert all(post["author_role"] == "user" for post in posts)
    assert all(post["parent_post_id"] is None for post in posts)
    assert unbound_success_id not in by_id


def test_duplicate_account_events_deduplicate_and_unavailable_text_is_partial() -> None:
    root_id = "2092161223542378794"
    event_a = account_root_posted(
        root_id,
        created_at="2026-08-25T08:04:41Z",
        local_time="2026-08-25 09:04:41",
        public_text=None,
    )
    event_b = account_root_posted(
        root_id,
        created_at="2026-08-25T08:04:41Z",
        local_time="2026-08-25 09:05:41",
        public_text=None,
    )
    records, _warnings = extractor.parse_log_records((event_a + event_b).encode())
    posts = extractor.normalise_canonical_posts(records, [], b"y" * 32)

    assert len(posts) == 1
    assert posts[0]["text"] is None
    assert posts[0]["text_source"] == "unavailable"
    assert posts[0]["reconstruction_confidence"] == "medium"
    assert posts[0]["publication_status"] == "published"
    assert len(posts[0]["publication_evidence"]) == 2
    assert "confirmed_account_root_text_unavailable" in posts[0]["warnings"]


def test_daily_meme_structured_image_summary_survives_empty_legacy_text() -> None:
    root_id = "2093000000000000001"
    logs = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text=None,
        image_summary="A visible daily-meme description.",
        lane="daily_meme",
    ) + legacy_main_publication(
        root_id,
        lane="daily_meme",
        text="",
    )
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"m" * 32)[0]

    assert post["text"] == "A visible daily-meme description."
    assert post["text_source"] == "image_summary"
    assert post["visible_media_text"] == "A visible daily-meme description."
    assert post["publication_authority"] == "structured_confirmation"
    assert post["graph_evidence_authority"] == "structured_confirmation"
    assert post["content_evidence_authority"] == "structured_confirmation"
    assert post["reconstruction_confidence"] == "high"
    assert not extractor.ACCOUNT_UNAVAILABLE_TEXT_WARNINGS & set(post["warnings"])


def test_matching_structured_and_legacy_quote_content_merges_without_conflict() -> None:
    root_id = "2093000000000000002"
    quotation = "The same source-faithful visible quotation."
    logs = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text=None,
        quote_text=quotation,
    ) + legacy_main_publication(root_id, text=quotation)
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"n" * 32)[0]

    assert post["text"] == quotation
    assert post["text_source"] == "image_quote_text"
    assert post["publication_authority"] == "structured_confirmation"
    assert post["content_evidence_authority"] == "structured_confirmation"
    assert post["account_content_conflicts"] == []
    assert {row["authority"] for row in post["publication_evidence"]} == {
        "legacy_confirmed_sequence",
        "structured_confirmation",
    }


def test_richer_structured_root_content_wins_over_poorer_legacy_content() -> None:
    root_id = "2093000000000000003"
    logs = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text=None,
        quote_text="The authoritative structured quotation.",
    ) + legacy_main_publication(root_id, text="A poorer legacy reconstruction.")
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"o" * 32)[0]

    assert post["text"] == "The authoritative structured quotation."
    assert post["text_source"] == "image_quote_text"
    assert post["content_evidence_authority"] == "structured_confirmation"
    assert post["publication_authority"] == "structured_confirmation"
    assert "lower_priority_account_content_conflict" in post["warnings"]
    assert {
        row["text"]["excerpt"] for row in post["account_content_conflicts"]
    } == {"A poorer legacy reconstruction."}


def test_legacy_content_fills_structured_unavailable_without_lowering_publication() -> None:
    root_id = "2093000000000000004"
    legacy_text = "Legacy text from a complete durable publication chain."
    logs = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text=None,
    ) + legacy_main_publication(root_id, text=legacy_text)
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"p" * 32)[0]

    assert post["text"] == legacy_text
    assert post["text_source"] == "public_text"
    assert post["publication_authority"] == "structured_confirmation"
    assert post["graph_evidence_authority"] == "structured_confirmation"
    assert post["content_evidence_authority"] == "legacy_confirmed_sequence"
    assert post["reconstruction_confidence"] == "high"
    assert not extractor.ACCOUNT_UNAVAILABLE_TEXT_WARNINGS & set(post["warnings"])


def test_structured_historical_context_content_survives_legacy_replay() -> None:
    root_id = "2093000000000000005"
    reply_id = "2093000000000000006"
    logs = "".join(
        [
            account_root_posted(
                root_id,
                created_at="2026-08-25T00:07:00Z",
                local_time="2026-08-25 00:07:00",
                public_text=None,
                quote_text="A source-faithful visible quotation.",
            ),
            historical_context_posted(
                root_id,
                reply_id,
                created_at="2026-08-25T00:07:01Z",
                local_time="2026-08-25 00:07:01",
                reply_text="Structured historical context.",
            ),
            legacy_account_pair(root_id, reply_id, quote_id="a" * 64),
        ]
    )
    records, _warnings = extractor.parse_log_records(logs.encode())
    by_id = {
        post["post_id"]: post
        for post in extractor.normalise_canonical_posts(records, [], b"q" * 32)
    }

    context = by_id[reply_id]
    assert context["text"] == "Structured historical context."
    assert context["text_source"] == "historical_context_reply"
    assert context["parent_post_id"] == root_id
    assert context["root_post_id"] == root_id
    assert context["conversation_id"] == root_id
    assert context["graph_evidence_authority"] == "structured_confirmation"
    assert context["content_evidence_authority"] == "structured_confirmation"
    assert context["publication_authority"] == "structured_confirmation"
    assert len(context["account_content_conflicts"]) == 1


def test_lower_priority_identity_disagreement_cannot_reparent_account_reply() -> None:
    legacy_root = "2093000000000000007"
    structured_root = "2093000000000000008"
    reply_id = "2093000000000000009"
    logs = "".join(
        [
            account_root_posted(
                structured_root,
                created_at="2026-08-25T00:07:00Z",
                local_time="2026-08-25 00:07:00",
            ),
            historical_context_posted(
                structured_root,
                reply_id,
                created_at="2026-08-25T00:07:01Z",
                local_time="2026-08-25 00:07:01",
                reply_text="Structured parent identity.",
            ),
            legacy_account_pair(legacy_root, reply_id),
        ]
    )
    records, _warnings = extractor.parse_log_records(logs.encode())
    by_id = {
        post["post_id"]: post
        for post in extractor.normalise_canonical_posts(records, [], b"r" * 32)
    }

    context = by_id[reply_id]
    assert context["parent_post_id"] == structured_root
    assert context["root_post_id"] == structured_root
    assert context["conversation_id"] == structured_root
    assert context["graph_evidence_authority"] == "structured_confirmation"
    assert "lower_priority_account_graph_conflict" in context["warnings"]
    assert any(
        row["parent_post_id"] == legacy_root
        and row["disposition"] == "rejected_lower_priority"
        for row in context["account_graph_conflicts"]
    )


def test_structured_and_legacy_merge_is_semantically_order_independent() -> None:
    root_id = "2093000000000000010"
    structured = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text=None,
        quote_text="Structured visible content.",
    )
    legacy = legacy_main_publication(root_id, text="Different legacy content.")
    structured_records, _warnings = extractor.parse_log_records(structured.encode())
    legacy_records, _warnings = extractor.parse_log_records(legacy.encode())
    combined_records, _warnings = extractor.parse_log_records(
        (structured + legacy).encode()
    )

    legacy_first = extractor.normalise_canonical_posts(
        structured_records,
        extractor.normalise_canonical_posts(legacy_records, [], b"s" * 32),
        b"s" * 32,
    )[0]
    structured_first = extractor.normalise_canonical_posts(
        legacy_records,
        extractor.normalise_canonical_posts(structured_records, [], b"s" * 32),
        b"s" * 32,
    )[0]
    combined = extractor.normalise_canonical_posts(
        combined_records, [], b"s" * 32
    )[0]
    semantic_fields = (
        "author_role",
        "author_key",
        "lane",
        "publication_status",
        "publication_authority",
        "parent_post_id",
        "root_post_id",
        "conversation_id",
        "text",
        "text_source",
        "public_text",
        "visible_media_text",
        "graph_evidence_authority",
        "content_evidence_authority",
        "reconstruction_confidence",
        "account_graph_conflicts",
        "account_content_conflicts",
    )

    expected = {field: combined[field] for field in semantic_fields}
    assert {field: legacy_first[field] for field in semantic_fields} == expected
    assert {field: structured_first[field] for field in semantic_fields} == expected


def test_duplicate_equal_structured_account_observations_merge_cleanly() -> None:
    root_id = "2093000000000000011"
    logs = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text="Identical structured content.",
    ) + account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:01",
        public_text="Identical structured content.",
    )
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"t" * 32)[0]

    assert post["text"] == "Identical structured content."
    assert post["account_graph_conflicts"] == []
    assert post["account_content_conflicts"] == []
    assert len(post["publication_evidence"]) == 2


def test_conflicting_equal_structured_content_fails_closed_order_independently() -> None:
    root_id = "2093000000000000012"
    first = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
        public_text="First equally authoritative content.",
    )
    second = account_root_posted(
        root_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:01",
        public_text="Second equally authoritative content.",
    )
    records, _warnings = extractor.parse_log_records((first + second).encode())

    forward = extractor.normalise_canonical_posts(records, [], b"u" * 32)[0]
    reverse = extractor.normalise_canonical_posts(
        list(reversed(records)), [], b"u" * 32
    )[0]

    assert forward["text"] is None
    assert forward["text_source"] == "unavailable"
    assert forward["content_evidence_authority"] is None
    assert forward["account_content_ambiguity_authority"] == "structured_confirmation"
    assert forward["reconstruction_confidence"] == "medium"
    assert len(forward["account_content_conflicts"]) == 2
    assert forward["account_content_conflicts"] == reverse[
        "account_content_conflicts"
    ]
    assert "equal_authority_account_content_conflict" in forward["warnings"]


def test_conflicting_equal_structured_graph_observations_fail_closed() -> None:
    post_id = "2093000000000000013"
    parent_id = "2093000000000000014"
    logs = account_root_posted(
        post_id,
        created_at="2026-08-25T00:17:00Z",
        local_time="2026-08-25 00:17:00",
    ) + historical_context_posted(
        parent_id,
        post_id,
        created_at="2026-08-25T00:17:01Z",
        local_time="2026-08-25 00:17:01",
    )
    records, _warnings = extractor.parse_log_records(logs.encode())

    post = extractor.normalise_canonical_posts(records, [], b"v" * 32)[0]

    assert post["account_graph_kind"] == "ambiguous"
    assert post["parent_post_id"] is None
    assert post["root_post_id"] is None
    assert post["conversation_id"] is None
    assert post["reconstruction_confidence"] == "low"
    assert len(post["account_graph_conflicts"]) == 2
    assert "equal_authority_account_graph_conflict" in post["warnings"]


def test_context_confirmation_without_root_authority_retains_start_unknown() -> None:
    root_id = "2092026402438627782"
    context_id = "2092026406586753468"
    records, _warnings = extractor.parse_log_records(
        historical_context_posted(
            root_id,
            context_id,
            created_at="2026-08-24T23:08:58Z",
        ).encode()
    )
    posts = extractor.normalise_canonical_posts(records, [], b"2" * 32)
    assert len(posts) == 1
    assert posts[0]["author_role"] == "account"
    assert posts[0]["parent_post_id"] == root_id
    assert posts[0]["root_post_id"] == root_id
    assert posts[0]["conversation_id"] == root_id
    assert "authoritative_account_root_unavailable" in posts[0]["warnings"]

    conversations, _candidates = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )
    assert conversations[0]["prospective_status"] == "start_unknown"


def test_literal_audited_legacy_sequences_reconstruct_root_and_context() -> None:
    root_id = "2092026402438627782"
    context_id = "2092026406586753468"
    records, _warnings = extractor.parse_log_records(
        legacy_account_pair(root_id, context_id).encode()
    )
    posts = extractor.normalise_canonical_posts(records, [], b"z" * 32)
    by_id = {post["post_id"]: post for post in posts}

    assert set(by_id) == {root_id, context_id}
    assert by_id[root_id]["publication_authority"] == "legacy_confirmed_sequence"
    assert by_id[root_id]["parent_post_id"] is None
    assert by_id[root_id]["text"] == "A source-faithful visible quotation."
    assert by_id[context_id]["publication_authority"] == "legacy_confirmed_sequence"
    assert by_id[context_id]["parent_post_id"] == root_id
    assert by_id[context_id]["text"] == "Context — verified historical context."


def test_incomplete_legacy_sequences_remain_unresolved() -> None:
    root_id = "2092026402438627782"
    context_id = "2092026406586753468"
    incomplete = "".join(
        [
            create_attempt(
                root_id,
                "c" * 64,
                lane="quote_image",
                text="Unconfirmed root.",
            ),
            generic_success(root_id),
            create_attempt(
                root_id,
                "d" * 64,
                lane="historical_context_reply",
                text="Context — unconfirmed reply.",
            ),
            generic_success(context_id),
        ]
    )
    records, _warnings = extractor.parse_log_records(incomplete.encode())

    assert extractor.normalise_canonical_posts(records, [], b"0" * 32) == []


def test_five_named_roots_get_boundary_status_only_from_authority() -> None:
    post_boundary = {
        "2092026402438627782": "2026-08-24T23:08:57Z",
        "2091994786873909414": "2026-08-24T21:03:20Z",
        "2092161223542378794": "2026-08-25T08:04:41Z",
    }
    pre_boundary = {
        "2090347591150063892": "2026-08-20T07:00:00Z",
        "2091614032448872556": "2026-08-23T18:00:00Z",
    }
    lines = []
    for index, (post_id, created_at) in enumerate(
        [*pre_boundary.items(), *post_boundary.items()]
    ):
        lines.append(
            account_root_posted(
                post_id,
                created_at=created_at,
                local_time=f"2026-08-25 11:00:{index:02d}",
            )
        )
    unresolved_id = snowflake_id("2026-08-25T12:00:00Z")
    lines.extend(
        [
            log_line(
                "2026-08-25 13:00:00",
                f"Considering mention id={unresolved_id} author_id=user-z "
                "text='A genuine external reply with an unavailable parent.'",
            ),
            log_line(
                "2026-08-25 13:00:01",
                f"Built AI reply context for mention {unresolved_id}. "
                "chain_items=1 immediate_parent=999999999999999999 quoted=False",
            ),
        ]
    )
    records, _warnings = extractor.parse_log_records("".join(lines).encode())
    posts = extractor.normalise_canonical_posts(records, [], b"1" * 32)
    conversations, _candidates = extractor.build_conversations(
        posts,
        boundary=extractor.parse_aware_timestamp(BOUNDARY, option="test"),
        cutoff=extractor.parse_aware_timestamp(DEFAULT_UNTIL, option="test"),
        quiescence_hours=48,
    )
    status_by_turn = {
        turn["post_id"]: conversation["prospective_status"]
        for conversation in conversations
        for turn in conversation["turns"]
    }

    assert {status_by_turn[post_id] for post_id in post_boundary} == {"eligible"}
    assert {status_by_turn[post_id] for post_id in pre_boundary} == {"pre_boundary"}
    assert status_by_turn[unresolved_id] == "start_unknown"


def test_branch_candidates_are_exact_author_paths_and_ignore_siblings() -> None:
    turns = [
        branch_turn("root", None, "account", "account-a", 0),
        branch_turn("a-u1", "root", "user", "user-a", 1),
        branch_turn("a-a1", "a-u1", "account", "account-a", 2),
        branch_turn(
            "a-u2",
            "a-a1",
            "user",
            "user-a",
            3,
            correction_cues=["not what i said"],
        ),
        branch_turn("b-u1", "root", "user", "user-b", 4),
        branch_turn("b-a1", "b-u1", "account", "account-a", 5),
        branch_turn("b-u2", "b-a1", "user", "user-b", 6),
    ]
    conversation = {
        "activity_status": "quiescent",
        "completeness": "complete",
        "conversation_key": "conversation-branches",
        "prospective_status": "eligible",
        "reconstruction_confidence": "high",
        "root_post_id": "root",
        "start_time": "2026-08-25T00:00:00Z",
        "turns": turns,
        "warnings": [],
    }

    candidates = extractor.build_review_candidates(conversation)
    assert candidates == extractor.build_review_candidates(conversation)
    assert len(candidates) == 2
    by_author = {candidate["principal_author_key"]: candidate for candidate in candidates}
    assert set(by_author) == {"user-a", "user-b"}
    for author, candidate in by_author.items():
        assert candidate["same_author_user_turn_count"] == 2
        assert candidate["same_author_continuation_depth"] == 1
        assert candidate["account_turn_count_on_path"] == 2
        assert candidate["substantive_turn_count_on_path"] == 4
        assert candidate["path_turns"][0]["post_id"] == "root"
        assert {
            turn["author_key"]
            for turn in candidate["path_turns"]
            if turn["author_role"] == "user"
        } == {author}
        assert "same_author_path_continuation" in candidate["review_reason_codes"]
        assert "sibling_branch_context" in candidate["review_reason_codes"]
    assert by_author["user-a"]["correction_cues"] == ["not what i said"]
    assert by_author["user-b"]["correction_cues"] == []


def test_divergent_same_author_paths_are_maximal_and_context_stays_sibling() -> None:
    turns = [
        branch_turn("root", None, "account", "account-a", 0),
        branch_turn(
            "context",
            "root",
            "account",
            "account-a",
            1,
            lane="historical_context_reply",
        ),
        branch_turn("u1", "root", "user", "user-a", 2),
        branch_turn("a1", "u1", "account", "account-a", 3),
        branch_turn(
            "left",
            "a1",
            "user",
            "user-a",
            4,
            correction_cues=["you misunderstood"],
        ),
        branch_turn("right", "a1", "user", "user-a", 5),
    ]
    conversation = {
        "activity_status": "open",
        "completeness": "complete",
        "conversation_key": "conversation-divergent",
        "prospective_status": "eligible",
        "reconstruction_confidence": "high",
        "root_post_id": "root",
        "start_time": "2026-08-25T00:00:00Z",
        "turns": turns,
        "warnings": [],
    }

    candidates = extractor.build_review_candidates(conversation)
    assert {row["branch_tip_post_id"] for row in candidates} == {"left", "right"}
    assert len(candidates) == 2
    for candidate in candidates:
        assert [turn["post_id"] for turn in candidate["path_turns"]] == [
            "root",
            "u1",
            "a1",
            candidate["branch_tip_post_id"],
        ]
        assert "context" not in {turn["post_id"] for turn in candidate["path_turns"]}
        assert "context" in {
            row["post_id"] for row in candidate["sibling_context_refs"]
        }
        assert candidate["same_author_continuation_depth"] == 1
    by_tip = {row["branch_tip_post_id"]: row for row in candidates}
    assert by_tip["left"]["correction_cues"] == ["you misunderstood"]
    assert by_tip["right"]["correction_cues"] == []


def test_linear_external_author_handoff_creates_isolated_segments() -> None:
    conversation = branch_conversation(
        [
            branch_turn("root", None, "account", "account-a", 0),
            branch_turn("a1", "root", "user", "user-a", 1),
            branch_turn("account-a", "a1", "account", "account-a", 2),
            branch_turn("b1", "account-a", "user", "user-b", 3),
            branch_turn("account-b", "b1", "account", "account-a", 4),
        ],
        key="linear-handoff",
    )

    candidates = extractor.build_review_candidates(conversation)
    by_author = {row["principal_author_key"]: row for row in candidates}

    assert set(by_author) == {"user-a", "user-b"}
    assert [turn["post_id"] for turn in by_author["user-a"]["path_turns"]] == [
        "root",
        "a1",
        "account-a",
    ]
    assert [turn["post_id"] for turn in by_author["user-b"]["path_turns"]] == [
        "account-a",
        "b1",
        "account-b",
    ]
    assert by_author["user-a"]["account_turn_count_on_path"] == 2
    assert by_author["user-b"]["same_author_user_turn_count"] == 1
    assert {
        turn["author_key"]
        for candidate in candidates
        for turn in candidate["path_turns"]
        if turn["author_role"] == "user"
    } == {"user-a", "user-b"}
    for candidate in candidates:
        assert {
            turn["author_key"]
            for turn in candidate["path_turns"]
            if turn["author_role"] == "user"
        } == {candidate["principal_author_key"]}
        assert candidate["handoff_context_refs"]
        assert "external_author_handoff_context" in candidate[
            "review_reason_codes"
        ]
    assert {row["post_id"] for row in by_author["user-a"]["handoff_context_refs"]} == {
        "b1"
    }
    assert {row["post_id"] for row in by_author["user-b"]["handoff_context_refs"]} == {
        "a1"
    }


def test_external_author_reentry_creates_distinct_stable_segments() -> None:
    conversation = branch_conversation(
        [
            branch_turn("root", None, "account", "account-a", 0),
            branch_turn("a1", "root", "user", "user-a", 1),
            branch_turn("account-1", "a1", "account", "account-a", 2),
            branch_turn("b1", "account-1", "user", "user-b", 3),
            branch_turn("account-2", "b1", "account", "account-a", 4),
            branch_turn("a2", "account-2", "user", "user-a", 5),
            branch_turn("account-3", "a2", "account", "account-a", 6),
        ],
        key="author-reentry",
    )

    first = extractor.build_review_candidates(conversation)
    second = extractor.build_review_candidates(conversation)
    a_candidates = [row for row in first if row["principal_author_key"] == "user-a"]

    assert first == second
    assert len(first) == 3
    assert len(a_candidates) == 2
    assert len({row["branch_key"] for row in a_candidates}) == 2
    assert {row["segment_start_post_id"] for row in a_candidates} == {
        "root",
        "account-2",
    }
    assert {row["branch_tip_post_id"] for row in a_candidates} == {
        "account-1",
        "account-3",
    }


def test_repeated_single_author_account_alternation_remains_one_segment() -> None:
    conversation = branch_conversation(
        [
            branch_turn("root", None, "account", "account-a", 0),
            branch_turn("a1", "root", "user", "user-a", 1),
            branch_turn("account-1", "a1", "account", "account-a", 2),
            branch_turn("a2", "account-1", "user", "user-a", 3),
            branch_turn("account-2", "a2", "account", "account-a", 4),
        ],
        key="single-author-alternation",
    )

    candidates = extractor.build_review_candidates(conversation)

    assert len(candidates) == 1
    assert [turn["post_id"] for turn in candidates[0]["path_turns"]] == [
        "root",
        "a1",
        "account-1",
        "a2",
        "account-2",
    ]
    assert candidates[0]["same_author_continuation_depth"] == 1
    assert candidates[0]["handoff_context_refs"] == []


def test_handoff_correction_cues_are_scoped_to_the_principal_segment() -> None:
    conversation = branch_conversation(
        [
            branch_turn("root", None, "account", "account-a", 0),
            branch_turn("a1", "root", "user", "user-a", 1),
            branch_turn("account-a", "a1", "account", "account-a", 2),
            branch_turn(
                "b1",
                "account-a",
                "user",
                "user-b",
                3,
                correction_cues=["not what i said"],
            ),
            branch_turn("account-b", "b1", "account", "account-a", 4),
        ],
        key="handoff-correction",
    )

    by_author = {
        row["principal_author_key"]: row
        for row in extractor.build_review_candidates(conversation)
    }

    assert by_author["user-a"]["correction_cues"] == []
    assert "explicit_correction_cue" not in by_author["user-a"][
        "review_reason_codes"
    ]
    assert by_author["user-b"]["correction_cues"] == ["not what i said"]
    assert "explicit_correction_cue" in by_author["user-b"][
        "review_reason_codes"
    ]


def test_handoff_clarification_continuation_is_scoped_to_matching_segment() -> None:
    conversation = branch_conversation(
        [
            branch_turn("root", None, "account", "account-a", 0),
            branch_turn("a1", "root", "user", "user-a", 1),
            branch_turn(
                "account-a",
                "a1",
                "account",
                "account-a",
                2,
                clarification=True,
            ),
            branch_turn("b1", "account-a", "user", "user-b", 3),
            branch_turn("account-b", "b1", "account", "account-a", 4),
        ],
        key="handoff-clarification",
    )

    by_author = {
        row["principal_author_key"]: row
        for row in extractor.build_review_candidates(conversation)
    }

    assert "post_clarification_continuation" not in by_author["user-a"][
        "review_reason_codes"
    ]
    assert "post_clarification_continuation" in by_author["user-b"][
        "review_reason_codes"
    ]


def missing_parent_exchange(*, missing_root: bool, handoff: bool = False) -> str:
    root_id = snowflake_id("2026-08-25T00:00:00Z")
    parent_id = root_id if missing_root else snowflake_id("2026-08-25T00:05:00Z")
    log = ""
    if not missing_root:
        log += event_line(
            "2026-08-25 01:00:00",
            "ai_reply_pipeline_decision",
            target_id=root_id,
            root_post_id=root_id,
            author_id="root-author",
            incoming_text="The observed conversation root.",
        )
    log += event_line(
        "2026-08-25 01:10:00",
        "ai_reply_pipeline_decision",
        target_id="201",
        root_post_id=root_id,
        parent_post_id=parent_id,
        author_id="author-a",
        incoming_text="A contribution whose immediate parent is unavailable.",
        target_created_at="2026-08-25T00:10:00Z",
    )
    log += publish_reply(
        "201", "202", author_id="author-a", local_time="2026-08-25 01:11"
    )
    if handoff:
        log += continuation(
            post_id="203",
            parent_id="202",
            author_id="author-b",
            local_time="2026-08-25 01:12",
        )
        log += publish_reply(
            "203", "204", author_id="author-b", local_time="2026-08-25 01:13"
        )
    return log


@pytest.mark.parametrize("missing_root", [True, False], ids=["root", "intermediate"])
def test_scan_accepts_explicitly_partial_candidate_with_missing_parent(
    tmp_path: Path, missing_root: bool
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, missing_parent_exchange(missing_root=missing_root))

    run_scan(project, output)
    candidate = next(
        row for row in rows(output, "review-candidates.jsonl")
        if row["segment_start_post_id"] == "201"
    )

    assert [turn["post_id"] for turn in candidate["path_turns"]] == ["201", "202"]
    assert "missing_parent_post" in candidate["warnings"]
    assert "root_not_reached_by_parent_path" in candidate["warnings"]
    assert "partial_path_reconstruction" in candidate["review_reason_codes"]
    assert candidate["reconstruction_confidence"] != "high"
    assert candidate["handoff_context_refs"] == []
    assert extractor.validate_output_root(output)["valid"] is True


def test_validation_requires_observed_handoff_despite_missing_ancestor(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, missing_parent_exchange(missing_root=True, handoff=True))
    run_scan(project, output)
    batch = current_batch(output)
    candidates = rows(output, "review-candidates.jsonl")
    later_segment = next(
        row for row in candidates if row["segment_start_post_id"] == "202"
    )
    assert "missing_parent_post" in later_segment["warnings"]
    assert "partial_path_reconstruction" in later_segment["review_reason_codes"]
    assert {row["post_id"] for row in later_segment["handoff_context_refs"]} == {"201"}
    later_segment["handoff_context_refs"] = []
    later_segment["review_reason_codes"].remove("external_author_handoff_context")
    rewrite_batch_candidates_and_hashes(batch, candidates)

    problems = extractor._validate_batch_directory(
        batch, require_immutable=False, expected_boundary=BOUNDARY
    )

    assert any("external first parent lacks hand-off context" in problem for problem in problems)


@pytest.mark.parametrize(
    ("field", "value"),
    [("warnings", "missing_parent_post"), ("review_reason_codes", "partial_path_reconstruction")],
    ids=["missing-warning", "missing-partial-reason"],
)
def test_validation_requires_partial_evidence_for_missing_parent(
    tmp_path: Path, field: str, value: str
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, missing_parent_exchange(missing_root=True))
    run_scan(project, output)
    batch = current_batch(output)
    candidates = rows(output, "review-candidates.jsonl")
    candidates[0][field].remove(value)
    rewrite_batch_candidates_and_hashes(batch, candidates)

    problems = extractor._validate_batch_directory(
        batch, require_immutable=False, expected_boundary=BOUNDARY
    )

    assert any("external first parent lacks hand-off context" in problem for problem in problems)


def test_validation_rejects_candidate_path_with_foreign_user_author(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        first_exchange() + continuation() + publish_reply("102", "103"),
    )
    run_scan(project, output)
    batch = current_batch(output)
    candidates = rows(output, "review-candidates.jsonl")
    foreign_turn = next(
        turn
        for turn in candidates[0]["path_turns"]
        if turn["author_role"] == "user"
    )
    foreign_turn["author_key"] = "user-foreign"
    rewrite_batch_candidates_and_hashes(batch, candidates)

    problems = extractor._validate_batch_directory(
        batch,
        require_immutable=False,
        expected_boundary=BOUNDARY,
    )

    assert any("foreign user author" in problem for problem in problems)


def test_unrelated_sibling_order_does_not_change_segment_identities() -> None:
    turns = [
        branch_turn("root", None, "account", "account-a", 0),
        branch_turn("a1", "root", "user", "user-a", 1),
        branch_turn("account-a", "a1", "account", "account-a", 2),
        branch_turn("b1", "account-a", "user", "user-b", 3),
        branch_turn("account-b", "b1", "account", "account-a", 4),
        branch_turn("sibling", "root", "user", "user-c", 5),
        branch_turn("sibling-account", "sibling", "account", "account-a", 6),
    ]
    forward = extractor.build_review_candidates(
        branch_conversation(turns, key="sibling-order")
    )
    reverse = extractor.build_review_candidates(
        branch_conversation(list(reversed(turns)), key="sibling-order")
    )

    def identity(row: dict[str, object]) -> tuple[object, object, object, object]:
        return (
            row["branch_key"],
            row["candidate_key"],
            row["segment_start_post_id"],
            row["branch_tip_post_id"],
        )

    assert sorted(map(identity, forward)) == sorted(map(identity, reverse))


def test_ignored_event_histogram_is_bounded_ordered_and_has_exact_remainder() -> None:
    counts = {f"kind-{index:02d}": 100 - index for index in range(70)}
    target_counts = {
        kind: count // 2 for kind, count in counts.items() if int(kind[-2:]) % 2 == 0
    }
    statistics = {
        "ignored_structured_event_kinds_by_kind": counts,
        "ignored_target_like_event_kinds_by_kind": target_counts,
    }

    rows_a, other_a, target_other_a = extractor.ignored_structured_event_histogram(
        statistics
    )
    rows_b, other_b, target_other_b = extractor.ignored_structured_event_histogram(
        statistics
    )
    omitted = {f"kind-{index:02d}" for index in range(64, 70)}
    assert (rows_a, other_a, target_other_a) == (rows_b, other_b, target_other_b)
    assert len(rows_a) == 64
    assert [(row["count"], row["event_kind"]) for row in rows_a] == sorted(
        ((row["count"], row["event_kind"]) for row in rows_a),
        key=lambda item: (-item[0], item[1]),
    )
    assert other_a == sum(counts[kind] for kind in omitted)
    assert target_other_a == sum(target_counts.get(kind, 0) for kind in omitted)


def test_source_lag_is_reported_warned_and_does_not_invalidate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(
        project,
        log_line("2026-08-24 16:00:00", "Pre-boundary coverage")
        + log_line("2026-08-24 17:00:00", "Latest retained source record"),
    )

    result = run_scan(project, output, until="2026-08-25T00:00:00Z")
    status = extractor.get_status(output)
    batch = current_batch(output)
    batch_status = extractor._strict_read_json(batch / "status.json")
    report = (batch / "extraction-report.md").read_text(encoding="utf-8")

    assert result["source_lag_seconds"] == 8 * 60 * 60
    assert status["source_lag_seconds"] == 8 * 60 * 60
    assert batch_status["source_lag_seconds"] == 8 * 60 * 60
    assert "retained_source_stale_relative_to_scan_cutoff" in status["warnings"]
    assert "Retained source lag: `28800` seconds" in report
    assert extractor.validate_output_root(output)["valid"] is True


def test_normal_scan_rejects_v6_state_and_rebuild_rejects_unregistered_tuple(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    write_active(
        project,
        log_line("2026-08-24 16:00:00", "Boundary coverage") + first_exchange(),
    )
    run_scan(project, old_root)
    mark_state_as_registered_v6(old_root)

    with pytest.raises(extractor.ExtractorError, match="unsupported extractor state schema"):
        run_scan(project, old_root)

    state_path = old_root / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    state["parser_version"] = "unregistered-v4-parser"
    rewrite_private_json(state_path, state, mode=0o600)
    with pytest.raises(extractor.ExtractorError, match="unsupported rebuild source"):
        extractor.rebuild_to_new_root(
            project_dir=project,
            source_output_root=old_root,
            new_output_root=tmp_path / "new-root",
            until=DEFAULT_UNTIL,
        )
    assert not (tmp_path / "new-root").exists()
