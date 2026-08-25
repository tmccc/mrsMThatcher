from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools import extract_prospective_conversations as extractor


BOUNDARY = "2026-08-24T15:08:39Z"
DEFAULT_UNTIL = "2026-08-27T18:00:00Z"


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


def test_first_scan_initialises_state_second_unchanged_scan_has_no_duplicate_batch(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    write_active(project, first_exchange())

    first = run_scan(project, output)
    first_batches = sorted((output / "batches").iterdir())
    second = run_scan(project, output)

    assert first["status"] == "published"
    assert second["status"] == "no_change"
    assert sorted((output / "batches").iterdir()) == first_batches
    status = extractor.get_status(output)
    assert status["initialised"] is True
    assert status["prospective_boundary"] == BOUNDARY
    assert stat.S_IMODE((output / "state" / "pseudonym-key").stat().st_mode) == 0o600


def test_append_creates_one_snapshot_duplicate_event_deduplicates_and_late_turn_updates(
    tmp_path: Path,
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

    result = run_scan(project, output)
    posts = rows(output, "canonical-posts.jsonl")
    conversations = rows(output, "conversations.jsonl")

    assert result["status"] == "published"
    assert current_target(output) != original_batch
    assert [row["post_id"] for row in posts].count("101") == 1
    assert len(conversations) == 1
    assert conversations[0]["user_turn_count"] == 2


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


def test_failed_state_publication_restores_previous_state_current_and_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    active = write_active(project, first_exchange())
    run_scan(project, output)
    before_state = state_bytes(output)
    before_current = current_target(output)
    before_batches = sorted(path.name for path in (output / "batches").iterdir())
    with active.open("a", encoding="utf-8") as handle:
        handle.write(continuation())

    def fail_state(_root: Path, _value: object) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(extractor, "_atomic_write_state", fail_state)
    with pytest.raises(OSError, match="injected"):
        run_scan(project, output)

    assert state_bytes(output) == before_state
    assert current_target(output) == before_current
    assert sorted(path.name for path in (output / "batches").iterdir()) == before_batches


def test_explicit_conversation_id_groups_posts() -> None:
    key = b"a" * 32
    records, _ = extractor.parse_log_records(
        (
            event_line("2026-08-24 16:10:00", "candidate_observed", target_id="301", conversation_id="conv-x", incoming_text="First meaningful turn")
            + event_line("2026-08-24 16:11:00", "candidate_observed", target_id="302", conversation_id="conv-x", incoming_text="Second meaningful turn")
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
            event_line("2026-08-24 16:10:00", "candidate_observed", target_id="400", root_post_id="400", incoming_text="Root one"),
            event_line("2026-08-24 16:11:00", "candidate_observed", target_id="401", root_post_id="400", parent_post_id="400", incoming_text="Branch one"),
            event_line("2026-08-24 16:12:00", "candidate_observed", target_id="402", root_post_id="400", parent_post_id="400", incoming_text="Branch two"),
            event_line("2026-08-24 16:13:00", "candidate_observed", target_id="403", root_post_id="403", incoming_text="Separate root"),
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
                "candidate_observed",
                target_id="550",
                root_post_id="550",
                author_id="same-author",
                incoming_text="First root",
            ),
            event_line(
                "2026-08-24 16:11:00",
                "candidate_observed",
                target_id="551",
                root_post_id="551",
                author_id="same-author",
                incoming_text="Second root",
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
        "same_chain_user_continuation",
        "multiple_substantive_user_turns",
        "multiple_account_replies",
        "third_or_later_substantive_turn",
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

    candidate = extractor.build_review_candidate(conversation)
    assert candidate is not None
    assert "post_clarification_continuation" in candidate["review_reason_codes"]
    assert "sibling_branch_activity" in candidate["review_reason_codes"]


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
    assert status["schema_version"] == 1
    assert set(tmp_path.iterdir()) == before


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
