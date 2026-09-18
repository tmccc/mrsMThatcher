"""Materialise the reviewed 627-packet corpus for historical audit tests."""
from __future__ import annotations

import io
from functools import cache
from pathlib import Path
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_CORPUS_COMMIT = "9c142bc8485819061756a88ea99ef991a0683be0"
RESEARCH_RELATIVE = Path("semantic_alignment_research/quote_research_full_001")
_ROOT_FILES = (
    "mrsMThatcher.txt",
    "thatcher_quote_research_project/quote_manifest.json",
    "quote_analysis.json",
    "historical_context_formatter.py",
    "historical_context_reply_semantic_gate.py",
    "historical_context_published_reply_semantic_review.json",
    "historical_context_evidence_truth_audit.json",
    "historical_context_mtf_primary_review.json",
    "historical_context_reply_semantic_gate_audit.json",
    "historical_context_v9_transport_url_redaction_transition_manifest.json",
    "historical_context_v9_mtf_live_context_transition_manifest.json",
    "tests/fixtures/historical_context_reply_history.reviewed.json",
    "semantic_alignment_research/quote_attribution_cleanup_001/"
    "deployment_candidate/runtime_eligible_quote_manifest.json",
    "semantic_alignment_research/quote_attribution_cleanup_001/"
    "deployment_candidate/material_veto_v3_shadow_manifest.json",
)
_RESEARCH_FILES = (
    "research_packets.json",
    "permanent_failures.json",
    "cost_ledger.json",
    "retry_batches/recovery_stage_meta_report.json",
    "retry_analysis/full_retry_manifest.json",
    "corpus_manifest.json",
    "final_unresolved/final_research_status.json",
    "final_unresolved/corpus_closure_audit.json",
    "retry_analysis/retry_validation_20/cost_ledger.json",
    "retry_batches/retry_batch_030_001_run/cost_ledger.json",
    "retry_batches/retry_batch_030_002_recovery_19_run/cost_ledger.json",
    "retry_batches/retry_batch_030_002_run/cost_ledger.json",
    "retry_batches/retry_batch_030_003_run/cost_ledger.json",
    "retry_batches/retry_batch_030_004_run/cost_ledger.json",
    "retry_batches/retry_batch_030_005_run/cost_ledger.json",
    "retry_batches/retry_validation_20/cost_ledger.json",
    "unresolved_quotes.json",
    "grounding_sources.json",
    "historical_context_packet_corrections.json",
    "historical_context_source_role_audit.json",
    "historical_context_source_recovery.json",
    "historical_context_source_resolution.json",
    "historical_context_source_research.json",
    "historical_context_source_openai_research.json",
    "historical_context_source_independent_review.json",
    "historical_context_source_curated_evidence.json",
)


@pytest.fixture(scope="session")
def historical_corpus_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Share one immutable historical corpus per pytest worker."""
    return _materialise_historical_corpus(
        tmp_path_factory.getbasetemp() / "historical-corpus-627"
    )


@cache
def _materialise_historical_corpus(destination: Path) -> Path:
    """Read exact original Git blobs without changing the growing checkout.

    Historical tools deliberately pin this batch's sizes and findings. Tests of
    those tools need the corresponding inputs, while current-runtime tests must
    continue to use the active corpus. A full repository history containing the
    reviewed commit is required; missing history is a setup error, not a skip.
    """
    destination.mkdir()
    paths = [*_ROOT_FILES, *(str(RESEARCH_RELATIVE / name) for name in _RESEARCH_FILES)]
    result = subprocess.run(
        ["git", "archive", "--format=tar", ORIGINAL_CORPUS_COMMIT, *paths],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    expected = set(paths)
    extracted = set()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile() or member.name not in expected:
                raise AssertionError(f"Unexpected historical fixture member: {member.name}")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                target.write_bytes(source.read())
            extracted.add(member.name)
    assert extracted == expected
    # Live posting history is intentionally untracked. Supply the committed
    # reviewed history fixture at the historical tools' conventional location.
    (destination / "historical_context_reply_history.json").write_bytes(
        (destination / "tests/fixtures/historical_context_reply_history.reviewed.json").read_bytes()
    )
    return destination
