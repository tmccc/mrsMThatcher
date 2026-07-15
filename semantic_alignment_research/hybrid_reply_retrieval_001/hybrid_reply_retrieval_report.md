# Hybrid Reply Retrieval Report

## Status

Live shadow enabled: **yes** (`shadow`).
The production conversational reply path remains lexical. Hybrid results are not supplied to xAI and cannot alter reply modes, text, receipts, posting, or scheduling.

## Live shadow observation

- Events/completed/failures: 5 / 5 / 0
- Top-five lexical/hybrid overlap: 56.0%
- Changed evidence sets: 5
- Hybrid-only / lexical-only evidence: 0 / 1
- No-evidence disagreements: 1
- Latency p50/p95/max: 299.871 / 589.34 / 639.848 ms
The observed production decisions were checked against the separately recorded shadow rows: selected evidence remained a subset of the authoritative lexical evidence, with no hybrid-only quote ID entering a production decision.

## Model and index

- Model: `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`
- Licence: MIT
- Runtime: ONNX, local files only, `trust_remote_code=False`
- Package pins: {"numpy": "2.2.6", "onnxruntime": "1.22.1", "tokenizers": "0.21.4"}
- Documents: 626
- Dimensions: 384 float32
- Embedding matrix: 0.92 MiB
- Build time: 180.856 seconds
- Model/index load time: 3778.5 ms
- Peak build RSS increase: 1077.4 MiB
- Corpus hash: `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`
- Embeddings hash: `298179def67bf0fe17812652684c16440e93e98d65d2a4b66b74b7ae3fea6962`

## Retrieval method

The retriever forms the union of the unchanged lexical top 20 and exact cosine semantic top 20, adds bounded metadata matches, and ranks with weighted reciprocal-rank fusion. Metadata cannot rescue a candidate below both absolute lexical and semantic floors. Exact and >=0.90-Jaccard quote variants are deduplicated.
Selected thresholds: `{"fusion_version": "weighted-rrf-v1", "lexical_candidate_count": 20, "lexical_weight": 1.0, "maximum_results": 5, "metadata_weight": 0.15, "minimum_fused_score": 0.012, "minimum_lexical_score": 3.0, "minimum_packet_confidence": "medium", "minimum_semantic_similarity": 0.84, "minimum_top_margin": 1e-05, "rrf_k": 60, "schema_version": 1, "selected_at": "2026-07-15T17:12:46.517259Z", "selected_from": "fixed_grid_calibration_v1", "semantic_candidate_count": 20, "semantic_weight": 0.8}`

## Automated evaluation

- Evaluation positives: 502
- Lexical recall@1/@5/MRR: 87.6% / 99.2% / 0.926
- Semantic recall@1/@5/MRR: 70.1% / 89.0% / 0.787
- Hybrid recall@1/@5/MRR: 81.3% / 94.0% / 0.866
- Held-out hard-negative no-evidence accuracy: 100.0%
- Multilingual topic-match, overall/holdout: 80.0% / 50.0%
- Query latency p50/p95/max: 121.5 / 175.3 / 238.7 ms

Packet-derived evaluation structurally favours lexical retrieval because its queries are drawn from the same English packets. The multilingual smoke set is small and hand-labelled by broad topic. Neither is evidence for active promotion.

## 30-day replay

- Structured candidates: 156
- Completed/failures: 156 / 0
- Changed evidence sets: 113
- Hybrid-only / lexical-only evidence: 10 / 18
- No-evidence disagreements: 28
- Candidates containing non-ASCII letters: 18
- Replay latency p50/p95/max: 147.868 / 229.88 / 281.51 ms

Potentially beneficial additions include 8 hybrid-only non-English cases. These are review candidates, not validated gains. Risk review identified 6 accepted one- or two-word queries, where semantic retrieval can be over-broad.

## Human review queue

The blind queue contains 100 real historical candidates selected across lane, language, substantive-content, disagreement, and historical-use strata. Composition counts: `{"different_evidence_set": 32, "english_or_unknown": 82, "historical_reply": 1, "humour_or_unreplied": 99, "hybrid_only_evidence": 10, "lexical_only_evidence": 18, "low_substance": 29, "mention": 64, "non_english": 18, "quote_tweet": 36, "same_evidence": 39, "same_set_different_order": 1, "substantive": 71}`.
Launch: `python3 hybrid_reply_retrieval.py serve-review --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 --host 127.0.0.1 --port 8767`

## Safety conclusion

The automated and replay results justify shadow observation and human review only. Hybrid retrieval remains unproven for production evidence selection. The source default is disabled; the only permitted configured mode is `shadow`; failures and stale indexes fail open to the unchanged lexical path.

## Digest and verification

`mrs_log_digest.py` reads only the ignored local shadow status JSON. It does not import the embedder or make an embedding, X, or xAI call. Missing or malformed telemetry is reported as unavailable.
The final focused regression run passed 120 tests. The broader reply, digest, formatter, fail-safe, receipt, integration, and production-isolation run passed 816 tests with one skip. Python compilation, Ruff checks on the new modules, and `git diff --check` passed.

## Limitations

Packet-derived queries favour lexical retrieval, the multilingual labelled set is very small, and no human relevance review is complete. Broad one- or two-word concepts can still produce unsafe semantic matches. These limitations prohibit active promotion.
