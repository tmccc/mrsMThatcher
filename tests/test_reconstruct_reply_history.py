from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    body = line("2026-07-10 05:30:00", "ordinary record")
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body)
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["raw_record_occurrence_count"] == 2
    assert manifest(output)["counts"]["canonical_record_count"] == 1
    assert len(jsonl(output / "record_occurrences.jsonl")) == 2


def test_active_log_growing_between_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    first = line("2026-07-10 05:30:00", "first")
    second = line("2026-07-10 05:31:00", "second")
    write_project(root, "snap-2026-07-10-0525", first)
    write_project(root, "snap-2026-07-11-0525", first + second)
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["raw_record_occurrence_count"] == 3
    assert manifest(output)["counts"]["canonical_record_count"] == 2


def test_log_rotation_renaming_same_record(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    body = line("2026-07-10 05:30:00", "rotated")
    write_project(root, "snap-2026-07-10-0525", body)
    write_project(root, "snap-2026-07-11-0525", body, filename="mrsMThatcher.log.1")
    output = run_extract(tmp_path, root)
    assert manifest(output)["counts"]["canonical_record_count"] == 1
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
    assert row["inferred_snapshot_ordering_timestamp"] == "2026-07-10T05:25:00"
