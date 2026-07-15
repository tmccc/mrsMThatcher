# Formatter v2 Candidate

Version: `historical_context_reply_schema_v2_candidate`.

## Format

The candidate is an offline-only compact archive entry:

```text
Context — [source event, British-formatted date: immediate historical issue.]

Meaning — [one sentence, only when the deterministic rule retains it.]

Verification — [canonical v1 label]

Source — [canonical v1 source title]
[stable public URL, when the selected source has one]
```

It has no separate Historical context, Occasion, Date or Immediate context labels. Unknown components are omitted or described explicitly without printing placeholder values.

## Meaning Rule

Meaning is retained for uncertain wording, context-dependent references, contrastive claims, distinct mechanisms or consequences, and unresolved ambiguity. It is omitted only when substantive terms are already supplied by the quotation or Context and no retention rule applies.

Each record stores the decision reason, quote/Context overlap measurements, mechanism and consequence distinct-token counts, both candidate forms and the final choice. Omission is not driven by length alone.

## Provenance

Corpus renderings: **626**; unresolved records excluded: **6**. The strict rerender-based parity audit is **PASS**, with zero regressions in quote identity, verification status or label, source identity or locator, historical confidence, event date or source event.

The source-priority and redirect-exclusion behavior is inherited from the production v1 formatter. V2 never substitutes or improves a source independently.

## Lengths

| Formatter | Mean weighted | Median weighted | Minimum | Maximum | >450 | >600 | >1,000 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 453.88 | 450 | 154 | 645 | 310 | 6 | 0 |
| v2 candidate | 400.55 | 404 | 181 | 577 | 125 | 0 | 0 |

Mean reduction is **11.72%** and median reduction is **9.01%**. Meaning is omitted in **16** records and retained in **610**.

## Review Sample

The deterministic 50-record sample contains the ten longest v1 replies and covers all verification labels, eight source classes, all confidence levels, six event types, nine topic groups, simple and complex contexts, all five public-URL records, Meaning included/omitted cases, ambiguous events, difficult source titles, high-overlap cases and both large and negligible reductions. Near-duplicate quote families are excluded and review order is deterministically shuffled.

The blind reviewer stores no formatter identity in its normal A/B payload, filenames, URLs, element IDs or CSS classes. Assignments are available separately for audit.

Launch it with:

```bash
python3 compare_historical_context_formatters.py serve \
  --trial-dir semantic_alignment_research/historical_context_formatter_trial_001 \
  --host 127.0.0.1 \
  --port 8766
```

Open <http://127.0.0.1:8766>.

## Verification

- Candidate-focused tests: **26 passed**.
- Historical-context, engagement, digest, corpus and production-isolation regression tests: **159 passed**.
- Strict corpus audit: **626/626**, zero blocking provenance regressions.
- Local reviewer smoke test: passed; normal page exposed only A/B identities.
- No external or paid calls were made.

## Files

Implementation is in `compare_historical_context_formatters.py` and `semantic_alignment/historical_context_formatter_trial.py`; focused tests are in `tests/test_historical_context_formatter_trial.py`. Trial artifacts are contained in this directory.

Production v1 remains unchanged; this version is reachable only through the offline trial CLI.
