from __future__ import annotations

import math
from collections import Counter
from typing import Any, Callable, Iterable


FORMULAS: dict[str, Callable[[float, dict[str, Any]], float | None]] = {
    "semantic_only": lambda base, critic: float(critic["semantic_alignment_score"]),
    "small_adjustment": lambda base, critic: base + 0.05 * float(critic["semantic_alignment_score"]) + 0.025 * float(critic["directness_score"]),
    "moderate_adjustment": lambda base, critic: base + 0.10 * float(critic["semantic_alignment_score"]) + 0.05 * float(critic["directness_score"]),
    "gate_45": lambda base, critic: base if float(critic["semantic_alignment_score"]) >= 45 else None,
    "generic_overlap_penalty": lambda base, critic: base - (8.0 if critic.get("mismatch_type") == "generic_ideological_overlap" else 0.0),
}


def shannon_entropy(values: Iterable[str]) -> float:
    counts = Counter(values)
    total = sum(counts.values())
    return -sum((count / total) * math.log2(count / total) for count in counts.values()) if total else 0.0


def select_formula(candidates: list[dict[str, Any]], critic: dict[tuple[str, str], dict[str, Any]], formula: str) -> dict[str, Any] | None:
    scorer = FORMULAS[formula]
    scored = []
    for candidate in candidates:
        key = (candidate["quote_hash"], candidate["image_basename"])
        if key not in critic:
            continue
        score = scorer(float(candidate.get("baseline_score", 0)), critic[key])
        if score is not None:
            scored.append((score, candidate["image_basename"], candidate))
    return max(scored, key=lambda row: (row[0], row[1]))[2] if scored else None


def replay_candidate_cache(events: list[dict[str, Any]], critic: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {"schema_version": 1, "analysis_kind": "semantic_alignment_replay", "formulas": {}}
    for formula in FORMULAS:
        winners, exhausted, changed, improvements = [], 0, 0, []
        for event in events:
            winner = select_formula(event.get("candidates", []), critic, formula)
            if winner is None:
                exhausted += 1
                continue
            basename = winner["image_basename"]
            winners.append(basename)
            changed += basename != event.get("production_winner")
            current = critic.get((event["quote_hash"], event.get("production_winner", "")))
            replacement = critic.get((event["quote_hash"], basename))
            if current and replacement:
                improvements.append(float(replacement["semantic_alignment_score"]) - float(current["semantic_alignment_score"]))
        counts = Counter(winners)
        total = len(winners)
        output["formulas"][formula] = {
            "selections": total, "winner_changes": changed, "candidate_exhaustion": exhausted,
            "unique_images": len(counts), "entropy": shannon_entropy(winners),
            "top_10_share": sum(value for _, value in counts.most_common(10)) / total if total else 0.0,
            "average_alignment_improvement": sum(improvements) / len(improvements) if improvements else None,
        }
    return output
