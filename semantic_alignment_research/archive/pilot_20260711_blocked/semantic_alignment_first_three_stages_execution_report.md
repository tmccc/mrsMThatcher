# Semantic alignment first-three-stages execution report

## Executive result

**Execution stopped safely during the quote stage.** Pricing and cost accounting
were corrected and verified, all preflight limits passed, and 136 valid quote
fingerprints were completed. The research process was then interrupted while one
additional inference request was waiting for its HTTP response. Because that
request returned no `usage.cost_in_usd_ticks`, its billing outcome is ambiguous.

The cost ledger was marked blocked and no further paid request was sent. Active
generated-image analysis and the 150-case critic were not started. This follows
the explicit requirement to stop rather than continue without authoritative
cost tracking.

## 1. Model and official pricing

Selected model: `grok-4.5` for quote, image, and critic stages.

An authenticated, non-inference `GET https://api.x.ai/v1/models` returned:

```text
prompt_text_token_price        = 20,000 ticks/token
prompt_image_token_price       = 20,000 ticks/token
cached_prompt_text_token_price =  5,000 ticks/token
completion_text_token_price    = 60,000 ticks/token
```

xAI's official cost-tracking documentation states that one US dollar equals
10,000,000,000 ticks and that `cost_in_usd_ticks` is the exact billed request
cost. These metadata values therefore equal:

```text
text/image input = US$2.00 per 1,000,000 tokens
completion output = US$6.00 per 1,000,000 tokens
cached text input = US$0.50 per 1,000,000 tokens
```

Sources inspected:

- `https://api.x.ai/v1/models` (authenticated model metadata)
- `https://docs.x.ai/developers/cost-tracking`
- `https://docs.x.ai/docs/guides/structured-outputs`

Sanitised pricing/model evidence is under
`semantic_alignment_research/pricing/`. No credential is stored there.

## 2. Estimator and accounting correction

The old `$0.02 per call` planning placeholder was removed. The estimator now
calculates text/image input and output separately at the verified unit prices.
It reports image-token allowance, reasoning/output allowance, retry allowance,
per-stage estimate, cumulative estimate, model, prices, and ceilings.

Every inference request now uses:

```text
model             = grok-4.5
reasoning_effort  = low
max_tokens        = 1,000
response_format   = strict json_schema
tools             = none
```

The exact response usage is persisted per call with item key, model, timestamp,
input/cached/reasoning/completion tokens, integer ticks, and dollar conversion.
Response bodies are atomically cached before idempotent ledger registration.
There are no automatic retries; retry allowance and observed retry cost are zero.

Before each request, the ledger reserves the conservative maximum next-call
cost against both the stage ceiling and the `$12` total ceiling. Missing exact
cost permanently blocks the ledger.

## 3. Preflight estimate

| stage | calls | text input | image allowance | output | estimated cost | ceiling |
|---|---:|---:|---:|---:|---:|---:|
| quote fingerprints | 632 | 568,800 | 0 | 410,800 | $3.602400 | $6.00 |
| active image fingerprints | 79 | 31,600 | 323,900 | 59,250 | $1.066500 | $3.00 |
| validation critic | 150 | 225,000 | 0 | 105,000 | $1.080000 | $3.00 |
| **total** | **861** | **825,400** | **323,900** | **575,050** | **$5.748900** | **$12.00** |

The preflight was saved to
`semantic_alignment_research/preflight_estimate.json`. Pricing verification,
model identity, no-tools configuration, structured output, and low reasoning
all passed before the first inference.

## 4. Prompt isolation verification

The quote request contained only the static quote instructions and quote text.
It contained no image, filename, image metadata, winner, generation prompt,
origin relationship, or validation category.

The image request implementation accepts only the static no-caption prompt and
image bytes, verifies SHA-256 before and after the request, and passes no quote,
origin hash meaning, generation prompt, labels, expected themes, or selection
history. No image request was executed in this run.

The critic allow-lists only validated quote/image fingerprint fields. Expected
categories and reviewer notes remain solely in the human validation file and
are not included in the critic prompt. No critic request was executed.

Focused tests verify these isolation properties and confirm request payloads
contain strict JSON Schema, low reasoning, bounded output, and no tools.

## 5. Quote-stage result and actual cost

```text
inventory                         = 632
valid fingerprints persisted      = 136
remaining                         = 496
schema/content failures           = 0
retries                           = 0
calls with exact cost             = 136
known exact input tokens          = 87,348
known exact cached tokens         = 51,072
known exact reasoning tokens      = 50,789
known exact completion tokens     = 48,042
known exact billed cost           = $0.691074
mean known call cost              = $0.00508143
median known call cost            = $0.005032
maximum known call cost           = $0.007504
```

One further request was interrupted before its response arrived. It has no
response cache, usage record, or exact tick cost. It may or may not have been
billed. Therefore `$0.691074` is the exact **known** cumulative cost, not a claim
about the complete account charge for this run.

The blocked item key recorded in the ledger is:

```text
3dd04269c7014b5ab25d7fc9d7b2ea5195ed1e8a2b2c44278ed5cad73486a2d5
```

The ledger cannot be reopened by normal pipeline execution until this ambiguity
is explicitly reviewed.

## 6. Quote quality review

A stratified 20-record review covered free trade, capitalism/socialism,
taxation, sovereignty/national identity, liberty, trade unions, defence/Cold
War, consumer interests, and general political philosophy.

Findings:

- 136/136 dominant messages were unique rather than templated repetitions.
- Dominant messages accurately separated primary claims from adjacent themes.
- `not_about` boundaries were specific and useful.
- Desired visual evidence was generally concrete and issue-specific.
- The free-trade fingerprint explicitly identified lower prices, competition,
  faster growth, consumer benefit, and trade/payments rather than generic
  capitalism-versus-socialism.
- Some `primary_issue` values are prose descriptions rather than compact stable
  labels. This is acceptable for validation but should be normalised before any
  large-scale deterministic topic aggregation.
- Desired evidence sometimes favours charts/data visualisations over editorial
  photography. Prompt refinement should ask for both concrete scenes and
  evidentiary graphics.

Overall, the sampled quote quality was strong enough to have permitted image
analysis, but cost ambiguity independently required the stop.

## 7. Image stage

Not started.

```text
active inventory = 79
completed        = 0
failures         = 0
actual cost      = $0
```

No quarantined or original image was analysed. The free-trade image therefore
has no saved independent image fingerprint in this execution.

## 8. Critic stage

Not started.

The validation set contains exactly 150 unique generated quote/image pairs and
includes the free-trade pairing:

```text
quote = 1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b
image = tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png
```

No critic score, agreement rate, false-positive/negative count, or mismatch
distribution exists yet. The generated partial machine-readable report leaves
those values empty rather than fabricating them.

## 9. Recommendation

Do not resume paid execution from the current ledger without first deciding how
to account for the interrupted request. The safest approach is to archive this
run as partial and begin a new ledger/run identifier with the known `$0.691074`
carried into the overall approved budget plus a conservative allowance for the
ambiguous request.

Before further scaling, add explicit claim-level fields:

- quote `component_claims`;
- image `implied_claims`;
- optional scene-based versus chart-based visual directions.

The existing primary/secondary fields are good, but explicit claim arrays would
make critic explanations and human error analysis more auditable.

## 10. Tests

After accounting changes:

```text
14 passed in 0.17s
python3 -m py_compile ... = passed
git diff --check = passed
```

Tests cover exact tick conversion, missing-cost blocking, cached/reasoning token
capture, strict structured-output payloads, no tools, prompt isolation, resume,
atomic writes, and cost gates. No test made an external call.

## 11. Research files created or updated

Key outputs include:

- `semantic_alignment_research/quote_semantic_fingerprints.json`
- `semantic_alignment_research/cost_ledger.json`
- `semantic_alignment_research/preflight_estimate.json`
- `semantic_alignment_research/pricing/models_response.json`
- `semantic_alignment_research/pricing/pricing_verification.json`
- `semantic_alignment_research/runs/responses/quote/`
- `semantic_alignment_research/validation_execution_summary.json`
- `semantic_alignment_research/semantic_alignment_validation_execution_report.md`
- `semantic_alignment_research/validation_critic_results.csv`

Source changes remain untracked in `analyse_semantic_alignment_xai.py`,
`semantic_alignment/`, and `tests/test_semantic_alignment_pipeline.py`.

## 12. Production safety

Production config and generated metadata hashes remained unchanged. The live bot
continued running and may legitimately update its own state/history/log. No
semantic-alignment process remained running after the stop, and the research
code has no write path to production state, history, receipts, config, logs, or
metadata.

No production receipt or ambiguous-post barrier was present at final inspection.

## 13. Git status/diff

Task source and research outputs are untracked, so ordinary `git diff --stat`
does not list them. Pre-existing generated metadata modifications, four
quarantined PNG deletions, and unrelated runtime/research artefacts remain
untouched. Nothing was staged.

## 14. Confirmations

- No production behaviour changed.
- The bot was not stopped, restarted, or signalled.
- No X post or reply was created.
- No web-search, X-search, or agent tool was enabled in model requests.
- The research process did not edit production state, configuration, history,
  receipts, logs, or production metadata.
- The canonical counterfactual simulation was not rerun.
- No image-fingerprint call, validation-critic call, full-shortlist critic run,
  or canonical replay occurred.
- Nothing was staged, committed, pushed, deployed, or enabled in production.

The only external operations were the non-inference model-metadata lookup and
the quote-fingerprint requests described above. Exact known inference spend is
`$0.691074`; one interrupted request has an unknown billing outcome, so no
stronger cumulative-cost claim is made.
