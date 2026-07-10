import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "analyse_counterfactual_simulation_dataset.py"
SPEC = importlib.util.spec_from_file_location("counterfactual_analysis", TOOL)
analysis = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(analysis)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def fixture_session(tmp_path, posts=3):
    root = tmp_path / "session"
    snapshot = root / "input_snapshot"
    (snapshot / "images").mkdir(parents=True)
    (snapshot / "generated_images").mkdir()
    (snapshot / "images" / "t01.jpg").write_bytes(b"one")
    (snapshot / "images" / "t02.jpg").write_bytes(b"two")
    (snapshot / "generated_images" / "tg_" + "a" * 64 + ".png") if False else None
    generated = "tg_" + "a" * 64 + ".png"
    (snapshot / "generated_images" / generated).write_bytes(b"gen")
    write_json(snapshot / "manifest.json", {"git_commit": "abc", "original_image_count": 2, "generated_image_count": 1, "quote_count": 3, "sha256": {}})
    manifest = {
        "mode": "counterfactual", "quote_coupling": "shared", "runs": 1,
        "posts_per_run": posts, "seed_base": 1000, "session_id": "fixture",
        "schema_version": 1, "created_at": "2026-01-01T00:00:00+00:00",
        "start_local_time": "2026-01-01T00:00:00+00:00", "accumulated_runtime_seconds": 1.0,
        "simulator_source_sha256": "1" * 64, "production_source_sha256": "2" * 64,
        "snapshot_manifest_sha256": "3" * 64,
        "effective_config": {"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
            "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": .32, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0,
            "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 6.0, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 15.0},
    }
    write_json(root / "session_manifest.json", manifest)
    run = root / "counterfactual" / "runs" / "run_0000"
    shared, comparisons = [], []
    branch_rows = {branch: [] for branch in analysis.BRANCHES}
    winners = {
        "production": ["t01.jpg", "t02.jpg", "t01.jpg"],
        "editorial": ["t01.jpg", "t01.jpg", "t01.jpg"],
        "identity": ["t01.jpg", "t02.jpg", "t02.jpg"],
    }
    spacing = {branch: 0 for branch in analysis.BRANCHES}
    for index in range(1, posts + 1):
        timestamp = 1000 + index
        qhash = f"q{index}"
        shared.append({"post_index": index, "quote_hash": qhash, "quote_text": f"quote {index}", "virtual_timestamp": timestamp})
        current_winners, sources = {}, {}
        for branch in analysis.BRANCHES:
            winner = winners[branch][index - 1]
            source = "generated" if winner.startswith("tg_") else "original"
            before = spacing[branch]
            spacing[branch] = 0 if source == "generated" else min(2, before + 1)
            row = {"post_index": index, "branch": branch, "run_seed": 1000, "virtual_timestamp": timestamp,
                   "baseline_score": 10 + index, "policy_score": 10 + index + (1 if branch == "editorial" else 0),
                   "winner": winner, "winner_source": source, "candidate_count": 2,
                   "original_candidate_count": 2, "generated_candidate_count": 0,
                   "generated_spacing_counter_before": before, "generated_spacing_counter_after": spacing[branch],
                   "image_cycle_reset": False, "selection_phase": "normal", "origin_quote_match": False,
                   "editorial_adjustment": 1 if branch == "editorial" else 0, "editorial_cap_hit": False,
                   "policy_excluded_count": 0, "diagnostic_candidate_present": False,
                   "diagnostic_candidate_origin_match": False, "diagnostic_candidate_excluded": False}
            branch_rows[branch].append(row); current_winners[branch] = winner; sources[branch] = source
        comparisons.append({"post_index": index, "run_id": "run_0000", "run_seed": 1000,
            "virtual_timestamp": timestamp, "quote_hash": qhash, "quote_text": f"quote {index}",
            "winners": current_winners, "winner_sources": sources,
            "production_editorial_same": current_winners["production"] == current_winners["editorial"],
            "production_identity_same": current_winners["production"] == current_winners["identity"],
            "editorial_identity_same": current_winners["editorial"] == current_winners["identity"],
            "all_three_same": len(set(current_winners.values())) == 1,
            "production_candidate_identity_action": "unchanged", "production_candidate_identity_excluded": False})
    write_jsonl(run / "shared_quotes.jsonl", shared)
    write_jsonl(run / "branch_comparison.jsonl", comparisons)
    for branch, rows in branch_rows.items():
        write_jsonl(run / branch / "selections.jsonl", rows)
    write_json(run / "counterfactual_checkpoint.json", {"completed_posts": posts, "branches": {branch: {} for branch in analysis.BRANCHES}})
    return root


def test_complete_dataset_and_metrics(tmp_path):
    dataset = analysis.load_and_validate(fixture_session(tmp_path))
    result = analysis.analyse(dataset)
    assert result["metrics"]["totals"] == {"runs": 1, "post_indices": 3, "branch_selections": 9}
    assert result["metrics"]["divergence"]["production_editorial_count"] == 1
    assert result["metrics"]["divergence"]["production_identity_count"] == 1
    assert analysis.reuse_intervals(dataset["runs"]["run_0000"]["branches"]["production"]) == [2]
    assert analysis.entropy({"a": 2, "b": 2}) == 1.0
    assert analysis.top_share({"a": 3, "b": 1}, 1) == .75


@pytest.mark.parametrize("mutation,match", [
    ("missing", "non-contiguous"), ("duplicate", "non-contiguous"),
    ("quote", "quote mismatch"), ("branch", "invalid branch"),
    ("nan", "finite"), ("checkpoint", "checkpoint"),
])
def test_validation_failures(tmp_path, mutation, match):
    root = fixture_session(tmp_path)
    run = root / "counterfactual" / "runs" / "run_0000"
    if mutation in {"missing", "duplicate"}:
        path = run / "production" / "selections.jsonl"; rows = analysis.read_jsonl(path)
        rows = rows[:-1] if mutation == "missing" else [rows[0], rows[0], rows[2]]; write_jsonl(path, rows)
    elif mutation == "quote":
        path = run / "branch_comparison.jsonl"; rows = analysis.read_jsonl(path); rows[1]["quote_hash"] = "wrong"; write_jsonl(path, rows)
    elif mutation == "branch":
        path = run / "identity" / "selections.jsonl"; rows = analysis.read_jsonl(path); rows[0]["branch"] = "wrong"; write_jsonl(path, rows)
    elif mutation == "nan":
        path = run / "editorial" / "selections.jsonl"; rows = analysis.read_jsonl(path); rows[0]["policy_score"] = float("nan"); write_jsonl(path, rows)
    else:
        write_json(run / "counterfactual_checkpoint.json", {"completed_posts": 2})
    with pytest.raises(analysis.DatasetError, match=match):
        analysis.load_and_validate(root)


def test_divergence_episode_and_reconvergence():
    rows = analysis.divergence_episodes([True, False, False, True, False], "r", "p")
    assert rows == [
        {"run_id": "r", "pair": "p", "start_index": 2, "end_index": 3, "length": 2, "reconverged": True, "first_reconvergence_index": 4},
        {"run_id": "r", "pair": "p", "start_index": 5, "end_index": 5, "length": 1, "reconverged": False, "first_reconvergence_index": None},
    ]


def test_same_winner_different_observable_state(tmp_path):
    dataset = analysis.load_and_validate(fixture_session(tmp_path))
    result = analysis.analyse(dataset)
    assert result["metrics"]["divergence"]["same_winner_different_observable_state"] >= 1


def test_manifest_hash_rejects_source_change(tmp_path, monkeypatch):
    root = fixture_session(tmp_path); dataset = analysis.load_and_validate(root)
    monkeypatch.chdir(tmp_path)
    manifest = analysis.make_manifest(dataset, tmp_path / "manifest.json")
    (root / "counterfactual" / "runs" / "run_0000" / "shared_quotes.jsonl").write_text("changed\n")
    with pytest.raises(analysis.DatasetError, match="hash mismatch"):
        analysis.validate_manifest_hashes(manifest, root)


def test_deterministic_outputs_source_unchanged_and_no_simulator_or_network(tmp_path, monkeypatch):
    root = fixture_session(tmp_path); before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden call")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setitem(sys.modules, "simulate_regular_post_futures", None)
    monkeypatch.chdir(tmp_path)
    for suffix in ("a", "b"):
        args = analysis.parser().parse_args(["--session-dir", str(root), "--output-dir", str(tmp_path / suffix), "--manifest", str(tmp_path / "canonical.json"), "--overwrite"])
        analysis.run_analysis(args)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    comparable = ["analysis_summary.json", "detailed_metrics.json", "image_metrics.csv", "quote_metrics.csv", "divergence_events.csv", "divergence_episodes.csv", "transition_matrix.csv", "case_studies.json", "analysis_report.md"]
    assert all((tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes() for name in comparable)


def test_output_path_cannot_be_inside_source(tmp_path, monkeypatch):
    root = fixture_session(tmp_path); monkeypatch.chdir(tmp_path)
    args = analysis.parser().parse_args(["--session-dir", str(root), "--output-dir", str(root / "derived")])
    with pytest.raises(analysis.DatasetError, match="must not be inside"):
        analysis.run_analysis(args)
