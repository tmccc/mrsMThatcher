# Python Architecture And Documentation

## Documentation Standard

Maintained non-test Python follows the public-docstring portion of PEP 257:

- every versioned or pending non-ignored module states its purpose;
- every non-private top-level function and class has a docstring;
- every non-private method and public constructor has a docstring;
- complex safety, persistence, network and cost-boundary APIs describe their
  material side effects rather than merely repeating their names.

Names beginning with `_`, nested implementation helpers and tests are not part
of the public documentation contract. Tests use descriptive names instead.
Run the dependency-free contract check with:

```bash
python3 tools/check_python_documentation.py
```

The check derives its scope from versioned and pending non-ignored files, so it
catches omissions before commit while ignoring runtime payloads, model caches
and AppleDouble files.

## Operational Modules

| Module | Responsibility | External effects |
|---|---|---|
| `mrsMThatcher2.py` | Production scheduling, quotation/image selection, replies, receipts and recovery | X and xAI only after explicit production bootstrap; durable production state |
| `reply_strategy.py` | Retrieval, structured reply parsing, grounding, relevance and safety validation | Local corpus reads; its audit CLI is offline |
| `historical_context_formatter.py` | Canonical research loading, compact context formatting and context-reply persistence | Local state; posting only through an injected callback |
| `shadow_lifecycle.py` | Strict validation for the versioned shadow-feature lifecycle register | Local file reads only |
| `mrs_log_digest.py` | Structured/legacy log parsing, aggregation and Markdown/JSON reports | Local log and resume-state reads/writes; no provider calls |
| `mrs_engagement_analytics.py` | Read-only X metrics collection and isolated SQLite reporting | X reads only with explicit flags; writes only under `engagement_analytics/` |
| `hybrid_reply_retrieval.py` | CLI for local hybrid retrieval experiments and review artefacts | Offline by default; provider-review commands require explicit execution and budgets |
| `semantic_alignment/hybrid_reply_retrieval.py` | Local E5 indexing, lexical-versus-hybrid evaluation and historical replay | Offline research files only; it is not imported by the production bot |

`mrsMThatcher2.py` is intentionally import-safe: importing it does not load the
private host configuration, acquire the production lock or enter the posting
loop. Operational entry points require `production_bootstrap()` first.

## Quotation Corpus Accounting

The maintained research corpus contains 627 completed packets and five
unresolved quotations. The active source is partitioned as follows:

| Layer | Count |
|---|---:|
| Canonical records currently in `mrsMThatcher.txt` | 619 |
| Current attribution-eligible runtime quotations | 611 |
| Source-retained but runtime-ineligible records | 8 |

The eight runtime exclusions comprise the five unresolved records and three
additional packets rejected by the current source-grounded attribution
predicate. Historical research, logs and receipts retain their original IDs.
The production selector, reply retriever and historical-context path fail closed
unless attribution eligibility is exactly 611.

## Safety Boundaries

- Main-post, meme, context-reply and conversational-reply receipts are separate
  durable transaction barriers.
- An ambiguous X write pauses all posting until an operator reconciles it.
- Generated images are disabled by source default and, when enabled locally,
  obey the configured original-post spacing rule.
- Hybrid retrieval is an offline-only benchmark and is absent from the
  production reply path.
- Generated-image identity-policy work is suspended whenever the generated
  pool is disabled. The original-editorial selector remains observational.
- Research/provider CLIs require explicit execution flags and bounded spend;
  offline audit, replay and report commands do not contact providers.
- Tests use temporary state and fake endpoints. They must never point at live X
  or xAI endpoints without the deliberate test override phrase.

## Support And Research Code

The `semantic_alignment` package contains reusable, versioned research logic:
schemas, provider routing, cost ledgers, corpus validation, semantic contracts,
replay and reporting. Root-level `analyse_*`, `compare_*`, `recover_*` and
`run_*` scripts are command-line orchestration over those modules. Tools under
`tools/` provide isolated analysis and local review applications; their own
README files define mutable-data boundaries.

Historical reports and paid raw responses are evidence, not runtime APIs. Do
not rewrite them to match newer terminology; new reports should link back to
their source hashes and state the policy/schema version they observed.

`semantic_quote_image_veto.py` and
`semantic_alignment/quote_image_semantic_veto.py` are retained solely for
offline replay and historical research. They are not production dependencies
and are not loaded by the bot, digest or support monitor.
