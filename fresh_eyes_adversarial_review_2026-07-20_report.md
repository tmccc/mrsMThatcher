# Fresh-eyes adversarial review

Date: 20 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Scope: `mrsMThatcher2.py`, `reply_strategy.py`, `historical_context_formatter.py`, `mrs_log_digest.py`, `semantic_alignment/`, and directly relevant tests.

## Executive verdict

Six defects were confirmed with deterministic, network-free reproductions. Three are production safety defects: an ambiguous X write can be retried after an HTTP 5xx, unsupported factual allegations can pass in non-historical reply modes, and the Berlin Wall factual-answer guard accepts the direction in reverse. The other three affect monitoring or support tooling: semantic-veto events can be attached to the wrong post, `shadow-status` can call a stale manifest valid, and the historical-context CLI can format a completed packet known not to be Thatcher's.

No finding below is based solely on static-analysis output. Each was reproduced through the affected function or CLI. No code was fixed in this review.

## Confirmed findings

### High: HTTP 5xx responses to X writes bypass the ambiguous-write barrier

Locations:

- `mrsMThatcher2.py:2637-2668` (`x_request`)
- `mrsMThatcher2.py:4099-4119` (`create_post`)
- `mrsMThatcher2.py:10837-10851` (regular-post error scheduling)

`create_post` correctly calls `x_request(..., ambiguous_write=True)`. Transport exceptions and malformed successful responses become `AmbiguousRemotePostOutcome`, which records the durable ambiguity marker. However, every HTTP response at or above 400, including 500, 502, 503 and 504, is converted to ordinary `ApiError` without considering `ambiguous_write`.

The synthetic-response reproduction returned:

```text
exception=ApiError
ambiguous_outcome=false
durable_ambiguity_marker=false
process_ambiguity_latch=false
```

for a `503` response to `create_post`. No network request was made. A gateway or upstream 5xx can occur after a write reached the remote service, so the ordinary retry path can create a duplicate public post or reply. The same primitive is used by all X post writes.

Required correction: classify write responses whose remote acceptance cannot be disproved as ambiguous, persist the normal barrier, and add explicit 5xx-after-write regression coverage. Deterministic client rejections such as 400/403 should remain ordinary errors where acceptance is impossible.

### High: unsupported allegations pass when a non-historical reply mode falsely declares `factual_claim_made=false`

Locations:

- `reply_strategy.py:1114-1224` (`validate_reply_decision`)
- `reply_strategy.py:627-678` (`principle_reply_assertion_error`)
- `mrsMThatcher2.py:8530-8647` (`ask_grok_for_reply` validation path)
- `mrsMThatcher2.py:7898-7932` (`generated_reply_is_safe_enough`)

The structured model response is untrusted, but local validation relies on its `factual_claim_made` flag for humour and warm-reply modes. The assertion detector is applied only to `principle_reply`; the final generic safety check tests length, blocked patterns and persona phrases, not unsupported factual claims.

Full `validate_reply_decision` calls accepted all of these with empty evidence, `grounded=false` and `factual_claim_made=false`; the downstream safety check also returned `true`:

```text
wry_reply:      Andy Burnham lied about his record.
warm_reply:     The government covered up the figures.
deadpan_reply:  Keir Starmer has betrayed every promise.
```

The narrower principle detector also accepted this unsupported premise-preserving assertion:

```text
Incoming: Why does the government keep concealing the truth?
Reply:    The government continues to conceal the truth.
```

Impact: a malformed or overconfident model decision can publish an unsupported actor-specific allegation while claiming that no factual assertion was made. The prompt prohibition is not an adequate post-generation safety boundary.

Required correction: independently detect or conservatively reject concrete assertions in every ungrounded mode, without relying on the model's Boolean. Preserve genuinely non-factual humour and general principles.

### High: the direct factual-answer guard accepts the Berlin Wall direction in reverse

Locations:

- `reply_strategy.py:911-918` (Berlin-specific condition)
- `reply_strategy.py:919-1027` (evidence token validation)
- `reply_strategy.py:1240-1248` (production decision gate)

For the question:

```text
Where did people run towards when the Berlin Wall fell?
```

with evidence explicitly stating movement from East Berlin/East Germany towards West Berlin/West Germany, the full validator accepted:

```text
People moved from West Berlin and West Germany towards East Berlin and East Germany.
```

The special case requires only the presence of `east`, `west`, and `Berlin` or `Germany`. The subsequent evidence check is an unordered token-overlap check, so it cannot verify the relation or direction. It also accepts the correct answer, demonstrating that the defect is specifically the missing relation check rather than an unusable guard.

Impact: the bot can answer a concrete factual question directly and confidently while stating the opposite fact from its grounded evidence.

Required correction: validate the directed relation, preferably from structured evidence or a tightly scoped relation-aware check, and add the reversed answer as a regression case.

### Medium: the digest can mark an unrelated semantic-veto selection as a confirmed post

Locations:

- `mrs_log_digest.py:2381-2388` (single pending event slot)
- `mrs_log_digest.py:2600-2629` (event correlation)

The digest stores the latest `quote_image_semantic_veto_shadow` event in one pending variable. The next successful `main_post_posted` quote event marks it confirmed without comparing quote ID/hash, image hash/basename, or elapsed time.

A synthetic digest stream containing a veto event for quote `a...a` with `t01.jpg`, followed three hours later by an unrelated posted quote `d...d` with `t02.jpg`, produced a semantic-veto row equivalent to:

```text
quote_hash=a...a
selected_image=t01.jpg
confirmed_post=true
post_id=2079000000000000000
```

Impact: if one selection fails before posting, or its structured event is absent, the digest can attribute the next post receipt to the stale selection and misreport what was publicly posted.

Required correction: correlate by immutable quote and image identities, impose a bounded sequence/time relationship, and leave unmatched events unconfirmed.

### Medium: `shadow-status` validates schema but not source hashes

Locations:

- `semantic_alignment/quote_image_semantic_veto.py:790-824` (`ShadowRuntime.load`)
- `semantic_alignment/quote_image_semantic_veto.py:1405-1447` (`shadow_status`)

Production startup correctly verifies every manifest source path and SHA-256 and returns `manifest_stale` on a mismatch. The status command directly calls `validate_compiled_manifest` and reports `valid=true` without performing those source checks.

Reproduction: a valid compiled manifest was copied into an otherwise empty temporary project directory, so none of its recorded source files existed. `shadow_status()` returned:

```text
manifest_valid=true
source_paths_exist=false
```

Impact: the operator-facing status command can report a manifest as valid even though production will disable it as stale. `shadow-preflight` uses `ShadowRuntime` and does not share this defect.

Required correction: make status use the same source-hash validation as startup/preflight and distinguish schema validity from runtime freshness.

### Medium-low: `historical_context_formatter.py --quote-id` bypasses Thatcher attribution eligibility

Locations:

- `historical_context_formatter.py:161-177` (`packet_for_posted_quote`)
- `historical_context_formatter.py:867-881` (CLI entry point)

The production resolver checks `packet_is_attributed_to_margaret_thatcher`. The CLI uses that resolver only for `--quote-text`; with `--quote-id` it performs a direct `packets.get()` and passes the result to the formatter without attribution validation.

The current completed corpus contains packets explicitly attributed to other speakers. Running the CLI with quote ID `69a1c2be69f8e802aaad1948b85557bdff3130e126a7602600b485e7cff048c8` successfully generated a context reply for the quotation beginning:

```text
To be successful you have to be selfish...
```

The packet identifies Michael Jordan as speaker and `Driven from Within` as the source. The generated text did not disclose that it was not Thatcher material.

Impact: the support CLI can emit polished, apparently usable historical-context copy for an attribution-ineligible quotation. The live bot path uses `packet_for_posted_quote` and is not exposed to this bypass.

Required correction: apply the same attribution check for both CLI lookup forms and add a non-Thatcher packet regression test.

## Areas checked without a confirmed defect

- Regular-post, meme-post and reply receipt barriers were inspected; no new bypass was confirmed in this pass.
- Semantic-veto runtime classification remains fail-open and non-enforcing as configured.
- The source-hash defect is confined to `shadow-status`; startup and `shadow-preflight` use the stricter runtime loader.
- The historical-context attribution bypass is confined to direct CLI lookup; the production reply resolver remains attribution-gated.
- Static-analysis warnings around duplicate event keys and optional digest values were inspected and found to be false positives or guarded paths.

## Validation performed

Focused offline tests:

```text
348 passed in 12.55s
36 passed, 430 deselected in 1.89s
```

These covered reply strategy, historical-context replies, semantic-veto shadow behaviour, digest safety/observability, follow-up fail-safe handling, generated-image utilisation, shadow health and selected posting/receipt helpers. The focused suites passing is consistent with the findings: the reproduced boundary cases are currently absent from the tests.

Additional checks:

- deterministic direct-function and synthetic-event reproductions for all six findings;
- network-free digest dry run using `--no-state --no-update-state`, with all outputs under `/tmp`;
- `python3 -m py_compile` for the reviewed top-level modules and `semantic_alignment/*.py`: passed;
- `git diff --check`: passed before this report was added;
- full 12-minute suite: not run because the task was a review and focused suites plus direct reproductions were sufficient to confirm the defects.

## Production isolation

Before and after the review:

```text
service state: active/running
wrapper PID:   1595442
Python child:  1595443
start time:    Sun 2026-07-19 23:07:45 BST
restart count: 0
```

The service uses the reviewed project file through `/usr/local/bin/mrsMThatcher2.py`, which resolves to `/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`.

No X request, provider call, service stop/restart/signal, production-state write, receipt write, log modification, configuration change, commit, push or deployment occurred. The only repository change made by this task is this report.

## Recommended order

1. Close the ambiguous HTTP 5xx write gap and add receipt/duplicate regression tests.
2. Make unsupported-assertion validation independent of model metadata across all ungrounded modes.
3. Make factual-answer validation relation-aware for the Berlin direction regression.
4. Correct semantic-veto event correlation in the digest.
5. Align `shadow-status` freshness checks with production startup.
6. Apply attribution eligibility consistently in the historical-context CLI.
