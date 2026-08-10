from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import stat
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest


WORKTREE = Path(__file__).resolve().parents[1]
TOOL_PATH = WORKTREE / "tools" / "build_reply_evaluation_pool.py"
SPEC = importlib.util.spec_from_file_location("build_reply_evaluation_pool", TOOL_PATH)
assert SPEC and SPEC.loader
pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pool)


CREATED_AT = "2026-08-10T12:00:00Z"
FIRST_TIMESTAMP = "2026-08-10T11:00:00Z"


def snowflake(timestamp: str = FIRST_TIMESTAMP) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    milliseconds = int(parsed.timestamp() * 1000)
    return str((milliseconds - pool.SNOWFLAKE_EPOCH_MS) << 22)


def terminal_event(
    evidence_id: str,
    outcome: str,
    reason: str | None,
    order: int,
    *,
    kind: str = "reply_strategy_decision",
    timestamp: str = FIRST_TIMESTAMP,
    specific: bool = True,
    generic: bool = False,
) -> dict:
    return {
        "attempt_index": order,
        "chronology_status": "placed",
        "event_kind": kind,
        "evidence_id": evidence_id,
        "evidence_locations": [
            {
                "ordering_domain": "log_stream",
                "source_identity": "synthetic",
                "source_order": order,
                "source_type": "synthetic",
            }
        ],
        "generic_wrapper_for_evidence_id": None,
        "is_generic_wrapper": generic,
        "is_specific": specific,
        "order_index": order,
        "outcome": outcome,
        "reason": reason,
        "timestamp": timestamp,
    }


def candidate(
    candidate_id: str = "candidate-a",
    *,
    outcome: str = "posted",
    incoming: str = "What year did this policy begin?",
    reply: str | None = "It began in 1981.",
    reply_post_id: str | None = "2000000000000000001",
) -> dict:
    reason = None
    event = terminal_event("event-final", outcome, reason, 1)
    return {
        "candidate_id": candidate_id,
        "target_id": snowflake(),
        "first_timestamp": FIRST_TIMESTAMP,
        "terminal_timestamp": FIRST_TIMESTAMP,
        "lane": "mention",
        "incoming_text": incoming,
        "author_id": "author-1",
        "outcome": outcome,
        "reconstruction_status": "complete" if outcome != "unresolved" else "partial",
        "actual_reply_text": reply if outcome == "posted" else None,
        "reply_post_id": reply_post_id if outcome == "posted" else None,
        "mode": "direct_factual_answer" if outcome == "posted" else "no_reply",
        "tone": "neutral",
        "reviewer_verdict": "approve" if outcome == "posted" else "confirm_no_reply",
        "model_call_count": 1,
        "revision_count": 0,
        "no_reply_reason": None,
        "deterministic_rejection_reason": None,
        "final_outcome_evidence_id": "event-final",
        "terminal_attempt_history": [] if outcome == "unresolved" else [event],
        "conflict_evidence": [],
        "raw_reasons": [],
        "prompt_era_id": "era-a",
        "source_record_ids": ["event-final"],
    }


def inventory(formulaic_ids: list[str] | None = None) -> dict:
    ids = formulaic_ids or []
    groups = []
    if ids:
        groups.append({"candidate_ids": ids, "count": len(ids), "rule": "synthetic", "type": "test"})
    return {
        "schema_version": 2,
        "tool_version": pool.SOURCE_TOOL_VERSION,
        "candidate_formulaic_clusters": groups,
        "exact_duplicate_groups": [],
        "normalised_duplicate_groups": [],
        "near_duplicate_groups": {},
    }


def write_checksums(corpus: Path) -> None:
    names = ["run_manifest.json", "conversational_candidates.jsonl", "reply_quality_inventory.json"]
    lines = []
    for name in names:
        digest = hashlib.sha256((corpus / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (corpus / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def make_corpus(tmp_path: Path, rows: list[dict] | None = None, inv: dict | None = None) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manifest = {
        "schema_version": 2,
        "tool_version": pool.SOURCE_TOOL_VERSION,
        "extractor_git_commit": pool.SOURCE_EXTRACTOR_COMMIT,
        "extractor_git_commit_confidence": "exact",
        "live_project_included": False,
        "selected_snapshots": [f"snapshot-{number:02d}" for number in range(32)],
    }
    (corpus / "run_manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    with (corpus / "conversational_candidates.jsonl").open("wb") as handle:
        for row in rows or [candidate()]:
            handle.write(pool.canonical_json(row))
    (corpus / "reply_quality_inventory.json").write_text(
        json.dumps(inv or inventory(), sort_keys=True) + "\n", encoding="utf-8"
    )
    write_checksums(corpus)
    return corpus


def args(corpus: Path, output: Path, per_stratum: int = 12) -> Namespace:
    return Namespace(
        corpus=str(corpus),
        output=str(output),
        created_at=CREATED_AT,
        shortlist_per_stratum=per_stratum,
        snowflake_tolerance_hours=24.0,
    )


def normalise(row: dict) -> tuple[dict, list[dict]]:
    return pool.normalise_candidate(row, hashlib.sha256(pool.canonical_json(row).rstrip(b"\n")).hexdigest())


def eligible_row(candidate_id: str, incoming: str, outcome: str = "posted", mode: str = "courtesy") -> dict:
    row = candidate(candidate_id, outcome=outcome, incoming=incoming)
    if outcome != "posted":
        row["actual_reply_text"] = None
        row["reply_post_id"] = None
    row["normalised_outcome"] = outcome
    row["normalised_reconstruction_status"] = "complete"
    row["normalised_no_reply_reason"] = "abuse or obvious bait" if outcome == "editorial_no_reply" else None
    row["normalised_deterministic_rejection_reason"] = "exact_duplicate_reply" if outcome == "deterministic_rejection" else None
    row["evaluation_eligibility"] = {"eligible": True, "basis": "synthetic"}
    row["mode"] = mode
    row["suggested_strata"] = []
    return row


def test_required_input_checksum_verification(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    manifest, _inventory, hashes, verification = pool.verify_source(corpus)
    assert manifest["schema_version"] == 2
    assert hashes["conversational_candidates.jsonl"]
    assert all(item["verified"] for item in verification["verified_files"])


def test_checksum_mismatch_refusal(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    with (corpus / "conversational_candidates.jsonl").open("ab") as handle:
        handle.write(b" \n")
    with pytest.raises(pool.PoolBuildError, match="checksum mismatch"):
        pool.verify_source(corpus)


@pytest.mark.parametrize("field,value", [("schema_version", 1), ("extractor_git_commit", "bad")])
def test_schema_or_extractor_mismatch_refusal(tmp_path: Path, field: str, value: object) -> None:
    corpus = make_corpus(tmp_path)
    manifest = json.loads((corpus / "run_manifest.json").read_text(encoding="utf-8"))
    manifest[field] = value
    (corpus / "run_manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    write_checksums(corpus)
    with pytest.raises(pool.PoolBuildError, match="mismatch"):
        pool.verify_source(corpus)


def test_unsafe_checksum_paths_refused() -> None:
    digest = "0" * 64
    for content in (
        f"{digest}  ../escape\n",
        f"{digest}  /absolute\n",
        f"{digest}  ..\\escape\n",
        f"{digest}  file\n{digest}  file\n",
        "malformed\n",
    ):
        with pytest.raises(pool.PoolBuildError):
            pool.parse_sha256sums(content.encode())


def test_output_inside_corpus_or_worktree_refused(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    with pytest.raises(pool.PoolBuildError, match="source corpus"):
        pool.validate_paths(corpus, corpus / "out", WORKTREE)
    with pytest.raises(pool.PoolBuildError, match="worktree"):
        pool.validate_paths(corpus, WORKTREE / "out-not-created", WORKTREE)


def test_private_output_permissions(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    output = tmp_path / "output"
    pool.build(args(corpus, output))
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_fixed_created_at_gives_byte_identical_output(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    output = tmp_path / "output"
    pool.build(args(corpus, output))
    first = {path.name: path.read_bytes() for path in output.iterdir()}
    shutil.rmtree(output)
    pool.build(args(corpus, output))
    second = {path.name: path.read_bytes() for path in output.iterdir()}
    assert first == second


def test_valid_x_snowflake_near_candidate_time_retained() -> None:
    diagnostic = pool.decode_target_snowflake(candidate(), 24)
    assert diagnostic["valid_for_candidate_time"] is True
    assert diagnostic["absolute_time_delta_hours"] == 0


def test_invalid_synthetic_snowflake_excluded() -> None:
    row = candidate()
    row["target_id"] = "910"
    diagnostic = pool.decode_target_snowflake(row, 24)
    assert diagnostic["valid_for_candidate_time"] is False
    assert diagnostic["decoded_target_timestamp"].startswith("2010-")


@pytest.mark.parametrize("reason", ["exact_duplicate_reply", "near_duplicate_reply"])
def test_specific_duplicate_outranks_generic_wrappers(reason: str) -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["no_reply_reason"] = pool.GENERIC_REASON
    row["terminal_attempt_history"] = [
        terminal_event("specific", "deterministic_rejection", reason, 1),
        terminal_event("wrapper-1", "editorial_no_reply", pool.GENERIC_REASON, 2, kind="legacy_editorial_no_reply", specific=False, generic=True),
        terminal_event("wrapper-2", "editorial_no_reply", pool.GENERIC_REASON, 3, kind="candidate_skipped"),
    ]
    result, actions = normalise(row)
    assert result["normalised_outcome"] == "deterministic_rejection"
    assert result["normalised_deterministic_rejection_reason"] == reason
    assert any(action["field"] == "outcome" for action in actions)


def test_later_genuine_specific_editorial_decision_is_not_overwritten() -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["terminal_attempt_history"] = [
        terminal_event("duplicate", "deterministic_rejection", "exact_duplicate_reply", 1),
        terminal_event("genuine", "editorial_no_reply", "unsupported allegation", 2),
    ]
    result, _actions = normalise(row)
    assert result["normalised_outcome"] == "editorial_no_reply"


def test_no_reply_generic_reason_removed_as_conflict() -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["no_reply_reason"] = pool.GENERIC_REASON
    row["final_outcome_evidence_id"] = "generic-final"
    row["terminal_attempt_history"] = [
        terminal_event("specific", "editorial_no_reply", "missing context", 1),
        terminal_event("generic-final", "editorial_no_reply", pool.GENERIC_REASON, 2, kind="candidate_skipped"),
    ]
    row["conflict_evidence"] = [{"field": "no_reply_reason", "values": [pool.GENERIC_REASON, "missing context"], "evidence_ids": ["generic-old", "specific"]}]
    result, _actions = normalise(row)
    assert result["normalised_no_reply_reason"] == "missing context"
    assert result["remaining_conflict_evidence"] == []
    assert result["normalised_reconstruction_status"] == "complete"


def test_separate_retry_reasons_choose_latest_specific_attempt() -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["terminal_attempt_history"] = [
        terminal_event("first", "editorial_no_reply", "first specific reason", 1, timestamp="2026-08-10T10:00:00Z"),
        terminal_event("second", "editorial_no_reply", "second specific reason", 2, timestamp="2026-08-10T11:00:00Z"),
    ]
    row["conflict_evidence"] = [{"field": "no_reply_reason", "values": ["first specific reason", "second specific reason"], "evidence_ids": ["first", "second"]}]
    row["final_outcome_evidence_id"] = "wrapper"
    result, _actions = normalise(row)
    assert result["normalised_no_reply_reason"] == "second specific reason"


@pytest.mark.parametrize(
    "field,values,expected",
    [("mode", ["unavailable", "no_reply"], "no_reply"), ("reviewer_verdict", ["invalid", "confirm_no_reply"], "confirm_no_reply")],
)
def test_attempt_metadata_resolved_from_final_outcome_evidence(field: str, values: list, expected: object) -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["no_reply_reason"] = "specific reason"
    row["final_outcome_evidence_id"] = "final"
    row["terminal_attempt_history"] = [terminal_event("final", "editorial_no_reply", "specific reason", 1)]
    row["conflict_evidence"] = [{"field": field, "values": values, "evidence_ids": ["old", "final"]}]
    result, _actions = normalise(row)
    assert result[field] == expected
    assert result[f"original_{field}"] == row[field]
    assert result[f"normalised_{field}"] == expected
    assert result["remaining_conflict_evidence"] == []


def test_malformed_value_evidence_arrays_remain_ambiguous() -> None:
    row = candidate(outcome="editorial_no_reply", reply=None, reply_post_id=None)
    row["no_reply_reason"] = "specific reason"
    row["terminal_attempt_history"] = [terminal_event("final", "editorial_no_reply", "specific reason", 1)]
    row["conflict_evidence"] = [{"field": "mode", "values": ["one", "two"], "evidence_ids": ["final"]}]
    result, _actions = normalise(row)
    assert result["normalised_reconstruction_status"] == "ambiguous"
    assert result["remaining_conflict_evidence"]
    assert result["mode"] is None


def test_unresolved_candidates_remain_partial_and_ineligible() -> None:
    row = candidate(outcome="unresolved", reply=None, reply_post_id=None)
    result, _actions = normalise(row)
    assert result["normalised_reconstruction_status"] == "partial"
    assert result["evaluation_eligibility"]["eligible"] is False


@pytest.mark.parametrize("reply,reply_id", [(None, "post"), ("reply", None), ("reply", "post")])
def test_posted_candidate_completeness_requirements(reply: str | None, reply_id: str | None) -> None:
    row = candidate(reply=reply, reply_post_id=reply_id)
    result, _actions = normalise(row)
    expected = "complete" if reply and reply_id else "partial"
    assert result["normalised_reconstruction_status"] == expected


def test_exact_and_near_input_clustering() -> None:
    rows = [
        eligible_row("candidate-a", "The cycle turns again!"),
        eligible_row("candidate-b", "the cycle turns again"),
        eligible_row("candidate-c", "The cycle turn again"),
        eligible_row("candidate-z", "A wholly different contribution about history"),
    ]
    clusters, mapping = pool.cluster_inputs(rows)
    assert mapping["candidate-a"] == mapping["candidate-b"]
    assert mapping["candidate-a"] == mapping["candidate-c"]
    assert mapping["candidate-a"] != mapping["candidate-z"]
    assert any(row["size"] == 3 for row in clusters)


def test_formulaic_inventory_candidate_ids_are_imported() -> None:
    ids, basis = pool.inventory_formulaic_ids(
        {
            "candidate_formulaic_clusters": [{"candidate_ids": ["candidate-a"]}],
            "exact_duplicate_groups": [{"candidate_ids": ["candidate-b"]}],
            "normalised_duplicate_groups": [],
            "near_duplicate_groups": {"0.90": [{"candidate_ids": ["candidate-c"]}]},
        }
    )
    assert ids == {"candidate-a", "candidate-b", "candidate-c"}
    assert basis["candidate-c"] == ["near_duplicate_groups[0.90]"]


def test_each_suggested_stratum_has_transparent_rule_evidence() -> None:
    formulaic = eligible_row(
        "candidate-formulaic",
        "However, when the policy cycle turns, should public trust follow service, or must principle come first?",
        mode="wry_reply",
    )
    formulaic["actual_reply_text"] = "Your observation is well noted."
    courtesy = eligible_row("candidate-courtesy", "Thank you, very much appreciated.")
    factual = eligible_row("candidate-factual", "What year did the historical policy begin?", outcome="editorial_no_reply")
    factual["normalised_no_reply_reason"] = "Question requires missing historical context"
    safety = eligible_row("candidate-safety", "An unrelated provocation", outcome="deterministic_rejection")
    all_matches = []
    for row in (formulaic, courtesy, factual, safety):
        matches = pool.score_candidate(row, {"candidate-formulaic"}, {"candidate-formulaic": ["candidate_formulaic_clusters"]})
        all_matches.extend(matches)
        assert all({"score", "rule_hits", "rule_misses", "rank_basis"} <= set(match) for match in matches)
    assert set(pool.STRATA) <= {match["stratum"] for match in all_matches}


def test_author_and_input_cluster_shortlist_limits() -> None:
    rows = []
    for name, author in (("a", "same"), ("b", "same"), ("c", "same"), ("d", "other")):
        row = eligible_row(f"candidate-{name}", f"input {name}")
        row["author_id"] = author
        row["suggested_strata"] = [pool.make_match(pool.STRATA[1], 5, ["civil"], [], {"civil": 5})]
        rows.append(row)
    mapping = {"candidate-a": "cluster-1", "candidate-b": "cluster-1", "candidate-c": "cluster-3", "candidate-d": "cluster-4"}
    pairs = pool.select_shortlist(rows, mapping, 4)
    assert len(pairs) == 3
    assert len({pair["input_cluster_id"] for pair in pairs}) == len(pairs)
    assert sum(pair["author_id"] == "same" for pair in pairs) == 2


def test_deterministic_ranking_and_candidate_id_tie_breaking() -> None:
    rows = []
    for name in ("candidate-c", "candidate-a", "candidate-b"):
        row = eligible_row(name, name)
        row["author_id"] = None
        row["suggested_strata"] = [pool.make_match(pool.STRATA[1], 5, ["civil"], [], {"civil": 5})]
        rows.append(row)
    mapping = {row["candidate_id"]: "cluster-" + row["candidate_id"] for row in rows}
    pairs = pool.select_shortlist(rows, mapping, 3)
    assert [pair["candidate_id"] for pair in pairs] == ["candidate-a", "candidate-b", "candidate-c"]


def test_blank_manual_selection_fields() -> None:
    pair = {
        "stratum": "test", "provisional_rank": 1, "candidate_id": "candidate-a", "lane": "mention",
        "prompt_era_id": "era", "historical_outcome": "posted",
    }
    rows = list(csv.DictReader(pool.csv_bytes([pair]).decode().splitlines()))
    assert rows[0]["selected"] == rows[0]["final_stratum"] == rows[0]["reviewer_note"] == ""


def test_output_sha256sums_verifies(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    output = tmp_path / "output"
    pool.build(args(corpus, output))
    sums = pool.parse_sha256sums((output / "SHA256SUMS").read_bytes())
    assert sums
    assert all(pool.sha256_file(output / name) == digest for name, digest in sums.items())


def test_tool_never_opens_large_record_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = make_corpus(tmp_path)
    (corpus / "unique_log_records.jsonl").write_text("forbidden", encoding="utf-8")
    (corpus / "record_occurrences.jsonl").write_text("forbidden", encoding="utf-8")
    opened: list[str] = []
    original_open = Path.open

    def recording_open(path: Path, *open_args, **open_kwargs):
        opened.append(path.name)
        if path.name in {"unique_log_records.jsonl", "record_occurrences.jsonl"}:
            raise AssertionError("large record file was opened")
        return original_open(path, *open_args, **open_kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    pool.build(args(corpus, tmp_path / "output"))
    assert "unique_log_records.jsonl" not in opened
    assert "record_occurrences.jsonl" not in opened
