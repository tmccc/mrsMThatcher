from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools import reconstruct_reply_history as tool


CREATED_AT = "2026-08-09T21:00:00Z"


def line(timestamp: str, message: str, *, level: str = "INFO", function: str = "worker", number: int = 10) -> bytes:
    return f"{timestamp} {level:<8} {function}:{number} - {message}\n".encode()


def event(timestamp: str, kind: str, **fields: object) -> bytes:
    payload = {"event": kind, **fields}
    return line(timestamp, "EVENT " + json.dumps(payload, sort_keys=True, separators=(",", ":")))


def project(snapshot_root: Path, snapshot: str) -> Path:
    result = snapshot_root / snapshot / "project"
    result.mkdir(parents=True)
    return result


def write_project(snapshot_root: Path, snapshot: str, log: bytes, *, filename: str = "mrsMThatcher.log") -> Path:
    result = project(snapshot_root, snapshot)
    (result / filename).write_bytes(log)
    return result


def run_extract(
    tmp_path: Path,
    snapshot_root: Path,
    *,
    output_name: str = "output",
    snapshots: list[str] | None = None,
    live: Path | None = None,
    created_at: str = CREATED_AT,
) -> Path:
    output = tmp_path / output_name
    argv = [
        "--snapshot-root",
        str(snapshot_root),
        "--project-relative-path",
        "project",
        "--output",
        str(output),
        "--created-at",
        created_at,
    ]
    for name in snapshots or []:
        argv.extend(["--snapshot", name])
    if live is not None:
        argv.extend(["--live-project", str(live)])
    tool.execute(tool.build_parser().parse_args(argv))
    return output


def jsonl(path: Path) -> list[dict]:
    return [json.loads(row) for row in path.read_text().splitlines() if row]


def manifest(output: Path) -> dict:
    return json.loads((output / "run_manifest.json").read_text())


def tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def posted_candidate_log(target: str, reply: str, timestamp: str = "2026-07-10 05:30:00") -> bytes:
    return b"".join(
        [
            event(
                timestamp,
                "mention_reply_posted",
                mention_id=target,
                author_id=f"author-{target}",
                incoming_text=f"incoming contribution {target}",
                reply=reply,
                reply_post_id=f"reply-{target}",
                lane="mention",
                strategy_version="strategy-v1",
            )
        ]
    )


def test_duplicate_whole_log_file_across_two_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = b"".join(
        line(f"2026-07-10 05:30:0{index}", message)
        for index, message in enumerate(("before", "ordinary record", "after"))
    )
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body)
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["raw_record_occurrence_count"] == 6
    assert manifest(output)["counts"]["canonical_record_count"] == 3
    assert len(jsonl(output / "record_occurrences.jsonl")) == 6


def test_active_log_growing_between_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    first = line("2026-07-10 05:30:00", "first")
    second = line("2026-07-10 05:31:00", "second")
    write_project(root, "snap-2026-07-10-0525", first + second)
    write_project(root, "snap-2026-07-11-0525", first + second)
    (root / "snap-2026-07-11-0525" / "project" / "mrsMThatcher.log").write_bytes(
        first + second + line("2026-07-10 05:32:00", "third")
    )
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["raw_record_occurrence_count"] == 5
    assert manifest(output)["counts"]["canonical_record_count"] == 3


def test_log_rotation_renaming_same_record(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = (
        line("2026-07-10 05:29:00", "before")
        + line("2026-07-10 05:30:00", "rotated")
        + line("2026-07-10 05:31:00", "after")
    )
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body, filename="mrsMThatcher.log.1")
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["canonical_record_count"] == 3
    assert {row["source_path"] for row in jsonl(output / "record_occurrences.jsonl")} == {
        "mrsMThatcher.log",
        "mrsMThatcher.log.1",
    }


def test_multiline_traceback_is_grouped_with_parent_record(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line("2026-07-10 05:30:00", "failure", level="ERROR") + b"Traceback (most recent call last):\n  File \"bot.py\", line 1\nValueError: bad\n" + line("2026-07-10 05:31:00", "after")
    write_project(root, "snap-2026-07-10-0525", body)
    rows = jsonl(run_extract(tmp_path, root) / "unique_log_records.jsonl")
    assert len(rows) == 2
    assert "Traceback (most recent call last):" in rows[0]["raw_record_text"]
    assert "ValueError: bad" in rows[0]["message"]


def test_malformed_or_unterminated_final_material_warns(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line("2026-07-10 05:30:00", "complete") + b"2026-07-10 05:31:00 INFO malformed-final"
    write_project(root, "snap-2026-07-10-0525", body)
    output = run_extract(tmp_path, root)
    rows = jsonl(output / "unique_log_records.jsonl")
    assert any(item["kind"] == "malformed_timestamp_prefix_in_record" for item in rows[0]["parse_warnings"])
    assert any(item["kind"] == "unterminated_final_record" for item in rows[0]["parse_warnings"])


def test_two_identical_records_at_same_timestamp_remain_distinct(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    repeated = line("2026-07-10 05:30:00", "same")
    write_project(root, "snap-2026-07-10-0525", repeated + repeated)
    rows = jsonl(run_extract(tmp_path, root) / "unique_log_records.jsonl")
    assert len(rows) == 2
    assert {row["pair_ordinal"] for row in rows} == {1, 2}
    assert len({row["record_id"] for row in rows}) == 2


def test_structured_posted_candidate_reconstruction(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = b"".join(
        [
            line("2026-07-10 05:30:00", "Considering mention id=10 author_id=20 text='A useful question'"),
            event("2026-07-10 05:30:01", "ai_reply_pipeline_decision", lane="mention", target_id="10", status="approved", strategy_version="v3", mode="courtesy", tone="warm", reviewer_verdict="approve", model_call_count=2, revision_count=0),
            line("2026-07-10 05:30:02", "Generated reply to mention 10: 'A useful answer.'"),
            event("2026-07-10 05:30:03", "ai_reply_pipeline_outcome", lane="mention", target_id="10", status="confirmed", reply_post_id="30", strategy_version="v3"),
        ]
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["actual_reply_text"] == "A useful answer."
    assert candidate["reply_post_id"] == "30"
    assert candidate["version_confidence"] == "exact"
    assert candidate["reconstruction_status"] == "complete"


def test_structured_editorial_no_reply_reconstruction(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line("2026-07-10 05:30:00", "Considering mention id=11 author_id=21 text='Question'" ) + event(
        "2026-07-10 05:30:01",
        "ai_reply_pipeline_decision",
        lane="mention",
        target_id="11",
        status="no_reply",
        mode="no_reply",
        reason="no_substantive_prompt",
        strategy_version="v3",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "editorial_no_reply"
    assert candidate["no_reply_reason"] == "no_substantive_prompt"


def test_deterministic_rejection_reconstruction(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line("2026-07-10 05:30:00", "Considering mention id=12 author_id=22 text='Buy now'" ) + event(
        "2026-07-10 05:30:01", "candidate_skipped", lane="mention", id="12", reason="spam_or_not_worth_replying"
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "deterministic_rejection"
    assert candidate["deterministic_rejection_reason"] == "spam_or_not_worth_replying"


def test_routine_spacing_skip_remains_separate(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", event("2026-07-10 05:30:00", "candidate_skipped", lane="mention", id="13", reason="spacing"))
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    assert jsonl(output / "routine_skips.jsonl")[0]["reason"] == "spacing"


def test_legacy_scheduler_spacing_skip_has_no_candidate_or_unmatched_record(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Skipping mention check: minimum interval between replies not reached"),
    )
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    routine = jsonl(output / "routine_skips.jsonl")
    assert [(row["lane"], row["reason"], row["target_id"]) for row in routine] == [("mention", "spacing", None)]
    assert jsonl(output / "unmatched_reply_records.jsonl") == []


def test_confirmed_reply_receipt_lifecycle_reconstructs_posted_identity(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Wrote confirmed reply receipt pending local reconciliation source=mention target_id=18 reply_post_id=38 path=/source/receipt",
    ) + line(
        "2026-07-10 05:30:01",
        "Removed reconciled confirmed-reply receipt source=mention target_id=18 reply_post_id=38 path=/source/receipt",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["reply_post_id"] == "38"
    assert candidate["reconstruction_status"] == "partial"


def test_conflicting_candidate_evidence_becomes_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = event("2026-07-10 05:30:00", "mention_reply_posted", mention_id="14", lane="mention", incoming_text="Question", reply="First reply", reply_post_id="31") + event(
        "2026-07-10 05:30:01", "mention_reply_posted", mention_id="14", lane="mention", incoming_text="Question", reply="Conflicting reply", reply_post_id="32"
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["reconstruction_status"] == "ambiguous"
    assert {item["field"] for item in candidate["conflict_evidence"]} >= {"actual_reply_text", "reply_post_id"}


def test_ast_only_version_extraction_does_not_execute_source(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    source = (
        'STRATEGY_VERSION = "strategy-v9"\n'
        'PROPOSER_PROMPT_VERSION: str = "prompt-v2"\n'
        f'open({str(marker)!r}, "w").write("bad")\n'
        'raise RuntimeError("must not execute")\n'
    ).encode()
    values, warnings = tool.ast_versions(source)
    assert values == {"PROPOSER_PROMPT_VERSION": "prompt-v2", "STRATEGY_VERSION": "strategy-v9"}
    assert warnings == []
    assert not marker.exists()


def test_output_path_inside_source_or_worktree_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    args = tool.build_parser().parse_args(
        ["--snapshot-root", str(root), "--project-relative-path", "project", "--output", str(source / "corpus"), "--created-at", CREATED_AT]
    )
    with pytest.raises(tool.ExtractionError, match="inside protected source tree"):
        tool.execute(args)


def test_source_symlink_escape_is_refused_without_reading_target(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = project(root, "snap-2026-07-10-0525")
    outside = tmp_path / "outside.log"
    outside.write_bytes(line("2026-07-10 05:30:00", "secret"))
    (source / "mrsMThatcher.log").symlink_to(outside)
    args = tool.build_parser().parse_args(
        ["--snapshot-root", str(root), "--project-relative-path", "project", "--output", str(tmp_path / "output"), "--created-at", CREATED_AT]
    )
    with pytest.raises(tool.ExtractionError, match="source symlink escapes project"):
        tool.execute(args)


def test_fixed_created_at_produces_byte_identical_output_and_preserves_sources(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", posted_candidate_log("15", "Thank you for the question."))
    before = tree_digest(root)
    output = run_extract(tmp_path, root)
    first = {path.name: path.read_bytes() for path in output.iterdir()}
    shutil.rmtree(output)
    output = run_extract(tmp_path, root)
    second = {path.name: path.read_bytes() for path in output.iterdir()}
    assert first == second
    assert tree_digest(root) == before


def test_sha256sums_verifies(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    output = run_extract(tmp_path, root)
    rows = (output / "SHA256SUMS").read_text().splitlines()
    assert len(rows) == len(tool.OUTPUT_FILES) - 1
    for row in rows:
        expected, filename = row.split("  ", 1)
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == expected


def test_generated_permissions_are_private(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    output = run_extract(tmp_path, root)
    assert os.stat(output).st_mode & 0o777 == 0o700
    assert all(os.stat(path).st_mode & 0o777 == 0o600 for path in output.iterdir())


def test_quality_inventory_retains_replies_and_recurring_suffixes(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = posted_candidate_log("16", "Your point is well noted.") + posted_candidate_log(
        "17", "This contribution is well noted.", timestamp="2026-07-10 05:31:00"
    )
    write_project(root, "snap-2026-07-10-0525", body)
    output = run_extract(tmp_path, root)
    inventory = json.loads((output / "reply_quality_inventory.json").read_text())
    assert inventory["total_posted_replies_with_text"] == 2
    suffixes = {row["text"]: row["count"] for row in inventory["most_frequent_suffixes_2_to_6_words"]}
    assert suffixes["is well noted"] == 2
    assert inventory["diagnostic_phrase_frequency"]["well noted"] == 2


def test_list_snapshots_prints_only_ordered_names_and_creates_no_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-11-0525", b"")
    write_project(root, "snap-2026-07-10-0525", b"")
    output = tmp_path / "should-not-exist"
    args = tool.build_parser().parse_args(
        ["--snapshot-root", str(root), "--project-relative-path", "project", "--output", str(output), "--list-snapshots"]
    )
    assert tool.execute(args) == {"listed": True}
    assert capsys.readouterr().out == "snap-2026-07-10-0525\nsnap-2026-07-11-0525\n"
    assert not output.exists()


def test_snapshot_version_row_uses_ast_constants(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    (source / "reply_strategy.py").write_text('STRATEGY_VERSION = "era-1"\nREVIEWER_PROMPT_VERSION = "review-2"\n')
    row = jsonl(run_extract(tmp_path, root) / "snapshot_versions.jsonl")[0]
    assert row["constants"] == {"REVIEWER_PROMPT_VERSION": "review-2", "STRATEGY_VERSION": "era-1"}
    assert row["inferred_snapshot_ordering_timestamp"] == "2026-07-10T04:25:00Z"
    assert row["snapshot_timestamp"]["timezone_basis"] == "Europe/London local time"


def test_legacy_mention_state_survives_rotation_boundary(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Considering mention id=101 author_id=201 text='Question'"),
        filename="mrsMThatcher.log.1",
    )
    (source / "mrsMThatcher.log").write_bytes(
        line("2026-07-10 05:30:01", "Generated reply to mention 101: 'Answer.'")
        + line("2026-07-10 05:30:02", "Recorded and cached own auto-reply id=301")
        + line("2026-07-10 05:30:03", "Reply posted successfully")
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["reply_post_id"] == "301"
    assert candidate["reconstruction_status"] == "complete"


def test_legacy_quote_tweet_state_survives_rotation_boundary(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "snap-2026-07-10-0525",
        line(
            "2026-07-10 05:30:00",
            "Considering quote tweet id=102 author_id=202 original_post_id=402 text='Question'",
            function="maybe_reply_to_quote_tweets",
        ),
        filename="mrsMThatcher.log.1",
    )
    (source / "mrsMThatcher.log").write_bytes(
        line("2026-07-10 05:30:01", "Generated reply to quote tweet 102: 'Answer.'", function="maybe_reply_to_quote_tweets")
        + line("2026-07-10 05:30:02", "Recorded and cached own quote-tweet auto-reply id=302", function="maybe_reply_to_quote_tweets")
        + line("2026-07-10 05:30:03", "Quote-tweet reply posted successfully", function="maybe_reply_to_quote_tweets")
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["lane"] == "quote-tweet"
    assert candidate["outcome"] == "posted"
    assert candidate["reply_post_id"] == "302"


def test_legacy_editorial_no_reply_survives_rotation_boundary(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Considering mention id=103 author_id=203 text='Question'"),
        filename="mrsMThatcher.log.1",
    )
    (source / "mrsMThatcher.log").write_bytes(
        line("2026-07-10 05:30:01", "No usable reply generated for mention 103")
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "editorial_no_reply"
    assert candidate["incoming_text"] == "Question"


def test_legacy_logs_are_ordered_by_numeric_rotation_then_active(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Considering mention id=104 author_id=204 text='Question'"),
        filename="mrsMThatcher.log.10",
    )
    (source / "mrsMThatcher.log.2").write_bytes(
        line("2026-07-10 05:30:01", "Generated reply to mention 104: 'Answer.'")
    )
    (source / "mrsMThatcher.log.1").write_bytes(
        line("2026-07-10 05:30:02", "Recorded and cached own auto-reply id=304")
    )
    (source / "mrsMThatcher.log").write_bytes(
        line("2026-07-10 05:30:03", "Reply posted successfully")
    )
    output = run_extract(tmp_path, root)
    log_sources = [row["source_path"] for row in jsonl(output / "source_files.jsonl") if row["is_log"]]
    assert log_sources == [
        "mrsMThatcher.log.10",
        "mrsMThatcher.log.2",
        "mrsMThatcher.log.1",
        "mrsMThatcher.log",
    ]
    candidate = jsonl(output / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["reply_post_id"] == "304"


def test_legacy_pending_state_does_not_leak_between_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Considering mention id=105 author_id=205 text='Question'"),
    )
    write_project(
        root,
        "snap-2026-07-11-0525",
        line("2026-07-11 05:30:00", "Reply posted successfully"),
    )
    output = run_extract(tmp_path, root)
    candidate = jsonl(output / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "unresolved"
    assert any(row["event_kind"] == "legacy_reply_posted" for row in jsonl(output / "unmatched_reply_records.jsonl"))


@pytest.mark.parametrize("reason", ["exact_duplicate_reply", "near_duplicate_reply"])
def test_pipeline_duplicate_reason_is_deterministic_rejection_in_non_no_reply_mode(
    tmp_path: Path, reason: str
) -> None:
    root = tmp_path / "snapshots"
    body = event(
        "2026-07-10 05:30:00",
        "ai_reply_pipeline_decision",
        lane="mention",
        target_id="110",
        status="no_reply",
        mode="courtesy",
        reason=reason,
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "deterministic_rejection"
    assert candidate["deterministic_rejection_reason"] == reason


def test_clarification_mode_refusal_is_deterministic_rejection(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "reply_strategy_decision",
            lane="mention",
            target_id="111",
            status="no_reply",
            mode="opinion_or_principle",
            no_reply_reason="clarification_not_direct_factual_answer",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "deterministic_rejection"
    assert candidate["deterministic_rejection_reason"] == "clarification_not_direct_factual_answer"


def test_local_rejection_outcome_precedes_generic_legacy_no_reply(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Considering mention id=1111 author_id=2111 text='Question'",
    ) + event(
        "2026-07-10 05:30:01",
        "reply_strategy_decision",
        lane="mention",
        target_id="1111",
        mode="courtesy",
        reason="exact_duplicate_reply",
        status="no_reply",
    ) + line(
        "2026-07-10 05:30:02",
        "No usable reply generated for mention 1111",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "deterministic_rejection"
    assert candidate["reconstruction_status"] == "complete"
    assert not any(item["field"] == "outcome" for item in candidate["conflict_evidence"])
    assert candidate["terminal_attempt_history"][-1]["is_generic_wrapper"] is True


def test_strategy_disabled_is_non_terminal_evidence_with_raw_reason(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "ai_reply_pipeline_decision",
            lane="mention",
            target_id="112",
            status="disabled",
            mode="no_reply",
            reason="strategy_disabled",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "unresolved"
    assert candidate["raw_reasons"] == ["strategy_disabled"]
    assert any("non-terminal" in note for note in candidate["reconstruction_notes"])


def test_unknown_candidate_skip_reason_remains_unresolved(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "candidate_skipped",
            lane="mention",
            id="113",
            reason="future_policy_gate",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "unresolved"
    assert candidate["raw_reasons"] == ["future_policy_gate"]
    assert any("unknown candidate skip reason" in note for note in candidate["reconstruction_notes"])


def test_known_operational_candidate_skip_is_operational_failure(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event("2026-07-10 05:30:00", "candidate_skipped", lane="mention", id="114", reason="context_unavailable"),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "operational_failure"
    assert candidate["raw_reasons"] == ["context_unavailable"]


def test_structured_confirmed_receipt_lifecycle_reconstructs_posted(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = event(
        "2026-07-10 05:30:00",
        "confirmed_reply_receipt_sending",
        source="mention",
        target_id="120",
    ) + event(
        "2026-07-10 05:30:01",
        "confirmed_reply_receipt_promoted",
        source="mention",
        target_id="120",
        reply_post_id="320",
    ) + event(
        "2026-07-10 05:30:02",
        "confirmed_reply_receipt_removed",
        source="mention",
        target_id="120",
        reply_post_id="320",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["reply_post_id"] == "320"


def test_structured_sending_receipt_alone_does_not_establish_posted(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "confirmed_reply_receipt_sending",
            source="mention",
            target_id="121",
            reply_post_id="321",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "unresolved"
    assert candidate["terminal_timestamp"] is None


def test_unknown_reply_related_structured_event_is_unmatched(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event("2026-07-10 05:30:00", "reply_strategy_future_marker", lane="mention", target_id="122"),
    )
    unmatched = jsonl(run_extract(tmp_path, root) / "unmatched_reply_records.jsonl")
    assert [(row["event_kind"], row["message_class"]) for row in unmatched] == [
        ("reply_strategy_future_marker", "unrecognised_structured_reply_event")
    ]


def test_unknown_reply_related_legacy_record_is_unmatched(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 05:30:00", "Conversational reply future state was selected", function="maybe_reply_to_mentions"),
    )
    unmatched = jsonl(run_extract(tmp_path, root) / "unmatched_reply_records.jsonl")
    assert unmatched[0]["message_class"] == "unrecognised_legacy_reply_record"


def test_ordinary_unrelated_legacy_record_is_not_unmatched(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "ordinary scheduler record"))
    assert jsonl(run_extract(tmp_path, root) / "unmatched_reply_records.jsonl") == []


def test_log_timestamp_in_july_bst_is_normalised_to_utc(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "ordinary record"))
    record = jsonl(run_extract(tmp_path, root) / "unique_log_records.jsonl")[0]
    assert record["timestamp"] == "2026-07-10T04:30:00Z"
    assert record["original_timestamp_text"] == "2026-07-10 05:30:00"


def test_log_timestamp_in_winter_gmt_is_normalised_to_utc(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-01-10-0525", line("2026-01-10 05:30:00", "ordinary record"))
    record = jsonl(run_extract(tmp_path, root) / "unique_log_records.jsonl")[0]
    assert record["timestamp"] == "2026-01-10T05:30:00Z"
    assert record["original_timestamp_text"] == "2026-01-10 05:30:00"


def test_log_and_supplemental_iso_timestamp_share_utc_chronology(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "snap-2026-07-10-0525",
        line("2026-07-10 13:00:00", "Considering mention id=130 author_id=230 text='Question'"),
    )
    (source / "bot_state.json").write_text(
        json.dumps(
            {
                "ai_reply_history": [
                    {
                        "created_at": "2026-07-10T12:00:00Z",
                        "lane": "mention",
                        "proposed_reply": "Answer.",
                        "reply_post_id": "330",
                        "status": "confirmed",
                        "target_id": "130",
                    }
                ]
            }
        )
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["first_timestamp"] == "2026-07-10T12:00:00Z"
    assert candidate["terminal_timestamp"] == "2026-07-10T12:00:00Z"
    assert {row["original_timestamp_text"] for row in candidate["timestamp_evidence"]} == {
        "2026-07-10 13:00:00",
        "2026-07-10T12:00:00Z",
    }


def test_supplemental_epoch_timestamp_is_normalised_to_utc(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(root, "snap-2026-07-10-0525", b"")
    (source / "bot_state.json").write_text(
        json.dumps(
            {
                "ai_reply_history": [
                    {
                        "lane": "mention",
                        "reply_epoch": 1,
                        "status": "failed",
                        "target_id": "131",
                    }
                ]
            }
        )
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["first_timestamp"] == "1970-01-01T00:00:01Z"
    assert candidate["terminal_timestamp"] == "1970-01-01T00:00:01Z"
    assert candidate["timestamp_evidence"][0]["original_timestamp_text"] == "1"


def test_historical_context_receipt_is_excluded_from_conversational_corpus(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(root, "snap-2026-07-10-0525", b"")
    (source / "historical_context_reply_receipt.json").write_text(
        json.dumps(
            {
                "created_at": "2026-07-10T12:00:00Z",
                "lane": "historical-context",
                "reply_post_id": "340",
                "reply_text": "Historical output.",
                "status": "confirmed",
                "target_id": "140",
            }
        )
    )
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    assert all(row["source_path"] != "historical_context_reply_receipt.json" for row in jsonl(output / "source_files.jsonl"))
    inventory = json.loads((output / "reply_quality_inventory.json").read_text())
    assert inventory["total_posted_candidates"] == 0
    assert inventory["total_posted_replies_with_text"] == 0


def test_write_jsonl_streams_a_one_pass_generator_with_stable_bytes(tmp_path: Path) -> None:
    consumed: list[int] = []

    def rows():
        for value in (2, 1):
            consumed.append(value)
            yield {"value": value, "label": "row"}

    output = tmp_path / "rows.jsonl"
    tool.write_jsonl(output, rows())
    assert consumed == [2, 1]
    assert output.read_bytes() == (
        b'{"label":"row","value":2}\n'
        b'{"label":"row","value":1}\n'
    )


def test_many_duplicate_log_occurrences_retain_compact_canonical_semantics(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = b"".join(
        line(f"2026-07-10 05:{index // 60:02d}:{index % 60:02d}", f"ordinary record {index}")
        for index in range(100)
    )
    for day in range(1, 21):
        write_project(root, f"snap-2026-07-{day:02d}-0525", body)
    output = run_extract(tmp_path, root)
    counts = manifest(output)["counts"]
    assert counts["raw_record_occurrence_count"] == 2000
    assert counts["canonical_record_count"] == 100
    assert len(jsonl(output / "record_occurrences.jsonl")) == 2000
    assert {row["occurrence_count"] for row in jsonl(output / "unique_log_records.jsonl")} == {20}


def test_generated_quote_reply_requires_matching_pending_target(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Considering quote tweet id=150 author_id=250 original_post_id=450 text='Question'",
        function="maybe_reply_to_quote_tweets",
    ) + line(
        "2026-07-10 05:30:01",
        "Generated reply to quote tweet 151: 'Wrong target.'",
        function="maybe_reply_to_quote_tweets",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    output = run_extract(tmp_path, root)
    candidate = jsonl(output / "conversational_candidates.jsonl")[0]
    assert candidate["target_id"] == "150"
    assert candidate["actual_reply_text"] is None
    unmatched = jsonl(output / "unmatched_reply_records.jsonl")
    assert unmatched[0]["event_kind"] == "legacy_quote_reply_generated"
    assert unmatched[0]["target_id"] == "151"


def test_same_timestamp_routine_records_remain_distinct_while_snapshot_copies_collapse(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    record = event(
        "2026-07-10 05:30:00",
        "candidate_skipped",
        lane="mention",
        id="160",
        reason="spacing",
    )
    write_project(root, "snap-2026-07-10-0525", record + record)
    write_project(root, "snap-2026-07-11-0525", record + record)
    routine = jsonl(run_extract(tmp_path, root) / "routine_skips.jsonl")
    assert len(routine) == 2
    assert len({row["routine_skip_id"] for row in routine}) == 2
    assert {len(row["source_record_ids"]) for row in routine} == {1}


def test_identical_supplemental_evidence_retains_every_snapshot_occurrence(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    document = json.dumps(
        {
            "ai_reply_history": [
                {
                    "created_at": "2026-07-10T12:00:00Z",
                    "lane": "mention",
                    "proposed_reply": "Answer.",
                    "reply_post_id": "370",
                    "status": "confirmed",
                    "target_id": "170",
                }
            ]
        }
    )
    for snapshot in ("snap-2026-07-10-0525", "snap-2026-07-11-0525"):
        source = write_project(root, snapshot, b"")
        (source / "bot_state.json").write_text(document)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert len(candidate["supplemental_evidence_ids"]) == 1
    assert len(candidate["supplemental_occurrences"]) == 2
    assert {row["supplemental_source"].split(":", 2)[1] for row in candidate["supplemental_occurrences"]} == {
        "snap-2026-07-10-0525",
        "snap-2026-07-11-0525",
    }


def test_identical_same_timestamp_records_split_across_rotation_are_not_lost(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    repeated = line(
        "2026-07-10 05:30:00",
        "Considering mention id=180 author_id=280 text='Question'",
        function="maybe_reply_to_mentions",
    )
    source = write_project(root, "snap-2026-07-10-0525", repeated, filename="mrsMThatcher.log.1")
    (source / "mrsMThatcher.log").write_bytes(repeated)
    output = run_extract(tmp_path, root)
    records = jsonl(output / "unique_log_records.jsonl")
    assert len(records) == 2
    assert {row["pair_ordinal"] for row in records} == {1, 2}
    assert any(
        item["kind"] == "ambiguous_identical_record_across_rotation_boundary"
        for row in records
        for item in row["parse_warnings"]
    )


def test_each_version_field_is_attributed_independently(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(
        root,
        "zfs-auto-snap_daily-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "mention_reply_posted",
            mention_id="version-1",
            lane="mention",
            incoming_text="Question",
            reply="Answer.",
            reply_post_id="posted-version-1",
            strategy_version="strategy-explicit",
        ),
    )
    (source / "reply_strategy.py").write_text(
        '\n'.join(
            (
                'STRATEGY_VERSION = "strategy-snapshot"',
                'PROPOSER_PROMPT_VERSION = "proposer-snapshot"',
                'REVIEWER_PROMPT_VERSION = "reviewer-snapshot"',
                'NO_REPLY_REVIEW_PROMPT_VERSION = "no-reply-snapshot"',
                'CLAIM_AUDITOR_PROMPT_VERSION = "auditor-snapshot"',
            )
        )
        + "\n"
    )
    output = run_extract(tmp_path, root)
    candidate = jsonl(output / "conversational_candidates.jsonl")[0]
    row = jsonl(output / "snapshot_versions.jsonl")[0]
    attribution = candidate["version_attribution"]
    assert attribution["strategy_version"]["value"] == "strategy-explicit"
    assert attribution["strategy_version"]["confidence"] == "exact"
    for field in tool.PROMPT_VERSION_FIELDS:
        assert attribution[field]["confidence"] == "approximate"
        assert attribution[field]["source_identity"] == "zfs-auto-snap_daily-2026-07-10-0525"
        assert attribution[field]["snapshot_version_row_id"] == row["version_row_id"]


def test_supplemental_only_candidate_uses_its_occurrence_snapshot_versions(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    source = write_project(root, "snap-2026-07-10-0525", b"")
    (source / "reply_strategy.py").write_text(
        'PROPOSER_PROMPT_VERSION = "supplemental-proposer"\n'
    )
    (source / "bot_state.json").write_text(
        json.dumps(
            {
                "ai_reply_history": [
                    {
                        "created_at": "2026-07-10T12:00:00Z",
                        "lane": "mention",
                        "proposed_reply": "Answer.",
                        "reply_post_id": "supplemental-post",
                        "status": "confirmed",
                        "target_id": "supplemental-version",
                    }
                ]
            }
        )
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    attribution = candidate["version_attribution"]["proposer_prompt_version"]
    assert attribution["value"] == "supplemental-proposer"
    assert attribution["confidence"] == "approximate"
    assert attribution["source_identity"] == "snap-2026-07-10-0525"
    assert attribution["evidence_ids"] == candidate["supplemental_evidence_ids"]


def test_conflicting_explicit_prompt_versions_are_null_and_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = event(
        "2026-07-10 05:30:00",
        "reply_strategy_decision",
        lane="mention",
        target_id="prompt-conflict",
        proposer_prompt_version="prompt-a",
        status="approved",
    ) + event(
        "2026-07-10 05:30:01",
        "mention_reply_posted",
        mention_id="prompt-conflict",
        lane="mention",
        incoming_text="Question",
        proposer_prompt_version="prompt-b",
        reply="Answer.",
        reply_post_id="prompt-conflict-post",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    attribution = candidate["version_attribution"]["proposer_prompt_version"]
    assert attribution["value"] is None
    assert attribution["confidence"] == "exact"
    assert {row["value"] for row in attribution["conflicting_values"]} == {
        "prompt-a",
        "prompt-b",
    }
    assert len(attribution["evidence_ids"]) == 2
    assert candidate["reconstruction_status"] == "ambiguous"


def test_same_strategy_different_prompt_tuples_have_separate_prompt_eras(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = b"".join(
        event(
            f"2026-07-10 05:30:0{index}",
            "mention_reply_posted",
            mention_id=f"prompt-era-{index}",
            lane="mention",
            incoming_text=f"Question {index}",
            proposer_prompt_version=f"proposer-{index}",
            reply=f"Answer {index}.",
            reply_post_id=f"prompt-era-post-{index}",
            strategy_version="shared-strategy",
        )
        for index in (1, 2)
    )
    write_project(root, "snap-2026-07-10-0525", body)
    output = run_extract(tmp_path, root)
    candidates = jsonl(output / "conversational_candidates.jsonl")
    assert {row["strategy_version"] for row in candidates} == {"shared-strategy"}
    assert len({row["prompt_era_id"] for row in candidates}) == 2
    inventory = json.loads((output / "reply_quality_inventory.json").read_text())
    assert inventory["counts_by"]["strategy_version"] == {"shared-strategy": 2}
    assert sorted(inventory["counts_by"]["prompt_era"].values()) == [1, 1]


def test_absent_prompt_evidence_is_explicitly_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", posted_candidate_log("prompt-absent", "Answer."))
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    for field in tool.PROMPT_VERSION_FIELDS:
        assert candidate[field] is None
        assert candidate["version_attribution"][field]["confidence"] == "unavailable"
    assert [row["value"] for row in candidate["prompt_era_components"]] == [
        "unavailable",
        "unavailable",
        "unavailable",
        "unavailable",
    ]


@pytest.mark.parametrize(
    ("later", "expected"),
    [
        (
            event(
                "2026-07-10 05:30:01",
                "reply_strategy_decision",
                lane="mention",
                target_id="outcome-order",
                mode="no_reply",
                reason="no_substantive_prompt",
                status="no_reply",
            ),
            "editorial_no_reply",
        ),
        (
            event(
                "2026-07-10 05:30:01",
                "reply_strategy_decision",
                lane="mention",
                target_id="outcome-order",
                reason="exact_duplicate_reply",
                status="no_reply",
            ),
            "deterministic_rejection",
        ),
        (
            event(
                "2026-07-10 05:30:01",
                "mention_reply_posted",
                mention_id="outcome-order",
                lane="mention",
                incoming_text="Question",
                reply="Answer.",
                reply_post_id="outcome-order-post",
            ),
            "posted",
        ),
    ],
)
def test_operational_failure_yields_to_valid_terminal_disposition(
    tmp_path: Path, later: bytes, expected: str
) -> None:
    root = tmp_path / "snapshots"
    failure = event(
        "2026-07-10 05:30:00",
        "reply_strategy_failure",
        lane="mention",
        target_id="outcome-order",
        reason="context_unavailable",
    )
    write_project(root, "snap-2026-07-10-0525", failure + later)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == expected
    assert [row["outcome"] for row in candidate["terminal_attempt_history"]] == [
        "operational_failure",
        expected,
    ]


def test_confirmed_post_is_not_erased_by_later_operational_failure(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = event(
        "2026-07-10 05:30:00",
        "mention_reply_posted",
        mention_id="posted-then-failed",
        lane="mention",
        incoming_text="Question",
        reply="Answer.",
        reply_post_id="posted-then-failed-post",
    ) + event(
        "2026-07-10 05:30:01",
        "reply_strategy_failure",
        lane="mention",
        target_id="posted-then-failed",
        reason="context_unavailable",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "posted"
    assert candidate["terminal_timestamp"] == "2026-07-10T04:30:00Z"
    assert candidate["terminal_attempt_history"][-1]["outcome"] == "operational_failure"


def test_operational_failure_is_final_when_it_is_the_only_terminal(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "reply_strategy_failure",
            lane="mention",
            target_id="failure-only",
            reason="context_unavailable",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "operational_failure"


def test_same_time_unordered_specific_dispositions_are_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "candidate_skipped",
            lane="mention",
            id="same-time-conflict",
            reason="exact_duplicate_reply",
        ),
    )
    write_project(
        root,
        "snap-2026-07-11-0525",
        event(
            "2026-07-10 05:30:00",
            "reply_strategy_decision",
            lane="mention",
            target_id="same-time-conflict",
            reason="no_substantive_prompt",
            status="no_reply",
        ),
    )
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "unresolved"
    assert candidate["reconstruction_status"] == "ambiguous"
    assert any(
        item.get("basis", "").startswith("contradictory specific terminal")
        for item in candidate["conflict_evidence"]
    )


@pytest.mark.parametrize(
    "reason",
    [
        "exact_duplicate_reply",
        "near_duplicate_reply",
        "clarification_not_direct_factual_answer",
    ],
)
def test_generic_no_reply_wrapper_supports_specific_rejection(
    tmp_path: Path, reason: str
) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Considering mention id=401 author_id=author text='Question'",
    ) + event(
        "2026-07-10 05:30:01",
        "reply_strategy_decision",
        lane="mention",
        target_id="401",
        reason=reason,
        status="no_reply",
    ) + line(
        "2026-07-10 05:30:02",
        "No usable reply generated for mention 401",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "deterministic_rejection"
    assert candidate["reconstruction_status"] != "ambiguous"
    wrapper = candidate["terminal_attempt_history"][-1]
    assert wrapper["event_kind"] == "legacy_editorial_no_reply"
    assert wrapper["is_generic_wrapper"] is True


def test_generic_no_reply_without_specific_decision_is_editorial(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Considering mention id=402 author_id=author text='Question'",
    ) + line(
        "2026-07-10 05:30:01",
        "No usable reply generated for mention 402",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "editorial_no_reply"
    assert candidate["terminal_attempt_history"][0]["is_generic_wrapper"] is False


def test_generic_no_reply_outside_wrapper_window_is_a_separate_attempt(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line(
        "2026-07-10 05:30:00",
        "Considering mention id=403 author_id=author text='Question'",
    ) + event(
        "2026-07-10 05:30:01",
        "reply_strategy_decision",
        lane="mention",
        target_id="403",
        reason="exact_duplicate_reply",
        status="no_reply",
    ) + line(
        "2026-07-10 05:30:07",
        "No usable reply generated for mention 403",
    )
    write_project(root, "snap-2026-07-10-0525", body)
    candidate = jsonl(run_extract(tmp_path, root) / "conversational_candidates.jsonl")[0]
    assert candidate["outcome"] == "editorial_no_reply"
    assert candidate["terminal_attempt_history"][-1]["is_generic_wrapper"] is False


def test_repeated_pair_then_single_survivor_is_not_linked_to_first(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    repeated = line("2026-07-10 05:30:00", "same occurrence")
    write_project(root, "snap-2026-07-10-0525", repeated + repeated)
    write_project(root, "snap-2026-07-11-0525", repeated)
    output = run_extract(tmp_path, root)
    occurrences = jsonl(output / "record_occurrences.jsonl")
    first_source = [row for row in occurrences if row["source_identity"].endswith("07-10-0525")]
    survivor = next(row for row in occurrences if row["source_identity"].endswith("07-11-0525"))
    assert survivor["record_id"] != first_source[0]["record_id"]
    assert survivor["occurrence_reconciliation_status"] == "ambiguous_unmerged"
    assert survivor["occurrence_ambiguity_id"]


def test_isolated_identical_records_from_different_snapshots_do_not_collapse(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    repeated = line("2026-07-10 05:30:00", "isolated")
    write_project(root, "snap-2026-07-10-0525", repeated)
    write_project(root, "snap-2026-07-11-0525", repeated)
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["canonical_record_count"] == 2
    occurrences = jsonl(output / "record_occurrences.jsonl")
    assert len({row["record_id"] for row in occurrences}) == 2
    assert manifest(output)["counts"]["occurrence_ambiguity_count"] == 1


def test_complete_identical_streams_collapse_with_sequence_context(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = b"".join(
        line(f"2026-07-10 05:30:0{index}", value)
        for index, value in enumerate(("left", "middle", "right"))
    )
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body)
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["canonical_record_count"] == 3
    assert {row["occurrence_count"] for row in jsonl(output / "unique_log_records.jsonl")} == {2}


def test_repeated_run_split_across_rotation_boundaries_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    left = line("2026-07-10 05:29:59", "left")
    repeated = line("2026-07-10 05:30:00", "repeat")
    right = line("2026-07-10 05:30:01", "right")
    for snapshot in ("snap-2026-07-10-0525", "snap-2026-07-11-0525"):
        source = write_project(root, snapshot, left + repeated, filename="mrsMThatcher.log.1")
        (source / "mrsMThatcher.log").write_bytes(repeated + right)
    output = run_extract(tmp_path, root)
    repeat_rows = [
        row for row in jsonl(output / "unique_log_records.jsonl") if row["message"] == "repeat"
    ]
    assert len(repeat_rows) == 2
    assert {row["occurrence_count"] for row in repeat_rows} == {2}


def test_one_sided_stream_context_matches_only_when_unique(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = (
        line("2026-07-10 05:30:00", "beginning")
        + line("2026-07-10 05:30:01", "middle")
        + line("2026-07-10 05:30:02", "ending")
    )
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body)
    occurrences = jsonl(run_extract(tmp_path, root) / "record_occurrences.jsonl")
    second = [row for row in occurrences if row["source_identity"].endswith("07-11-0525")]
    assert {row["occurrence_reconciliation_status"] for row in second} == {
        "matched_unique_context"
    }


def test_irreconcilable_repeated_context_emits_occurrence_ambiguity(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    left = line("2026-07-10 05:29:59", "left")
    repeated = line("2026-07-10 05:30:00", "ambiguous repeat")
    right = line("2026-07-10 05:30:01", "right")
    write_project(root, "snap-2026-07-10-0525", left + repeated + right + left + repeated + right)
    write_project(root, "snap-2026-07-11-0525", left + repeated + right)
    output = run_extract(tmp_path, root)
    fingerprints = {
        row["record_id"]
        for row in jsonl(output / "unique_log_records.jsonl")
        if row["message"] == "ambiguous repeat"
    }
    occurrence = next(
        row
        for row in jsonl(output / "record_occurrences.jsonl")
        if row["source_identity"].endswith("07-11-0525")
        and row["record_id"] in fingerprints
    )
    assert occurrence["occurrence_reconciliation_status"] == "ambiguous_unmerged"
    assert occurrence["occurrence_ambiguity_id"]
    assert manifest(output)["counts"]["occurrence_ambiguity_count"] >= 1


def test_real_snapshot_name_forms_sort_in_true_utc_order() -> None:
    names = [
        "zfs-auto-snap_daily-2026-08-04-0525",
        "mrs-deploy-pre-feca-20260803T184612Z",
        "zfs-auto-snap_daily-2026-08-03-0525",
    ]
    assert sorted(names, key=tool.snapshot_sort_key) == [
        "zfs-auto-snap_daily-2026-08-03-0525",
        "mrs-deploy-pre-feca-20260803T184612Z",
        "zfs-auto-snap_daily-2026-08-04-0525",
    ]
    daily = tool.snapshot_timestamp_metadata(names[2])
    deploy = tool.snapshot_timestamp_metadata(names[1])
    assert daily == {
        "timestamp_utc": "2026-08-03T04:25:00Z",
        "original_name": names[2],
        "timezone_basis": "Europe/London local time",
        "inference_method": "daily YYYY-MM-DD-HHMM timestamp parsed from snapshot name",
    }
    assert deploy["timestamp_utc"] == "2026-08-03T18:46:12Z"
    assert deploy["timezone_basis"] == "explicit UTC suffix Z"


@pytest.mark.parametrize("lane", [None, "unavailable", "future-conversation-lane"])
def test_recognised_reply_event_with_unknown_lane_is_unmatched(
    tmp_path: Path, lane: str | None
) -> None:
    root = tmp_path / "snapshots"
    fields: dict[str, object] = {"target_id": "unknown-lane", "status": "approved"}
    if lane is not None:
        fields["lane"] = lane
    write_project(
        root,
        "snap-2026-07-10-0525",
        event("2026-07-10 05:30:00", "reply_strategy_decision", **fields),
    )
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    unmatched = jsonl(output / "unmatched_reply_records.jsonl")
    assert unmatched[0]["message_class"] == "recognised_reply_event_unknown_lane"


def test_historical_context_structured_event_is_explicitly_out_of_scope(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "reply_strategy_decision",
            lane="historical-context",
            target_id="historical",
            status="approved",
        ),
    )
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    assert jsonl(output / "unmatched_reply_records.jsonl") == []


@pytest.mark.parametrize("lane", ["regular-post", "quote-image", "daily-meme", "meme"])
def test_regular_post_and_meme_structured_evidence_is_out_of_scope(
    tmp_path: Path, lane: str
) -> None:
    root = tmp_path / "snapshots"
    write_project(
        root,
        "snap-2026-07-10-0525",
        event(
            "2026-07-10 05:30:00",
            "reply_strategy_decision",
            lane=lane,
            target_id="out-of-scope",
            status="approved",
        ),
    )
    output = run_extract(tmp_path, root)
    assert jsonl(output / "conversational_candidates.jsonl") == []
    assert jsonl(output / "unmatched_reply_records.jsonl") == []


def test_sha256_file_reads_multiple_chunks(tmp_path: Path) -> None:
    path = tmp_path / "large.bin"
    data = b"a" * (tool.HASH_BLOCK_SIZE + 37) + b"tail"
    path.write_bytes(data)
    assert tool.sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_occurrence_spool_streams_once_in_deterministic_order(tmp_path: Path) -> None:
    spool = tool.OccurrenceSpool(tmp_path, "ordering")
    try:
        for record_id, source_identity, sequence in (
            ("record-b", "snap-b", 2),
            ("record-a", "snap-b", 2),
            ("record-a", "snap-a", 1),
        ):
            row = {
                "occurrence_id": f"{record_id}-{source_identity}",
                "occurrence_reconciliation_status": "new",
                "occurrence_reconciliation_basis": "test",
                "occurrence_ambiguity_id": None,
                "pair_ordinal": 1,
                "record_id": record_id,
                "record_fingerprint": "fingerprint",
                "source_file_sequence": sequence,
                "source_identity": source_identity,
                "source_path": "mrsMThatcher.log",
                "source_stream_sequence": sequence,
                "source_type": "snapshot",
            }
            spool.add_occurrence(row, source_order=sequence)
        rows = spool.iter_occurrences()
        assert iter(rows) is rows
        assert [row["occurrence_id"] for row in rows] == [
            "record-a-snap-a",
            "record-a-snap-b",
            "record-b-snap-b",
        ]
        assert list(rows) == []
    finally:
        spool.close(remove=True)


def test_successful_run_removes_private_occurrence_spool(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    prefix = ".spool-output.occurrence-spool-"
    before = set(Path(tempfile.gettempdir()).glob(prefix + "*.sqlite3"))
    run_extract(tmp_path, root, output_name="spool-output")
    after = set(Path(tempfile.gettempdir()).glob(prefix + "*.sqlite3"))
    assert after == before


def test_schema_v2_manifest_includes_extractor_and_python_provenance(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    write_project(root, "snap-2026-07-10-0525", line("2026-07-10 05:30:00", "record"))
    run_manifest = manifest(run_extract(tmp_path, root))
    assert run_manifest["schema_version"] == 2
    assert run_manifest["tool_version"] == "reply-history-reconstruction-v2"
    assert run_manifest["extractor_source_sha256"] == tool.sha256_file(Path(tool.__file__))
    assert run_manifest["python_implementation"]
    assert run_manifest["python_version"]
    assert run_manifest["extractor_git_commit_confidence"] in {
        "exact",
        "approximate",
        "unavailable",
    }


def test_extractor_provenance_distinguishes_clean_and_dirty_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    source = repository / "tools" / "reconstruct_reply_history.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('clean')\n")
    commands = (
        ["git", "init", str(repository)],
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        ["git", "-C", str(repository), "add", "tools/reconstruct_reply_history.py"],
        ["git", "-C", str(repository), "commit", "-m", "test source"],
    )
    for command in commands:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
    clean = tool.extractor_provenance(source, repository)
    assert clean["extractor_git_commit_confidence"] == "exact"
    assert clean["extractor_git_commit"]
    source.write_text("print('dirty')\n")
    dirty = tool.extractor_provenance(source, repository)
    assert dirty["extractor_git_commit"] == clean["extractor_git_commit"]
    assert dirty["extractor_git_commit_confidence"] == "approximate"
    assert dirty["extractor_source_sha256"] != clean["extractor_source_sha256"]
