# Historical-context public confidence cleanup

## Result

Public historical-context replies now omit the six-dimensional confidence line. The live posting path calls an explicit public renderer; internal tooling has an explicit internal renderer that retains the complete confidence breakdown. No quotation, research packet, source role, confidence field, or eligibility decision was changed.

## Rendering behaviour

`historical_context_formatter.py` now defines schema v4 and two explicit entry points:

- `format_context_reply_public`: renders `Context`, optional `Meaning`, `Verification`, and useful `Source` lines only.
- `format_context_reply_internal`: renders the same material plus the complete `Confidence` line.

Both return the original six confidence dimensions in structured metadata. The standalone developer CLI defaults to internal rendering and supports `--rendering-mode public` for an exact public preview. Existing v2 and v3 receipt metadata remains readable; new v4 receipt metadata records `rendering_mode`.

The public verification labels are derived without changing the underlying evidence:

- exact audited wording: `Exact wording verified`;
- historically verified or normalised wording: `Historically verified variant`;
- verified excerpt: `Verified excerpt`;
- attribution support without exact wording: `Attributed, but exact wording not independently verified`;
- insufficient verification evidence: `Research incomplete`.

`mrsMThatcher2.py` imports and calls only `format_context_reply_public` for X historical-context replies. Detailed confidence dimensions remain in formatter results, receipt metadata, and structured logs.

## Regression example

Quote ID: `9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4`

Canonical text remains unchanged:

> We were the first country to attempt and to succeed in rolling back the frontiers of socialism, which is the first cousin to communism.

The old public output ended with:

> Confidence — Attribution: unknown; wording: unknown; source event: unknown; date: unknown; historical context: unknown; interpretation: high

The new public output is:

```text
Context — The surviving attribution does not establish an occasion, date or immediate historical issue.

Meaning — That Britain under her leadership successfully reversed socialist policies, which she equated with the dangers of communism, by promoting individual self-reliance.

Verification — Research incomplete

Source — No reliable source located
```

The internal renderer still emits the detailed line above and returns the same six-field confidence object as the public renderer.

## Corpus invariants

- Completed research packets: 626
- Research-unresolved records: 6
- Attribution-eligible quotations: 610
- Eligible quote ID/text set SHA-256: `d928dd7adb3b7a5e7dc016d07ebaadd575dc8e898bc7ea2693f914a494a26ae7`
- `mrsMThatcher.txt` SHA-256 before/after: `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`
- `quote_analysis.json` SHA-256 before/after: `e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a`
- Research packets SHA-256 before/after: `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`
- Source-role audit SHA-256 before/after: `ebab8591365d341b9968a6964d3b33b98d9de5a51eab1c29268d6206eb5dfa5c`

All 610 eligible quote IDs and texts are unchanged. The excluded non-Thatcher, misattributed, and unresolved records remain excluded. Source-role audit records and internal confidence values are byte-for-byte unchanged.

## Derived manifest

The inactive attribution-cleaned v3 candidate fingerprints `historical_context_formatter.py`. After the formatter changed, its strict runtime validation correctly reported a stale source hash. It was regenerated using the existing offline `prepare-v3-shadow` command; no pair decision or policy changed.

- Candidate manifest SHA-256: `2f7913f5b86b3b3a192255e769336086c0179db7c08ddfdfcff484dc7ad70ecd`
- Quotations: 610
- Images: 91
- Known pairs: 22,066
- Allow: 21,938
- Veto: 128
- Active enforcement: false
- New AI calls: 0

Only the formatter source fingerprint, dependent hashes, audit generation time, and measured load time changed. The running shadow runtime was not reloaded.

## Tests

Focused and affected suites covered:

- public output for all 610 eligible packets;
- the requested socialism regression;
- Thatcher Foundation transcript, Thatcher-authored book, verified variant, secondary recollection, and no-source cases;
- internal retention of all six confidence dimensions;
- live production use of the public renderer;
- v2/v3 receipt compatibility and v4 durability;
- strict candidate-manifest loading.

Results:

- Initial focused set: 134 passed, 410 deselected.
- Additional affected groups: 102 passed.
- Initial full suite: 3 failed, 2,064 passed, 1 skipped. The failures exposed a stale inactive manifest fingerprint, two missing docstrings in pre-existing untracked tooling, and a five-second test-only worker scheduling assumption under full-suite I/O load.
- Focused fixes: 3 passed.
- Final full suite: **2,067 passed, 1 skipped, 12 warnings** in 781.93 seconds.
- Targeted `py_compile`: passed.
- `git diff --check`: passed.

The worker test retains its bounded deadlock assertion with a 30-second ceiling; production concurrency code was not changed.

## Files changed

Production and schema:

- `historical_context_formatter.py`
- `historical_context_reply_schema.json`
- `mrsMThatcher2.py`

Tests:

- `tests/test_historical_context_reply.py`
- `tests/test_historical_context_source_roles.py`
- `tests/test_quote_research_corpus.py`
- `tests/test_unit_helpers.py`

Regenerated inactive deployment-candidate metadata:

- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

The pre-existing untracked `tools/build_historical_context_chatgpt_research_pack.py` received only the two docstrings required by the repository documentation invariant. Its pre-existing untracked research-pack output was otherwise untouched.

## Production isolation

Before and after work, `mrsMThatcher.service` remained active with:

- wrapper PID: 771839
- Python child PID: 771840
- start time: 2026-07-21 20:51:45 BST
- restart count: 0

No service was stopped, restarted, reloaded, or signalled. No X post was made. No production receipt, posting ledger, posting history, analytics data, configuration, source-role evidence, or live shadow manifest was modified. No commit, push, or deployment occurred.

READY FOR COMMIT, PUSH AND CONTROLLED DEPLOYMENT
