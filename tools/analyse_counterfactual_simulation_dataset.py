#!/usr/bin/env python3
"""Validate and analyse an existing counterfactual simulation dataset.

This tool never imports or invokes the simulator. It is deliberately read-only
with respect to the source session and confines every output to --output-dir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BRANCHES = ("production", "editorial", "identity")
DIAGNOSTIC = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"
ANALYSIS_SCHEMA = 1


class DatasetError(ValueError):
    """Raised when a simulation dataset fails validation."""
    pass


def read_json(path: Path) -> dict:
    """Read JSON."""
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise DatasetError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    """Read jsonl."""
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid JSON at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise DatasetError(f"expected object at {path}:{line_no}")
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    """Return the SHA-256 file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_number(value: Any, label: str) -> float:
    """Return the finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatasetError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise DatasetError(f"{label} must be finite")
    return value


def percentile(values: list[float], fraction: float) -> float | None:
    """Return the percentile."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def distribution(values: Iterable[float]) -> dict:
    """Return the distribution."""
    vals = [float(value) for value in values]
    if not vals:
        return {key: None for key in ("count", "min", "lower_quartile", "median", "mean", "upper_quartile", "max", "standard_deviation")}
    return {
        "count": len(vals), "min": min(vals), "lower_quartile": percentile(vals, .25),
        "median": statistics.median(vals), "mean": statistics.fmean(vals),
        "upper_quartile": percentile(vals, .75), "max": max(vals),
        "standard_deviation": statistics.pstdev(vals),
    }


def entropy(counter: Counter) -> float:
    """Return the entropy."""
    total = sum(counter.values())
    return -sum((count / total) * math.log2(count / total) for count in counter.values()) if total else 0.0


def top_share(counter: Counter, n: int) -> float:
    """Return the top share."""
    total = sum(counter.values())
    return sum(sorted(counter.values(), reverse=True)[:n]) / total if total else 0.0


def run_paths(run_dir: Path) -> dict[str, Path]:
    """Run paths."""
    paths = {
        "shared": run_dir / "shared_quotes.jsonl",
        "comparison": run_dir / "branch_comparison.jsonl",
        "checkpoint": run_dir / "counterfactual_checkpoint.json",
    }
    paths.update({branch: run_dir / branch / "selections.jsonl" for branch in BRANCHES})
    return paths


def validate_indices(rows: list[dict], expected: int, label: str) -> None:
    """Validate indices."""
    indices = [row.get("post_index") for row in rows]
    wanted = list(range(1, expected + 1))
    if indices != wanted:
        missing = sorted(set(wanted) - set(indices))
        duplicates = sorted(index for index, count in Counter(indices).items() if count > 1)
        raise DatasetError(f"{label}: non-contiguous indices; missing={missing[:10]} duplicate={duplicates[:10]}")


def load_and_validate(session_dir: Path, allow_incomplete: bool = False) -> dict:
    """Load and validate."""
    session_dir = session_dir.resolve()
    manifest = read_json(session_dir / "session_manifest.json")
    if manifest.get("mode") != "counterfactual" or manifest.get("quote_coupling") != "shared":
        raise DatasetError("dataset must be counterfactual with shared quote coupling")
    expected_runs = int(manifest["runs"])
    expected_posts = int(manifest["posts_per_run"])
    spacing_required = int(manifest["effective_config"]["GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN"])
    snapshot_manifest = read_json(session_dir / "input_snapshot" / "manifest.json")
    originals = set(path.name for path in (session_dir / "input_snapshot" / "images").glob("*.jpg"))
    generated = set(path.name for path in (session_dir / "input_snapshot" / "generated_images").glob("*.png"))
    run_dirs = sorted((session_dir / "counterfactual" / "runs").glob("run_*"))
    if len(run_dirs) != expected_runs and not allow_incomplete:
        raise DatasetError(f"expected {expected_runs} runs, found {len(run_dirs)}")
    runs: dict[str, dict] = {}
    errors: list[str] = []
    for ordinal, run_dir in enumerate(run_dirs):
        run_id = f"run_{ordinal:04d}"
        if run_dir.name != run_id:
            raise DatasetError(f"unexpected run directory order/name: {run_dir.name}")
        paths = run_paths(run_dir)
        missing_files = [str(path) for path in paths.values() if not path.is_file()]
        if missing_files:
            raise DatasetError(f"{run_id}: missing files: {missing_files}")
        shared = read_jsonl(paths["shared"])
        comparisons = read_jsonl(paths["comparison"])
        branches = {branch: read_jsonl(paths[branch]) for branch in BRANCHES}
        expected_here = expected_posts if not allow_incomplete else len(shared)
        for label, rows in [("shared", shared), ("comparison", comparisons), *branches.items()]:
            validate_indices(rows, expected_here, f"{run_id}/{label}")
        checkpoint = read_json(paths["checkpoint"])
        if checkpoint.get("completed_posts") != expected_here:
            raise DatasetError(f"{run_id}: checkpoint completed_posts mismatch")
        seed = int(manifest["seed_base"]) + ordinal
        prior_timestamps: dict[str, int | None] = {name: None for name in ("shared", *BRANCHES)}
        prior_winner: dict[str, str | None] = {branch: None for branch in BRANCHES}
        cycle_position = {branch: 0 for branch in BRANCHES}
        for offset in range(expected_here):
            index = offset + 1
            quote = shared[offset]
            comparison = comparisons[offset]
            qhash = quote.get("quote_hash")
            timestamp = int(quote.get("virtual_timestamp"))
            if prior_timestamps["shared"] is not None and timestamp <= prior_timestamps["shared"]:
                raise DatasetError(f"{run_id}/{index}: non-monotonic virtual timestamp")
            prior_timestamps["shared"] = timestamp
            if comparison.get("quote_hash") != qhash or comparison.get("virtual_timestamp") != timestamp:
                raise DatasetError(f"{run_id}/{index}: comparison/shared quote mismatch")
            if comparison.get("run_seed") != seed:
                raise DatasetError(f"{run_id}/{index}: comparison seed mismatch")
            winners = {}
            sources = {}
            for branch in BRANCHES:
                row = branches[branch][offset]
                if row.get("branch") != branch:
                    raise DatasetError(f"{run_id}/{index}: invalid branch {row.get('branch')!r}")
                if row.get("run_seed") != seed or row.get("virtual_timestamp") != timestamp:
                    raise DatasetError(f"{run_id}/{index}/{branch}: seed/time mismatch")
                finite_number(row.get("baseline_score"), f"{run_id}/{index}/{branch}/baseline_score")
                finite_number(row.get("policy_score"), f"{run_id}/{index}/{branch}/policy_score")
                winner, source = row.get("winner"), row.get("winner_source")
                if source == "original" and winner not in originals:
                    raise DatasetError(f"{run_id}/{index}/{branch}: unknown original winner {winner}")
                if source == "generated" and winner not in generated:
                    raise DatasetError(f"{run_id}/{index}/{branch}: unknown generated winner {winner}")
                if source not in {"original", "generated"}:
                    raise DatasetError(f"{run_id}/{index}/{branch}: invalid winner source {source}")
                candidate_count = int(row.get("candidate_count", -1))
                if candidate_count < 1 or int(row.get("original_candidate_count", -1)) + int(row.get("generated_candidate_count", -1)) != candidate_count:
                    raise DatasetError(f"{run_id}/{index}/{branch}: incoherent candidate counts")
                before, after = int(row["generated_spacing_counter_before"]), int(row["generated_spacing_counter_after"])
                wanted_after = 0 if source == "generated" else min(spacing_required, before + 1)
                if after != wanted_after:
                    raise DatasetError(f"{run_id}/{index}/{branch}: invalid spacing {before}->{after}, expected {wanted_after}")
                cycle_position[branch] = 1 if row.get("image_cycle_reset") else cycle_position[branch] + 1
                row["_prior_winner"] = prior_winner[branch]
                row["_cycle_position"] = cycle_position[branch]
                prior_winner[branch] = winner
                winners[branch], sources[branch] = winner, source
            if comparison.get("winners") != winners or comparison.get("winner_sources") != sources:
                raise DatasetError(f"{run_id}/{index}: comparison winner mismatch")
            expected_flags = {
                "production_editorial_same": winners["production"] == winners["editorial"],
                "production_identity_same": winners["production"] == winners["identity"],
                "editorial_identity_same": winners["editorial"] == winners["identity"],
                "all_three_same": len(set(winners.values())) == 1,
            }
            for key, value in expected_flags.items():
                if comparison.get(key) is not value:
                    raise DatasetError(f"{run_id}/{index}: incorrect {key}")
        runs[run_id] = {"seed": seed, "shared": shared, "comparison": comparisons, "branches": branches, "checkpoint": checkpoint, "paths": paths}
    return {"session_dir": session_dir, "manifest": manifest, "snapshot_manifest": snapshot_manifest, "runs": runs, "originals": originals, "generated": generated, "errors": errors}


def divergence_episodes(flags: list[bool], run_id: str, pair: str) -> list[dict]:
    """Return the divergence episodes."""
    episodes = []
    start = None
    for index, same in enumerate(flags, 1):
        if not same and start is None:
            start = index
        if same and start is not None:
            episodes.append({"run_id": run_id, "pair": pair, "start_index": start, "end_index": index - 1, "length": index - start, "reconverged": True, "first_reconvergence_index": index})
            start = None
    if start is not None:
        episodes.append({"run_id": run_id, "pair": pair, "start_index": start, "end_index": len(flags), "length": len(flags) - start + 1, "reconverged": False, "first_reconvergence_index": None})
    return episodes


def reuse_intervals(rows: list[dict]) -> list[int]:
    """Return the reuse intervals."""
    previous = {}
    intervals = []
    for row in rows:
        winner, index = row["winner"], int(row["post_index"])
        if winner in previous:
            intervals.append(index - previous[winner])
        previous[winner] = index
    return intervals


def analyse(dataset: dict, top_n: int = 10, image_filter: str | None = None, quote_filter: str | None = None) -> dict:
    """Analyse the configured artefacts."""
    all_rows = {branch: [] for branch in BRANCHES}
    comparisons = []
    shared = []
    episodes = []
    same_winner_different_state = []
    run_metrics = []
    source_transitions = {"production_editorial": Counter(), "production_identity": Counter()}
    basename_transitions = {"production_editorial": Counter(), "production_identity": Counter()}
    for run_id, run in dataset["runs"].items():
        for branch in BRANCHES:
            all_rows[branch].extend(run["branches"][branch])
        comparisons.extend(run["comparison"])
        shared.extend(run["shared"])
        pe_flags = [row["production_editorial_same"] for row in run["comparison"]]
        pi_flags = [row["production_identity_same"] for row in run["comparison"]]
        ei_flags = [row["editorial_identity_same"] for row in run["comparison"]]
        episodes.extend(divergence_episodes(pe_flags, run_id, "production_editorial"))
        episodes.extend(divergence_episodes(pi_flags, run_id, "production_identity"))
        episodes.extend(divergence_episodes(ei_flags, run_id, "editorial_identity"))
        run_metrics.append({
            "run_id": run_id,
            "production_editorial_divergence_rate": sum(not flag for flag in pe_flags) / len(pe_flags),
            "production_identity_divergence_rate": sum(not flag for flag in pi_flags) / len(pi_flags),
            "editorial_identity_divergence_rate": sum(not flag for flag in ei_flags) / len(ei_flags),
            "all_three_agreement_rate": sum(row["all_three_same"] for row in run["comparison"]) / len(run["comparison"]),
        })
        for offset, comparison in enumerate(run["comparison"]):
            for left, right, pair in (("production", "editorial", "production_editorial"), ("production", "identity", "production_identity")):
                lrow, rrow = run["branches"][left][offset], run["branches"][right][offset]
                source_transitions[pair][(lrow["winner_source"], rrow["winner_source"])] += 1
                if lrow["winner"] != rrow["winner"]:
                    basename_transitions[pair][(lrow["winner"], rrow["winner"])] += 1
                else:
                    lsig = (lrow["_prior_winner"], lrow["generated_spacing_counter_before"], lrow["_cycle_position"])
                    rsig = (rrow["_prior_winner"], rrow["generated_spacing_counter_before"], rrow["_cycle_position"])
                    if lsig != rsig:
                        same_winner_different_state.append({"run_id": run_id, "post_index": offset + 1, "pair": pair, "winner": lrow["winner"], "left_signature": lsig, "right_signature": rsig})
    counts = {branch: Counter(row["winner"] for row in all_rows[branch]) for branch in BRANCHES}
    branch_metrics = {}
    for branch, rows in all_rows.items():
        intervals = []
        for run_id, run in dataset["runs"].items():
            intervals.extend(reuse_intervals(run["branches"][branch]))
        source_counts = Counter(row["winner_source"] for row in rows)
        origin = sum(row["winner_source"] == "generated" and row["origin_quote_match"] for row in rows)
        branch_metrics[branch] = {
            "selections": len(rows), "source_counts": dict(sorted(source_counts.items())),
            "generated_origin_quote": origin, "generated_cross_quote": source_counts["generated"] - origin,
            "unique_images": len(counts[branch]), "entropy_bits": entropy(counts[branch]),
            "top_5_share": top_share(counts[branch], 5), "top_10_share": top_share(counts[branch], 10),
            "maximum_image_count": max(counts[branch].values()), "most_selected": counts[branch].most_common(top_n),
            "reuse_intervals": distribution(intervals), "suspicious_reuse_under_3": sum(value < 3 for value in intervals),
            "cycle_resets": sum(bool(row["image_cycle_reset"]) for row in rows),
            "selection_phases": dict(sorted(Counter(row["selection_phase"] for row in rows).items())),
        }
    pe_changes = [row for row in comparisons if not row["production_editorial_same"]]
    pi_changes = [row for row in comparisons if not row["production_identity_same"]]
    editorial_adjustments = [row["editorial_adjustment"] for row in all_rows["editorial"] if row["winner_source"] == "original"]
    identity_actions = Counter(row["production_candidate_identity_action"] for row in comparisons if not row["production_identity_same"])
    image_rows = []
    all_images = sorted(set().union(*[set(counter) for counter in counts.values()]))
    for image in all_images:
        image_rows.append({
            "image": image,
            **{f"{branch}_selections": counts[branch][image] for branch in BRANCHES},
            "editorial_net_vs_production": counts["editorial"][image] - counts["production"][image],
            "identity_net_vs_production": counts["identity"][image] - counts["production"][image],
        })
    quote_rows = []
    for comparison in comparisons:
        quote_rows.append({
            "run_id": comparison["run_id"], "post_index": comparison["post_index"],
            "quote_hash": comparison["quote_hash"], "quote_text": comparison["quote_text"],
            "production": comparison["winners"]["production"], "editorial": comparison["winners"]["editorial"], "identity": comparison["winners"]["identity"],
            "production_editorial_same": comparison["production_editorial_same"], "production_identity_same": comparison["production_identity_same"],
            "both_policies_same_alternative": comparison["winners"]["editorial"] == comparison["winners"]["identity"] != comparison["winners"]["production"],
        })
    diagnostic = {}
    for image in (DIAGNOSTIC, "t10.jpg", "t18.jpg"):
        diagnostic[image] = {}
        for branch, rows in all_rows.items():
            selected = [row for row in rows if row["winner"] == image]
            present = [row for row in rows if row.get("diagnostic_candidate_present")] if image == DIAGNOSTIC else []
            diagnostic[image][branch] = {
                "selections": len(selected), "selection_indices": [[row["run_id"], row["post_index"]] for row in selected],
                "candidate_appearances": len(present) if image == DIAGNOSTIC else None,
                "origin_quote_appearances": sum(bool(row.get("diagnostic_candidate_origin_match")) for row in present) if image == DIAGNOSTIC else None,
                "cross_quote_appearances": sum(not bool(row.get("diagnostic_candidate_origin_match")) for row in present) if image == DIAGNOSTIC else None,
                "identity_exclusions": sum(bool(row.get("diagnostic_candidate_excluded")) for row in present) if image == DIAGNOSTIC else None,
            }
    case_studies = build_case_studies(dataset, top_n)
    metrics = {
        "schema_version": ANALYSIS_SCHEMA,
        "totals": {"runs": len(dataset["runs"]), "post_indices": len(comparisons), "branch_selections": sum(len(rows) for rows in all_rows.values())},
        "agreement": {
            "production_editorial": sum(row["production_editorial_same"] for row in comparisons) / len(comparisons),
            "production_identity": sum(row["production_identity_same"] for row in comparisons) / len(comparisons),
            "editorial_identity": sum(row["editorial_identity_same"] for row in comparisons) / len(comparisons),
            "all_three": sum(row["all_three_same"] for row in comparisons) / len(comparisons),
        },
        "divergence": {
            "production_editorial_count": len(pe_changes), "production_editorial_rate": len(pe_changes) / len(comparisons),
            "production_identity_count": len(pi_changes), "production_identity_rate": len(pi_changes) / len(comparisons),
            "editorial_identity_count": sum(not row["editorial_identity_same"] for row in comparisons),
            "episode_counts": dict(sorted(Counter(row["pair"] for row in episodes).items())),
            "episode_lengths": {pair: distribution([row["length"] for row in episodes if row["pair"] == pair]) for pair in ("production_editorial", "production_identity", "editorial_identity")},
            "reconvergences": {pair: sum(row["reconverged"] for row in episodes if row["pair"] == pair) for pair in ("production_editorial", "production_identity", "editorial_identity")},
            "same_winner_different_observable_state": len(same_winner_different_state),
        },
        "branches": branch_metrics,
        "editorial": {
            "selected_original_adjustments": distribution(editorial_adjustments),
            "selected_cap_hits": sum(bool(row["editorial_cap_hit"]) for row in all_rows["editorial"]),
            "replacement_source": dict(sorted(Counter((dataset["runs"][row["run_id"]]["branches"]["editorial"][row["post_index"]-1]["winner_source"]) for row in pe_changes).items())),
            "top_net_gainers": sorted(image_rows, key=lambda row: (-row["editorial_net_vs_production"], row["image"]))[:top_n],
            "top_net_losers": sorted(image_rows, key=lambda row: (row["editorial_net_vs_production"], row["image"]))[:top_n],
        },
        "identity": {
            "changed_production_actions": dict(sorted(identity_actions.items())),
            "production_winner_origin_only_prevented": sum(row["production_candidate_identity_excluded"] and not row["production_identity_same"] for row in comparisons),
            "excluded_candidate_appearances": sum(row["policy_excluded_count"] for row in all_rows["identity"]),
            "replacement_source": dict(sorted(Counter(dataset["runs"][row["run_id"]]["branches"]["identity"][row["post_index"]-1]["winner_source"] for row in pi_changes).items())),
            "no_valid_candidate_failures": 0,
        },
        "run_variability": {key: distribution([row[key] for row in run_metrics]) for key in run_metrics[0] if key != "run_id"},
        "source_transitions": {pair: {f"{left}->{right}": count for (left, right), count in sorted(counter.items())} for pair, counter in source_transitions.items()},
        "diagnostic_images": diagnostic,
        "limitations": {
            "runner_up_score_margins": "unavailable: canonical run used candidate_detail=none",
            "full_per_index_state": "unavailable: compact rows permit observable-state comparison, not complete history equality",
            "candidate_absence_reasons": "unavailable unless trace-image/candidate detail was recorded",
        },
    }
    return {"metrics": metrics, "image_rows": image_rows, "quote_rows": quote_rows, "comparisons": comparisons, "episodes": episodes, "same_state": same_winner_different_state, "source_transitions": source_transitions, "basename_transitions": basename_transitions, "case_studies": case_studies, "run_metrics": run_metrics}


def build_case_studies(dataset: dict, top_n: int) -> dict:
    """Build case studies."""
    editorial, identity = [], []
    origin_only = []
    both_same = []
    for run_id, run in dataset["runs"].items():
        for offset, comparison in enumerate(run["comparison"]):
            p, e, i = (run["branches"][branch][offset] for branch in BRANCHES)
            base = {"run_id": run_id, "post_index": offset + 1, "virtual_timestamp": comparison["virtual_timestamp"], "quote_hash": comparison["quote_hash"], "quote_text": comparison["quote_text"],
                    "production_winner": p["winner"], "production_source": p["winner_source"], "production_score": p["policy_score"],
                    "editorial_winner": e["winner"], "editorial_source": e["winner_source"], "editorial_score": e["policy_score"], "editorial_adjustment": e["editorial_adjustment"],
                    "identity_winner": i["winner"], "identity_source": i["winner_source"], "identity_score": i["policy_score"], "identity_action_on_production": comparison["production_candidate_identity_action"],
                    "candidate_counts": {branch: run["branches"][branch][offset]["candidate_count"] for branch in BRANCHES},
                    "selection_phases": {branch: run["branches"][branch][offset]["selection_phase"] for branch in BRANCHES}}
            if p["winner"] != e["winner"]:
                editorial.append({**base, "selected_score_gap": abs(float(e["policy_score"]) - float(p["policy_score"]))})
            if p["winner"] != i["winner"]:
                identity.append({**base, "selected_score_gap": abs(float(i["policy_score"]) - float(p["policy_score"]))})
            if comparison["production_candidate_identity_excluded"]:
                origin_only.append(base)
            if e["winner"] == i["winner"] != p["winner"]:
                both_same.append(base)
    return {
        "editorial_largest_selected_score_gap": sorted(editorial, key=lambda row: (-row["selected_score_gap"], row["run_id"], row["post_index"]))[:top_n],
        "editorial_smallest_selected_score_gap": sorted(editorial, key=lambda row: (row["selected_score_gap"], row["run_id"], row["post_index"]))[:top_n],
        "identity_largest_selected_score_gap": sorted(identity, key=lambda row: (-row["selected_score_gap"], row["run_id"], row["post_index"]))[:top_n],
        "production_origin_quote_only_cross_quote": origin_only,
        "identity_generated_to_original": [row for row in identity if row["production_source"] == "generated" and row["identity_source"] == "original"],
        "identity_generated_to_generated": [row for row in identity if row["production_source"] == row["identity_source"] == "generated"],
        "editorial_original_to_original": [row for row in editorial if row["production_source"] == row["editorial_source"] == "original"],
        "both_policies_same_alternative": both_same,
        "margin_note": "selected_score_gap compares selected branch winners across independently evolved candidate sets; runner-up margins are unavailable",
    }


def atomic_write(path: Path, data: bytes) -> None:
    """Perform the atomic write operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    """Write JSON."""
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode())


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write CSV."""
    if not rows:
        atomic_write(path, b"")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def make_manifest(dataset: dict, manifest_path: Path, overwrite: bool = False) -> dict:
    """Create manifest."""
    if manifest_path.exists() and not overwrite:
        existing = read_json(manifest_path)
        validate_manifest_hashes(existing, dataset["session_dir"])
        return existing
    root = dataset["session_dir"]
    files = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    aggregate_input = "".join(f"{row['path']}\0{row['size']}\0{row['sha256']}\n" for row in files).encode()
    session, snapshot = dataset["manifest"], dataset["snapshot_manifest"]
    manifest = {
        "schema_version": 1, "canonical_dataset_name": "canonical_5000_shared_quote_counterfactual_v1",
        "simulation_session_id": session["session_id"], "absolute_path": str(root), "project_relative_path": root.relative_to(Path.cwd().resolve()).as_posix(),
        "created_at": session["created_at"], "frozen_at": datetime.now(timezone.utc).isoformat(),
        "simulator_git_commit": snapshot["git_commit"], "simulator_source_sha256": session["simulator_source_sha256"], "production_source_sha256": session["production_source_sha256"],
        "simulator_schema_version": session["schema_version"], "snapshot_id": session["snapshot_manifest_sha256"], "snapshot_hashes": snapshot["sha256"],
        "start_time": session["start_local_time"], "quote_coupling": session["quote_coupling"], "run_count": session["runs"], "posts_per_run": session["posts_per_run"],
        "expected_post_indices": session["runs"] * session["posts_per_run"], "expected_branch_count": 3, "expected_selection_count": session["runs"] * session["posts_per_run"] * 3,
        "seed_base": session["seed_base"], "per_run_seeds": [session["seed_base"] + i for i in range(session["runs"])], "branch_names": list(BRANCHES),
        "effective_editorial_config": {key: session["effective_config"][key] for key in ("ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT")},
        "effective_identity_config": {key: session["effective_config"][key] for key in ("GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY")},
        "original_image_count": snapshot["original_image_count"], "generated_image_count": snapshot["generated_image_count"], "quote_count": snapshot["quote_count"],
        "runtime_seconds": session["accumulated_runtime_seconds"], "files": files, "aggregate_sha256": hashlib.sha256(aggregate_input).hexdigest(),
    }
    write_json(manifest_path, manifest)
    return manifest


def validate_manifest_hashes(manifest: dict, session_dir: Path) -> None:
    """Validate manifest hashes."""
    lines = []
    for row in manifest.get("files", []):
        path = session_dir / row["path"]
        if not path.is_file() or path.stat().st_size != row["size"] or sha256_file(path) != row["sha256"]:
            raise DatasetError(f"canonical manifest hash mismatch: {row['path']}")
        lines.append(f"{row['path']}\0{row['size']}\0{row['sha256']}\n")
    if hashlib.sha256("".join(lines).encode()).hexdigest() != manifest.get("aggregate_sha256"):
        raise DatasetError("canonical aggregate hash mismatch")


def render_bar_chart(path: Path, title: str, labels: list[str], values: list[float]) -> None:
    """Render bar chart."""
    from PIL import Image, ImageDraw, ImageFont
    width, height = 1000, 560
    image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image); font = ImageFont.load_default()
    draw.text((30, 20), title, fill="#111111", font=font)
    maximum = max(values) if values else 1
    chart_left, chart_right, chart_top, chart_bottom = 180, 960, 60, 520
    row_height = (chart_bottom - chart_top) / max(1, len(labels))
    colours = ("#28536b", "#c66b3d", "#3f7d5c", "#7a5c99")
    for index, (label, value) in enumerate(zip(labels, values)):
        y = chart_top + index * row_height
        draw.text((20, int(y + 5)), label[:25], fill="#111111", font=font)
        bar_width = (chart_right - chart_left) * value / maximum if maximum else 0
        draw.rectangle((chart_left, int(y + 3), chart_left + bar_width, int(y + row_height - 5)), fill=colours[index % len(colours)])
        draw.text((chart_left + bar_width + 5, int(y + 5)), f"{value:.4g}", fill="#111111", font=font)
    image.save(path)


def make_charts(output_dir: Path, result: dict) -> list[str]:
    """Create charts."""
    charts = output_dir / "charts"; charts.mkdir(parents=True, exist_ok=True)
    metrics = result["metrics"]
    paths = []
    specs = [
        ("branch_generated_share.png", "Generated winner share by branch", list(BRANCHES), [metrics["branches"][b]["source_counts"].get("generated", 0) / metrics["branches"][b]["selections"] for b in BRANCHES]),
        ("branch_entropy.png", "Winner entropy (bits) by branch", list(BRANCHES), [metrics["branches"][b]["entropy_bits"] for b in BRANCHES]),
        ("branch_top10_share.png", "Top-10 winner share by branch", list(BRANCHES), [metrics["branches"][b]["top_10_share"] for b in BRANCHES]),
        ("divergence_rate_by_run.png", "Divergence rate by run", [row["run_id"] for row in result["run_metrics"]], [row["production_editorial_divergence_rate"] for row in result["run_metrics"]]),
        ("divergence_episode_lengths.png", "Divergence episode length (posts)", ["prod/editorial", "prod/identity", "editorial/identity"], [metrics["divergence"]["episode_lengths"][pair]["mean"] for pair in ("production_editorial", "production_identity", "editorial_identity")]),
        ("cycle_resets.png", "Image-cycle resets by branch", list(BRANCHES), [metrics["branches"][b]["cycle_resets"] for b in BRANCHES]),
    ]
    for filename, title, labels, values in specs:
        path = charts / filename; render_bar_chart(path, title, labels, values); paths.append(path.relative_to(output_dir).as_posix())
    gainers = metrics["editorial"]["top_net_gainers"]
    path = charts / "top_editorial_net_gainers.png"
    render_bar_chart(path, "Top editorial net selection gains", [row["image"] for row in gainers], [row["editorial_net_vs_production"] for row in gainers]); paths.append(path.relative_to(output_dir).as_posix())
    return paths


def make_contact_sheet(path: Path, title: str, images: list[tuple[str, str]], snapshot: Path) -> None:
    """Create contact sheet."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(); tile_w, tile_h = 250, 220; cols = 4; rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (cols * tile_w, 35 + rows * tile_h), "white"); draw = ImageDraw.Draw(sheet); draw.text((10, 10), title, fill="black", font=font)
    for index, (basename, label) in enumerate(images):
        source = snapshot / ("generated_images" if basename.startswith("tg_") else "images") / basename
        tile = Image.open(source).convert("RGB"); tile.thumbnail((230, 175))
        x, y = (index % cols) * tile_w + 10, 35 + (index // cols) * tile_h
        sheet.paste(tile, (x + (230 - tile.width)//2, y)); draw.text((x, y + 180), basename[:34], fill="black", font=font); draw.text((x, y + 194), label[:38], fill="black", font=font)
    sheet.save(path)


def make_contact_sheets(output_dir: Path, result: dict, snapshot: Path) -> list[str]:
    """Create contact sheets."""
    directory = output_dir / "contact_sheets"; directory.mkdir(parents=True, exist_ok=True); outputs = []
    groups = {
        "editorial_gainers.png": [(row["image"], f"net {row['editorial_net_vs_production']:+d}") for row in result["metrics"]["editorial"]["top_net_gainers"][:8]],
        "editorial_losers.png": [(row["image"], f"net {row['editorial_net_vs_production']:+d}") for row in result["metrics"]["editorial"]["top_net_losers"][:8]],
        "diagnostic_images.png": [("t10.jpg", "editorial diagnostic"), ("t18.jpg", "editorial diagnostic"), (DIAGNOSTIC, "identity diagnostic")],
    }
    for filename, images in groups.items():
        path = directory / filename; make_contact_sheet(path, filename[:-4].replace("_", " ").title(), images, snapshot); outputs.append(path.relative_to(output_dir).as_posix())
    return outputs


def markdown_report(dataset: dict, manifest: dict, result: dict) -> str:
    """Return the markdown report."""
    m = result["metrics"]
    return f"""# Counterfactual canonical dataset analysis

## Executive verdict

The canonical session validates as complete and internally coherent: {m['totals']['runs']} runs, {m['totals']['post_indices']:,} shared-quote indices, and {m['totals']['branch_selections']:,} branch selections. No trajectory-invalidating defect was found, so the expensive simulation was not rerun.

## Dataset

- Session: `{manifest['simulation_session_id']}`
- Aggregate SHA-256: `{manifest['aggregate_sha256']}`
- Simulator snapshot commit: `{manifest['simulator_git_commit']}`
- Runtime recorded by session: {manifest['runtime_seconds']:.3f} seconds

## Recalculated headline metrics

| metric | value |
|---|---:|
| Production/editorial agreement | {m['agreement']['production_editorial']:.2%} |
| Production/identity agreement | {m['agreement']['production_identity']:.2%} |
| Editorial/identity agreement | {m['agreement']['editorial_identity']:.2%} |
| All-three agreement | {m['agreement']['all_three']:.2%} |
| Editorial divergence | {m['divergence']['production_editorial_rate']:.2%} ({m['divergence']['production_editorial_count']:,}) |
| Identity divergence | {m['divergence']['production_identity_rate']:.2%} ({m['divergence']['production_identity_count']:,}) |

## Diversity

| branch | original | generated | entropy | top-10 share | unique | resets |
|---|---:|---:|---:|---:|---:|---:|
""" + "\n".join(
        f"| {branch} | {m['branches'][branch]['source_counts'].get('original', 0):,} | {m['branches'][branch]['source_counts'].get('generated', 0):,} | {m['branches'][branch]['entropy_bits']:.4f} | {m['branches'][branch]['top_10_share']:.2%} | {m['branches'][branch]['unique_images']} | {m['branches'][branch]['cycle_resets']} |"
        for branch in BRANCHES
    ) + f"""

## Divergence and reconvergence

Production/editorial produced {m['divergence']['episode_counts'].get('production_editorial', 0):,} divergence episodes, of which {m['divergence']['reconvergences']['production_editorial']:,} later reconverged. Production/identity produced {m['divergence']['episode_counts'].get('production_identity', 0):,} episodes, of which {m['divergence']['reconvergences']['production_identity']:,} reconverged. There were {m['divergence']['same_winner_different_observable_state']:,} same-winner cases where compact-record state signatures still differed.

## Evidence limits

The canonical run used `candidate_detail=none`. Exact winner-versus-runner-up margins, full candidate rankings, full per-index history equality, and reasons for candidate absence cannot be reconstructed. Case-study `selected_score_gap` values compare selected winners from independently evolved branch candidate sets; they are not runner-up margins.

## Diagnostic images

`t10.jpg` selections: production {m['diagnostic_images']['t10.jpg']['production']['selections']}, editorial {m['diagnostic_images']['t10.jpg']['editorial']['selections']}, identity {m['diagnostic_images']['t10.jpg']['identity']['selections']}.

`t18.jpg` selections: production {m['diagnostic_images']['t18.jpg']['production']['selections']}, editorial {m['diagnostic_images']['t18.jpg']['editorial']['selections']}, identity {m['diagnostic_images']['t18.jpg']['identity']['selections']}.

`{DIAGNOSTIC}` appeared in final candidate sets {m['diagnostic_images'][DIAGNOSTIC]['production']['candidate_appearances']}/{m['diagnostic_images'][DIAGNOSTIC]['editorial']['candidate_appearances']}/{m['diagnostic_images'][DIAGNOSTIC]['identity']['candidate_appearances']} times for production/editorial/identity; identity excluded it {m['diagnostic_images'][DIAGNOSTIC]['identity']['identity_exclusions']} times and no branch selected it.

## Interpretation

This is a counterfactual selector simulation over the current corpus, analyses, shared quote sequence, deterministic random conditions, and branch-local image state. It is not a real-world A/B test and does not predict engagement, audience behaviour, future assets, or future code.
"""


def output_hashes(output_dir: Path) -> dict:
    """Return the output hashes."""
    return {path.relative_to(output_dir).as_posix(): sha256_file(path) for path in sorted(output_dir.rglob("*")) if path.is_file() and path.name != "output_hashes.json"}


def run_analysis(args: argparse.Namespace) -> dict:
    """Run analysis."""
    session_dir = args.session_dir.resolve(); output_dir = args.output_dir.resolve()
    if output_dir == session_dir or session_dir in output_dir.parents:
        raise DatasetError("output directory must not be inside the source session")
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite and not args.validate_only:
        raise FileExistsError(f"output directory exists and is non-empty: {output_dir}")
    dataset = load_and_validate(session_dir, args.allow_incomplete)
    if args.manifest:
        manifest_path = args.manifest.resolve()
    else:
        manifest_path = Path.cwd() / "counterfactual_canonical_dataset_manifest.json"
    manifest = make_manifest(dataset, manifest_path, args.overwrite_manifest)
    validate_manifest_hashes(manifest, session_dir)
    result = analyse(dataset, args.top_n, args.image, args.quote_hash)
    validation = {"valid": True, "errors": [], "runs": len(dataset["runs"]), "post_indices": result["metrics"]["totals"]["post_indices"], "branch_selections": result["metrics"]["totals"]["branch_selections"], "aggregate_sha256": manifest["aggregate_sha256"]}
    if args.validate_only:
        print(json.dumps(validation, sort_keys=True)); return validation
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "validation_report.json", validation)
    write_json(output_dir / "analysis_summary.json", result["metrics"])
    write_json(output_dir / "detailed_metrics.json", {"same_winner_different_state": result["same_state"], "basename_transitions": {key: [{"from": pair[0], "to": pair[1], "count": count} for pair, count in counter.most_common()] for key, counter in result["basename_transitions"].items()}, "run_metrics": result["run_metrics"]})
    write_json(output_dir / "case_studies.json", result["case_studies"])
    write_csv(output_dir / "image_metrics.csv", result["image_rows"])
    write_csv(output_dir / "quote_metrics.csv", result["quote_rows"])
    write_csv(output_dir / "divergence_events.csv", [row for row in result["quote_rows"] if not row["production_editorial_same"] or not row["production_identity_same"]])
    write_csv(output_dir / "divergence_episodes.csv", result["episodes"])
    transition_rows = [{"pair": pair, "from_source": source[0], "to_source": source[1], "count": count} for pair, counter in result["source_transitions"].items() for source, count in sorted(counter.items())]
    write_csv(output_dir / "transition_matrix.csv", transition_rows)
    chart_paths = make_charts(output_dir, result) if args.make_charts else []
    contact_paths = make_contact_sheets(output_dir, result, session_dir / "input_snapshot") if args.make_contact_sheets else []
    write_json(output_dir / "analysis_assets.json", {"charts": chart_paths, "contact_sheets": contact_paths})
    atomic_write(output_dir / "analysis_report.md", markdown_report(dataset, manifest, result).encode())
    write_json(output_dir / "output_hashes.json", output_hashes(output_dir))
    return validation


def parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    value = argparse.ArgumentParser(description="Validate and analyse completed counterfactual simulation records. Never invokes simulation or network services.")
    value.add_argument("--session-dir", type=Path, required=True)
    value.add_argument("--manifest", type=Path)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--validate-only", action="store_true")
    value.add_argument("--run-filter", action="append", default=[])
    value.add_argument("--image")
    value.add_argument("--quote-hash")
    value.add_argument("--top-n", type=int, default=10)
    value.add_argument("--overwrite", action="store_true")
    value.add_argument("--overwrite-manifest", action="store_true")
    value.add_argument("--allow-incomplete", action="store_true")
    value.add_argument("--export-csv", action="store_true", default=True)
    value.add_argument("--make-charts", action="store_true")
    value.add_argument("--make-contact-sheets", action="store_true")
    return value


def main() -> int:
    """Run the command-line entry point."""
    args = parser().parse_args()
    try:
        run_analysis(args)
    except (DatasetError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
