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
| `mrsMThatcher2.py` | Production scheduling, quotation/image selection, replies, receipts and recovery | X and OpenAI only after explicit production bootstrap; durable production state |
| `single_call_reply.py` | Frozen Sol prompt/schema, bounded context and facts, one-call orchestration, mechanical validation and durable-draft validation | One injected OpenAI Responses call; local validation and hashing |
| `reply_evidence.py` | Lexically shortlist validated local passages for compact trusted facts | Local corpus reads only |
| `historical_context_formatter.py` | Canonical research loading, compact context formatting and context-reply persistence | Local state; posting only through an injected callback |
| `shadow_lifecycle.py` | Strict validation for the versioned shadow-feature lifecycle register | Local file reads only |
| `mrs_log_digest.py` | Structured/legacy log parsing, aggregation and Markdown/JSON reports | Local log and resume-state reads/writes; no provider calls |
| `mrs_log_digest_markdown.py` | Prepared-report Markdown presentation and section rendering | None; receipt lifecycle analysis is supplied by the caller |
| `mrs_log_digest_costs.py` | Published-cost cache validation, UTC-window accounting and report preparation | Cache reads only through an explicitly supplied stable reader; paths, clock observations and strict JSON parser supplied by caller |
| `mrs_log_digest_runtime.py` | Current state/configuration validation and operator pause observations | Reads only through supplied stable readers; explicit project paths, strict JSON parsers, file-time conversion and pause clock; no import-time runtime access |
| `mrs_log_digest_corpus.py` | Historical-corpus counts, availability, policies and hashes | Reads the existing research/audit paths under an explicit project directory using supplied strict parsing and file hashing; parsing and hashing remain separate reads |
| `mrs_log_digest_generated_pool.py` | Generated-image discovery, metadata/hash validation, curation and used-history observations | Reads/scans the existing pool locations relative to an explicit base directory; supplied strict parsers, hashing, clock and ISO timestamp parser; no writes or import-time runtime access |
| `mrs_log_digest_historical_events.py` | Historical-context event field projection, family counters and emitted-event quality summaries | Only supplied invocation-local counters and event insertion callbacks are mutated/called; no I/O or import-time runtime access |
| `mrs_log_digest_generated_identity.py` | Generated-identity policy/shadow observation parsing and summaries | Mutates only supplied observation/error lists, local counters and the parser result's timestamp; strict parser, diagnostic formatter and lazy source-reference callback supplied by caller; no I/O or import-time runtime access |
| `mrs_log_digest_values.py` | Shared digest scalar conversions and report vocabulary | None |
| `mrs_engagement_analytics.py` | Read-only X metrics collection and isolated SQLite reporting | X reads only with explicit flags; writes only under `engagement_analytics/` |
| `hybrid_reply_retrieval.py` | CLI for local hybrid retrieval experiments and review artefacts | Offline by default; provider-review commands require explicit execution and budgets |
| `semantic_alignment/hybrid_reply_retrieval.py` | Local E5 indexing, lexical-versus-hybrid evaluation and historical replay | Offline research files only; it is not imported by the production bot |

`mrsMThatcher2.py` is intentionally import-safe: importing it does not load the
private host configuration, acquire the production lock or enter the posting
loop. Operational entry points require `production_bootstrap()` first.

Digest cost callers retain `load_openai_cost_cache`,
`estimate_openai_cost_window` and `openai_published_cost_report` in
`mrs_log_digest`. The digest resolves cache-path and clock defaults; the costs
module accepts explicit inputs and has no import-time runtime access.
`prepare_openai_published_cost_report` consumes an already loaded cache without
I/O. The digest's historical `provider_usage` argument remains accepted and
ignored.

Digest runtime callers retain `load_current_runtime_state`,
`load_current_runtime_config` and `runtime_control_snapshot` with their original
signatures and result shapes. Their wrappers pass the digest's stable reader,
strict parser and time dependencies into `mrs_log_digest_runtime`. State and
configuration retain native JSON number types; controls use the exact Decimal
parser. The control clock is a callable sampled after document/key/generation
validation and before boolean/time validation. State observation time remains
sampled in the digest after the state loader returns, separately from report
generation time and the file mtime. Analysis and current-health overlays remain
in the digest. Shared `dt_text` and `bounded_exception_status` now live in the
values leaf and remain explicitly importable through the digest.

Digest corpus and image-pool callers retain `historical_context_corpus_snapshot`
and `generated_pool_health_snapshot` with their original signatures and result
shapes. Their wrappers supply the digest's strict native JSON parsers and
`file_sha256`; the pool wrapper also supplies `datetime.now` and
`datetime.fromisoformat`, preserving test patches. The corpus retains separate
parse/hash reads, including partially loaded counts when hashing fails. Pool
basename and metadata schema/kind constants belong to the generated-pool module
and remain explicitly importable through the digest. Shared policy vocabulary
still comes from the values leaf.

The pool's implicit clock is sampled after active-image validation and before
curation transaction reads. Explicit times bypass that clock; naive times retain
host-local timezone handling. `run_digest` still supplies the selected window's
end (explicit `--until`, otherwise the last selected record), using an implicit
pool clock only when neither exists. This reference time remains distinct from
digest generation time. Post rates, utilisation, runway, configuration loading,
analysis and report assembly stay in the digest. Both snapshot modules have no
import-time runtime effects or imports back into the digest, Markdown or bot.

Historical event callers use `record_historical_context_semantic_gate`,
`record_historical_context_runtime`, `record_historical_context_obligation` and
`record_historical_context_outbox` with parsed fields, the record timestamp and
the coordinator's `add_event` callback. Only the latter three need the local
counter. They preserve generic insertion/counting through that callback and
update their own status/state counters afterward.

`prepare_historical_context_reply` returns ordered presentation fields; the
digest emits them through `add_event` and keeps that exact returned object.
The digest then validates completed/already-completed anchors against the
original strict structured record before calling `count_historical_context_reply`
with the projected, pre-truncation status. Preparation confers no publication
authority. The digest retains `historical_context_reply_posted` validation,
production/self-test attribution, durable-history/receipt correlation and public
text enrichment. No pending state or general dispatcher is introduced.

`historical_context_quality_summary` and its verification/confidence count
helpers now belong to the historical-events module and remain importable through
the digest, along with their vocabulary. Quality still consumes the same emitted,
truncated and filtered events at the original point in analysis. The shared
post-ID/UTF-8 validators, bounded text/integer/boolean projections, their bounds
and regexes, and `_count_optional` belong to the values leaf and remain explicitly
importable through the digest. Durable-history validation retains its distinct
schema and vocabulary rules. Dependency direction is digest → historical events
→ values; the new module imports no coordinator, renderer, bot or runtime reader.

Generated-identity callers retain `generated_identity_shadow_summary(events)`
and `generated_identity_policy_summary(events)` as explicit digest re-exports
with unchanged signatures. Their category definitions, percentage denominators,
compatibility aliases, ordering and legacy/current counterfactual distinctions
are unchanged. Summaries do not mutate their inputs, and baseline/shadow winners
remain observational rather than evidence of publication.

`record_generated_identity_shadow` and `record_generated_identity_policy` handle
already matched log messages. Each receives the message, record timestamp and
level, plus the dedicated observation list, local counter, error list, strict
native JSON-object parser, diagnostic text formatter and a zero-argument
source-reference callback. The digest retains substring matching at the original
branch positions and continues after either outcome. Helpers retain first-marker
splitting, whitespace stripping, native JSON types and the original parser-result
object, replacing its `time` with the formatted record timestamp. A successful
observation acquires no generic event counter or provenance fields. Both
production and self-test observations retain their existing treatment.

Only encoding/parsing is inside the parsing exception handler. Malformed input
retains the exact error level, exception detail, 240-character escaped raw-text
limit and source reference; the callback is invoked only on failure. Observation
schema/count problems accepted by parsing still fail later in summarisation,
without becoming parse errors or being coerced to zero. All collections belong
to one `analyse` invocation; no state is accumulated by the module.

Dependency direction: digest → generated identity → values.
`most_common_with_cutoff_ties` moves unchanged to the values leaf and remains an
explicit digest import for original-editorial reporting. The generated-identity
module imports no digest, renderer, bot or snapshot readers. Import performs no
home lookup, runtime I/O, logging setup or service initialisation. Report assembly,
original-editorial companion deduplication, generated-image spacing/resume state
and image-selection algorithms remain with their existing owners.

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
  or OpenAI endpoints without the deliberate test override phrase.

## Support And Research Code

The `semantic_alignment` package contains reusable, versioned research logic:
schemas, provider routing, cost ledgers, corpus validation, semantic contracts,
replay and reporting. Root-level `analyse_*`, `compare_*`, `recover_*` and
`run_*` scripts are command-line orchestration over those modules. Tools under
`tools/` provide isolated analysis and local review applications; their own
README files define mutable-data boundaries.

`reply_strategy.py` and `tools/reply_claim_diagnostics.py` are retained for
offline historical evaluation only. Neither is imported by the production bot;
the sole production conversational implementation is `single_call_reply.py`.

Historical reports and paid raw responses are evidence, not runtime APIs. Do
not rewrite them to match newer terminology; new reports should link back to
their source hashes and state the policy/schema version they observed.

`semantic_quote_image_veto.py` and
`semantic_alignment/quote_image_semantic_veto.py` are retained solely for
offline replay and historical research. They are not production dependencies
and are not loaded by the bot, digest or support monitor.
