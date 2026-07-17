# Quote/Image Metadata Remediation Report

Generated: 2026-07-17T20:04:34.800328Z

## Architecture

Deterministic source-grounded contract repair precedes residual two-pass Gemini adjudication. Unknown is never treated as safe, while missing literal depiction is never itself a veto.

## Corpus and simulator evidence

- Completed quotations: 626
- Authorised historical images: 91
- Unique simulated quote/image pairs: 18767
- Simulated occurrences aggregated: 247331

## Metadata defects

- image_secondary_identity_missing: 8
- neutral_portrait_policy_missing: 7
- quote_actor_count_overreach: 366
- quote_attribution_requires_safety_policy: 13
- quote_entity_overreach: 23
- quote_event_overreach: 1
- quote_period_overreach: 17
- quote_relationship_overreach: 5

## Offline adjudication

- changed_image_count: 0
- changed_quote_count: 55
- decision_counts: {'allow': 18036, 'unknown': 508, 'veto': 223}
- deterministic_count: 14423
- invalidated_count: 42
- monte_carlo_weighted_counts: {'allow': 186965, 'unknown': 3974, 'veto': 3994}
- pair_count: 18767
- residual_count: 508
- reused_count: 3836

## Candidate manifest v3

- Pair count: 20430
- Decisions: {'allow': 19837, 'unknown': 272, 'veto': 321}
- Additional research candidates incorporated: 1663
- Reused prior judgements: 3836
- Invalidated prior judgements: 42

## Attribution safety

- Confirmed Thatcher speakers: 613
- Confirmed non-Thatcher or misattributed speakers: 9
- Canonical speaker unavailable: 4
- A confirmed non-Thatcher quotation paired with a Thatcher portrait is an affirmative attribution veto.
- An unavailable speaker remains unknown; missing evidence is not converted to safe.

## Local model

- Decision: skip_local_model
- Repository: Qwen/Qwen3-VL-8B-Instruct-GGUF
- Qualification: skipped_hardware_safety_gate
- No model files were downloaded when the hardware safety gate failed.

## Gemini execution

- Model: `gemini-3.1-pro-preview`
- Configuration: thinking=HIGH; temperature=0.0; typed response schema
- Pilot: PASS (5 cases)
- Completed billed Developer operations: 136
- Completed billed Vertex operations: 0

## Validation

- Stateful known-winner coverage: 99.2146%
- Stateful unknown-winner rate: 0.7854%
- Seasonal-boundary known-winner coverage: 100.0000%
- Abstract neutral-portrait allowance: 233/237
- Literal-depiction-only stateful vetoes: 0
- Generated profiles exercised: 7; all remain out of scope

## Cost

- Known spend: $4.799443
- Batch spend: $2.725217
- Interactive/repair spend: $2.074226
- Ambiguous exposure: $0.110796
- Remaining to US$100 ceiling: $95.089761

## Remaining source-evidence gaps

- Pair-level abstentions: 188
- canonical_speaker_not_source_grounded: 140
- required_participant_identity_not_source_grounded: 25
- required_relationship_not_source_grounded: 23
- Current winners with unknown canonical speaker: 4
- No model may repair these identities without source evidence.

## Acceptance gates

Passed: False
Stop condition: `genuine_source_evidence_gaps_remain`
The unmet current-winner gate is retained rather than treating unknown attribution as safe.

## Tests

- Focused: 126 passed in 5.06s; final remediation corpus-backed suite 44 passed in 2.54s
- Full suite: 1723 passed, 1 skipped, 3 warnings in 639.72s
- Py compile: passed
- Git diff check: passed

## Isolation

- Live production behaviour and the live shadow manifest were not changed.
- No human pairing decisions were requested.
- The diagnostic browser was not started. It is read-only and optional.
- Launch command: `python3 quote_image_metadata_remediation.py serve --run-dir semantic_alignment_research/quote_image_metadata_remediation_001 --host 127.0.0.1 --port 8771`

## Git diff --stat

```text
.gitignore                                  |  4 ++++
 semantic_alignment/relation_aware_veto.py   |  7 ++++---
 tests/test_simulate_regular_post_futures.py | 19 +++++++++++++++++++
 tools/simulate_regular_post_futures.py      |  3 ++-
 4 files changed, 29 insertions(+), 4 deletions(-)
```

## Git status --short

```text
M .gitignore
 M semantic_alignment/relation_aware_veto.py
 M tests/test_simulate_regular_post_futures.py
 M tools/simulate_regular_post_futures.py
?? analyse_mrs_assets_xai.py
?? analyse_mrs_assets_xai_v2.py
?? analyse_mrs_assets_xai_v3.py
?? check_and_fix_thatcher_faces_xai_revised.py
?? digest063_analysis.txt
?? generate_first10_from_quote_analysis.py
?? generate_openai_low_first10.py
?? generated_review_approved_images/grok_face_corrected/
?? generated_review_approved_images/grok_face_rejected_candidates/
?? generated_review_approved_images/pre_face_correction_backup_20260709_033026/
?? image_discovery_research/thatcher_image_hunt_001/
?? image_discovery_research/thatcher_image_hunt_002/commons_archive_search_titles.json
?? image_discovery_research/thatcher_image_hunt_002/commons_expansion_state.json
?? image_discovery_research/thatcher_image_hunt_002/download_failures.jsonl
?? image_discovery_research/thatcher_image_hunt_002/exported_kept/maybe/
?? image_discovery_research/thatcher_image_hunt_002/exported_kept/rights_pending/
?? image_discovery_research/thatcher_image_hunt_002/grounded_discovery/
?? image_discovery_research/thatcher_image_hunt_002/integration_preparation/human_pairing_review_pre_source_grounded.json
?? image_discovery_research/thatcher_image_hunt_002/integration_preparation/quote_matching_dry_run_pre_source_grounded.json
?? image_discovery_research/thatcher_image_hunt_002/integration_preparation/quote_matching_dry_run_pre_source_grounded.md
?? image_discovery_research/thatcher_image_hunt_002/integration_preparation/source_grounded_raw_responses/
?? image_discovery_research/thatcher_image_hunt_002/raw_candidates.jsonl
?? image_discovery_research/thatcher_image_hunt_002/review_state.backup.json
?? image_discovery_research/thatcher_image_hunt_002/review_state.json
?? image_discovery_research/thatcher_image_hunt_002/triage_batches/
?? quote_image_metadata_remediation.py
?? quote_image_selection_harness.py
?? review/
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/context_follow_up_queue.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/gemini_review_manifest.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/gemini_review_preflight.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/gemini_review_transport_status.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/human_review_audit.jsonl
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/human_reviews.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/review_results.json
?? semantic_alignment_research/hybrid_reply_retrieval_001/manual_review/stale_reviews.json
?? semantic_alignment_research/image_quote_eligibility_trial_001/providers/
?? semantic_alignment_research/image_quote_provider_compatibility_001/providers/
?? semantic_alignment_research/image_quote_shortlist_rerank_001/providers/
?? semantic_alignment_research/quote_image_metadata_remediation_001/
?? semantic_alignment_research/quote_image_selection_harness_001/
?? semantic_alignment_research/relation_aware_semantic_veto_001/attempts.jsonl
?? semantic_alignment_research/relation_aware_semantic_veto_001/batch_jobs.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/cost_preflight.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/cost_preflight.md
?? semantic_alignment_research/relation_aware_semantic_veto_001/image_analysis_for_scoring.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/normalised_responses/
?? semantic_alignment_research/relation_aware_semantic_veto_001/pair_candidates.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/pair_judgements.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/pilot_evaluation.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/pilot_manifest.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/production_top5_batch_manifest.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/production_top5_pair_candidates.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/production_top5_preflight.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/production_top5_preflight.md
?? semantic_alignment_research/relation_aware_semantic_veto_001/provider_route_state.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/semantic_contracts.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/shadow_integration_status.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/transport_configuration.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/transport_preflight.json
?? semantic_alignment_research/relation_aware_semantic_veto_001/veto_decisions.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/attested_image_corpus_manifest.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/material_veto_v2_final_report.md
?? semantic_alignment_research/relation_aware_semantic_veto_002/normalised_responses/
?? semantic_alignment_research/relation_aware_semantic_veto_002/pair_judgements_v2.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/production_top8_pair_candidates_v2.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/relation_aware_material_veto_v2_report.md
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_batch_manifest.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_final_evaluation.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_final_status.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_missing_judgement_manifest.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_offline_evaluation.json
?? semantic_alignment_research/relation_aware_semantic_veto_002/v2_reuse_manifest.json
?? tests/test_quote_image_metadata_remediation.py
?? tests/test_quote_image_selection_harness.py
?? vertex_test.py
?? veto.json
```
