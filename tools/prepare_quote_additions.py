"""Prepare a local quotation/context overlay; never install or publish it.

Input records contain reviewed packets and their saved-source evidence. Existing
runtime formats and validators are reused. Rebuilding always starts from the
unchanged base checkout, so repeating a run cannot append the batch twice.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.quote_addition_evidence import curated_source_for
RESEARCH = Path("semantic_alignment_research/quote_research_full_001")
RUNTIME = Path("semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json")
SOURCE_FILES = (
    "historical_context_source_recovery.json",
    "historical_context_source_resolution.json",
    "historical_context_source_research.json",
    "historical_context_source_openai_research.json",
    "historical_context_source_independent_review.json",
    "historical_context_source_curated_evidence.json",
)


def read_json(path: Path) -> Any:
    """Read a local JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    """Write deterministic JSON within the explicitly supplied output tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(data: bytes) -> str:
    """Hash bytes without interpreting their contents."""
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    """Hash one retained file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_records(batch: Path) -> list[dict]:
    """Read the two independently prepared source groups."""
    records = []
    for name in ("html_records.json", "book_records.json"):
        value = read_json(batch / name)
        records.extend(value if isinstance(value, list) else value["records"])
    ids = [row["candidate_id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate IDs in addition inputs")
    return sorted(records, key=lambda row: row["candidate_id"])


def append_source(source: bytes, quotations: list[str]) -> bytes:
    """Preserve the existing bytes and append distinct single-line UTF-8 quotes."""
    from historical_context_formatter import quote_text_hash
    known = {quote_text_hash(line) for line in source.decode("utf-8").splitlines() if line.strip()}
    for text in quotations:
        if not text or text != " ".join(text.split()):
            raise ValueError("New quotations must be nonempty single lines with normal spacing")
        identity = quote_text_hash(text)
        if identity in known:
            raise ValueError("Quotation already present or duplicated in this batch")
        known.add(identity)
    separator = b"" if not source or source.endswith(b"\n") else b"\n"
    return source + separator + ("\n".join(quotations) + "\n").encode("utf-8")


def analysis_for(packet: dict) -> dict:
    """Create conservative editorial metadata locally, without a provider call."""
    text = packet["quote_text"].casefold()
    topics = [topic for topic in ("socialism", "communism") if topic[:7] in text]
    topics = topics or ["communism"]
    return {
        "summary": packet["intended_argument"],
        "primary_topics": topics,
        "secondary_topics": ["state_power", "individual_liberty", "economy"],
        "specific_keywords": topics + ["freedom", "state control"],
        "tone": ["serious", "combative"],
        "emotional_intensity": 60, "visual_energy": "medium",
        "literal_visual_concepts": [],
        "archive_image_preferences": {
            "preferred_scenes": ["formal_portrait", "podium_speech", "parliament"],
            "preferred_subject_moods": ["serious", "determined"],
            "preferred_activities": ["speaking", "explaining policy"],
            "preferred_visible_symbols": [],
            "visual_affinities": ["Margaret Thatcher speaking or in a formal portrait"],
            "weak_visual_mismatches": ["celebrations unrelated to the argument"],
            "strong_visual_mismatches": ["depicting a communist leader as the quotation's speaker"],
            "matching_summary": "Prefer Thatcher speaking or a formal portrait; an archive photograph need not depict the exact occasion and must not be labelled as doing so without evidence.",
        },
        "seasonality": {"relevance": "none", "seasons": [], "occasions": [],
                        "preferred_windows": [], "hard_exclude_outside_windows": False,
                        "explanation": "No calendar restriction is inherent in this passage."},
        "historical_context": {
            "specificity": "specific_period", "referenced_people": ["Margaret Thatcher"],
            "referenced_places": [], "referenced_events": [packet["source_event"]],
            "needs_historical_image_match": False,
            "explanation": "The accompanying research record supplies the historical setting; these words do not require a photograph of that exact occasion.",
        },
        "scores": {"standalone_clarity": 70, "visual_matchability": 50, "general_post_suitability": 50},
    }


def update_analysis(document: dict, source: bytes, records: list[dict], batch_name: str, *, batch_timestamp: str) -> dict:
    """Extend metadata while retaining every existing analysis payload and index."""
    from historical_context_formatter import quote_text_hash
    result = copy.deepcopy(document)
    for row in records:
        packet = row["packet"]
        qid = quote_text_hash(packet["quote_text"])
        result["items"][qid] = {
            "quote_hash": qid, "text": packet["quote_text"],
            "analysis": analysis_for(packet), "analysis_model": "local_editorial_preparation",
            "prompt_version": batch_name, "analysed_at": batch_timestamp,
            "response_id": None, "usage": {},
        }
    line_index, line_numbers = {}, {}
    lines = source.decode("utf-8").splitlines()
    for number, text in enumerate(lines, 1):
        if not text.strip():
            continue
        qid = quote_text_hash(text)
        line_index[str(number)] = qid
        line_numbers.setdefault(qid, []).append(number)
    if set(result["items"]) != set(line_numbers):
        raise ValueError("Quotation/analysis identity coverage differs")
    for qid, numbers in line_numbers.items():
        result["items"][qid]["line_numbers"] = numbers
    result["line_index"] = line_index
    result["current_hashes"] = sorted(line_numbers)
    result["source"].update(source_sha256=sha256(source), line_count=len(lines),
                            non_empty_quote_count=len(line_index), unique_quote_count=len(line_numbers))
    result["updated_at"] = batch_timestamp
    return result


def validate_batch_timestamp(value: str) -> str:
    """Accept an explicit ISO date or UTC timestamp without inventing precision."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?", value):
        raise ValueError("batch timestamp must be an ISO date or UTC timestamp")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ" if "T" in value else "%Y-%m-%d")
    return value


def publish_preparation(work: Path, batch: Path) -> None:
    """Replace completed outputs, restoring the old set if publication fails."""
    names = ("staged", "previews.json", "PREVIEW.md", "validation.json")
    previous = work / "previous"
    previous.mkdir()
    saved, installed = [], []
    try:
        for name in names:
            target = batch / name
            if target.is_symlink():
                raise ValueError("Preparation outputs must not be symbolic links")
            if target.exists():
                os.replace(target, previous / name)
                saved.append(name)
            os.replace(work / name, target)
            installed.append(name)
    except BaseException:
        for name in reversed(installed):
            target = batch / name
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        for name in saved:
            os.replace(previous / name, batch / name)
        raise


def prepare(batch: Path, project: Path, *, batch_timestamp: str) -> dict:
    """Build in a clean private overlay and publish only a complete preparation."""
    batch_timestamp = validate_batch_timestamp(batch_timestamp)
    # Check original path components before resolving away symlinks.
    if any(path.is_symlink() for path in (batch, *batch.parents)):
        raise ValueError("Batch path must not contain symbolic links")
    batch, project = batch.resolve(), project.resolve()
    if project == Path("/disks/disk1/etc/mrsMThatcher").resolve():
        raise ValueError("Run this preparation from an isolated checkout")
    if not batch.is_relative_to(project / "quotation_additions"):
        raise ValueError("Batch directory must be inside the checkout's quotation_additions directory")
    if any(path.is_symlink() for path in batch.rglob("*")):
        raise ValueError("Batch inputs and outputs must not contain symbolic links")
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=batch) as temporary:
        work = Path(temporary)
        staged = work / "staged"
        staged.mkdir()
        result = _build_preparation(batch, project, staged, work, batch_timestamp)
        publish_preparation(work, batch)
    return result


def _build_preparation(batch: Path, project: Path, staged: Path, work: Path, batch_timestamp: str) -> dict:
    """Produce the complete, validated overlay without touching prior outputs."""
    from historical_context_formatter import (
        load_and_validate_corpus_core, load_and_validate_corpus,
        packet_is_attributed_to_margaret_thatcher, quote_text_hash,
        format_context_reply_public, x_weighted_length,
    )
    from historical_context_source_curated_evidence import curated_source_id
    from historical_context_source_roles import build_audit
    from semantic_alignment.quote_research_schema import validate_packet
    from mrs_bot_historical_context_delivery import context_reply_research_is_complete
    from mrs_bot_quote_candidates import load_completed_research_quote_hashes
    from historical_context_published_reply_semantic_review import build_review
    from analyse_mrs_assets_xai_v4 import QUOTE_ANALYSIS_SCHEMA
    from jsonschema import validate as validate_json
    from semantic_alignment.quote_research_corpus import finalise_corpus_manifest, verify_corpus_manifest
    from historical_context_reply_semantic_gate_audit import build_audit as build_gate_audit

    records = load_records(batch)
    original_source = (project / "mrsMThatcher.txt").read_bytes()
    texts = [row["proposed_quote"] for row in records]
    source = append_source(original_source, texts)
    packets_doc = read_json(project / RESEARCH / "research_packets.json")
    old_packets = copy.deepcopy(packets_doc["items"])
    old_loaded, old_unresolved = load_and_validate_corpus(project / RESEARCH, require_source_role_audit=True)
    old_renders = {qid: format_context_reply_public(packet) for qid, packet in old_loaded.items()}
    manifest = read_json(project / RESEARCH / "corpus_manifest.json")
    verify_corpus_manifest(manifest)
    # Coordinates belong to the append-only research source, not today's bot list.
    last_occurrence = max(
        occurrence["line_number"] for record in manifest["records"]
        for occurrence in record["source_occurrences"]
    )
    status = read_json(project / RESEARCH / "final_unresolved/final_research_status.json")
    recovery = read_json(project / RESEARCH / SOURCE_FILES[0])
    curated = read_json(project / RESEARCH / SOURCE_FILES[-1])

    def copy_input(relative: Path) -> None:
        """Copy only a necessary unchanged runtime dependency into the overlay."""
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError(f"Unexpected staging symlink: {target}")
        shutil.copyfile(project / relative, target)

    dependencies = [RESEARCH / name for name in SOURCE_FILES]
    dependencies += [RESEARCH / name for name in (
        "grounding_sources.json", "historical_context_packet_corrections.json", "unresolved_quotes.json",
        "final_unresolved/unresolved_cases.json",
    )]
    dependencies += [Path(name) for name in (
        "historical_context_evidence_truth_audit.json",
        "historical_context_mtf_primary_review.json",
        "historical_context_reply_history.json", "quote_analysis_overrides.json",
        "historical_context_formatter.py",
    ) if (project / name).is_file()]
    for relative in dependencies:
        copy_input(relative)

    for offset, row in enumerate(records, 1):
        packet = row["packet"]
        text = row["proposed_quote"]
        qid = sha256(text.encode("utf-8"))
        if packet["quote_text"] != text or packet["quote_id"] != qid:
            raise ValueError(f"Input quotation identity mismatch: {row['candidate_id']}")
        validate_packet(packet)
        if x_weighted_length(text) > 280:
            raise ValueError(f"Quotation exceeds 280 weighted characters: {row['candidate_id']}")
        if qid in packets_doc["items"]:
            raise ValueError("New packet ID already exists")
        packets_doc["items"][qid] = packet
        line_number = last_occurrence + offset
        manifest["records"].append({
            "quote_id": qid, "quote_hash": quote_text_hash(text), "quote_text": text,
            "source_occurrences": [{"line_number": line_number}],
            "duplicate_occurrence_count": 1, "research_status": "completed",
            "input_hash": sha256(json.dumps({"reviewed_input": row, "source_occurrences": [{"line_number": line_number}]}, ensure_ascii=False, sort_keys=True).encode()),
        })
        recovery["items"][qid] = {"quote_id": qid, "quote_text_sha256": qid,
                                  "citation_sources": [], "model_proposed_source_leads": []}
        reviewed_source = curated_source_for(row, recorded_at=batch_timestamp)
        reviewed_source["source_id"] = curated_source_id(qid, reviewed_source)
        curated["items"][qid] = {"quote_id": qid, "quote_text": text,
                                 "quote_text_sha256": qid, "sources": [reviewed_source]}

    manifest = finalise_corpus_manifest(manifest)
    recovery["research_packet_count"] = len(packets_doc["items"])
    curated["quote_count"] = len(curated["items"])
    curated["source_count"] = sum(len(item["sources"]) for item in curated["items"].values())
    write_json(staged / RESEARCH / "research_packets.json", packets_doc)
    write_json(staged / RESEARCH / "corpus_manifest.json", manifest)
    status.update(generated_timestamp=batch_timestamp, total_manifest_quotes=len(manifest["records"]), completed_quotes=len(packets_doc["items"]),
                  completion_percentage=100 * len(packets_doc["items"]) / len(manifest["records"]),
                  corpus_hash=file_hash(staged / RESEARCH / "research_packets.json"))
    write_json(staged / RESEARCH / "final_unresolved/final_research_status.json", status)
    write_json(staged / RESEARCH / SOURCE_FILES[0], recovery)
    write_json(staged / RESEARCH / SOURCE_FILES[-1], curated)
    (staged / "mrsMThatcher.txt").write_bytes(source)
    analysis = update_analysis(read_json(project / "quote_analysis.json"), source, records, batch.name, batch_timestamp=batch_timestamp)
    for row in records:
        validate_json(analysis["items"][row["packet"]["quote_id"]]["analysis"], QUOTE_ANALYSIS_SCHEMA)
    write_json(staged / "quote_analysis.json", analysis)
    packets, unresolved = load_and_validate_corpus_core(staged / RESEARCH)
    eligible = {qid for qid, packet in packets.items() if packet_is_attributed_to_margaret_thatcher(packet)}
    sidecars = [read_json(staged / RESEARCH / name) for name in SOURCE_FILES]
    audit = build_audit(packets, unresolved, research_dir=staged / RESEARCH,
                        attribution_eligible_ids=eligible, recovered_evidence=sidecars[0],
                        source_resolution=sidecars[1], researched_evidence=sidecars[2],
                        openai_researched_evidence=sidecars[3], independent_review=sidecars[4],
                        curated_evidence=sidecars[5], audit_date=batch_timestamp[:10])
    old_audit = read_json(project / RESEARCH / "historical_context_source_role_audit.json")
    if any(audit["items"][qid] != item for qid, item in old_audit["items"].items()):
        raise ValueError("An existing source-role assessment changed")
    write_json(staged / RESEARCH / "historical_context_source_role_audit.json", audit)
    runtime = read_json(project / RUNTIME)
    runtime.update(runtime_eligible_quote_ids=sorted(quote_text_hash(packets[qid]["quote_text"]) for qid in eligible),
                   resolved_manifest_quote_ids=sorted(eligible), runtime_eligible_quote_count=len(eligible),
                   source_record_count=len(analysis["items"]))
    runtime["source_file_hashes"].update(active_source=sha256(source),
        completed_quote_research=file_hash(staged / RESEARCH / "research_packets.json"))
    write_json(staged / RUNTIME, runtime)
    actual = load_completed_research_quote_hashes(
        historical_context_research_dir=staged / RESEARCH,
        runtime_eligible_quote_manifest_file=staged / RUNTIME,
        lines_file=staged / "mrsMThatcher.txt",
        completed_quote_research_file=staged / RESEARCH / "research_packets.json",
        quote_text_hash=quote_text_hash, file_sha256=file_hash,
        load_json_object=lambda path, **_kwargs: read_json(path),
    )
    if actual != set(runtime["runtime_eligible_quote_ids"]):
        raise ValueError("Runtime rejected staged eligibility")

    ledger = build_review(staged / "historical_context_evidence_truth_audit.json",
                          staged / RESEARCH / "historical_context_source_role_audit.json",
                          staged / "historical_context_mtf_primary_review.json", reference_root=staged)
    old_ledger = read_json(project / "historical_context_published_reply_semantic_review.json")
    for field in ("records", "remaining_items"):
        if ledger[field] != old_ledger[field]:
            raise ValueError(f"Historical context gate changed: {field}")
    ledger_path = staged / "historical_context_published_reply_semantic_review.json"
    write_json(ledger_path, ledger)
    gate_source = (project / "historical_context_reply_semantic_gate.py").read_text()
    pins = [node.value.value for node in ast.parse(gate_source).body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and any(isinstance(target, ast.Name) and target.id == "EXPECTED_LEDGER_SHA256" for target in node.targets)]
    if len(pins) != 1 or gate_source.count(pins[0]) != 1:
        raise ValueError("Cannot identify the historical gate's expected ledger hash")
    gate_source = gate_source.replace(pins[0], file_hash(ledger_path))
    (staged / "historical_context_reply_semantic_gate.py").write_text(gate_source)
    # Load the actual staged default, so success cannot rely on an override that
    # the deployed wrapper would omit.
    module_name = "prepared_historical_context_semantic_gate"
    spec = importlib.util.spec_from_file_location(module_name, staged / "historical_context_reply_semantic_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    exec(compile(gate_source, str(staged / "historical_context_reply_semantic_gate.py"), "exec"), module.__dict__)
    gate = module.load_historical_context_semantic_gate(root=staged, eligible_quote_ids=eligible)
    if not gate.available:
        raise ValueError(f"Staged historical-context gate failed: {gate.reason}")
    gate_audit = build_gate_audit(
        root=staged, research_dir=staged / RESEARCH,
        runtime_manifest_path=staged / RUNTIME,
        generated_at=batch_timestamp, expected_ledger_sha256=file_hash(ledger_path),
    )
    if gate_audit["invariant_failure_count"]:
        raise ValueError("Staged semantic-gate audit invariants failed")
    write_json(staged / "historical_context_reply_semantic_gate_audit.json", gate_audit)
    loaded, final_unresolved = load_and_validate_corpus(staged / RESEARCH, require_source_role_audit=True)
    if final_unresolved != old_unresolved or any(packets[qid] != packet for qid, packet in old_packets.items()):
        raise ValueError("Existing research or unresolved records changed")
    if any(format_context_reply_public(loaded[qid]) != rendered for qid, rendered in old_renders.items()):
        raise ValueError("An existing public context rendering changed")
    previews = []
    for row in records:
        qid = row["packet"]["quote_id"]
        rendered = format_context_reply_public(loaded[qid], maximum_length=4000)
        if not rendered or not context_reply_research_is_complete(loaded[qid]) or gate.blocks(qid):
            raise ValueError(f"Context not ready for {row['candidate_id']}")
        if "/disks/" in rendered["text"] or "/home/" in rendered["text"]:
            raise ValueError("Private path leaked into public context")
        previews.append({"candidate_id": row["candidate_id"], "quote": row["proposed_quote"],
                         "original_quote": row["original_quote"], "quote_id": qid,
                         "quote_weighted_characters": x_weighted_length(row["proposed_quote"]),
                         "context": rendered["text"], "verification_label": rendered["verification_label"],
                         "context_weighted_characters": rendered["character_count"]})
    write_json(work / "previews.json", previews)
    markdown = ["# Proposed quotations and historical context", "", "Prepared for review. No production files changed and nothing posted.", ""]
    for preview in previews:
        markdown += [f"## {preview['candidate_id']} — {preview['quote_weighted_characters']} characters", "",
                     "> " + preview["quote"], "", preview["context"], ""]
        if preview["original_quote"] != preview["quote"]:
            markdown += ["Original mined wording: " + preview["original_quote"], ""]
    (work / "PREVIEW.md").write_text("\n".join(markdown), encoding="utf-8")
    changed = []
    for path in sorted(staged.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(staged)
            baseline = project / relative
            if not baseline.exists() or file_hash(path) != file_hash(baseline):
                changed.append({"path": str(relative), "base_sha256": file_hash(baseline) if baseline.exists() else None,
                                "prepared_sha256": file_hash(path)})
    result = {"status": "prepared_for_review", "batch_timestamp": batch_timestamp, "added": len(records),
              "physical_lines": len(source.splitlines()), "unique_quotes": len(analysis["items"]),
              "eligible_quotes": len(actual), "completed_research_packets": len(packets),
              "unresolved_quotes": len(unresolved), "existing_packets_preserved": True,
              "existing_context_renderings_preserved": True, "historical_gate_records_preserved": True,
              "context_ready_for_all_additions": True, "production_writes": 0, "provider_calls": 0,
              "input_sha256": {name: file_hash(batch / name) for name in ("html_records.json", "book_records.json")},
              "changed_files": changed,
              "image_shadow_limitation": "The historical quotation/image veto matrix has no reviews for the additions and becomes stale against the extended corpus. It is retained as offline research; production does not load it. No new image-pair approval is claimed."}
    write_json(work / "validation.json", result)
    return result


def main() -> None:
    """Prepare a batch beneath an isolated checkout and print its result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--batch-timestamp", required=True, help="Authoritative ISO batch date or UTC timestamp; never wall-clock time")
    parser.add_argument("--base-project", type=Path, default=ROOT, help="Isolated base corpus; batch must be under its quotation_additions directory")
    args = parser.parse_args()
    result = prepare(args.batch, args.base_project, batch_timestamp=args.batch_timestamp)
    print(json.dumps({key: value for key, value in result.items() if key != "changed_files"}, indent=2))


if __name__ == "__main__":
    main()
