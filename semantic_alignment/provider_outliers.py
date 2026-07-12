from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

PROVIDERS = ("grok", "openai", "anthropic", "gemini")
SCORES = (
    "relevance_score", "directness_score", "mechanism_alignment_score",
    "consequence_alignment_score", "principle_alignment_score",
    "specificity_score", "editorial_power_score", "overall_suitability_score",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def classify_votes(votes: dict[str, str | None]) -> dict[str, Any]:
    available = {p: v for p, v in votes.items() if v in {"keep", "replace", "unsure"}}
    counts = Counter(available.values())
    result: dict[str, Any] = {
        "available": len(available), "counts": dict(counts),
        "outlier_provider": None, "outlier_vote": None,
    }
    if len(available) < 4:
        result["pattern"] = "incomplete_provider_set"
        return result
    if counts.get("unsure"):
        result["pattern"] = "mixed_with_unsure"
        return result
    keep, replace = counts.get("keep", 0), counts.get("replace", 0)
    if keep == 4:
        result["pattern"] = "4_keep"
    elif replace == 4:
        result["pattern"] = "4_replace"
    elif keep == 3:
        result["pattern"] = "3_keep_1_replace"
        result["outlier_vote"] = "replace"
    elif replace == 3:
        result["pattern"] = "3_replace_1_keep"
        result["outlier_vote"] = "keep"
    elif keep == replace == 2:
        result["pattern"] = "2_keep_2_replace"
    else:
        result["pattern"] = "mixed_with_unsure"
    if result["outlier_vote"]:
        result["outlier_provider"] = next(
            p for p, vote in available.items() if vote == result["outlier_vote"]
        )
    return result


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total == 0:
        return None
    p = successes / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def score_profile(outlier: dict[str, Any], majority: Iterable[dict[str, Any]]) -> dict[str, Any]:
    majority = list(majority)
    profile = {}
    for field in SCORES:
        values = [float(item[field]) for item in majority]
        median = statistics.median(values)
        profile[field] = {
            "outlier": float(outlier[field]),
            "majority_median": median,
            "difference": float(outlier[field]) - median,
            "absolute_difference": abs(float(outlier[field]) - median),
        }
    return profile


def summarise_differences(cases: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for field in SCORES:
        values = [case["score_profile"][field]["difference"] for case in cases]
        if not values:
            result[field] = {"count": 0}
            continue
        ordered = sorted(values)
        q = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 3
        result[field] = {
            "count": len(values), "median_difference": statistics.median(values),
            "mean_absolute_difference": statistics.mean(abs(x) for x in values),
            "q1": q[0], "q3": q[2], "minimum": min(values), "maximum": max(values),
        }
    return result


def human_metrics(results: dict[str, dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case_id, label in labels.items():
        human = label.get("human_action")
        if human not in {"keep", "replace"} or case_id not in results:
            continue
        predicted = results[case_id]["keep_or_replace"]
        rows.append((predicted, human))
    correct = sum(a == b for a, b in rows)
    false_keep = sum(a == "keep" and b == "replace" for a, b in rows)
    false_replace = sum(a == "replace" and b == "keep" for a, b in rows)
    true_keep = sum(a == b == "keep" for a, b in rows)
    true_replace = sum(a == b == "replace" for a, b in rows)
    return {
        "labelled": len(rows), "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
        "accuracy_95ci": wilson_interval(correct, len(rows)),
        "false_keep": false_keep, "false_replace": false_replace,
        "keep_precision": true_keep / (true_keep + false_keep) if true_keep + false_keep else None,
        "keep_recall": true_keep / (true_keep + false_replace) if true_keep + false_replace else None,
        "replace_precision": true_replace / (true_replace + false_replace) if true_replace + false_replace else None,
        "replace_recall": true_replace / (true_replace + false_keep) if true_replace + false_keep else None,
    }


def compare_regression(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]], case_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for case_id in sorted(case_ids):
        if case_id not in old or case_id not in new:
            rows.append({"case_id": case_id, "missing": True})
            continue
        a, b = old[case_id], new[case_id]
        row = {
            "case_id": case_id, "missing": False,
            "old_decision": a["keep_or_replace"], "new_decision": b["keep_or_replace"],
            "decision_change": f'{a["keep_or_replace"]}_to_{b["keep_or_replace"]}',
            "old_relationship": a["primary_relationship"], "new_relationship": b["primary_relationship"],
            "taxonomy_changed": a["primary_relationship"] != b["primary_relationship"],
            "model_changed": a.get("model") != b.get("model"),
        }
        for field in SCORES:
            row[f"{field}_shift"] = b[field] - a[field]
        rows.append(row)
    return rows


def sensitivity(completed_keep: int, completed: int, missing: int) -> list[dict[str, Any]]:
    observed = completed_keep / completed if completed else 0
    expected_missing = round(missing * observed)
    scenarios = (("all_missing_keep", missing), ("all_missing_replace", 0),
                 ("observed_rate", expected_missing))
    return [{
        "scenario": name, "assumed_missing_keeps": keeps,
        "aggregate_keeps": completed_keep + keeps,
        "aggregate_keep_rate": (completed_keep + keeps) / (completed + missing),
    } for name, keeps in scenarios]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
