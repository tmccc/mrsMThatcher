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
EXPERIMENT_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-experiment-v1.schema.json"
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


@pytest.fixture(scope="module")
def experiment_schema() -> dict[str, Any]:
    return _load_json(EXPERIMENT_SCHEMA_PATH)


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


def test_two_part_compound_accusation_without_motive_is_valid(
    ledger_schema: dict[str, Any],
) -> None:
    transcript, _ = _fixture("05-compound-allegation")
    transcript = copy.deepcopy(transcript)
    ledger = copy.deepcopy(transcript["ledger_history"][0])
    no_motive_text = "The curator hid the notice, which delayed the vote"
    retained_proposition_ids = {
        "p-curator-hid-notice",
        "p-hiding-caused-delay",
    }

    transcript["turns"] = [transcript["turns"][0]]
    transcript["turns"][0]["text"] = no_motive_text
    transcript["ledger_history"] = []
    ledger["propositions"] = [
        proposition
        for proposition in ledger["propositions"]
        if proposition["proposition_id"] in retained_proposition_ids
    ]
    ledger["participant_commitments"] = [
        commitment
        for commitment in ledger["participant_commitments"]
        if commitment["proposition_id"] in retained_proposition_ids
    ]
    group = ledger["proposition_groups"][0]
    group["members"] = [
        member
        for member in group["members"]
        if member["proposition_id"] in retained_proposition_ids
    ]
    for ordinal, member in enumerate(group["members"]):
        member["ordinal"] = ordinal
    group["exact_evidence_spans"] = [
        {
            "turn_id": "t0",
            "start_char": 0,
            "end_char": len(no_motive_text),
            "exact_text": no_motive_text,
        }
    ]
    ledger["turn_refs"][0]["text_sha256"] = phase1.sha256_bytes(
        no_motive_text.encode("utf-8")
    )
    transition = ledger["state_transitions"][0]
    transition["propositions_added"] = sorted(retained_proposition_ids)
    transition["commitments_added"] = sorted(
        commitment["commitment_id"]
        for commitment in ledger["participant_commitments"]
    )
    transition["state_patch"] = phase1.build_state_patch(None, ledger)
    ledger["ledger_sha256"] = phase1.ledger_sha256(ledger)

    assert {member["role"] for member in group["members"]} == {
        "conduct",
        "cause",
    }
    assert all(
        proposition["proposition_id"] != "p-curator-wanted-rival-win"
        for proposition in ledger["propositions"]
    )
    assert phase1.validate_ledger(ledger, transcript, ledger_schema) == []


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


def _complete_grade_arguments() -> dict[str, Any]:
    return {
        "exact_text_complete": True,
        "immutable_post_identity_complete": True,
        "role_assignment_complete": True,
        "chronology_complete": True,
        "turn_order_unambiguous": True,
        "account_publication_confirmed": True,
        "root_identity_complete": True,
        "parent_graph_unambiguous": True,
        "complete_prefix_through_targets": True,
        "source_complete": True,
        "reconstruction_confidence": "high",
        "hard_exclusion_reasons": [],
    }


def test_grade_a_directly_requires_unambiguous_turn_order() -> None:
    complete = _complete_grade_arguments()
    assert phase1._assign_reconstruction_grade(**complete) == ("A", [])

    ambiguous = {**complete, "turn_order_unambiguous": False}
    grade, limitations = phase1._assign_reconstruction_grade(**ambiguous)

    assert grade != "A"
    assert limitations == ["one_limited_turn_order_gap"]


def test_exactly_one_limited_turn_order_gap_is_grade_b() -> None:
    arguments = {
        **_complete_grade_arguments(),
        "turn_order_unambiguous": False,
    }

    assert phase1._assign_reconstruction_grade(**arguments) == (
        "B",
        ["one_limited_turn_order_gap"],
    )


def test_material_turn_order_ambiguity_is_grade_c() -> None:
    arguments = {
        **_complete_grade_arguments(),
        "chronology_complete": False,
        "turn_order_unambiguous": False,
    }

    assert phase1._assign_reconstruction_grade(**arguments) == ("C", [])


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


def _pending_provider_schema_compatibility() -> dict[str, Any]:
    from tools import proposition_ledger_provider_schema as provider_schema

    inventory = provider_schema.build_schema_feature_inventory(
        PROJECT_DIR
        / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
    )
    return provider_schema.build_provider_schema_compatibility(inventory)


def test_protocol_v1_2_separates_provider_materialiser_and_persisted_contracts(
    experiment_schema: dict[str, Any],
) -> None:
    protocol = phase1._build_protocol(
        "a" * 64, "b" * 64, _pending_provider_schema_compatibility()
    )

    assert protocol["schema_version"] == "proposition-ledger-experiment-v1.2.0"
    assert phase1._jsonschema_errors(protocol, experiment_schema) == []
    provider = protocol["provider_response_schema"]
    materialiser = protocol["deterministic_materialiser"]
    persisted = protocol["persisted_ledger_schema"]
    assert provider["schema_version"] == (
        "proposition-ledger-semantic-delta-v1.1.0"
    )
    assert provider["contract_role"] == "current_turn_semantic_analysis_only"
    assert provider["provider_emits_cumulative_state"] is False
    assert provider["provider_emits_persistence_hashes"] is False
    assert provider["provider_emits_state_patch"] is False
    assert provider["provider_assigns_permanent_ids"] is False
    assert materialiser["contract_role"] == (
        "semantic_delta_to_authoritative_persisted_ledger"
    )
    assert materialiser["assigns_permanent_ids"] is True
    assert materialiser["constructs_state_patch"] is True
    assert materialiser["calculates_predecessor_and_ledger_hashes"] is True
    assert materialiser["materialiser_id"] == (
        "proposition-ledger-semantic-delta-materialiser-v2"
    )
    assert persisted["contract_role"] == "authoritative_cumulative_persisted_state"
    assert persisted["schema_version"] == "proposition-ledger-v1.0.0"
    assert persisted["contains_cumulative_state"] is True
    assert persisted["contains_state_patch"] is True
    assert persisted["contains_persistence_hashes"] is True
    compatibility = protocol["provider_schema_compatibility"]
    assert compatibility["status"] == "pending_model_profile_selection"
    assert compatibility["selected_provider"] is None
    assert compatibility["selected_model"] is None
    assert compatibility["provider_specific_validator_run"] is False
    assert compatibility["network_call_made"] is False
    assert len(
        {
            provider["contract_role"],
            materialiser["contract_role"],
            persisted["contract_role"],
        }
    ) == 3

    invalid = copy.deepcopy(protocol)
    invalid["provider_response_schema"]["provider_emits_state_patch"] = True
    assert phase1._jsonschema_errors(invalid, experiment_schema)


def test_transcript_first_gold_requires_exactly_two_independent_blinded_raters(
    experiment_schema: dict[str, Any],
) -> None:
    protocol = phase1._build_protocol(
        "a" * 64, "b" * 64, _pending_provider_schema_compatibility()
    )
    adjudication = protocol["adjudication"]

    assert adjudication["construction_input"] == "exact_transcript_prefix"
    assert adjudication["independent_raters"] == 2
    assert adjudication["rater_annotations_independently_authored"] is True
    assert set(adjudication["rater_hidden_information"]) == {
        "machine_ledger",
        "other_rater_annotation",
        "production_pipeline_decision",
        "historical_account_reply",
        "arm_identity",
        "provider_or_model_identity",
    }
    assert set(adjudication["annotation_dimensions"]) == {
        "propositions",
        "compound_structure",
        "participant_commitments",
        "issue_state",
        "obligations",
        "proposition_relations",
        "answer_targets",
        "rejected_answer_targets",
        "uncertainty_and_abstentions",
    }

    wrong_rater_count = copy.deepcopy(protocol)
    wrong_rater_count["adjudication"]["independent_raters"] = 3
    assert phase1._jsonschema_errors(wrong_rater_count, experiment_schema)


def test_blinded_adjudication_and_gold_lock_precede_machine_reveal(
    experiment_schema: dict[str, Any],
) -> None:
    protocol = phase1._build_protocol(
        "a" * 64, "b" * 64, _pending_provider_schema_compatibility()
    )
    adjudication = protocol["adjudication"]

    assert adjudication["disagreement_resolution"] == (
        "blinded_adjudicator_resolves_from_transcript_and_two_independent_annotations"
    )
    assert adjudication["adjudicator_input"] == (
        "exact_transcript_prefix_and_two_independent_annotations"
    )
    assert set(adjudication["adjudicator_hidden_information"]) == {
        "machine_ledger",
        "production_pipeline_decision",
        "historical_account_reply",
        "arm_identity",
        "provider_or_model_identity",
    }
    assert adjudication["adjudication_before_machine_comparison"] is True
    assert adjudication["gold_lock"] == {
        "artifact": "transcript_first_independently_adjudicated_proposition_ledger",
        "hash_algorithm": "sha256",
        "locked_before_machine_ledger_reveal": True,
        "locked_before_machine_scoring": True,
    }
    assert adjudication["machine_reveal"]["permitted_only_after_gold_lock"] is True

    premature_reveal = copy.deepcopy(protocol)
    premature_reveal["adjudication"]["gold_lock"][
        "locked_before_machine_ledger_reveal"
    ] = False
    assert phase1._jsonschema_errors(premature_reveal, experiment_schema)


def test_arm_d_uses_locked_transcript_first_gold_in_exactly_four_arm_design(
    experiment_schema: dict[str, Any],
) -> None:
    protocol = phase1._build_protocol(
        "a" * 64, "b" * 64, _pending_provider_schema_compatibility()
    )
    arms = protocol["arms"]

    assert len(arms) == 4
    assert [arm["arm_id"] for arm in arms] == ["A", "B", "C", "D"]
    arm_d = arms[-1]
    assert arm_d["label"] == (
        "transcript plus transcript-first independently adjudicated proposition ledger"
    )
    assert arm_d["additional_representation"] == (
        "transcript_first_independently_adjudicated_proposition_ledger"
    )
    assert arm_d["gold_representation_source"] == (
        "locked_transcript_first_independently_adjudicated_proposition_ledger"
    )
    assert protocol["adjudication"]["machine_reveal"][
        "locked_gold_supplies_arm_d_representation"
    ] is True

    fifth_arm = copy.deepcopy(protocol)
    fifth_arm["arms"].append(copy.deepcopy(arm_d))
    assert phase1._jsonschema_errors(fifth_arm, experiment_schema)


def _outcome_case(
    *,
    events: list[dict[str, Any]] | None = None,
    published_reply: bool = False,
    activity_status: str = "quiescent",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    turn = {
        "turn_id": "turn-target",
        "post_id": "post-target",
        "parent_post_id": None,
        "author_role": "user",
        "public_text": "Is the synthetic footpath still open?",
        "timestamp": "2026-08-30T10:00:00Z",
        "pipeline_stage_summaries": events or [],
    }
    turns = [turn]
    if published_reply:
        turns.append(
            {
                "turn_id": "turn-reply",
                "post_id": "post-reply",
                "parent_post_id": "post-target",
                "author_role": "account",
                "public_text": "The synthetic path is open.",
                "timestamp": "2026-08-30T10:01:00Z",
                "publication_status": "published",
            }
        )
    candidate = {
        "activity_status": activity_status,
        "prospective_status": "eligible",
        "branch_tip_post_id": "post-target",
        "source_branch_tip_post_id": "post-target",
        "path_turns": [turn],
    }
    record = {
        "reconstruction_grade": "A",
        "published_reply_target_turn_ids": ["turn-target"] if published_reply else [],
        "source_ids": ["prospective_conversations"],
    }
    return turn, record, turns, candidate


def _pipeline_no_reply_event() -> dict[str, Any]:
    return {
        "event_id": "event-no-reply",
        "event_kind": "ai_reply_pipeline_decision",
        "status": "no_reply",
        "reviewer_verdict": "pipeline_no_reply",
        "effective_reason": "reply_necessity_review",
        "strategy_version": "synthetic-strategy-v1",
        "observed_at": "2026-08-30T10:00:30Z",
    }


def _production_shaped_local_skip_event() -> dict[str, Any]:
    return {
        "event_id": "event-local-rejection",
        "event_kind": "ai_reply_pipeline_decision",
        "status": "approved",
        "pipeline_stage_status": "approved",
        "effective_status": "local_rejection",
        "effective_reason": "direct_answer_repair_rejected",
        "original_local_rejection_reason": (
            "clarification_not_direct_factual_answer"
        ),
        "strategy_version": "synthetic-strategy-v1",
        "observed_at": "2026-08-30T10:00:31Z",
    }


def test_confirmed_published_reply_is_its_only_outcome_class() -> None:
    turn, record, turns, candidate = _outcome_case(published_reply=True)
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == "confirmed_published_reply"
    assert outcome["outcome_evidence_status"] == "confirmed"


def test_exact_structured_pipeline_terminal_no_reply_is_confirmed() -> None:
    turn, record, turns, candidate = _outcome_case(events=[_pipeline_no_reply_event()])
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == "confirmed_pipeline_terminal_no_reply"
    assert outcome["outcome_strategy_version"] == "synthetic-strategy-v1"
    assert outcome["outcome_reason"] == "reply_necessity_review"


def test_exact_structured_local_skip_is_confirmed() -> None:
    local = {
        "event_id": "event-local-skip",
        "event_kind": "ai_reply_pipeline_stage_summary",
        "status": "no_reply",
        "effective_status": "no_reply",
        "deterministic_suppressed": True,
        "effective_reason": "synthetic_local_skip",
        "strategy_version": "synthetic-strategy-v1",
    }
    turn, record, turns, candidate = _outcome_case(events=[local])
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == "confirmed_local_skip"
    assert outcome["outcome_reason"] == "synthetic_local_skip"


def test_production_shaped_effective_local_rejection_is_confirmed() -> None:
    turn, record, turns, candidate = _outcome_case(
        events=[_production_shaped_local_skip_event()]
    )

    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)

    assert outcome["outcome_evidence_class"] == "confirmed_local_skip"
    assert outcome["outcome_reason"] == "clarification_not_direct_factual_answer"
    assert outcome["outcome_evidence_record_refs"] == ["event-local-rejection"]


def test_repaired_approval_is_not_misclassified_from_original_local_reason() -> None:
    repaired = {
        **_production_shaped_local_skip_event(),
        "event_id": "event-repaired-approval",
        "effective_status": "approved_for_publication",
        "effective_reason": "direct_answer_repair_passed",
    }
    turn, record, turns, candidate = _outcome_case(
        events=[repaired],
        published_reply=True,
    )

    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)

    assert outcome["outcome_evidence_class"] == "confirmed_published_reply"


def test_distinct_local_skip_and_pipeline_no_reply_evidence_conflict() -> None:
    turn, record, turns, candidate = _outcome_case(
        events=[
            _pipeline_no_reply_event(),
            _production_shaped_local_skip_event(),
        ]
    )

    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)

    assert outcome["outcome_evidence_class"] == "conflicting_outcome_evidence"
    assert outcome["outcome_evidence_status"] == (
        "conflicting_structured_no_reply_evidence"
    )
    assert outcome["outcome_conflict_details"] == {
        "local_skip_record_refs": ["event-local-rejection"],
        "pipeline_terminal_no_reply_record_refs": ["event-no-reply"],
    }


def test_quiescent_unreplied_tip_without_decision_evidence_is_unknown() -> None:
    turn, record, turns, candidate = _outcome_case()
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == (
        "quiescent_unreplied_tip_outcome_unknown"
    )
    assert outcome["outcome_evidence_record_refs"] == []


def test_silence_alone_never_becomes_confirmed_no_reply() -> None:
    turn, record, turns, candidate = _outcome_case(activity_status="open")
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == "outcome_evidence_unavailable"
    assert "confirmed" not in outcome["outcome_evidence_class"]


def test_published_reply_and_no_reply_evidence_conflict() -> None:
    turn, record, turns, candidate = _outcome_case(
        events=[_pipeline_no_reply_event()], published_reply=True
    )
    outcome = phase1._classify_target_outcome(turn, record, turns, candidate)
    assert outcome["outcome_evidence_class"] == "conflicting_outcome_evidence"
    assert outcome["outcome_conflict_details"]["published_reply_record_refs"]
    assert outcome["outcome_conflict_details"]["no_reply_record_refs"]


def test_each_synthetic_target_contributes_to_exactly_one_outcome_count() -> None:
    cases = [
        _outcome_case(published_reply=True),
        _outcome_case(events=[_pipeline_no_reply_event()]),
        _outcome_case(),
        _outcome_case(activity_status="open"),
    ]
    outcomes = [
        phase1._classify_target_outcome(turn, record, turns, candidate)[
            "outcome_evidence_class"
        ]
        for turn, record, turns, candidate in cases
    ]
    counts = {name: outcomes.count(name) for name in phase1.OUTCOME_EVIDENCE_CLASSES}
    assert sum(counts.values()) == len(cases)


def _target_index_inputs(
    tmp_path: Path,
    *,
    activity_status: str = "quiescent",
    exposure_categories: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    turns = [
        {
            "turn_id": "t0",
            "post_id": "p0",
            "parent_post_id": None,
            "author_role": "user",
            "author_key": "author-one",
            "public_text": "Synthetic first question?",
            "timestamp": "2026-08-30T10:00:00Z",
        },
        {
            "turn_id": "t1",
            "post_id": "p1",
            "parent_post_id": "p0",
            "author_role": "account",
            "author_key": "account",
            "public_text": "Synthetic first answer.",
            "timestamp": "2026-08-30T10:01:00Z",
            "publication_status": "published",
        },
        {
            "turn_id": "t2",
            "post_id": "p2",
            "parent_post_id": "p1",
            "author_role": "user",
            "author_key": "author-one",
            "public_text": "Synthetic follow-up question?",
            "timestamp": "2026-08-30T10:02:00Z",
            "pipeline_stage_summaries": [_pipeline_no_reply_event()],
        },
    ]
    candidate_path = tmp_path / "review-candidates.jsonl"
    candidate_path.write_text(
        json.dumps(
            {
                "conversation_key": "synthetic:conversation",
                "activity_status": activity_status,
                "prospective_status": "eligible",
                "branch_tip_post_id": "p2",
                "source_branch_tip_post_id": "p2",
                "path_turns": turns,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "sources": [
            {
                "source_id": "prospective_review_candidates",
                "path": str(candidate_path),
            }
        ]
    }
    record = {
        "conversation_key": "synthetic:conversation",
        "root_post_id": "p0",
        "source_ids": ["prospective_conversations"],
        "reconstruction_grade": "A",
        "principal_author_key": "author-one",
        "author_key_scheme": "synthetic_prospective_domain",
        "prior_exposure_categories": exposure_categories or ["unexposed_candidate"],
        "activity_status": activity_status,
        "expected_target_turn_ids": ["t0", "t2"],
        "published_reply_target_turn_ids": ["t0"],
        "confirmed_pipeline_terminal_no_reply_target_turn_ids": ["t2"],
        "confirmed_local_skip_target_turn_ids": [],
        "quiescent_unreplied_tip_outcome_unknown_target_turn_ids": [],
        "outcome_evidence_unavailable_target_turn_ids": [],
        "conflicting_outcome_evidence_target_turn_ids": [],
    }
    return manifest, record, turns


def _target_index_case(
    tmp_path: Path,
    *,
    activity_status: str = "quiescent",
    exposure_categories: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest, record, turns = _target_index_inputs(
        tmp_path,
        activity_status=activity_status,
        exposure_categories=exposure_categories,
    )
    rows, structural_exclusions = phase1._build_target_prefix_rows(
        manifest,
        [record],
        [],
        {"synthetic:conversation": turns},
    )
    assert structural_exclusions == []
    return rows, phase1._build_target_prefix_crosstab(rows)


def test_target_prefix_distinguishes_first_response_and_multi_turn(tmp_path: Path) -> None:
    rows, _ = _target_index_case(tmp_path)
    by_turn = {row["target_turn_id"]: row for row in rows}
    assert by_turn["t0"]["target_sequence_class"] == "initial_user_target"
    assert by_turn["t0"]["first_response_control"] is True
    assert by_turn["t0"]["multi_turn_evaluation_candidate"] is False
    assert by_turn["t2"]["target_sequence_class"] == (
        "persistent_multiturn_target"
    )
    assert by_turn["t2"]["first_response_control"] is False
    assert by_turn["t2"]["multi_turn_evaluation_candidate"] is True
    assert by_turn["t2"]["persistent_multiturn_evaluation_candidate"] is True
    assert by_turn["t2"]["preceding_account_reply_count"] == 1
    assert by_turn["t2"]["preceding_account_root_count"] == 0


def _sequence_turn(
    post_id: str,
    role: str,
    parent_post_id: str | None,
    publication_status: str | None = None,
) -> dict[str, Any]:
    return {
        "turn_id": f"turn-{post_id}",
        "post_id": post_id,
        "parent_post_id": parent_post_id,
        "author_role": role,
        "publication_status": publication_status,
    }


def test_account_root_response_is_a_control_not_persistent() -> None:
    sequence = phase1._target_sequence_classification(
        [
            _sequence_turn("account-root", "account", None, "published"),
            _sequence_turn("user-response", "user", "account-root"),
        ]
    )

    assert sequence["target_sequence_class"] == "account_root_response"
    assert sequence["account_root_response_control"] is True
    assert sequence["persistent_multiturn_evaluation_candidate"] is False
    assert sequence["multi_turn_evaluation_candidate"] is False
    assert sequence["preceding_account_root_count"] == 1
    assert sequence["preceding_account_reply_count"] == 0
    assert sequence["target_follows_prior_account_reply"] is False


def test_initial_and_pre_account_user_targets_are_not_persistent() -> None:
    user_root = _sequence_turn("user-root", "user", None)
    initial = phase1._target_sequence_classification([user_root])
    follow_up = phase1._target_sequence_classification(
        [user_root, _sequence_turn("user-follow-up", "user", "user-root")]
    )

    assert initial["target_sequence_class"] == "initial_user_target"
    assert initial["persistent_multiturn_evaluation_candidate"] is False
    assert follow_up["target_sequence_class"] == "pre_account_user_follow_up"
    assert follow_up["persistent_multiturn_evaluation_candidate"] is False


@pytest.mark.parametrize("publication_status", ["published", "observed"])
def test_only_parent_linked_published_or_observed_account_reply_is_persistent(
    publication_status: str,
) -> None:
    sequence = phase1._target_sequence_classification(
        [
            _sequence_turn("user-root", "user", None),
            _sequence_turn(
                "account-reply", "account", "user-root", publication_status
            ),
            _sequence_turn("user-response", "user", "account-reply"),
        ]
    )

    assert sequence["target_sequence_class"] == "persistent_multiturn_target"
    assert sequence["preceding_account_reply_count"] == 1
    assert sequence["target_follows_prior_account_reply"] is True


def test_unparented_or_unconfirmed_account_turn_is_not_a_persistent_reply() -> None:
    unparented = phase1._target_sequence_classification(
        [
            _sequence_turn("user-root", "user", None),
            _sequence_turn("account-post", "account", None, "published"),
            _sequence_turn("user-response", "user", "account-post"),
        ]
    )
    draft = phase1._target_sequence_classification(
        [
            _sequence_turn("user-root", "user", None),
            _sequence_turn("account-draft", "account", "user-root", "draft"),
            _sequence_turn("user-response", "user", "account-draft"),
        ]
    )

    assert unparented["target_sequence_class"] == "other_sequence"
    assert unparented["preceding_account_reply_count"] == 0
    assert draft["target_sequence_class"] == "other_sequence"
    assert draft["preceding_account_reply_count"] == 0
    assert draft["target_follows_prior_account_reply"] is False


def test_sequence_classes_partition_rows_and_account_roots_are_ineligible(
    tmp_path: Path,
) -> None:
    rows, crosstab = _target_index_case(tmp_path)
    sequence_counts = crosstab["dimensions"]["target_sequence_class"]

    assert set(sequence_counts) == set(phase1.TARGET_SEQUENCE_CLASSES)
    assert sum(sequence_counts.values()) == len(rows)
    assert all(
        sum(row["target_sequence_class"] == value for value in phase1.TARGET_SEQUENCE_CLASSES)
        == 1
        for row in rows
    )
    assert all(
        row["preliminary_within_family_held_out_eligibility"] is False
        for row in rows
        if row["target_sequence_class"] != "persistent_multiturn_target"
    )


def test_open_or_exposed_target_is_preliminarily_ineligible(tmp_path: Path) -> None:
    open_rows, _ = _target_index_case(tmp_path / "open", activity_status="open")
    assert all(not row["preliminary_held_out_eligibility"] for row in open_rows)
    assert all(
        "conversation_not_frozen_or_quiescent"
        in row["preliminary_held_out_exclusion_reasons"]
        for row in open_rows
    )
    exposed_rows, _ = _target_index_case(
        tmp_path / "exposed", exposure_categories=["prior_model_experiment"]
    )
    assert all(not row["preliminary_held_out_eligibility"] for row in exposed_rows)
    assert all(row["effective_exposure_status"] == "exposed" for row in exposed_rows)


def test_quiescent_unexposed_grade_a_multiturn_target_is_eligible(
    tmp_path: Path,
) -> None:
    rows, _ = _target_index_case(tmp_path)
    follow_up = next(row for row in rows if row["target_turn_id"] == "t2")
    assert follow_up["preliminary_held_out_eligibility"] is True
    assert follow_up["preliminary_held_out_exclusion_reasons"] == []


def test_target_crosstab_reproduces_rows_and_outcomes_once(tmp_path: Path) -> None:
    rows, crosstab = _target_index_case(tmp_path)
    assert phase1._target_prefix_crosstab_errors(rows, crosstab) == []
    assert crosstab["target_prefix_count"] == len(rows)
    assert sum(crosstab["dimensions"]["outcome_evidence_class"].values()) == len(
        rows
    )


def test_published_target_cannot_be_omitted_from_declared_universe(
    tmp_path: Path,
) -> None:
    manifest, record, turns = _target_index_inputs(tmp_path)
    record["expected_target_turn_ids"] = ["t2"]

    with pytest.raises(
        phase1.Phase1Error,
        match="declared target universe differs",
    ):
        phase1._build_target_prefix_rows(
            manifest,
            [record],
            [],
            {"synthetic:conversation": turns},
        )


def test_candidate_target_absent_from_canonical_turns_is_not_silently_omitted(
    tmp_path: Path,
) -> None:
    manifest, record, turns = _target_index_inputs(tmp_path)

    rows, structural_exclusions = phase1._build_target_prefix_rows(
        manifest,
        [record],
        [],
        {"synthetic:conversation": turns[:-1]},
    )

    assert [row["target_turn_id"] for row in rows] == ["t0"]
    assert [row["target_turn_id"] for row in structural_exclusions] == ["t2"]
    assert structural_exclusions[0]["structural_exclusion_reasons"] == [
        "target_absent_from_canonical_conversation"
    ]


def test_account_tip_review_candidate_is_not_a_user_target(tmp_path: Path) -> None:
    manifest, _record, turns = _target_index_inputs(tmp_path)
    candidate_path = Path(manifest["sources"][0]["path"])
    account_tip_candidate = {
        "conversation_key": "synthetic:conversation",
        "activity_status": "quiescent",
        "prospective_status": "eligible",
        "branch_tip_post_id": "p1",
        "source_branch_tip_post_id": "p1",
        "path_turns": turns[:2],
    }
    with candidate_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(account_tip_candidate, sort_keys=True) + "\n")

    candidates = phase1._target_candidate_map(
        manifest,
        {"synthetic:conversation"},
    )

    assert set(candidates) == {("synthetic:conversation", "t2")}


def test_account_tip_review_candidate_is_validated_before_being_excluded(
    tmp_path: Path,
) -> None:
    manifest, _record, turns = _target_index_inputs(tmp_path)
    candidate_path = Path(manifest["sources"][0]["path"])
    account_tip_candidate = {
        "conversation_key": "synthetic:conversation",
        "activity_status": "quiescent",
        "prospective_status": "eligible",
        "branch_tip_post_id": "wrong-tip",
        "source_branch_tip_post_id": "p1",
        "path_turns": turns[:2],
    }
    with candidate_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(account_tip_candidate, sort_keys=True) + "\n")

    with pytest.raises(
        phase1.Phase1Error,
        match="tip identity is inconsistent",
    ):
        phase1._target_candidate_map(manifest, {"synthetic:conversation"})


def test_expected_target_ancestry_failure_is_not_silently_omitted(
    tmp_path: Path,
) -> None:
    manifest, record, turns = _target_index_inputs(tmp_path)
    turns[-1]["parent_post_id"] = "missing-parent"

    rows, structural_exclusions = phase1._build_target_prefix_rows(
        manifest,
        [record],
        [],
        {"synthetic:conversation": turns},
    )

    assert [row["target_turn_id"] for row in rows] == ["t0"]
    assert [row["target_turn_id"] for row in structural_exclusions] == ["t2"]
    assert structural_exclusions[0]["structural_exclusion_reasons"] == [
        "target_ancestry_does_not_reach_declared_root"
    ]


def test_text_incomplete_target_is_a_bounded_structural_exclusion(
    tmp_path: Path,
) -> None:
    manifest, record, turns = _target_index_inputs(tmp_path)
    turns[-1]["public_text"] = None

    rows, structural_exclusions = phase1._build_target_prefix_rows(
        manifest,
        [record],
        [],
        {"synthetic:conversation": turns},
    )

    assert [row["target_turn_id"] for row in rows] == ["t0"]
    assert structural_exclusions[0]["target_turn_id"] == "t2"
    assert structural_exclusions[0]["structural_exclusion_reasons"] == [
        "target_prefix_text_incomplete"
    ]
    assert "outcome_evidence_class" not in structural_exclusions[0]


def test_potential_targets_partition_into_usable_and_structurally_excluded(
    tmp_path: Path,
) -> None:
    manifest, record, turns = _target_index_inputs(tmp_path)
    turns[-1]["parent_post_id"] = "missing-parent"
    rows, structural_exclusions = phase1._build_target_prefix_rows(
        manifest,
        [record],
        [],
        {"synthetic:conversation": turns},
    )
    expected_pairs = phase1._declared_target_pairs([record])
    summary = phase1._target_structural_reconciliation_summary(
        expected_pairs, rows, structural_exclusions
    )

    assert summary == {
        "schema_version": phase1.OUTPUT_SCHEMA_VERSION,
        "potential_source_target_count": 2,
        "structurally_usable_target_prefix_count": 1,
        "structurally_excluded_target_count": 1,
        "structural_exclusion_reason_counts": {
            "target_ancestry_does_not_reach_declared_root": 1
        },
        "partition_complete": True,
        "partition_errors": [],
        "structural_exclusions_outside_target_outcome_and_crosstab_counts": True,
    }
    assert (
        phase1._target_structural_reconciliation_errors(
            expected_pairs, rows, structural_exclusions, summary
        )
        == []
    )
    crosstab = phase1._build_target_prefix_crosstab(rows)
    assert crosstab["target_prefix_count"] == 1
    assert sum(crosstab["dimensions"]["outcome_evidence_class"].values()) == 1

    missing_exclusion_errors = phase1._target_structural_reconciliation_errors(
        expected_pairs,
        rows,
        [],
        phase1._target_structural_reconciliation_summary(expected_pairs, rows, []),
    )
    assert "target_partition_missing_expected_pair" in missing_exclusion_errors


def test_author_groups_are_domain_qualified() -> None:
    rows = [
        {
            "target_key": "one",
            "conversation_key": "one",
            "outcome_evidence_class": "confirmed_published_reply",
            "author_key_scheme": "domain-a",
            "principal_author_key": "same-key",
            "author_group_comparability_status": "comparable_within_source_family_only",
        },
        {
            "target_key": "two",
            "conversation_key": "two",
            "outcome_evidence_class": "confirmed_published_reply",
            "author_key_scheme": "domain-b",
            "principal_author_key": "same-key",
            "author_group_comparability_status": "comparable_within_source_family_only",
        },
    ]
    crosstab = phase1._build_target_prefix_crosstab(rows)
    groups = crosstab["dimensions"]["principal_author_group_where_comparable"]
    assert groups == {"domain-a:same-key": 1, "domain-b:same-key": 1}
    assert crosstab["author_grouping_cross_family_performed"] is False


def _readiness(**overrides: Any) -> dict[str, Any]:
    values = {
        "source_identity_valid": True,
        "deterministic_rebuild_valid": True,
        "privacy_valid": True,
        "schema_valid": True,
        "synthetic_fixtures_valid": True,
        "target_outcomes_valid": True,
        "target_crosstab_valid": True,
        "no_future_turn_leakage": True,
        "semantic_schema_valid": True,
        "materialiser_valid": True,
        "genesis_materialisation_valid": True,
        "first_seen_participant_registration_valid": True,
        "complete_incremental_chain_valid": True,
        "persistent_multiturn_classification_complete": True,
        "within_family_author_group_exposure_enforced": True,
        "cross_family_author_identity_status": "unavailable",
        "provider_schema_feature_inventory_valid": True,
        "provider_schema_compatibility_status": (
            "pending_model_profile_selection"
        ),
        "transcript_first_protocol_valid": True,
        "preliminary_within_family_target_count": 1,
        "preliminary_cross_family_target_count": 0,
    }
    values.update(overrides)
    return phase1._derive_readiness_gates(**values)


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {"source_identity_valid": False},
            "phase1_2_blocked_by_source_identity_or_classification_failure",
        ),
        (
            {"privacy_valid": False},
            "phase1_2_blocked_by_privacy_failure",
        ),
        (
            {"schema_valid": False},
            "phase1_2_blocked_by_schema_or_materialiser_failure",
        ),
        (
            {"materialiser_valid": False},
            "phase1_2_blocked_by_schema_or_materialiser_failure",
        ),
        (
            {"preliminary_within_family_target_count": 0},
            "phase1_2_blocked_no_within_family_eligible_persistent_targets",
        ),
    ],
)
def test_readiness_failures_derive_blocked_dispositions(
    override: dict[str, Any], expected: str
) -> None:
    assert _readiness(**override)["disposition"] == expected


def test_nonzero_structurally_complete_run_reports_sample_threshold_pending() -> None:
    readiness = _readiness()
    assert readiness["disposition"] == (
        "phase1_2_complete_sample_and_provider_preflight_pending"
    )
    assert readiness["sample_size_threshold_status"]["status"] == (
        "pending_development_only_power_analysis"
    )
    assert readiness["provider_schema_compatibility_status"]["status"] == (
        "pending_model_profile_selection"
    )
    assert readiness["cross_family_author_identity_status"]["status"] == (
        "unavailable"
    )
    mutated = copy.deepcopy(readiness)
    mutated["privacy_validation"]["status"] = "failed"
    assert phase1._derive_disposition(mutated) != readiness["disposition"]


def test_readiness_rejects_bare_provider_compatibility_pass() -> None:
    readiness = _readiness(provider_schema_compatibility_status="passed")

    assert readiness["provider_schema_compatibility_status"]["status"] == "failed"
    assert readiness["disposition"] == (
        "phase1_2_blocked_by_schema_or_materialiser_failure"
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {"source_identity_valid": False},
            "phase1_2_blocked_by_source_identity_or_classification_failure",
        ),
        (
            {"privacy_valid": False},
            "phase1_2_blocked_by_privacy_failure",
        ),
        (
            {"schema_valid": False},
            "phase1_2_blocked_by_schema_or_materialiser_failure",
        ),
        (
            {"materialiser_valid": False},
            "phase1_2_blocked_by_schema_or_materialiser_failure",
        ),
        (
            {"preliminary_within_family_target_count": 0},
            "phase1_2_blocked_no_within_family_eligible_persistent_targets",
        ),
    ],
)
def test_failed_readiness_dispositions_are_reportable_blocks(
    override: dict[str, Any], expected: str
) -> None:
    readiness = _readiness(**override)

    assert phase1._emitted_blocking_disposition(readiness) == expected
    assert phase1._emitted_blocking_disposition(_readiness()) is None


def test_privacy_failure_selects_bounded_report_without_derived_details() -> None:
    readiness = _readiness(privacy_valid=False)
    report = phase1._phase1_2_report_for_readiness(
        {"source_count": "PRIVATE-SOURCE-SENTINEL"},
        {"grade_counts": "PRIVATE-GRADE-SENTINEL"},
        {"headline_counts": "PRIVATE-TARGET-SENTINEL"},
        "PRIVATE-PROTOCOL-HASH-SENTINEL",
        {"semantic_delta_schema_sha256": "PRIVATE-SCHEMA-SENTINEL"},
        {"source_sha256": "PRIVATE-MATERIALISER-SENTINEL"},
        {"schema_sha256": "PRIVATE-PROVIDER-SENTINEL"},
        readiness,
        {"passed": False},
    )
    text = "\n".join(report)

    assert readiness["disposition"] in text
    assert "deliberately omitted" in text
    assert "Old-versus-new metric comparison" not in text
    assert "PRIVATE-" not in text


def test_validation_exceptions_become_failed_readiness_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def schema_failure(
        project_dir: Path, protocol: dict[str, Any]
    ) -> dict[str, Any]:
        raise ValueError("synthetic malformed schema")

    def materialiser_failure(project_dir: Path) -> dict[str, Any]:
        raise SyntaxError("synthetic malformed materialiser")

    monkeypatch.setattr(phase1, "_schema_validation", schema_failure)
    monkeypatch.setattr(
        phase1, "_semantic_materialiser_validation", materialiser_failure
    )

    schema = phase1._schema_validation_for_readiness(tmp_path, {})
    materialiser = phase1._semantic_materialiser_validation_for_readiness(tmp_path)

    assert schema["passed"] is False
    assert schema["semantic_delta_schema_validation"]["passed"] is False
    assert schema["synthetic_fixtures"]["all_invalid_examples_detected"] is False
    assert schema["validation_error"].startswith("ValueError:")
    assert materialiser["passed"] is False
    assert materialiser["source_sha256"] == "unavailable"
    assert materialiser["validation_error"].startswith("SyntaxError:")


def test_phase1_2_report_contains_complete_old_versus_new_metric_set() -> None:
    outcomes = {
        "confirmed_published_reply": 101,
        "confirmed_pipeline_terminal_no_reply": 2,
        "confirmed_local_skip": 3,
        "quiescent_unreplied_tip_outcome_unknown": 4,
        "outcome_evidence_unavailable": 5,
        "conflicting_outcome_evidence": 6,
    }
    headline = {
        "grade_a_target_prefix_count": 110,
        "initial_user_target_count": 9,
        "pre_account_user_follow_up_count": 3,
        "account_root_response_count": 20,
        "persistent_multiturn_target_count": 80,
        "other_sequence_count": 9,
        "exposed_persistent_multiturn_target_prefix_count": 30,
        "structurally_mined_only_persistent_multiturn_target_prefix_count": 15,
        "genuinely_unexposed_stable_grade_a_persistent_multiturn_target_prefix_count": 25,
        "within_family_group_clean_persistent_target_prefix_count": 21,
        "cross_family_clean_persistent_target_prefix_count": 0,
        "preliminary_within_family_held_out_eligible_target_prefix_count": 21,
        "preliminary_within_family_held_out_eligible_conversation_count": 16,
        "preliminary_cross_family_clean_held_out_eligible_target_prefix_count": 0,
        "comparable_within_family_author_group_count": 32,
        "cross_family_identity_available_target_count": 0,
        "cross_family_identity_unavailable_target_count": 121,
        "author_group_contains_direct_exposure_count": 8,
        "author_group_requires_groupwise_split_count": 12,
    }
    feasibility = {
        "canonical_union_conversation_count": 155,
        "grade_counts": {"A": 143, "B": 1, "C": 11},
        "author_grouping": {
            "crosstab": {
                "comparable_within_family_author_group_count": 40,
                "headline_counts": {
                    "cross_family_identity_available_conversation_count": 0,
                    "cross_family_identity_unavailable_conversation_count": 155,
                    "within_family_groups_containing_direct_exposure": 8,
                    "within_family_groups_requiring_groupwise_split": 12,
                },
            }
        },
    }
    target_crosstab = {
        "target_prefix_count": 121,
        "headline_counts": headline,
        "dimensions": {
            "outcome_evidence_class": outcomes,
            "target_sequence_class": {
                "initial_user_target": 9,
                "pre_account_user_follow_up": 3,
                "account_root_response": 20,
                "persistent_multiturn_target": 80,
                "other_sequence": 9,
            },
        },
    }
    schema_validation = {
        "passed": True,
        "semantic_delta_schema_sha256": "a" * 64,
        "ledger_schema_sha256": "b" * 64,
        "experiment_schema_sha256": "c" * 64,
    }
    from tools import proposition_ledger_provider_schema as provider_schema

    provider_inventory = provider_schema.build_schema_feature_inventory(
        PROJECT_DIR
        / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
    )
    report = phase1._phase1_2_report(
        {"source_count": 9},
        feasibility,
        target_crosstab,
        "d" * 64,
        schema_validation,
        {
            "passed": True,
            "source_sha256": "e" * 64,
            "genesis_materialisation_valid": True,
            "first_seen_participant_registration_valid": True,
            "complete_incremental_chain_valid": True,
        },
        provider_inventory,
        _readiness(preliminary_within_family_target_count=21),
        {"passed": True},
    )
    text = "\n".join(report)
    comparison = {
        metric: (phase1_1_value, phase1_2_value)
        for metric, phase1_1_value, phase1_2_value in phase1._phase1_2_metric_comparison(
            feasibility, target_crosstab
        )
    }

    assert "## Old-versus-new metric comparison" in text
    assert comparison["Grade A conversation count"] == ("144", "143")
    assert comparison["Grade B conversation count"] == ("0", "1")
    assert comparison["Grade C conversation count"] == ("11", "11")
    assert comparison["Total structurally usable target-prefix count"] == ("219", "121")
    assert comparison["Confirmed published-reply target-prefix count"] == (
        "188",
        "101",
    )
    assert comparison["Confirmed pipeline-terminal no-reply target-prefix count"] == (
        "30",
        "2",
    )
    assert comparison["Account-root response count"] == (
        "not distinguished within 156 overbroad multi-turn labels",
        "20",
    )
    required_not_measured = {
        "Pre-account user follow-up count",
        "Exposed persistent target-prefix count",
        "Structurally mined-only persistent target-prefix count",
        "Genuinely unexposed stable Grade-A persistent target-prefix count",
        "Within-family group-clean persistent target-prefix count",
        "Cross-family-clean persistent target-prefix count",
        "Comparable within-family author-group count",
        "Cross-family identities available (conversations)",
        "Cross-family identities unavailable (conversations)",
        "Author groups containing direct exposure",
        "Author groups requiring groupwise split",
    }
    assert all(
        comparison[metric][0] == "not measured in Phase 1.1"
        for metric in required_not_measured
    )
    assert all(metric in text for metric in comparison)


def test_materialiser_gate_requires_behavioral_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import proposition_ledger_semantic_delta as semantic_delta

    passing = phase1._semantic_materialiser_validation(PROJECT_DIR)
    assert passing["behavioral_validation_passed"] is True
    assert passing["behavioral_validation"]["full_persisted_validator_passed"] is True
    assert passing["passed"] is True

    monkeypatch.setattr(
        semantic_delta,
        "behavioral_materialiser_validation",
        lambda _project_dir: {
            "passed": False,
            "valid_materialisation_status": "persisted_ledger_validation_failure",
        },
    )
    failing = phase1._semantic_materialiser_validation(PROJECT_DIR)
    assert failing["behavioral_validation_passed"] is False
    assert failing["passed"] is False


def test_materialiser_gate_binds_implementation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import proposition_ledger_semantic_delta as semantic_delta

    monkeypatch.setattr(
        semantic_delta,
        "MATERIALISER_VERSION",
        "proposition-ledger-semantic-delta-materialiser-v999",
    )
    validation = phase1._semantic_materialiser_validation(PROJECT_DIR)

    assert validation["materialiser_identity_matches"] is False
    assert validation["passed"] is False


def _write_fresh_determinism_fixture(
    root: Path,
    *,
    generated_at: str,
) -> None:
    phase1._ensure_private_directory(root)
    phase1._write_private(root / "private-author-key", b"k" * 32)
    phase1._write_json(root / "payload.json", {"stable": [1, 2, 3]})
    phase1._write_json(
        root / "run-manifest.json",
        {
            "generated_at": generated_at,
            "private_output": str(root),
            "source_manifest_path": str(root / "frozen-source-manifest.json"),
            "stable": "same",
        },
    )
    phase1._write_private(
        root / "SHA256SUMS",
        (
            f"{phase1.sha256_file(root / 'payload.json')}  payload.json\n"
            f"{phase1.sha256_file(root / 'run-manifest.json')}  run-manifest.json\n"
        ).encode("utf-8"),
    )


def test_fresh_build_comparison_isolates_only_run_metadata(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_fresh_determinism_fixture(left, generated_at="2026-08-31T17:00:00Z")
    _write_fresh_determinism_fixture(right, generated_at="2026-08-31T17:00:01Z")

    result = phase1._fresh_build_directory_comparison(left, right)

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["substantive_file_count"] == 1
    assert result["isolated_run_metadata"] == [
        "run-manifest.generated_at",
        "run-manifest.private_output",
        "run-manifest.source_manifest_path",
        "SHA256SUMS run-manifest entry",
    ]


def test_fresh_build_comparison_detects_substantive_and_key_differences(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_fresh_determinism_fixture(left, generated_at="2026-08-31T17:00:00Z")
    _write_fresh_determinism_fixture(right, generated_at="2026-08-31T17:00:01Z")
    phase1._write_json(right / "payload.json", {"stable": [1, 2, 4]})
    phase1._write_private(right / "private-author-key", b"q" * 32)

    result = phase1._fresh_build_directory_comparison(left, right)

    assert result["passed"] is False
    assert "fresh build output differs: payload.json" in result["errors"]
    assert "fresh builds did not reuse identical private key bytes" in result["errors"]
