from __future__ import annotations

import ast
import csv
import hashlib
import json
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import build_reply_replay_pack as replay


CREATED_AT = "2026-08-10T15:00:00Z"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def source_row(index: int, lane: str = "mention") -> dict[str, object]:
    first = datetime(2026, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=index)
    terminal = first + timedelta(minutes=5)
    candidate_id = f"candidate-{index:064x}"
    row: dict[str, object] = {
        "candidate_id": candidate_id,
        "lane": lane,
        "target_id": f"target-{index}",
        "thread_id": f"thread-{index}",
        "author_id": f"author-{index}",
        "incoming_text": f"incoming contribution {index}",
        "quoted_post_id": None,
        "quoted_post_text": None,
        "bounded_parent_context": [],
        "normalised_outcome": "posted",
        "normalised_reconstruction_status": "complete",
        "remaining_conflict_evidence": [],
        "actual_reply_text": f"historical response {index}",
        "normalised_mode": "courtesy",
        "normalised_tone": "warm",
        "normalised_no_reply_reason": None,
        "normalised_deterministic_rejection_reason": None,
        "normalised_reviewer_verdict": "approve",
        "normalised_model_call_count": 2,
        "normalised_revision_count": 0,
        "first_timestamp": first.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "terminal_timestamp": terminal.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_row_sha256": hashlib.sha256(candidate_id.encode()).hexdigest(),
        "prompt_era_id": f"prompt-era-{index % 3}",
        "prompt_version_evidence": {"strategy_version": "historical-v1"},
        "strategy_version": "historical-v1",
        "source_record_ids": [f"record-{index}"],
        "supplemental_evidence_ids": [],
    }
    if lane == "quote-tweet":
        row["quoted_post_id"] = f"quoted-{index}"
        row["quoted_post_text"] = f"quoted context {index}"
    return row


def selection_case(row: dict[str, object], stratum: str, rank: int) -> dict[str, object]:
    return {
        "candidate_id": row["candidate_id"],
        "final_stratum": stratum,
        "final_rank": rank,
        "reviewer_note": f"selection reason {stratum} {rank}",
        "provisional_strata": [{"stratum": stratum, "rank": rank}],
        "historical_outcome": row["normalised_outcome"],
        "historical_reply": row["actual_reply_text"],
        "incoming_text": row["incoming_text"],
        "lane": row["lane"],
        "prompt_era_id": row["prompt_era_id"],
    }


def write_checksums(shortlist: Path) -> None:
    lines = []
    for name in replay.SHORTLIST_FILES:
        digest = hashlib.sha256((shortlist / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (shortlist / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def rewrite_selection_csv(selection: Path, cases: list[dict[str, object]]) -> None:
    with (selection / replay.SELECTION_CSV).open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=["final_stratum", "final_rank", "candidate_id"],
        )
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "final_stratum": case["final_stratum"],
                    "final_rank": case["final_rank"],
                    "candidate_id": case["candidate_id"],
                }
            )


def make_inputs(tmp_path: Path) -> tuple[Path, Path, list[dict[str, object]]]:
    selection = tmp_path / "selection"
    shortlist = tmp_path / "shortlist"
    selection.mkdir()
    shortlist.mkdir()
    rows: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    index = 0
    for stratum in replay.FINAL_STRATA:
        for rank in range(1, 9):
            lane = "mention"
            if index == 1:
                lane = "quote-tweet"
            elif index == 2:
                lane = "hot-post"
            row = source_row(index, lane)
            rows.append(row)
            cases.append(selection_case(row, stratum, rank))
            index += 1
    manifest = {
        "schema_version": 1,
        "tool_version": replay.SOURCE_TOOL_VERSION,
        "normalisation_version": replay.SOURCE_NORMALISATION_VERSION,
        "source_extractor_commit": replay.SOURCE_EXTRACTOR_COMMIT,
        "running_tool_git_commit": replay.SOURCE_EXTRACTOR_COMMIT,
        "counts": {
            "input_candidates": 589,
            "excluded_candidates": 3,
            "normalised_candidates": 586,
            "evaluation_eligible_candidates": 277,
        },
    }
    dump_json(shortlist / "run_manifest.json", manifest)
    dump_jsonl(shortlist / "evaluation_eligible_candidates.jsonl", rows)
    dump_jsonl(shortlist / "normalised_candidates.jsonl", rows)
    write_checksums(shortlist)
    selection_document = {
        "schema_version": 1,
        "selection_status": "proposed_for_freeze",
        "selected_cases": cases,
        "counts": {
            "selected_cases": 48,
            "unique_selected_candidates": 48,
            "selected_cases_per_stratum": {stratum: 8 for stratum in replay.FINAL_STRATA},
        },
        "source": {
            "source_extractor_commit": replay.SOURCE_EXTRACTOR_COMMIT,
            "shortlist_run_manifest_sha256": hashlib.sha256(
                (shortlist / "run_manifest.json").read_bytes()
            ).hexdigest(),
        },
    }
    dump_json(selection / replay.SELECTION_JSON, selection_document)
    rewrite_selection_csv(selection, cases)
    (selection / replay.SELECTION_MD).write_text("# Synthetic manual selection\n", encoding="utf-8")
    return selection, shortlist, rows


def mutate_selection(selection: Path, mutator, *, sync_csv: bool = False) -> dict[str, object]:
    path = selection / replay.SELECTION_JSON
    document = json.loads(path.read_text(encoding="utf-8"))
    mutator(document)
    dump_json(path, document)
    if sync_csv:
        rewrite_selection_csv(selection, document["selected_cases"])
    return document


def mutate_rows(shortlist: Path, name: str, mutator) -> list[dict[str, object]]:
    path = shortlist / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutator(rows)
    dump_jsonl(path, rows)
    write_checksums(shortlist)
    return rows


def build(tmp_path: Path, **kwargs) -> tuple[dict[str, object], Path, Path, Path]:
    selection, shortlist, _ = make_inputs(tmp_path)
    output = tmp_path / "output"
    manifest = replay.prepare_pack(
        selection=selection,
        shortlist=shortlist,
        output=output,
        created_at=CREATED_AT,
        **kwargs,
    )
    return manifest, selection, shortlist, output


def test_selection_json_and_csv_agreement(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)
    rows, _, _ = replay.read_selection(selection)
    assert len(rows) == 48


def test_selection_json_and_csv_disagreement_refused(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)
    csv_path = selection / replay.SELECTION_CSV
    text = csv_path.read_text(encoding="utf-8").replace("candidate-", "different-", 1)
    csv_path.write_text(text, encoding="utf-8")
    with pytest.raises(replay.PackError, match="disagree"):
        replay.read_selection(selection)


def test_wrong_selected_count_refused(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)
    mutate_selection(selection, lambda doc: doc["counts"].update(selected_cases=47))
    with pytest.raises(replay.PackError, match="48"):
        replay.read_selection(selection)


def test_duplicate_selected_candidate_refused(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)

    def duplicate(doc):
        doc["selected_cases"][-1]["candidate_id"] = doc["selected_cases"][0]["candidate_id"]

    mutate_selection(selection, duplicate, sync_csv=True)
    with pytest.raises(replay.PackError, match="more than once"):
        replay.read_selection(selection)


def test_wrong_per_stratum_count_refused(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)

    def move(doc):
        doc["selected_cases"][-1]["final_stratum"] = replay.FINAL_STRATA[0]

    mutate_selection(selection, move, sync_csv=True)
    with pytest.raises(replay.PackError, match="eight cases"):
        replay.read_selection(selection)


def test_wrong_final_rank_refused(tmp_path: Path) -> None:
    selection, _, _ = make_inputs(tmp_path)

    def rank(doc):
        doc["selected_cases"][0]["final_rank"] = 8

    mutate_selection(selection, rank, sync_csv=True)
    with pytest.raises(replay.PackError, match="1 through 8"):
        replay.read_selection(selection)


def test_shortlist_checksum_verification(tmp_path: Path) -> None:
    _, shortlist, _ = make_inputs(tmp_path)
    hashes, checksum_hash = replay.verify_shortlist_checksums(shortlist)
    assert set(hashes) == set(replay.SHORTLIST_FILES)
    assert len(checksum_hash) == 64


def test_checksum_mismatch_refused(tmp_path: Path) -> None:
    _, shortlist, _ = make_inputs(tmp_path)
    with (shortlist / "normalised_candidates.jsonl").open("a", encoding="utf-8") as destination:
        destination.write("{}\n")
    with pytest.raises(replay.PackError, match="checksum mismatch"):
        replay.verify_shortlist_checksums(shortlist)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("tool_version", "wrong"),
        ("normalisation_version", "wrong"),
        ("source_extractor_commit", "0" * 40),
    ],
)
def test_source_schema_or_provenance_mismatch_refused(
    tmp_path: Path, field: str, value: object
) -> None:
    _, shortlist, _ = make_inputs(tmp_path)
    manifest_path = shortlist / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    dump_json(manifest_path, manifest)
    with pytest.raises(replay.PackError, match="mismatch"):
        replay.verify_source_manifest(shortlist)


def test_exact_candidate_join(tmp_path: Path) -> None:
    manifest, _, _, output = build(tmp_path)
    ids = [json.loads(line)["candidate_id"] for line in (output / "frozen_cases.jsonl").read_text().splitlines()]
    assert manifest["selected_count"] == 48
    assert len(ids) == len(set(ids)) == 48


def test_missing_candidate_refused(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    mutate_rows(shortlist, "normalised_candidates.jsonl", lambda rows: rows.pop())
    with pytest.raises(replay.PackError, match="missing"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=tmp_path / "output",
            created_at=CREATED_AT,
        )


def test_duplicate_candidate_row_refused(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    mutate_rows(
        shortlist,
        "evaluation_eligible_candidates.jsonl",
        lambda rows: rows.append(dict(rows[0])),
    )
    with pytest.raises(replay.PackError, match="duplicate"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=tmp_path / "output",
            created_at=CREATED_AT,
        )


def test_source_disagreement_refused(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    mutate_rows(
        shortlist,
        "evaluation_eligible_candidates.jsonl",
        lambda rows: rows[0].update(target_id="contradictory-target"),
    )
    with pytest.raises(replay.PackError, match="disagree"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=tmp_path / "output",
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    ("historical", "current"),
    [
        ("mention", "mention"),
        ("quote-tweet", "quote_tweet"),
        ("hot-post", "hot_post_reply"),
    ],
)
def test_lane_mapping(historical: str, current: str) -> None:
    assert replay.map_lane(historical, "candidate-test") == current


def test_unsupported_lane_refused() -> None:
    with pytest.raises(replay.PackError, match="unsupported"):
        replay.map_lane("other", "candidate-test")


def test_current_context_validator_accepts_generated_context() -> None:
    row = source_row(0, "quote-tweet")
    row["bounded_parent_context"] = [
        {"post_id": "parent", "author_role": "user", "text": "parent text"}
    ]
    context, adaptation = replay.build_validated_context(
        row, "quote_tweet", row["candidate_id"], "2026-08-10"
    )
    assert replay.reply_strategy.validate_reply_context(context) == context
    assert adaptation == "source_thread_id"


def test_missing_thread_uses_explicit_context_identity_adaptation() -> None:
    row = source_row(0)
    row["thread_id"] = None
    context, adaptation = replay.build_validated_context(
        row, "mention", row["candidate_id"], "2026-08-10"
    )
    assert context["thread_id"] == row["target_id"]
    assert adaptation == "target_id_as_context_identity"


def test_malformed_parent_context_refused_rather_than_invented(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    for name in ("evaluation_eligible_candidates.jsonl", "normalised_candidates.jsonl"):
        mutate_rows(
            shortlist,
            name,
            lambda rows: rows[0].update(
                bounded_parent_context=[{"post_id": "p", "text": "missing role"}]
            ),
        )
    with pytest.raises(replay.PackError, match="replay-ready"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=tmp_path / "output",
            created_at=CREATED_AT,
        )


def test_quote_tweet_without_sufficient_context_is_not_replay_ready(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)

    def remove_quote(rows):
        rows[1]["quoted_post_id"] = None
        rows[1]["quoted_post_text"] = None

    for name in ("evaluation_eligible_candidates.jsonl", "normalised_candidates.jsonl"):
        mutate_rows(shortlist, name, remove_quote)
    with pytest.raises(replay.PackError) as error:
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=tmp_path / "output",
            created_at=CREATED_AT,
        )
    assert set(error.value.candidate_fields[next(iter(error.value.candidate_fields))]) >= {
        "quoted_post_id",
        "quoted_post_text",
    }


def test_historical_reply_absent_from_model_input(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    model_rows = {
        row["candidate_id"]: row
        for row in map(json.loads, (output / "model_inputs.jsonl").read_text().splitlines())
    }
    baseline_rows = {
        row["candidate_id"]: row
        for row in map(
            json.loads, (output / "historical_baselines.jsonl").read_text().splitlines()
        )
    }
    recent_rows = {
        row["candidate_id"]: row
        for row in map(
            json.loads, (output / "recent_account_replies.jsonl").read_text().splitlines()
        )
    }
    for candidate_id, model_row in model_rows.items():
        own_reply = baseline_rows[candidate_id]["historical_reply"]
        assert own_reply not in replay.stable_json(model_row["validated_context"])
        assert candidate_id not in {
            item["candidate_id"]
            for item in recent_rows[candidate_id]["recent_account_replies"]
        }
        assert replay.audit_payload(candidate_id, "model_input", model_row) == []


def test_historical_outcome_absent_from_model_input(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    for row in map(json.loads, (output / "model_inputs.jsonl").read_text().splitlines()):
        assert "historical_outcome" not in row


def test_recursive_forbidden_key_audit() -> None:
    violations = replay.audit_payload(
        "candidate-test",
        "model_input",
        {"outer": [{"final_rank": 1}]},
        None,
    )
    assert any(item["kind"] == "forbidden_field:final_rank" for item in violations)


def test_recursive_forbidden_value_audit() -> None:
    violations = replay.audit_payload(
        "candidate-test",
        "model_input",
        {"outer": ["prefix historical_outcome suffix"]},
        None,
    )
    assert any(item["kind"] == "forbidden_field:historical_outcome" for item in violations)


def test_exact_historical_reply_leakage_detection() -> None:
    violations = replay.audit_payload(
        "candidate-test",
        "prepared_prompt_payload",
        {"recent": ["the exact old answer"]},
        "the exact old answer",
    )
    assert any(item["kind"] == "exact_historical_reply" for item in violations)


def test_earlier_candidate_reply_is_authorised_for_later_candidate(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    models = [json.loads(line) for line in (output / "model_inputs.jsonl").read_text().splitlines()]
    baselines = {
        row["candidate_id"]: row
        for row in map(
            json.loads, (output / "historical_baselines.jsonl").read_text().splitlines()
        )
    }
    candidate_a, candidate_b = models[:2]
    assert baselines[candidate_a["candidate_id"]]["historical_reply"] in candidate_b[
        "recent_account_replies_text"
    ]
    assert baselines[candidate_b["candidate_id"]]["historical_reply"] not in replay.stable_json(
        candidate_b["validated_context"]
    )


def test_future_candidate_reply_is_absent_from_earlier_case(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    models = [json.loads(line) for line in (output / "model_inputs.jsonl").read_text().splitlines()]
    baselines = {
        row["candidate_id"]: row
        for row in map(
            json.loads, (output / "historical_baselines.jsonl").read_text().splitlines()
        )
    }
    candidate_b, candidate_c = models[1], models[2]
    assert baselines[candidate_c["candidate_id"]]["historical_reply"] not in candidate_b[
        "recent_account_replies_text"
    ]


def test_identical_earlier_reply_is_authorised_by_provenance(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    shared_reply = "shared historical response"

    def share_reply(rows):
        rows[0]["actual_reply_text"] = shared_reply
        rows[1]["actual_reply_text"] = shared_reply

    for name in ("evaluation_eligible_candidates.jsonl", "normalised_candidates.jsonl"):
        mutate_rows(shortlist, name, share_reply)

    def update_selected(doc):
        doc["selected_cases"][0]["historical_reply"] = shared_reply
        doc["selected_cases"][1]["historical_reply"] = shared_reply

    mutate_selection(selection, update_selected, sync_csv=True)
    output = tmp_path / "output"
    replay.prepare_pack(
        selection=selection,
        shortlist=shortlist,
        output=output,
        created_at=CREATED_AT,
    )
    recent = [
        json.loads(line)
        for line in (output / "recent_account_replies.jsonl").read_text().splitlines()
    ][1]
    later_id = recent["candidate_id"]
    matching = [
        item
        for item in recent["recent_account_replies"]
        if item["reply_text"] == shared_reply
    ]
    audit = json.loads((output / "leakage_audit.json").read_text())
    assert len(matching) == 1
    assert matching[0]["candidate_id"] != later_id
    assert audit["self_answer_violations"] == 0
    assert audit["authorised_same_text_recent_reply_occurrences"] >= 1


def test_direct_historical_reply_in_validated_context_is_detected() -> None:
    historical_reply = "own historical response"
    model_record = {
        "candidate_id": "candidate-b",
        "current_pipeline_lane": "mention",
        "validated_context": {"incoming_contribution": historical_reply},
        "recent_account_replies_text": [],
    }
    audit = replay.audit_case_inputs(
        candidate_id="candidate-b",
        first_timestamp="2026-01-02T00:00:00Z",
        historical_reply=historical_reply,
        model_record=model_record,
        recent_record={
            "candidate_id": "candidate-b",
            "recent_account_replies": [],
            "recent_account_replies_text": [],
        },
        proposer_payload={"recent_account_replies_to_avoid_repeating": []},
        posted_index=[],
    )
    assert any(
        item["kind"] == "self_answer_historical_reply" for item in audit["violations"]
    )


def test_own_candidate_id_in_recent_metadata_is_detected() -> None:
    posted_index = replay.build_recent_reply_index(
        [recent_source("candidate-b", "2026-01-01T00:00:00Z", "earlier text")]
    )
    recent_records = [
        {
            "candidate_id": "candidate-b",
            "terminal_timestamp": "2026-01-01T00:00:00Z",
            "reply_text": "earlier text",
        }
    ]
    audit = replay.audit_case_inputs(
        candidate_id="candidate-b",
        first_timestamp="2026-01-02T00:00:00Z",
        historical_reply="own historical response",
        model_record={
            "candidate_id": "candidate-b",
            "current_pipeline_lane": "mention",
            "validated_context": {"incoming_contribution": "new input"},
            "recent_account_replies_text": ["earlier text"],
        },
        recent_record={
            "candidate_id": "candidate-b",
            "recent_account_replies": recent_records,
            "recent_account_replies_text": ["earlier text"],
        },
        proposer_payload={
            "recent_account_replies_to_avoid_repeating": ["earlier text"]
        },
        posted_index=posted_index,
    )
    assert any(item["kind"] == "self_answer_candidate_id" for item in audit["violations"])


def recent_source(
    candidate_id: str, terminal: str, reply: str, outcome: str = "posted"
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "normalised_outcome": outcome,
        "actual_reply_text": reply,
        "terminal_timestamp": terminal,
    }


def test_recent_replies_include_only_earlier_posted_replies() -> None:
    rows = [
        recent_source("earlier", "2026-01-01T00:00:00Z", "earlier reply"),
        recent_source("not-posted", "2026-01-01T00:00:01Z", "excluded", "editorial_no_reply"),
    ]
    records, texts = replay.recent_replies_for_case(
        "selected", "2026-01-02T00:00:00Z", replay.build_recent_reply_index(rows), 20
    )
    assert [row["candidate_id"] for row in records] == ["earlier"]
    assert texts == ["earlier reply"]


def test_future_replies_excluded() -> None:
    rows = [recent_source("future", "2026-01-03T00:00:00Z", "future reply")]
    records, _ = replay.recent_replies_for_case(
        "selected", "2026-01-02T00:00:00Z", replay.build_recent_reply_index(rows), 20
    )
    assert records == []


def test_selected_candidate_excluded_from_own_recent_list() -> None:
    rows = [recent_source("selected", "2026-01-01T00:00:00Z", "old attempt")]
    records, _ = replay.recent_replies_for_case(
        "selected", "2026-01-02T00:00:00Z", replay.build_recent_reply_index(rows), 20
    )
    assert records == []


def test_recent_reply_limit_uses_most_recent_in_chronological_order() -> None:
    rows = [
        recent_source(f"c-{index}", f"2026-01-0{index + 1}T00:00:00Z", f"reply {index}")
        for index in range(4)
    ]
    records, _ = replay.recent_replies_for_case(
        "selected", "2026-01-09T00:00:00Z", replay.build_recent_reply_index(rows), 2
    )
    assert [row["candidate_id"] for row in records] == ["c-2", "c-3"]


def test_repeated_recent_replies_preserved() -> None:
    rows = [
        recent_source("a", "2026-01-01T00:00:00Z", "same"),
        recent_source("b", "2026-01-02T00:00:00Z", "same"),
    ]
    _, texts = replay.recent_replies_for_case(
        "selected", "2026-01-03T00:00:00Z", replay.build_recent_reply_index(rows), 20
    )
    assert texts == ["same", "same"]


def test_one_calibration_case_per_stratum(tmp_path: Path) -> None:
    manifest, _, _, output = build(tmp_path)
    cases = [json.loads(line) for line in (output / "calibration_cases.jsonl").read_text().splitlines()]
    assert manifest["calibration_count"] == 6
    assert {row["final_stratum"] for row in cases} == set(replay.FINAL_STRATA)
    assert {row["final_rank"] for row in cases} == {1}


def test_replay_plan_has_12_calibration_pipeline_executions(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    plan = json.loads((output / "replay_plan.json").read_text())
    assert plan["calibration"]["pipeline_executions"] == 12


def test_replay_plan_has_96_full_pipeline_executions(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    plan = json.loads((output / "replay_plan.json").read_text())
    assert plan["full_run"]["pipeline_executions"] == 96
    assert plan["model_calls_performed"] == 0


def test_no_model_transport_is_imported_or_invoked() -> None:
    source = Path(replay.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"openai", "xai", "anthropic", "requests", "httpx"})
    assert "run_pipeline(" not in source


def test_fixed_created_at_produces_byte_identical_output(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    output = tmp_path / "output"
    replay.prepare_pack(
        selection=selection, shortlist=shortlist, output=output, created_at=CREATED_AT
    )
    first = {path.name: path.read_bytes() for path in output.iterdir()}
    shutil.rmtree(output)
    replay.prepare_pack(
        selection=selection, shortlist=shortlist, output=output, created_at=CREATED_AT
    )
    second = {path.name: path.read_bytes() for path in output.iterdir()}
    assert first == second


def test_private_output_permissions(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_sha256sums_verifies_every_output_except_itself(tmp_path: Path) -> None:
    _, _, _, output = build(tmp_path)
    entries = replay.parse_checksum_file(output / "SHA256SUMS")
    assert set(entries) == set(replay.OUTPUT_FILES) - {"SHA256SUMS"}
    assert all(replay.sha256_file(output / name) == digest for name, digest in entries.items())


def test_output_inside_protected_input_tree_refused(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    with pytest.raises(replay.PackError, match="protected"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=selection / "output",
            created_at=CREATED_AT,
        )


def test_nonempty_output_refused(tmp_path: Path) -> None:
    selection, shortlist, _ = make_inputs(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(replay.PackError, match="new or empty"):
        replay.prepare_pack(
            selection=selection,
            shortlist=shortlist,
            output=output,
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    "line",
    [
        f"{'0' * 64}  /absolute\n",
        f"{'0' * 64}  ../parent\n",
        "not-a-digest  run_manifest.json\n",
    ],
)
def test_unsafe_or_malformed_checksum_entry_refused(tmp_path: Path, line: str) -> None:
    path = tmp_path / "SHA256SUMS"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(replay.PackError):
        replay.parse_checksum_file(path)
