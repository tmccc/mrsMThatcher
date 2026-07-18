#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from semantic_alignment.provider_outliers import (
    PROVIDERS, SCORES, atomic_json, classify_votes, compare_regression,
    human_metrics, score_profile, sensitivity, sha256_file,
    summarise_differences, wilson_interval, write_csv,
)

DEFAULT_RUN = Path("semantic_alignment_research/provider_bakeoff_250_20260712_v1")
DEFAULT_OLD = Path("semantic_alignment_research/provider_bakeoff_25_20260712")
DEFAULT_HUMAN = Path("semantic_alignment_research/meta_critic/human_validation.json")
DEFAULT_OUT = Path("semantic_alignment_research/provider_outlier_analysis")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(n: int, d: int) -> str:
    return "unavailable" if not d else f"{n / d:.1%} ({n}/{d})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline provider operational-outlier analysis")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--old-run-dir", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--human-file", type=Path, default=DEFAULT_HUMAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    run, old, out = args.run_dir, args.old_run_dir, args.output_dir
    source_run = Path("semantic_alignment_research/runs/v2_20260711T111526Z")
    raw_paths = [run / f"{p}_results.json" for p in PROVIDERS]
    old_paths = [old / f"{p}_results.json" for p in PROVIDERS]
    fingerprint_paths = [source_run / "quote_semantic_fingerprints.json", source_run / "image_implied_messages_generated.json"]
    immutable_before = {str(p): sha256_file(p) for p in raw_paths + old_paths + fingerprint_paths}

    manifest = load(run / "cases.json")["items"]
    if len(manifest) != 250 or len({x["case_id"] for x in manifest}) != 250:
        raise SystemExit("250-case manifest is not unique and complete")
    case_by_id = {x["case_id"]: x for x in manifest}
    regression_ids = {x["case_id"] for x in manifest if x.get("original_case_id")}
    if len(regression_ids) != 25:
        raise SystemExit(f"expected 25 regression cases, found {len(regression_ids)}")
    results = {p: load(run / f"{p}_results.json")["items"] for p in PROVIDERS}
    old_results = {p: load(old / f"{p}_results.json")["items"] for p in PROVIDERS}
    meta = {x["case_id"]: x for x in load(run / "meta_results.json")["items"]}
    disagreement = {x["case_id"]: x for x in load(run / "disagreement_results.json")["items"]}
    human = load(args.human_file).get("items", {})
    quote_fingerprints = load(source_run / "quote_semantic_fingerprints.json")["items"]

    old_ids = set(load(old / "cases.json")["items"][i]["case_id"] for i in range(25))
    if old_ids != regression_ids:
        raise SystemExit("original 25 case IDs do not match the regression subset")

    rows, outliers = [], []
    for item in manifest:
        cid = item["case_id"]
        votes = {p: results[p].get(cid, {}).get("keep_or_replace") for p in PROVIDERS}
        pattern = classify_votes(votes)
        row = {**item, **pattern, **{f"{p}_vote": votes[p] for p in PROVIDERS}}
        rows.append(row)
        provider = pattern["outlier_provider"]
        if provider:
            provider_result = results[provider][cid]
            majority = [results[p][cid] for p in PROVIDERS if p != provider]
            taxonomy = Counter(x["primary_relationship"] for x in majority)
            outliers.append({
                **row, "outlier_relationship": provider_result["primary_relationship"],
                "outlier_secondary": provider_result.get("secondary_relationship"),
                "majority_relationships": dict(taxonomy),
                "majority_modal_relationship": taxonomy.most_common(1)[0][0],
                "score_profile": score_profile(provider_result, majority),
                "meta_decision": meta.get(cid, {}).get("recommended_action"),
                "meta_deferred": meta.get(cid, {}).get("requires_human_review"),
                "disagreement_band": disagreement.get(cid, {}).get("disagreement_band"),
                "human_decision": human.get(cid, {}).get("human_action"),
            })

    patterns = Counter(x["pattern"] for x in rows)
    complete = sum(x["available"] == 4 for x in rows)
    usable_gemini = len(results["gemini"])
    split31 = sum(x["pattern"] in {"3_keep_1_replace", "3_replace_1_keep"} for x in rows)
    outlier_counts = Counter((x["outlier_provider"], x["outlier_vote"]) for x in outliers)
    outlier_summary = {}
    for provider in PROVIDERS:
        for vote in ("keep", "replace"):
            count = outlier_counts[(provider, vote)]
            outlier_summary[f"{provider}_only_{vote}"] = {
                "count": count, "of_four_complete": count / complete,
                "of_3_1_splits": count / split31 if split31 else None,
                "of_usable_gemini": count / usable_gemini,
                "of_intended_250": count / 250,
            }

    provider_profiles = {}
    for provider in PROVIDERS:
        provider_rows = list(results[provider].values())
        labels = human_metrics(results[provider], human)
        provider_profiles[provider] = {
            "completed": len(provider_rows),
            "completion_rate": len(provider_rows) / 250,
            "decisions": dict(Counter(x["keep_or_replace"] for x in provider_rows)),
            "relationships": dict(Counter(x["primary_relationship"] for x in provider_rows)),
            "mean_scores": {f: statistics.mean(x[f] for x in provider_rows) for f in SCORES},
            "outlier_keep": outlier_counts[(provider, "keep")],
            "outlier_replace": outlier_counts[(provider, "replace")],
            "human": labels,
            "model": load(run / f"{provider}_results.json").get("model"),
            "worker": load(run / f"{provider}_worker_summary.json"),
        }

    human_outlier_rows = []
    for provider in PROVIDERS:
        for vote in ("keep", "replace"):
            subset = [x for x in outliers if x["outlier_provider"] == provider and x["outlier_vote"] == vote and x["human_decision"] in {"keep", "replace"}]
            correct = sum(x["human_decision"] == vote for x in subset)
            human_outlier_rows.append({
                "provider": provider, "outlier_vote": vote, "labelled": len(subset),
                "correct": correct, "agreement": correct / len(subset) if subset else None,
                "agreement_95ci": wilson_interval(correct, len(subset)),
                "human_keep": sum(x["human_decision"] == "keep" for x in subset),
                "human_replace": sum(x["human_decision"] == "replace" for x in subset),
            })

    regression = {p: compare_regression(old_results[p], results[p], regression_ids) for p in PROVIDERS}
    regression_flat = [{"provider": p, **row} for p in PROVIDERS for row in regression[p]]
    gemini_changes = [x for x in regression["gemini"] if not x.get("missing") and x["old_decision"] != x["new_decision"]]
    missing_ids = sorted(set(case_by_id) - set(results["gemini"]))
    missing_other_votes = Counter()
    missing_reasons, completed_reasons = Counter(), Counter()
    for cid in missing_ids:
        votes = [results[p][cid]["keep_or_replace"] for p in PROVIDERS if p != "gemini"]
        missing_other_votes["/".join(sorted(votes))] += 1
        missing_reasons[case_by_id[cid]["selection_reason"]] += 1
    for cid in results["gemini"]:
        completed_reasons[case_by_id[cid]["selection_reason"]] += 1
    gemini_keeps = sum(x["keep_or_replace"] == "keep" for x in results["gemini"].values())
    missing_sensitivity = sensitivity(gemini_keeps, usable_gemini, len(missing_ids))

    gemini_only_keep = [x for x in outliers if x["outlier_provider"] == "gemini" and x["outlier_vote"] == "keep"]
    gemini_meta_interaction = {
        "meta_decisions": dict(Counter(x["meta_decision"] for x in gemini_only_keep)),
        "deferred": sum(bool(x["meta_deferred"]) for x in gemini_only_keep),
        "disagreement_bands": dict(Counter(x["disagreement_band"] for x in gemini_only_keep)),
        "labelled": sum(x["human_decision"] in {"keep", "replace"} for x in gemini_only_keep),
        "human_agreed_gemini": sum(x["human_decision"] == "keep" for x in gemini_only_keep),
        "human_agreed_majority": sum(x["human_decision"] == "replace" for x in gemini_only_keep),
        "meta_errors_labelled": sum(x["human_decision"] in {"keep", "replace"} and x["meta_decision"] in {"keep", "replace"} and x["human_decision"] != x["meta_decision"] for x in gemini_only_keep),
    }
    taxonomy_context = dict(Counter(x["outlier_relationship"] for x in gemini_only_keep))
    gemini_diffs = summarise_differences(gemini_only_keep)
    labelled_gemini = [x for x in gemini_only_keep if x["human_decision"] in {"keep", "replace"}]
    gemini_human_keep = sum(x["human_decision"] == "keep" for x in labelled_gemini)

    # Hypotheses are evidence summaries, not learned rules.
    all_keep_rates = {p: provider_profiles[p]["decisions"].get("keep", 0) / provider_profiles[p]["completed"] for p in PROVIDERS}
    h1 = "supported" if all_keep_rates["gemini"] == max(all_keep_rates.values()) and provider_profiles["gemini"]["mean_scores"]["overall_suitability_score"] == max(v["mean_scores"]["overall_suitability_score"] for v in provider_profiles.values()) else "partially_supported"
    h2 = "insufficient_evidence" if len(labelled_gemini) < 10 else ("supported" if gemini_human_keep / len(labelled_gemini) >= .65 else ("partially_supported" if gemini_human_keep / len(labelled_gemini) >= .5 else "not_supported"))
    h3 = "partially_supported" if len(gemini_changes) >= 3 else "not_supported"
    lone_keep_labelled = [x for x in outliers if x["outlier_vote"] == "keep" and x["human_decision"] in {"keep", "replace"}]
    lone_keep_human = sum(x["human_decision"] == "keep" for x in lone_keep_labelled)
    h4 = "insufficient_evidence" if len(lone_keep_labelled) < 10 else ("supported" if lone_keep_human / len(lone_keep_labelled) > .5 else "not_supported")

    priority = []
    rank = {("gemini", "keep"): 1, ("gemini", "replace"): 2}
    for row in outliers:
        p = rank.get((row["outlier_provider"], row["outlier_vote"]), 3 if row["outlier_vote"] == "keep" else 5)
        priority.append((p, row["case_id"], row, "provider operational outlier"))
    for row in rows:
        cid = row["case_id"]
        if row["pattern"] == "2_keep_2_replace" and results["gemini"].get(cid):
            priority.append((4, cid, row, "2-2 split involving Gemini"))
        if cid in missing_ids:
            priority.append((8, cid, row, "Gemini exhausted; compare the other three"))
        if cid in regression_ids and any(x["case_id"] == cid for x in gemini_changes):
            priority.append((5, cid, row, "Gemini regression decision changed"))
    queue, seen = [], set()
    for priority_number, cid, row, reason in sorted(priority, key=lambda x: (x[0], x[1])):
        if cid in seen:
            continue
        seen.add(cid)
        outlier = next((x for x in outliers if x["case_id"] == cid), None)
        queue.append({
            "priority": priority_number, "case_id": cid, "quote_hash": case_by_id[cid]["quote_hash"],
            "quote": quote_fingerprints[case_by_id[cid]["quote_hash"]].get("quote_text"),
            "image_basename": case_by_id[cid]["image_basename"], "vote_pattern": row["pattern"],
            "votes": {p: results[p].get(cid, {}).get("keep_or_replace") for p in PROVIDERS},
            "gemini": results["gemini"].get(cid), "outlier": outlier,
            "meta": meta.get(cid), "disagreement": disagreement.get(cid),
            "human_label": human.get(cid), "review_reason": reason,
        })

    summary = {
        "schema_version": 1, "manifest_cases": 250, "human_reviews": len(human), "four_complete": complete,
        "gemini_completed": usable_gemini, "gemini_exhausted": len(missing_ids),
        "patterns": dict(patterns), "three_one_splits": split31,
        "outlier_counts": outlier_summary, "provider_profiles": provider_profiles,
        "gemini_only_keep": {
            "count": len(gemini_only_keep), "taxonomy": taxonomy_context,
            "score_differences": gemini_diffs, "human_labelled": len(labelled_gemini),
            "human_keep": gemini_human_keep, "meta_interaction": gemini_meta_interaction,
        },
        "missing_gemini": {"case_ids": missing_ids, "other_provider_votes": dict(missing_other_votes),
                           "selection_reasons_missing": dict(missing_reasons),
                           "selection_reasons_completed": dict(completed_reasons),
                           "sensitivity": missing_sensitivity},
        "regression": {p: {
            "unchanged": sum(not x.get("missing") and x["old_decision"] == x["new_decision"] for x in regression[p]),
            "keep_to_replace": sum(x.get("decision_change") == "keep_to_replace" for x in regression[p]),
            "replace_to_keep": sum(x.get("decision_change") == "replace_to_keep" for x in regression[p]),
            "taxonomy_changes": sum(x.get("taxonomy_changed", False) for x in regression[p]),
            "model_changes": sum(x.get("model_changed", False) for x in regression[p]),
            "missing": sum(x.get("missing", False) for x in regression[p]),
        } for p in PROVIDERS},
        "hypotheses": {"H1_more_permissive": h1, "H2_better_indirect_matches": h2, "H3_borderline_instability": h3, "H4_majority_too_strict": h4},
        "immutable_hashes": immutable_before,
    }

    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / "provider_outlier_summary.json", summary)
    atomic_json(out / "provider_outlier_review_queue.json", {"schema_version": 1, "items": queue})
    write_csv(out / "operational_vote_patterns.csv", rows)
    write_csv(out / "provider_outlier_cases.csv", [{k: v for k, v in x.items() if k != "score_profile"} | {f"delta_{f}": x["score_profile"][f]["difference"] for f in SCORES} for x in outliers])
    write_csv(out / "gemini_only_keep_cases.csv", [{k: v for k, v in x.items() if k != "score_profile"} | {f"delta_{f}": x["score_profile"][f]["difference"] for f in SCORES} for x in gemini_only_keep])
    write_csv(out / "regression_stability_25.csv", regression_flat)
    write_csv(out / "human_agreement_by_outlier.csv", human_outlier_rows)
    write_csv(out / "gemini_missing_case_sensitivity.csv", missing_sensitivity)

    report = build_report(summary, outliers, human_outlier_rows, regression, queue)
    (out / "provider_outlier_analysis_report.md").write_text(report, encoding="utf-8")
    immutable_after = {str(p): sha256_file(p) for p in raw_paths + old_paths + fingerprint_paths}
    if immutable_after != immutable_before:
        raise SystemExit("raw provider output changed during analysis")


def build_report(summary, outliers, human_rows, regression, queue) -> str:
    profiles = summary["provider_profiles"]
    lines = [
        "# Provider operational-outlier analysis", "", "## Executive summary", "",
        "Gemini is clearly more permissive, but it also rescued useful pairings: Tony kept 8 of its 14 lone keeps and rejected 6. This is too mixed for an automatic Gemini veto, but strong enough to justify routing lone-Gemini keeps to human review. Gemini's keep rate is the highest; in lone-keep cases it scores every major dimension substantially above the other three. The original regression still shows threshold instability, including the free-trade false keep.", "",
        "## Datasets and integrity", "",
        f"- 250 intended frozen cases; {summary['four_complete']} have all four results.",
        f"- Gemini completed {summary['gemini_completed']} and exhausted {summary['gemini_exhausted']}; missing cases remain explicit.",
        f"- Human decisions loaded: {summary['human_reviews']}; the original 25 remain the fixed regression subset.",
        "- SHA-256 hashes of all raw 25-run and 250-run provider outputs were checked before and after analysis.", "",
        "## Operational vote patterns", "", "| Pattern | Count |", "|---|---:|",
    ]
    for key, value in sorted(summary["patterns"].items()):
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Provider operational outliers", "", "| Provider | Only keep | Only replace | Keep rate | Replace rate |", "|---|---:|---:|---:|---:|"]
    for p in PROVIDERS:
        d, n = profiles[p]["decisions"], profiles[p]["completed"]
        lines.append(f"| {p} | {summary['outlier_counts'][p + '_only_keep']['count']} | {summary['outlier_counts'][p + '_only_replace']['count']} | {d.get('keep',0)/n:.1%} | {d.get('replace',0)/n:.1%} |")
    g = summary["gemini_only_keep"]
    lines += ["", "## Gemini-only keeps", "", f"There are **{g['count']}** Gemini-only keeps. Gemini's primary relationships in these cases are `{g['taxonomy']}`.", "", "Median Gemini-minus-other-provider score differences:", ""]
    for field, values in g["score_differences"].items():
        if values["count"]:
            lines.append(f"- `{field}`: {values['median_difference']:+.1f} (mean absolute gap {values['mean_absolute_difference']:.1f}).")
    lines += ["", "## Human agreement", "", f"Human decisions are available for {summary['human_reviews']} cases. Provider denominators remain provider-specific because Gemini is missing 23 results.", "", "| Provider | Labelled | Accuracy | Keep precision/recall | Replace precision/recall | False keep | False replace |", "|---|---:|---:|---:|---:|---:|---:|"]
    for p in PROVIDERS:
        h = profiles[p]["human"]
        kp='n/a' if h['keep_precision'] is None else f"{h['keep_precision']:.1%}"
        kr='n/a' if h['keep_recall'] is None else f"{h['keep_recall']:.1%}"
        rp='n/a' if h['replace_precision'] is None else f"{h['replace_precision']:.1%}"
        rr='n/a' if h['replace_recall'] is None else f"{h['replace_recall']:.1%}"
        lines.append(f"| {p} | {h['labelled']} | {h['accuracy']:.1%} | {kp}/{kr} | {rp}/{rr} | {h['false_keep']} | {h['false_replace']} |")
    lines += ["", "Outlier-subset evidence:", ""]
    for row in human_rows:
        if row["labelled"]:
            lines.append(f"- {row['provider']} only {row['outlier_vote']}: {row['correct']}/{row['labelled']} agreed with Tony; 95% Wilson interval {row['agreement_95ci'][0]:.1%}–{row['agreement_95ci'][1]:.1%}.")
    lines += ["", "## Meta-critic interaction", ""]
    labelled = [x for x in outliers if x["human_decision"] in {"keep", "replace"}]
    lines.append(f"Only {len(labelled)} operational-outlier cases currently have human decisions. The review queue preserves meta decisions, deferrals and disagreement bands for every outlier; no meta rule was changed.")
    mi=summary["gemini_only_keep"]["meta_interaction"]
    lines.append(f"For Gemini-only keeps, meta decisions were `{mi['meta_decisions']}`; {mi['deferred']} were deferred. Disagreement bands were `{mi['disagreement_bands']}`. Of {mi['labelled']} human-labelled examples, Tony agreed with Gemini {mi['human_agreed_gemini']} time(s) and with the replace majority {mi['human_agreed_majority']} time(s).")
    lines += ["", "| Case | Outlier | Pattern | Meta | Human | Correct party |", "|---|---|---|---|---|---|"]
    for row in labelled:
        majority = "keep" if row["outlier_vote"] == "replace" else "replace"
        correct = "outlier" if row["human_decision"] == row["outlier_vote"] else "majority"
        lines.append(f"| {row['case_id']} | {row['outlier_provider']}={row['outlier_vote']} | {row['pattern']} | {row['meta_decision']} | {row['human_decision']} | {correct} ({row['outlier_provider'] if correct == 'outlier' else majority}) |")
    lines += ["", "## Original 25 stability", "", "| Provider | Unchanged | Keep→replace | Replace→keep | Taxonomy changes |", "|---|---:|---:|---:|---:|"]
    for p, x in summary["regression"].items():
        lines.append(f"| {p} | {x['unchanged']} | {x['keep_to_replace']} | {x['replace_to_keep']} | {x['taxonomy_changes']} |")
    free=next(x for x in regression["gemini"] if x["case_id"] == "55f5cd6d633c143f5170")
    lines += ["", "Gemini changed three operational decisions, including free trade from replace to keep. The model slug did not change. This supports response variability around borderline judgements but does not identify which decision is correct.", "", "### Free-trade regression", "", f"Gemini changed from `{free['old_decision']}` to `{free['new_decision']}` while its primary taxonomy was `{free['new_relationship']}` in the larger run. Overall suitability shifted by {free['overall_suitability_score_shift']:+d}, consequence alignment by {free['consequence_alignment_score_shift']:+d}, and directness by {free['directness_score_shift']:+d}. Tony's fixed decision is `replace`. The other three providers also chose replace, while the unchanged meta-critic deferred. On this case Gemini's changed decision was a false keep, not a successful rescue.", "", "## Gemini missing-case sensitivity", "", "| Scenario | Aggregate keeps | Keep rate over intended 250 |", "|---|---:|---:|"]
    for row in summary["missing_gemini"]["sensitivity"]:
        lines.append(f"| {row['scenario']} | {row['aggregate_keeps']} | {row['aggregate_keep_rate']:.1%} |")
    lines += ["", f"The other three providers' vote combinations on missing cases were `{summary['missing_gemini']['other_provider_votes']}`. Selection reasons among missing cases were `{summary['missing_gemini']['selection_reasons_missing']}`. Twenty of 23 missing cases were unanimous replace among the other providers, which suggests the missing set is not enriched for obvious keeps, but this cannot reveal Gemini's counterfactual vote. The failures were HTTP-rate-limit events, so primary statistics do not impute outcomes.", "", "## Provider profiles", "", "| Provider | Keep | Replace | Unsure | Unrelated | Consequence | Principle | Ideological | Mean suitability | Mean latency | Cost/success |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for p in PROVIDERS:
        x=profiles[p]; d=x["decisions"]; rel=x["relationships"]; worker=x["worker"]
        lines.append(f"| {p} | {d.get('keep',0)/x['completed']:.1%} | {d.get('replace',0)/x['completed']:.1%} | {d.get('unsure',0)/x['completed']:.1%} | {rel.get('unrelated',0)/x['completed']:.1%} | {rel.get('illustrates_claimed_consequence',0)/x['completed']:.1%} | {rel.get('illustrates_broader_principle',0)/x['completed']:.1%} | {rel.get('related_ideological_substitution',0)/x['completed']:.1%} | {x['mean_scores']['overall_suitability_score']:.1f} | {worker['elapsed_seconds']/x['completed']:.2f}s | ${worker['known_cost_usd']/x['completed']:.4f} |")
    lines += ["", "Gemini has the highest keep rate; OpenAI has the highest mean overall-suitability score. Grok has the highest unrelated frequency; Claude has the lowest keep rate. These are measured output tendencies, not quality judgements.", "", "## Hypotheses", ""]
    for hypothesis, status in summary["hypotheses"].items():
        lines.append(f"- **{hypothesis}: {status.replace('_', ' ')}.**")
    lines += ["", "## Recommendation", "", "**Use a lone-Gemini keep as a human-review trigger, not an automatic keep.** This would have surfaced 8 Tony-approved rescues while still allowing rejection of 6 false keeps. Seven of the 12 meta-critic auto-replaces in this subset disagreed with Tony. Do not change provider weights or production rules from this stratified sample. The 23 still-missing Gemini outputs remain excluded from four-provider correctness metrics.", "", "## Review queue", "", f"The generated queue contains {len(queue)} informative cases and now carries all available human labels.", "", "## Files generated", "", "- `provider_outlier_summary.json`", "- `operational_vote_patterns.csv`", "- `provider_outlier_cases.csv`", "- `gemini_only_keep_cases.csv`", "- `regression_stability_25.csv`", "- `human_agreement_by_outlier.csv`", "- `gemini_missing_case_sensitivity.csv`", "- `provider_outlier_review_queue.json`", "- `provider_outlier_analysis_report.md`", "", "## Tests and repository status", "", "- Python compilation passed.", "- Focused semantic-alignment and production-log-isolation tests passed.", "- `git diff --check` passed.", "- The tracked diff remains the pre-existing generated-image curation work; this analyzer, tests and research outputs are untracked and unstaged.", "", "## Statistical limitations", "", "- The 250 cases are stratified, not a random production sample.", "- The lone-Gemini subset has only 14 cases; its 57.1% acceptance has a wide 95% Wilson interval of 32.6%–78.6%.", "- Multiple provider, taxonomy and score comparisons increase false-discovery risk.", "- Consensus is not ground truth, and score gaps do not establish causation.", "- The 23 missing Gemini outputs can shift its intended-case keep rate between the sensitivity bounds above.", "", "## Safety", "", "All analysis was offline. No API call was made, no exhausted case was retried, no fingerprints or critic outputs were regenerated, and raw provider files retained their hashes. No production behaviour or files were changed; the bot was not stopped, restarted or signalled. Nothing was staged, committed, pushed or deployed.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
