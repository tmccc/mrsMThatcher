from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import mrsMThatcher2 as bot


ROOT = Path(__file__).resolve().parents[1]
SOURCE_RESEARCH = (
    ROOT / "semantic_alignment_research" / "quote_research_full_001"
)
SOURCE_RUNTIME_MANIFEST = (
    ROOT
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "runtime_eligible_quote_manifest.json"
)
MISATTRIBUTED_QUOTE_ID = (
    "7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646"
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _configure_core_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    research = tmp_path / "research"
    final = research / "final_unresolved"
    final.mkdir(parents=True)
    for relative in (
        "research_packets.json",
        "corpus_manifest.json",
        "final_unresolved/final_research_status.json",
    ):
        destination = research / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_RESEARCH / relative, destination)

    lines = tmp_path / "mrsMThatcher.txt"
    runtime_manifest = tmp_path / "runtime_eligible_quote_manifest.json"
    shutil.copyfile(ROOT / "mrsMThatcher.txt", lines)
    shutil.copyfile(SOURCE_RUNTIME_MANIFEST, runtime_manifest)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", research)
    monkeypatch.setattr(
        bot,
        "COMPLETED_QUOTE_RESEARCH_FILE",
        research / "research_packets.json",
    )
    monkeypatch.setattr(bot, "LINES_FILE", lines)
    monkeypatch.setattr(
        bot,
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        runtime_manifest,
    )
    return research, runtime_manifest


def _rehash_status(research: Path) -> None:
    packets_path = research / "research_packets.json"
    status_path = research / "final_unresolved/final_research_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    packets = json.loads(packets_path.read_text(encoding="utf-8"))["items"]
    status["completed_quotes"] = len(packets)
    status["corpus_hash"] = hashlib.sha256(packets_path.read_bytes()).hexdigest()
    _write_json(status_path, status)


def test_current_runtime_eligibility_manifest_validates_without_context_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, runtime_manifest = _configure_core_fixture(
        tmp_path,
        monkeypatch,
    )

    # The ordinary lane depends only on the canonical packet partition and its
    # exact eligibility manifest, not on context-only role/correction files.
    assert not (research / "historical_context_source_role_audit.json").exists()
    assert not (research / "historical_context_packet_corrections.json").exists()
    expected_ids = set(
        json.loads(runtime_manifest.read_text(encoding="utf-8"))[
            "runtime_eligible_quote_ids"
        ]
    )
    assert expected_ids
    assert MISATTRIBUTED_QUOTE_ID not in expected_ids
    assert bot.load_completed_research_quote_hashes() == expected_ids


@pytest.mark.parametrize("addition_count", [1, 11])
def test_corpus_growth_preserves_existing_ids_and_requires_new_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    addition_count: int,
) -> None:
    research, runtime_path = _configure_core_fixture(tmp_path, monkeypatch)
    packets_path = research / "research_packets.json"
    corpus_path = research / "corpus_manifest.json"
    status_path = research / "final_unresolved/final_research_status.json"
    packets = json.loads(packets_path.read_text(encoding="utf-8"))
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    original_ids = bot.load_completed_research_quote_hashes()
    original_packets = dict(packets["items"])
    source = bot.LINES_FILE.read_text(encoding="utf-8")
    original_line_count = len(source.splitlines())
    if source and not source.endswith("\n"):
        source += "\n"
    added_ids: set[str] = set()

    # Reuse a schema-valid packet only inside this temporary fixture. These
    # invented strings test corpus growth; they are not historical quotations.
    template = packets["items"][runtime["resolved_manifest_quote_ids"][0]]
    for number in range(addition_count):
        text = f"Synthetic offline corpus addition number {number + 1}."
        quote_id = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert quote_id not in packets["items"]
        packet = copy.deepcopy(template)
        packet.update(quote_id=quote_id, quote_text=text, verified_text=text)
        packets["items"][quote_id] = packet
        corpus["records"].append(
            {
                "quote_id": quote_id,
                "quote_hash": quote_id,
                "quote_text": text,
                "source_occurrences": [
                    {"line_number": original_line_count + number + 1}
                ],
                "duplicate_occurrence_count": 1,
            }
        )
        source += text + "\n"
        added_ids.add(quote_id)

    bot.LINES_FILE.write_text(source, encoding="utf-8")
    _write_json(packets_path, packets)
    corpus["record_count"] = len(corpus["records"])
    corpus["source_occurrence_count"] += addition_count
    corpus.pop("manifest_sha256")
    corpus["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            corpus, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    _write_json(corpus_path, corpus)
    status["total_manifest_quotes"] = corpus["record_count"]
    _write_json(status_path, status)
    _rehash_status(research)
    runtime["runtime_eligible_quote_ids"] = sorted(original_ids | added_ids)
    runtime["resolved_manifest_quote_ids"] = sorted(
        set(runtime["resolved_manifest_quote_ids"]) | added_ids
    )
    runtime["runtime_eligible_quote_count"] = len(original_ids | added_ids)
    runtime["source_record_count"] = len(
        {bot.quote_text_hash(line) for line in source.splitlines() if line.strip()}
    )
    runtime["source_file_hashes"]["active_source"] = hashlib.sha256(
        bot.LINES_FILE.read_bytes()
    ).hexdigest()
    runtime["source_file_hashes"]["completed_quote_research"] = hashlib.sha256(
        packets_path.read_bytes()
    ).hexdigest()
    _write_json(runtime_path, runtime)

    actual_ids = bot.load_completed_research_quote_hashes()
    assert actual_ids == original_ids | added_ids
    assert actual_ids - original_ids == added_ids
    assert all(packets["items"][key] == value for key, value in original_packets.items())

    # Correct counts and hashes cannot excuse omitting a newly eligible packet.
    missing_id = sorted(added_ids)[0]
    runtime["runtime_eligible_quote_ids"].remove(missing_id)
    runtime["resolved_manifest_quote_ids"].remove(missing_id)
    runtime["runtime_eligible_quote_count"] -= 1
    _write_json(runtime_path, runtime)
    with pytest.raises(
        RuntimeError,
        match="differs from the validated canonical attribution partition",
    ):
        bot.load_completed_research_quote_hashes()


def test_packet_mutation_with_stale_runtime_manifest_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, _runtime_manifest = _configure_core_fixture(
        tmp_path,
        monkeypatch,
    )
    packets_path = research / "research_packets.json"
    packets_document = json.loads(packets_path.read_text(encoding="utf-8"))
    packet = packets_document["items"][MISATTRIBUTED_QUOTE_ID]
    packet["speaker"] = "Margaret Thatcher"
    packet["verification_status"] = "exact"
    _write_json(packets_path, packets_document)
    _rehash_status(research)

    with pytest.raises(RuntimeError, match="source hashes are stale"):
        bot.load_completed_research_quote_hashes()


def test_coherently_rehashed_packet_mutation_still_requires_exact_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, runtime_manifest_path = _configure_core_fixture(
        tmp_path,
        monkeypatch,
    )
    packets_path = research / "research_packets.json"
    packets_document = json.loads(packets_path.read_text(encoding="utf-8"))
    packet = packets_document["items"][MISATTRIBUTED_QUOTE_ID]
    packet["speaker"] = "Margaret Thatcher"
    packet["verification_status"] = "exact"
    _write_json(packets_path, packets_document)
    _rehash_status(research)

    runtime_manifest = json.loads(
        runtime_manifest_path.read_text(encoding="utf-8")
    )
    runtime_manifest["source_file_hashes"]["completed_quote_research"] = (
        hashlib.sha256(packets_path.read_bytes()).hexdigest()
    )
    _write_json(runtime_manifest_path, runtime_manifest)

    with pytest.raises(
        RuntimeError,
        match="differs from the validated canonical attribution partition",
    ):
        bot.load_completed_research_quote_hashes()


def test_coherently_rehashed_whitespace_drift_cannot_break_raw_quote_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, runtime_manifest_path = _configure_core_fixture(
        tmp_path,
        monkeypatch,
    )
    packets_path = research / "research_packets.json"
    manifest_path = research / "corpus_manifest.json"
    packets_document = json.loads(packets_path.read_text(encoding="utf-8"))
    corpus_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_manifest = json.loads(
        runtime_manifest_path.read_text(encoding="utf-8")
    )
    quote_id = runtime_manifest["resolved_manifest_quote_ids"][0]
    original_text = packets_document["items"][quote_id]["quote_text"]
    drifted_text = f" {original_text}"
    packets_document["items"][quote_id]["quote_text"] = drifted_text
    manifest_record = next(
        record
        for record in corpus_manifest["records"]
        if record["quote_id"] == quote_id
    )
    manifest_record["quote_text"] = drifted_text
    _write_json(packets_path, packets_document)
    _write_json(manifest_path, corpus_manifest)
    _rehash_status(research)
    runtime_manifest["source_file_hashes"]["completed_quote_research"] = (
        hashlib.sha256(packets_path.read_bytes()).hexdigest()
    )
    _write_json(runtime_manifest_path, runtime_manifest)

    # Normalised runtime IDs (including authorised aliases) are unchanged by
    # this edit, but the canonical raw packet identity must still fail closed.
    with pytest.raises(RuntimeError, match="canonical quote identity mismatch"):
        bot.load_completed_research_quote_hashes()


def test_core_partition_drift_fails_before_ordinary_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, _runtime_manifest = _configure_core_fixture(
        tmp_path,
        monkeypatch,
    )
    packets_path = research / "research_packets.json"
    status_path = research / "final_unresolved/final_research_status.json"
    packets_document = json.loads(packets_path.read_text(encoding="utf-8"))
    removed_id = next(iter(packets_document["items"]))
    packets_document["items"].pop(removed_id)
    _write_json(packets_path, packets_document)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["completed_quotes"] -= 1
    status["unresolved_quotes"] += 1
    status["unresolved_quote_ids"].append(removed_id)
    status["unresolved_quote_ids"].sort()
    status["corpus_hash"] = hashlib.sha256(packets_path.read_bytes()).hexdigest()
    _write_json(status_path, status)

    with pytest.raises(RuntimeError, match="source hashes are stale"):
        bot.load_completed_research_quote_hashes()
