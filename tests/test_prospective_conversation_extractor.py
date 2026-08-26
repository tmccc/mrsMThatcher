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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools import extract_prospective_conversations as extractor


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
) -> str:
    """Literal, identity-redacted form of the audited retained sequence."""
    attempt_id = "b" * 64
    root_transaction = "c" * 64
    context_transaction = "d" * 64
    quote_id = "e" * 64
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


def state_bytes(output: Path) -> bytes:
    return (output / "state" / "extractor-state.json").read_bytes()


def mark_state_as_registered_v2(output: Path) -> None:
    state_path = output / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    state.update(
        {
            "schema_version": 2,
            "extractor_version": "prospective-conversation-extractor-v2",
            "parser_version": "prospective-conversation-log-parser-v2",
        }
    )
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


def test_rebuild_to_new_root_preserves_key_pseudonyms_and_old_root(
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
    mark_state_as_registered_v2(old_root)
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


def test_rebuild_refuses_existing_destination_and_insufficient_boundary_coverage(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    existing = tmp_path / "existing"
    existing.mkdir()
    write_active(project, first_exchange())
    run_scan(project, old_root)
    mark_state_as_registered_v2(old_root)

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
    assert status["schema_version"] == 3
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


def test_normal_scan_rejects_v2_state_and_rebuild_rejects_unregistered_tuple(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = tmp_path / "old-root"
    write_active(
        project,
        log_line("2026-08-24 16:00:00", "Boundary coverage") + first_exchange(),
    )
    run_scan(project, old_root)
    mark_state_as_registered_v2(old_root)

    with pytest.raises(extractor.ExtractorError, match="unsupported extractor state schema"):
        run_scan(project, old_root)

    state_path = old_root / "state" / "extractor-state.json"
    state = extractor._strict_read_json(state_path)
    assert isinstance(state, dict)
    state["parser_version"] = "unregistered-v2-parser"
    rewrite_private_json(state_path, state, mode=0o600)
    with pytest.raises(extractor.ExtractorError, match="unsupported rebuild source"):
        extractor.rebuild_to_new_root(
            project_dir=project,
            source_output_root=old_root,
            new_output_root=tmp_path / "new-root",
            until=DEFAULT_UNTIL,
        )
    assert not (tmp_path / "new-root").exists()
