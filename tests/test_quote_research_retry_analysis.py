from __future__ import annotations

import socket
from collections import Counter
from pathlib import Path

from semantic_alignment.io import read_json
from semantic_alignment.quote_research_retry_analysis import (
    analyze_missing_grounding,
    build_retry_plan,
    cost_projection,
)


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "semantic_alignment_research" / "quote_research_full_001"


def taxonomy() -> dict:
    return read_json(RUN / "offline_recovery" / "failure_taxonomy.json")["items"]


def test_retry_plan_has_no_duplicates_and_excludes_recovered_packets():
    plan = build_retry_plan(RUN, taxonomy())["full"]
    ids = [row["quote_id"] for row in plan["records"]]
    completed = set(read_json(RUN / "research_packets.json")["items"])
    unresolved = set(read_json(RUN / "permanent_failures.json")["items"])
    assert len(ids) == len(set(ids))
    assert set(ids) == unresolved
    assert completed.isdisjoint(ids)


def test_retry_groups_are_disjoint_and_complete():
    rows = build_retry_plan(RUN, taxonomy())["full"]["records"]
    counts = Counter(row["retry_group"] for row in rows)
    assert set(counts) <= {
        "transport_failures", "missing_grounding", "identity_and_structured_output",
    }
    assert sum(counts.values()) == len(read_json(RUN / "permanent_failures.json")["items"])


def test_validation_batch_is_deterministic_and_below_ceiling():
    first = build_retry_plan(RUN, taxonomy(), 20)
    second = build_retry_plan(RUN, taxonomy(), 20)
    assert first["validation"] == second["validation"]
    assert first["validation"]["record_count"] == min(20, first["full"]["record_count"])
    costs = cost_projection(first, RUN)
    assert costs["validation_batch"]["allowed"]
    assert costs["validation_batch"]["high_projection_usd"] <= 5


def test_quote_identity_is_outside_model_editable_output():
    manifest = read_json(RUN / "retry_analysis/retry_manifest_20.json")
    policy = manifest["quote_identity_policy"]
    assert policy["immutable_request_envelope"] == ["quote_id", "quote_text"]
    assert policy["model_editable_identity_fields"] == []
    assert policy["mismatch_action"] == "reject"


def test_missing_grounding_analysis_is_offline(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    result = analyze_missing_grounding(RUN, taxonomy())
    assert result["correlations"]["case_count"] == 101
    assert set(result["items"]) == {
        quote_id for quote_id, item in taxonomy().items()
        if item["category"] == "missing_grounded_source"
    }
