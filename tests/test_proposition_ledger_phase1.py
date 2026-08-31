from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import build_proposition_ledger_phase1 as phase1


PROJECT_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = (
    PROJECT_DIR / "proposition_ledger_research/phase1/synthetic-fixtures"
)
LEDGER_SCHEMA_PATH = (
    PROJECT_DIR / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_dir = FIXTURES_DIR / name
    return (
        _load_json(fixture_dir / "transcript.json"),
        _load_json(fixture_dir / "expected-ledger.json"),
    )


def _invalid_example(name: str, invalid_example_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_dir = FIXTURES_DIR / name
    transcript = _load_json(fixture_dir / "transcript.json")
    pack = _load_json(fixture_dir / "invalid-ledger-examples.json")
    matches = [
        example
        for example in pack["examples"]
        if example["invalid_example_id"] == invalid_example_id
    ]
    assert len(matches) == 1
    return transcript, matches[0]


@pytest.fixture(scope="module")
def ledger_schema() -> dict[str, Any]:
    return _load_json(LEDGER_SCHEMA_PATH)


def _rehash(ledger: dict[str, Any]) -> dict[str, Any]:
    ledger["ledger_sha256"] = phase1.ledger_sha256(ledger)
    return ledger


def _has_error(errors: list[str], code: str) -> bool:
    return any(error == code or error.startswith(code + ":") for error in errors)


def test_driver_imports_no_production_or_provider_modules() -> None:
    source = (PROJECT_DIR / "tools/build_proposition_ledger_phase1.py").read_text(
        encoding="utf-8"
    )
    imported_roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])

    assert "mrsMThatcher2" not in sys.modules
    assert imported_roots.isdisjoint(
        {
            "mrsMThatcher2",
            "openai",
            "anthropic",
            "google",
            "xai",
            "tweepy",
        }
    )


def test_all_synthetic_ledgers_have_exact_matching_evidence_spans(
    ledger_schema: dict[str, Any],
) -> None:
    fixture_dirs = sorted(path for path in FIXTURES_DIR.iterdir() if path.is_dir())
    assert len(fixture_dirs) == 12
    for fixture_dir in fixture_dirs:
        transcript = _load_json(fixture_dir / "transcript.json")
        ledger = _load_json(fixture_dir / "expected-ledger.json")
        errors = phase1.validate_ledger(ledger, transcript, ledger_schema)
        assert errors == [], f"{fixture_dir.name}: {errors}"


def test_mismatched_exact_evidence_span_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["propositions"][0]["exact_evidence_spans"][0]["exact_text"] = (
        "the village bridge is closed"
    )
    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)
    assert _has_error(errors, "evidence_span_mismatch")


def test_out_of_bounds_evidence_span_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    evidence = ledger["propositions"][0]["exact_evidence_spans"][0]
    turn_text = next(
        turn["text"] for turn in transcript["turns"] if turn["turn_id"] == evidence["turn_id"]
    )
    evidence["start_char"] = 0
    evidence["end_char"] = len(turn_text) + 1
    evidence["exact_text"] = turn_text

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "evidence_span_out_of_bounds")


def test_future_turn_references_are_rejected(ledger_schema: dict[str, Any]) -> None:
    transcript, example = _invalid_example(
        "01-direct-question-answer", "future-turn-reference"
    )
    errors = phase1.validate_ledger(example["ledger"], transcript, ledger_schema)
    assert _has_error(errors, "future_turn_reference")


def test_orphan_proposition_relation_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["proposition_relations"][0]["target_proposition_ids"] = [
        "p-does-not-exist"
    ]
    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)
    assert _has_error(errors, "orphan_relation")


def test_all_referenced_ids_exist_in_valid_fixtures(
    ledger_schema: dict[str, Any],
) -> None:
    forbidden_prefixes = (
        "orphan_",
        "duplicate_id:",
        "non_backward_parent:",
    )
    for fixture_dir in sorted(path for path in FIXTURES_DIR.iterdir() if path.is_dir()):
        transcript = _load_json(fixture_dir / "transcript.json")
        ledger = _load_json(fixture_dir / "expected-ledger.json")
        errors = phase1.validate_ledger(ledger, transcript, ledger_schema)
        assert not [
            error for error in errors if error.startswith(forbidden_prefixes)
        ], fixture_dir.name


def test_quoted_claim_does_not_imply_speaker_commitment(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("07-quoted-without-endorsement")
    quoted = next(
        proposition
        for proposition in ledger["propositions"]
        if proposition["proposition_kind"] == "quoted_claim"
    )
    matching_commitments = [
        commitment
        for commitment in ledger["participant_commitments"]
        if commitment["proposition_id"] == quoted["proposition_id"]
    ]
    assert quoted["commitment_status"] == "attributed_to_another"
    assert {item["stance"] for item in matching_commitments} == {
        "attributed_to_another"
    }
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []

    quoted["commitment_status"] = "speaker_committed"
    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)
    assert _has_error(errors, "quoted_claim_implies_commitment")


def test_compound_allegation_remains_separable(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("05-compound-allegation")
    group = next(
        group
        for group in ledger["proposition_groups"]
        if group["structure_type"] == "compound_accusation"
    )
    proposition_ids = {item["proposition_id"] for item in ledger["propositions"]}
    member_ids = [member["proposition_id"] for member in group["members"]]
    assert len(member_ids) == len(set(member_ids)) >= 3
    assert set(member_ids) <= proposition_ids
    assert {"conduct", "cause", "motive"} <= {
        member["role"] for member in group["members"]
    }
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []

    invalid_transcript, example = _invalid_example(
        "05-compound-allegation", "collapsed-compound-allegation"
    )
    errors = phase1.validate_ledger(
        example["ledger"], invalid_transcript, ledger_schema
    )
    assert _has_error(errors, "compound_collapse")


def test_no_stable_issue_is_a_valid_abstention(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("08-rhetorical-no-stable-issue")
    issue = ledger["issue_states"][0]
    assert issue["issue_type"] == "no_stable_issue"
    assert issue["status"] == "no_stable_issue"
    assert issue["canonical_question"] is None
    assert "no_stable_issue" in ledger["extraction_status"]["abstentions"]
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []


def test_correction_rejects_substitute_while_live_proposition_survives(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("03-existence-versus-security")
    targets = {item["answer_target_id"]: item for item in ledger["answer_targets"]}
    rejected = ledger["rejected_answer_targets"][0]
    propositions = {
        item["proposition_id"]: item for item in ledger["propositions"]
    }

    assert targets[rejected["answer_target_id"]]["target_status"] == "rejected"
    replacement_id = rejected["replacement_answer_target_id"]
    assert targets[replacement_id]["target_status"] == "confirmed"
    assert targets[replacement_id]["proposition_ids"] == ["p-liberty-exists"]
    assert propositions["p-liberty-exists"]["lifecycle_status"] == "live"
    assert ledger["issue_states"][0]["status"] == "open"
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []


def test_not_x_but_y_records_rejected_and_replacement_targets(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("02-explicit-correction")
    rejected = ledger["rejected_answer_targets"][0]
    target_statuses = {
        item["answer_target_id"]: item["target_status"]
        for item in ledger["answer_targets"]
    }
    assert rejected["rejection_kind"] == "not_x_but_y"
    assert target_statuses[rejected["answer_target_id"]] == "rejected"
    assert target_statuses[rejected["replacement_answer_target_id"]] == "confirmed"
    assert any(
        proposition["lifecycle_status"] == "live"
        and proposition["proposition_id"] in rejected["related_proposition_ids"]
        for proposition in ledger["propositions"]
    )
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []


def test_invalid_lifecycle_transition_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, example = _invalid_example(
        "11-partial-concession-rebuttal", "silently-reopened-proposition"
    )
    errors = phase1.validate_ledger(example["ledger"], transcript, ledger_schema)
    assert _has_error(errors, "invalid_lifecycle_transition")


def test_canonical_and_ledger_hashes_are_deterministic() -> None:
    left = {"z": [3, 2, 1], "a": {"two": 2, "one": 1}}
    right = {"a": {"one": 1, "two": 2}, "z": [3, 2, 1]}
    assert phase1.canonical_json_bytes(left) == phase1.canonical_json_bytes(right)
    assert phase1.sha256_bytes(phase1.canonical_json_bytes(left)) == phase1.sha256_bytes(
        phase1.canonical_json_bytes(right)
    )

    _, ledger = _fixture("01-direct-question-answer")
    assert phase1.ledger_sha256(ledger) == ledger["ledger_sha256"]
    reordered = dict(reversed(list(ledger.items())))
    assert phase1.ledger_sha256(reordered) == ledger["ledger_sha256"]
    changed = copy.deepcopy(ledger)
    changed["conversation_key"] += "-changed"
    assert phase1.ledger_sha256(changed) != ledger["ledger_sha256"]


def test_source_scoped_hmac_pseudonyms_are_deterministic_and_nonreversible() -> None:
    key = bytes(range(32))
    raw = "synthetic-contributor-identifier"
    first = phase1.hmac_author_key(key, "family-a", raw)
    assert first == phase1.hmac_author_key(key, "family-a", raw)
    assert first != phase1.hmac_author_key(key, "family-b", raw)
    assert first.startswith("author-hmac-")
    assert raw not in first


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_is_rejected(tmp_path: Path, constant: str) -> None:
    path = tmp_path / "nonfinite.json"
    path.write_text('{"value": ' + constant + "}\n", encoding="utf-8")
    with pytest.raises(phase1.Phase1Error, match="non-finite JSON value"):
        phase1._read_json(path)


def test_nonfinite_python_values_and_malformed_jsonl_are_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(phase1.Phase1Error, match="non-finite number"):
        phase1.canonical_json_bytes({"nested": [float("nan")]})

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text('{"valid": true}\n{"broken": }\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        phase1._read_jsonl(malformed)

    incomplete = tmp_path / "incomplete.jsonl"
    incomplete.write_text('{"valid": true}', encoding="utf-8")
    with pytest.raises(phase1.Phase1Error, match="incomplete JSONL line"):
        phase1._read_jsonl(incomplete)


def test_row_and_record_hashes_reproduce_and_detect_mutation() -> None:
    original = {"conversation_key": "synthetic:c1", "turn_count": 3}
    row = phase1._row_with_hash(original)
    record = phase1._record_with_hash(original)
    assert "row_sha256" not in original
    assert "record_sha256" not in original
    assert phase1._validate_row_hashes([row], "row") == []
    assert phase1._validate_record_hashes([record], "record") == []

    changed_row = copy.deepcopy(row)
    changed_row["turn_count"] = 4
    changed_record = copy.deepcopy(record)
    changed_record["turn_count"] = 4
    assert phase1._validate_row_hashes([changed_row], "row") == [
        "row:0:row_sha256"
    ]
    assert phase1._validate_record_hashes([changed_record], "record") == [
        "record:0:record_sha256"
    ]


def test_private_output_helpers_enforce_modes_and_refuse_symlinks(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private"
    phase1._ensure_private_directory(private_dir)
    assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700

    calibration_path = private_dir / "calibration-pack/calibration-records.jsonl"
    phase1._write_jsonl(calibration_path, [{"synthetic": True}])
    phase1._write_json(private_dir / "result.json", {"passed": True})
    phase1._write_markdown(private_dir / "result.md", ["# Synthetic result"])
    assert stat.S_IMODE(calibration_path.parent.stat().st_mode) == 0o700
    for path in (calibration_path, private_dir / "result.json", private_dir / "result.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    target = private_dir / "target.txt"
    target.write_bytes(b"unchanged")
    symlink = private_dir / "unsafe-link"
    symlink.symlink_to(target)
    with pytest.raises(OSError):
        phase1._write_private(symlink, b"must not follow")
    assert target.read_bytes() == b"unchanged"


def test_source_collection_digest_is_deterministic_and_excludes_symlinks(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "responses"
    collection.mkdir()
    (collection / "b.json").write_text('{"case": 2}\n', encoding="utf-8")
    (collection / "a.json").write_text('{"case": 1}\n', encoding="utf-8")
    (collection / "ignored.txt").write_text("not in the collection\n", encoding="utf-8")
    (collection / "linked.json").symlink_to(collection / "a.json")
    entry = {
        "path": str(collection),
        "source_collection": True,
        "collection_pattern": "*.json",
        "collection_recursive": False,
    }

    first = phase1._source_digest(entry)
    assert first == phase1._source_digest(entry)
    assert [path.name for path in phase1._collection_files(entry)] == [
        "a.json",
        "b.json",
    ]
    (collection / "a.json").write_text('{"case": 3}\n', encoding="utf-8")
    assert phase1._source_digest(entry) != first


def test_privacy_validation_detects_synthetic_raw_identifier(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    raw_identifier = "private-synthetic-123456"
    private_conversation_text = (
        "This is invented private conversation text long enough for exact leak detection."
    )
    source_paths: list[Path] = []
    for filename in ("historical.jsonl", "shadow.jsonl"):
        path = source_dir / filename
        path.write_text(
            json.dumps({"author": {"id": raw_identifier}}) + "\n",
            encoding="utf-8",
        )
        source_paths.append(path)
    conversation_path = source_dir / "prospective-conversations.jsonl"
    conversation_path.write_text(
        json.dumps({"turns": [{"text": private_conversation_text}]}) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "sources": [
            {
                "source_id": "historical_replay_266",
                "path": str(source_paths[0]),
            },
            {
                "source_id": "historical_shadow_union_293",
                "path": str(source_paths[1]),
            },
            {
                "source_id": "prospective_conversations",
                "path": str(conversation_path),
                "structured_kind": "prospective_conversations",
            },
        ]
    }
    private_output = tmp_path / "private-output"
    phase1._ensure_private_directory(private_output)
    phase1._ensure_private_directory(private_output / "calibration-pack")
    phase1._write_private(private_output / "private-author-key", b"k" * 32)
    phase1._write_json(private_output / "clean.json", {"author_key": "pseudonym-1"})

    clean = phase1._privacy_validation(project_dir, private_output, manifest)
    assert clean["passed"] is True
    assert clean["raw_contributor_id_matches"] == 0
    assert clean["tracked_private_conversation_text_matches"] == 0
    assert clean["private_author_key_included_in_hash_manifest"] is False

    tracked_research_dir = project_dir / "proposition_ledger_research"
    tracked_research_dir.mkdir()
    tracked_leak = tracked_research_dir / "leak.txt"
    tracked_leak.write_text(private_conversation_text, encoding="utf-8")
    text_leaked = phase1._privacy_validation(project_dir, private_output, manifest)
    assert text_leaked["passed"] is False
    assert text_leaked["tracked_private_conversation_text_matches"] == 1
    tracked_leak.unlink()

    phase1._write_private(
        private_output / "synthetic-leak.txt", raw_identifier.encode("utf-8")
    )
    leaked = phase1._privacy_validation(project_dir, private_output, manifest)
    assert leaked["passed"] is False
    assert leaked["raw_contributor_id_matches"] >= 1


def test_cli_has_only_explicit_required_source_and_output_paths() -> None:
    parser = phase1.build_parser()
    actions = {action.dest: action for action in parser._actions}
    required = {
        "project_dir",
        "research_root",
        "prospective_root",
        "benchmark_run",
        "multi_turn_audit_run",
        "private_output",
        "cutoff",
        "source_manifest",
    }
    assert required <= actions.keys()
    for name in required:
        assert actions[name].required is True
        assert actions[name].default is None
    assert actions["verify_only"].default is False

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([])
    assert exc_info.value.code == 2


def test_argument_guard_refuses_production_project_directory(tmp_path: Path) -> None:
    args = argparse.Namespace(
        project_dir=Path("/disks/disk1/etc/mrsMThatcher"),
        private_output=tmp_path / "private",
    )
    with pytest.raises(phase1.Phase1Error, match="never production"):
        phase1._validate_arguments(args, {})


def test_synthetic_fixture_check_is_complete_and_deterministic() -> None:
    first = phase1.validate_synthetic_fixtures(PROJECT_DIR)
    second = phase1.validate_synthetic_fixtures(PROJECT_DIR)
    assert phase1.canonical_json_bytes(first) == phase1.canonical_json_bytes(second)
    assert first["fixture_count"] == 12
    assert first["valid_fixture_count"] == 12
    assert first["invalid_example_count"] == 12
    assert first["all_invalid_examples_detected"] is True
    assert [item["fixture_id"] for item in first["results"]] == [
        f"{index:02d}-{suffix}"
        for index, suffix in enumerate(
            (
                "direct-question-answer",
                "explicit-correction",
                "existence-versus-security",
                "desirability-versus-feasibility",
                "compound-allegation",
                "live-counterfactual",
                "quoted-without-endorsement",
                "rhetorical-no-stable-issue",
                "clarification-then-answer",
                "unrelated-topic-change",
                "partial-concession-rebuttal",
                "ambiguous-pronoun-abstention",
            ),
            start=1,
        )
    ]


def test_turn_ref_speaker_must_name_a_participant(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["turn_refs"][0]["speaker_id"] = "participant-does-not-exist"

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_turn_speaker")


def test_derivation_sources_must_name_existing_propositions(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["propositions"][0]["derivation"]["source_proposition_ids"] = [
        "p-does-not-exist"
    ]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_derivation_source")


def test_live_alternative_propositions_must_exist(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["issue_states"][0]["live_alternatives"][0]["proposition_ids"] = [
        "p-does-not-exist"
    ]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_live_alternative_proposition")


@pytest.mark.parametrize("field", ["initiating_speaker", "addressed_participant"])
def test_issue_participants_must_exist(
    ledger_schema: dict[str, Any], field: str
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["issue_states"][0][field] = "participant-does-not-exist"

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_issue_participant")


def test_rejected_target_related_propositions_must_exist(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("02-explicit-correction")
    ledger["rejected_answer_targets"][0]["related_proposition_ids"][0] = (
        "p-does-not-exist"
    )

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_rejected_reference")


@pytest.mark.parametrize(
    ("field", "missing_identifier"),
    [
        ("rejected_answer_target_ids", "rat-does-not-exist"),
        ("replacement_answer_target_ids", "at-does-not-exist"),
    ],
)
def test_repair_target_references_must_exist(
    ledger_schema: dict[str, Any], field: str, missing_identifier: str
) -> None:
    transcript, ledger = _fixture("02-explicit-correction")
    ledger["repair_records"][0][field] = [missing_identifier]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_repair_reference")


@pytest.mark.parametrize(
    ("field", "missing_value"),
    [
        ("propositions_added", ["p-does-not-exist"]),
        (
            "propositions_updated",
            [
                {
                    "item_id": "p-does-not-exist",
                    "from_status": "live",
                    "to_status": "challenged",
                    "reason": "Synthetic missing reference.",
                }
            ],
        ),
        ("issue_states_added", ["i-does-not-exist"]),
        (
            "issue_states_updated",
            [
                {
                    "item_id": "i-does-not-exist",
                    "from_status": "open",
                    "to_status": "challenged",
                    "reason": "Synthetic missing reference.",
                }
            ],
        ),
        ("commitments_added", ["c-does-not-exist"]),
        (
            "commitments_updated",
            [
                {
                    "item_id": "c-does-not-exist",
                    "from_status": "questioned",
                    "to_status": "asserted",
                    "reason": "Synthetic missing reference.",
                }
            ],
        ),
        ("obligations_added", ["o-does-not-exist"]),
        (
            "obligations_updated",
            [
                {
                    "item_id": "o-does-not-exist",
                    "from_status": "open",
                    "to_status": "satisfied",
                    "reason": "Synthetic missing reference.",
                }
            ],
        ),
        ("relations_added", ["r-does-not-exist"]),
        ("items_resolved", ["item-does-not-exist"]),
        ("warnings_added", ["warning-does-not-exist"]),
    ],
)
def test_transition_delta_references_must_exist(
    ledger_schema: dict[str, Any], field: str, missing_value: list[Any]
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["state_transitions"][0][field] = missing_value

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "orphan_transition_reference")


def test_target_turn_index_must_equal_as_of_index(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["target_turn_id"] = "t0"
    ledger["state_transitions"][0]["current_turn_id"] = "t0"
    ledger["state_transitions"][0]["current_turn_index"] = 1

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "target_as_of_mismatch")


def test_complete_prefix_requires_every_transcript_turn_through_target(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["turn_refs"] = [ledger["turn_refs"][1]]
    ledger["turn_refs"][0]["parent_turn_id"] = None

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "incomplete_turn_prefix")


@pytest.mark.parametrize("transition_count", [0, 2])
def test_snapshot_has_exactly_one_current_turn_transition(
    ledger_schema: dict[str, Any], transition_count: int
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    original = ledger["state_transitions"][0]
    ledger["state_transitions"] = []
    if transition_count:
        ledger["state_transitions"].append(original)
    if transition_count == 2:
        duplicate = copy.deepcopy(original)
        duplicate["transition_id"] = "transition-second"
        ledger["state_transitions"].append(duplicate)

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "state_transition_count") or any(
        error.startswith("schema:$.state_transitions:") for error in errors
    )


def test_state_patch_reconstructs_every_fixture_snapshot(
    ledger_schema: dict[str, Any],
) -> None:
    for fixture_dir in sorted(path for path in FIXTURES_DIR.iterdir() if path.is_dir()):
        transcript = _load_json(fixture_dir / "transcript.json")
        ledger = _load_json(fixture_dir / "expected-ledger.json")
        history = transcript.get("ledger_history")
        assert isinstance(history, list), fixture_dir.name
        assert [item["as_of_turn_index"] for item in history] == list(
            range(ledger["as_of_turn_index"])
        ), fixture_dir.name
        previous = history[-1]
        assert isinstance(previous, dict), fixture_dir.name
        transition = ledger["state_transitions"][0]
        expected_patch = phase1.build_state_patch(previous, ledger)
        assert transition["state_patch"] == expected_patch, fixture_dir.name
        reconstructed, errors = phase1.apply_state_patch(
            previous, transition["state_patch"]
        )
        assert errors == [], fixture_dir.name
        assert reconstructed == phase1.ledger_state_projection(ledger), fixture_dir.name
        assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []


def test_omitted_typed_delta_record_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    transition = ledger["state_transitions"][0]
    assert transition["propositions_added"]
    transition["propositions_added"] = transition["propositions_added"][1:]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "transition_delta_mismatch")


def test_fabricated_predecessor_hash_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    previous = transcript["ledger_history"][-1]
    previous["conversation_key"] += "-fabricated"
    previous["root_post_id"] = "synthetic-fabricated-root"
    previous["ledger_sha256"] = phase1.ledger_sha256(previous)
    ledger["previous_ledger_sha256"] = previous["ledger_sha256"]
    ledger["state_transitions"][0]["from_ledger_sha256"] = previous[
        "ledger_sha256"
    ]
    ledger["state_transitions"][0]["state_patch"] = phase1.build_state_patch(
        previous, ledger
    )
    _rehash(ledger)

    errors = phase1.validate_ledger(ledger, transcript, ledger_schema)

    assert _has_error(errors, "previous_ledger_envelope_mismatch")


def test_skipped_predecessor_turn_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("02-explicit-correction")
    assert len(transcript["ledger_history"]) == 2
    skipped_to = transcript["ledger_history"][0]
    transcript["ledger_history"] = [skipped_to]
    ledger["previous_ledger_sha256"] = skipped_to["ledger_sha256"]
    transition = ledger["state_transitions"][0]
    transition["from_ledger_sha256"] = skipped_to["ledger_sha256"]
    transition["state_patch"] = phase1.build_state_patch(skipped_to, ledger)
    _rehash(ledger)

    errors = phase1.validate_ledger(ledger, transcript, ledger_schema)

    assert _has_error(errors, "ledger_history_length_mismatch")
    assert _has_error(errors, "previous_ledger_boundary_mismatch")


def test_nonzero_genesis_snapshot_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("02-explicit-correction")
    nonzero_genesis = copy.deepcopy(transcript["ledger_history"][1])
    nonzero_genesis["previous_ledger_sha256"] = None
    transition = nonzero_genesis["state_transitions"][0]
    transition["from_ledger_sha256"] = None
    transition["state_patch"] = phase1.build_state_patch(None, nonzero_genesis)
    _rehash(nonzero_genesis)
    standalone_transcript = copy.deepcopy(transcript)
    standalone_transcript["ledger_history"] = []

    errors = phase1.validate_ledger(
        nonzero_genesis, standalone_transcript, ledger_schema
    )

    assert _has_error(errors, "nonzero_genesis_boundary")


def test_duplicate_logical_transition_update_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("11-partial-concession-rebuttal")
    updates = ledger["state_transitions"][0]["propositions_updated"]
    assert len(updates) == 1
    duplicate = copy.deepcopy(updates[0])
    duplicate["reason"] = "A different explanation must not create a second logical update."
    updates.append(duplicate)

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "duplicate_transition_update")


def test_duplicate_composite_state_key_is_rejected(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("11-partial-concession-rebuttal")
    duplicate = copy.deepcopy(ledger["unresolved_items"][0])
    duplicate["reason"] = "A different reason does not make a distinct state item."
    ledger["unresolved_items"].append(duplicate)

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "duplicate_state_key")


def test_resolved_proposition_requires_a_resolved_item(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    ledger["resolved_items"] = [
        item
        for item in ledger["resolved_items"]
        if not (
            item["item_type"] == "proposition"
            and item["item_id"] == "p-bridge-question"
        )
    ]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "resolved_state_missing_item")


def test_resolved_item_requires_coherent_proposition_lifecycle(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("01-direct-question-answer")
    proposition = next(
        item
        for item in ledger["propositions"]
        if item["proposition_id"] == "p-bridge-question"
    )
    proposition["lifecycle_status"] = "live"

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "resolved_item_state_mismatch")


@pytest.mark.parametrize("mutation", ["proposition_side", "group_side"])
def test_compound_group_membership_is_reciprocal(
    ledger_schema: dict[str, Any], mutation: str
) -> None:
    transcript, ledger = _fixture("05-compound-allegation")
    proposition_id = ledger["proposition_groups"][0]["members"][0][
        "proposition_id"
    ]
    proposition = next(
        item
        for item in ledger["propositions"]
        if item["proposition_id"] == proposition_id
    )
    if mutation == "proposition_side":
        proposition["proposition_group_id"] = None
    else:
        ledger["proposition_groups"][0]["members"] = ledger[
            "proposition_groups"
        ][0]["members"][1:]

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "nonreciprocal_group_membership")


def test_compound_group_membership_is_unique(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, ledger = _fixture("05-compound-allegation")
    duplicate = copy.deepcopy(ledger["proposition_groups"][0]["members"][0])
    duplicate["ordinal"] = len(ledger["proposition_groups"][0]["members"])
    ledger["proposition_groups"][0]["members"].append(duplicate)

    errors = phase1.validate_ledger(_rehash(ledger), transcript, ledger_schema)

    assert _has_error(errors, "duplicate_group_membership")


def _synthetic_prospective_row() -> dict[str, Any]:
    return {
        "conversation_key": "synthetic:grade-b",
        "root_post_id": "post-0",
        "conversation_id": "conversation-0",
        "start_time": "2026-08-30T10:00:00Z",
        "last_activity_time": "2026-08-30T10:01:00Z",
        "lane_sequence": ["reply"],
        "author_key": "synthetic-author",
        "source_provenance": ["synthetic-test"],
        "completeness": "complete",
        "reconstruction_confidence": "medium",
        "prospective_status": "prospective",
        "turns": [
            {
                "turn_id": "turn-0",
                "post_id": "post-0",
                "parent_post_id": None,
                "author_role": "user",
                "public_text": "Is the footpath open?",
                "timestamp": "2026-08-30T10:00:00Z",
            },
            {
                "turn_id": "turn-1",
                "post_id": "post-1",
                "parent_post_id": "post-0",
                "author_role": "account",
                "public_text": "Yes, it is open.",
                "timestamp": "2026-08-30T10:01:00Z",
                "publication_status": "published",
            },
        ],
    }


def test_limited_identity_gap_is_grade_b() -> None:
    record, _ = phase1._conversation_record_from_prospective(
        _synthetic_prospective_row()
    )

    assert record["reconstruction_grade"] == "B"
    assert record["exact_text_complete"] is True
    assert record["chronology_complete"] is True
    assert record["account_publication_confirmed"] is True


def test_material_defect_dominates_grade_b_eligibility() -> None:
    row = _synthetic_prospective_row()
    row["turns"][0]["public_text"] = ""

    record, _ = phase1._conversation_record_from_prospective(row)

    assert record["reconstruction_grade"] == "C"
    assert "missing_substantive_text" in record["exclusion_reasons"]


@pytest.mark.parametrize(
    ("activity_status", "expected"),
    [("quiescent", "turn-terminal"), ("open", None)],
)
def test_terminal_no_reply_target_must_be_quiescent(
    activity_status: str,
    expected: str | None,
) -> None:
    turn = {
        "turn_id": "turn-terminal",
        "post_id": "post-terminal",
        "parent_post_id": None,
        "author_role": "user",
        "public_text": "Is the synthetic footpath still open?",
        "timestamp": "2026-08-30T10:00:00Z",
    }
    candidate = {
        "activity_status": activity_status,
        "prospective_status": "eligible",
        "branch_tip_post_id": "post-terminal",
        "source_branch_tip_post_id": "post-terminal",
        "path_turns": [turn],
    }
    record = {
        "reconstruction_grade": "A",
        "published_reply_target_turn_ids": [],
    }

    assert phase1._quiescent_terminal_no_reply_target(candidate, record, [turn]) == expected
