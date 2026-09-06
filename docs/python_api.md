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
| `mrs_bot_image_scoring.py` | Quotation/image lexical matching, topic IDF, visual energy, seasonal exclusion and component scoring | Explicit current coordinator values and helper callbacks; no I/O, retained callbacks, cache or runtime initialisation |
| `mrs_bot_original_editorial.py` | Original-editorial concepts/profiles, metadata validation/loading, scoring, comparison logs and winner application | Reads metadata and discovers/hashes images only through explicit runtime loader calls; uses the supplied root cache and logger; no import-time work or retained callbacks |
| `mrs_bot_generated_identity.py` | Generated-image identity audit validation/loading, policy rows/selection and counterfactual diagnostics | Explicit runtime audit reads and current discovery/hash callbacks; supplied root cache, settings and logger; private RNG replay and caught shadow-log failures; no import-time work or retained callbacks |
| `mrs_bot_asset_metadata.py` | Quote/image/meme metadata loading and merging, quote/generated identity helpers, image validation and catalog discovery | Explicit metadata reads and image discovery; current root settings, helpers, logger and stale-image exception; existing fallbacks and reference boundaries; no writes, import-time work, retained callbacks or cache |
| `mrs_bot_quote_candidates.py` | Ordinary quotation windows/weights, source and canonical eligibility loading, candidate preparation and cycle selection | Current root settings, helpers and logger; explicit source/metadata reads and deferred canonical validation; shared production RNG and in-place caller used-set clears; no durable writes, retained callbacks, cache or import-time work |
| `mrs_bot_image_selection.py` | Image eligibility/cycles, generated-image spacing and counters, scored image selection, ordinary pair retries and fixed experimental quote images | Current root helpers, settings, exceptions and logger; metadata rechecks, shared production RNG and caller history/counter mutations; legacy normalization invokes the root save callback before the remaining-index check; persistence/receipts, publishing and configuration authority stay root; no retained callbacks, cache or import-time work |
| `mrs_bot_quote_posting.py` | Complete ordinary quotation posting orchestration, including experimental members and existing local recovery branches | `post_random_quote(lines_used: set, images_used: set, state: dict) -> None` remains a root adapter passing 76 current callbacks, settings, logger, exception/type authorities and the existing engagement-question module; original body/order, closure references and caller histories preserved; transport, receipt, persistence and scheduling implementations remain root; shared standard-library RNG, no retained dependencies or import-time work |
| `mrs_bot_daily_meme.py` | Daily meme filename/summary/catalog selection, production-calendar scheduling and the complete transactional posting workflow | Eighteen root APIs retain their signatures, defaults and annotations through seventeen adapters and the regex-only filename alias; current root callbacks, settings, logger and exception authority preserve caller state/path/item and closure references, conditional RNG draws, stage events and distinct recovery branches; asset metadata, shared state helpers, receipt/transport/persistence authority stay in existing owners; explicit calls may scan the supplied catalog, save caller state or publish through callbacks; no retained dependencies or import-time work |
| `mrs_bot_legacy_reply_validation.py` | Twelve frozen tested-pipeline, AI-first v3 and single-Sol schema 1/2 draft validators plus seventeen immutable schema/version/hash definitions | Root names/signatures/defaults/annotations remain through eight explicit adapters and four function aliases; all constants directly alias owner objects, and adapters pass current root constant references, helper callbacks and size limits on each call; exact historical bodies, unsigned hashes, dispatch order and native error boundaries preserved; current draft validation, lifecycle, recovery, transport and configuration authority stay in existing owners; standard-library-only import constructs fixed strings/frozensets without I/O, environment/provider/RNG work, runtime imports or retained callbacks |
| `mrs_bot_reply_state.py` | Fourteen current pending-draft/value validators and already-confirmed conversational/same-author history queries, plus the fixed conversational-lane frozenset | Eleven explicit root adapters and three aliases preserve names/signatures/defaults/annotations and supply current helpers, lane set, limits, logger, exception/result classes and configuration per call; the owner requires the recent-reply limit explicitly while the root retains its definition-time default and current body cap; original bodies preserve references, deep copies, ordering and recovery/error boundaries; transport, durable writes, persistence and receipt lifecycle authority remain in existing owners; standard-library-only import constructs the fixed frozenset without file/environment/provider/RNG work or retained callbacks |
| `single_call_reply.py` | Frozen Sol prompt/schema, bounded context and facts, one-call orchestration, mechanical validation and durable-draft validation | One injected OpenAI Responses call; local validation and hashing |
| `reply_evidence.py` | Lexically shortlist validated local passages for compact trusted facts | Local corpus reads only |
| `historical_context_formatter.py` | Canonical research loading, compact context formatting and context-reply persistence | Local state; posting only through an injected callback |
| `shadow_lifecycle.py` | Strict validation for the versioned shadow-feature lifecycle register | Local file reads only |
| `mrs_log_digest.py` | Log-input coordination, aggregation and Markdown/JSON reports | Local log and resume-state reads/writes; no provider calls |
| `mrs_log_digest_context.py` | Historical context, config backscan and digest-cursor persistence | Reads supplied log/cursor paths; saves through a temporary sibling and replacement; explicit current helpers, marker/tail limits, Counter factory, diagnostic and save-time clock; no import-time I/O or publication authority |
| `mrs_log_digest_input_io.py` | Stable file observations, strict native/Decimal JSON parsing and canonical receipt/history encodings | Reads only supplied paths; explicit current sibling callbacks; ordinary file hashing retains its separate read contract; no writes or import-time runtime access |
| `mrs_log_digest_records.py` | Shared frozen records, bounded source references, fingerprints, resume-boundary filtering and selected log input reading | Reads/stats supplied log paths and emits existing missing-input warnings; explicit current regex, constructor, parsers, readers and helpers; no import-time runtime access |
| `mrs_log_digest_legacy_posts.py` | Raw legacy quiet/lane, quote/image, spacing, meme, created-post and conversational reply observations and companion response parsing | Supplied shared records, pending/latest objects, lists, counters, production event identities and current event/literal/ID helpers; explicit handled/state returns; no I/O, clock sample, runtime access or provider/posting actions |
| `mrs_log_digest_transactions.py` | Passive X request, transaction, receipt and media observation preparation, legacy matching, receipt/media correlation and post-scan receipt/error reporting preparation | Supplied records, snapshots, health, pending state, lists/statistics and current helper/source callbacks; no I/O, clock sample, runtime access or publication authority |
| `mrs_log_digest_api_health.py` | Passive X/cooldown observation, latest-error enrichment and API counter/failure/report preparation | Supplied records, shared lists, production event identities and current helpers; separate preparation and report materialisation; no I/O, clock sample, source selection or publication authority |
| `mrs_log_digest_markdown.py` | Prepared-report Markdown presentation and section rendering | None; receipt lifecycle analysis is supplied by the caller |
| `mrs_log_digest_costs.py` | Published-cost cache validation, UTC-window accounting and report preparation | Cache reads only through an explicitly supplied stable reader; paths, clock observations and strict JSON parser supplied by caller |
| `mrs_log_digest_provider_costs.py` | Pure conversational provider usage totals, cost attribution, cache-metric coverage and currency formatting | None; consumes supplied observations without mutation |
| `mrs_log_digest_provider_observations.py` | Passive conversational provider call/usage/error parsing, context selection/reset, attempt matching and observation projection | Supplied records, pending/active state, lists/statistics and current parser/formatter/converter/source callbacks; returns active context/index and mutates shared attempts; later error observation returns context alone; no I/O, clock sample or runtime access |
| `mrs_log_digest_runtime.py` | Current state/configuration validation, operator pause and feature-lifecycle observations | Supplied stable readers for state/configuration/controls and a lazy local lifecycle reader; explicit project paths, strict JSON parsers, file-time conversion and pause clock; no import-time runtime access |
| `mrs_log_digest_state_reporting.py` | Prepared current-state, author-strike, headline/derived, reply-quality and mention-control reporting | Explicit data, current helpers, vocabulary, epoch conversion and observation clock; preparation preserves media rows and supplied state/record times; refreshes supplied reports and uses the supplied event callback and statistics counter; no I/O or import-time runtime access |
| `mrs_log_digest_remote_write.py` | Read-only remote-write barrier identities, grouping, safety, reconciliation archive and window annotations | Explicit paths, readers/parsers, diagnostic formatter, clock, snapshot callbacks and annotation time converters; supplied window/authority flag; lazy read-only inspectors; no import-time runtime access |
| `mrs_log_digest_incidents.py` | Per-record error/warning observation, operational-error classification, incident grouping/resolution, retirement evidence and remote pause scopes | Supplied observations, current helper/annotation callbacks, scope mappings and conditional clock/epoch conversion; preserves error/event identity and snapshot mutation; no file/home/configuration access or provider calls |
| `mrs_log_digest_snapshot_incidents.py` | Prepared current-snapshot and retirement-evidence incident reconciliation | Supplied incident/evidence references and current matching/text/time callbacks; in-place incident enrichment/appends and shallow evidence sharing; no reads, clock samples or provider calls |
| `mrs_log_digest_reply_evidence.py` | Durable confirmed conversational receipt and historical reply-history loading/validation | Explicit project paths, stable private reader, native-number parser, canonical encoders, time conversion and validator callbacks; no writes, clock sample or import-time runtime access |
| `mrs_log_digest_reply_text.py` | Exact confirmed public reply-text preparation from prepared runtime/receipt/history evidence | Mutates supplied report/events; explicit source-reference, epoch-conversion and helper/validator callbacks and warning limit; no evidence loading, I/O or clock sample |
| `mrs_log_digest_quote_publication.py` | Quote-publication and engagement-experiment validation, evidence correlation and prepared publication reporting | Mutates supplied evidence, events, invalid-evidence sets and warning/outcome storage; explicit timestamps, source-reference helpers, validators and vocabulary; no files, home/configuration, clock sampling or provider calls |
| `mrs_log_digest_corpus.py` | Historical-corpus counts, availability, policies and hashes | Reads the existing research/audit paths under an explicit project directory using supplied strict parsing and file hashing; parsing and hashing remain separate reads |
| `mrs_log_digest_generated_pool.py` | Generated-image discovery, metadata/hash validation, curation, used history, post rates and runway configuration inputs | Reads/scans existing pool locations and supplied log/project paths; current strict parsers, hashing, clocks, record reader, basename regex and defaults; no writes or import-time runtime access |
| `mrs_log_digest_historical_events.py` | Historical-context event field projection, family counters and emitted-event quality summaries | Only supplied invocation-local counters and event insertion callbacks are mutated/called; no I/O or import-time runtime access |
| `mrs_log_digest_consistency_events.py` | Passive production-consistency event projections, family counters and prepared consistency reporting | Explicit parsed fields, timestamps, local counter, insertion and current field helpers; shares control lists and emitted event rows; no I/O, clock sampling or publication authority |
| `mrs_log_digest_generated_identity.py` | Generated-identity policy/shadow observation parsing and summaries | Mutates only supplied observation/error lists, local counters and the parser result's timestamp; strict parser, diagnostic formatter and lazy source-reference callback supplied by caller; no I/O or import-time runtime access |
| `mrs_log_digest_original_editorial.py` | Original-editorial selection/shadow observations, companion deduplication and summary | Mutates only supplied observation/error lists, local statistics/companion counters and the parser result's timestamp and event mode; strict parser, diagnostic formatter and lazy source-reference callback supplied by caller; no I/O or import-time runtime access |
| `mrs_log_digest_single_call.py` | Single-call reply decision, provider usage, posting outcome and recovered-draft observations and summary | Emits only through the supplied `add_event` callback; summary reads emitted events without mutation; no I/O, publication/recovery actions or import-time runtime access |
| `mrs_log_digest_reply_pipeline.py` | Legacy pipeline observation projections, effective-outcome reconciliation, summaries and strict majority-review telemetry validation/utilisation | Supplied event/rejection callbacks; reconciliation mutates supplied events in place; no I/O |
| `mrs_log_digest_reply_strategy.py` | Legacy conversational evidence fields, strategy observations, inferred outcomes, local-rejection coalescing, summary and no-reply categorisation | Explicit events, rejection map, payload dictionary and current callbacks; summaries read events without mutation or publication authority; no I/O |
| `mrs_log_digest_visual_context.py` | Pure reply visual-description validation and visual-context correlation/reporting | None; validates supplied dictionaries and summarises prepared observations; no publication authority |
| `mrs_log_digest_image_usage.py` | Pure generated-image utilisation, current-cycle runway and regular-image selection summaries | None; consumes prepared pool, post-rate, configuration and event observations |
| `mrs_log_digest_values.py` | Shared digest scalar conversions, reason classifiers and report vocabulary | None |
| `mrs_engagement_analytics.py` | Read-only X metrics collection and isolated SQLite reporting | X reads only with explicit flags; writes only under `engagement_analytics/` |
| `hybrid_reply_retrieval.py` | CLI for local hybrid retrieval experiments and review artefacts | Offline by default; provider-review commands require explicit execution and budgets |
| `semantic_alignment/hybrid_reply_retrieval.py` | Local E5 indexing, lexical-versus-hybrid evaluation and historical replay | Offline research files only; it is not imported by the production bot |

`mrsMThatcher2.py` is intentionally import-safe: importing it does not load the
private host configuration, acquire the production lock or enter the posting
loop. Operational entry points require `production_bootstrap()` first.

The bot retains the original names, signatures and defaults for `normalise_tag`,
`as_string_list`, `meaningful_tokens`, `phrase_matches_text`,
`hard_mismatch_tokens`, `hard_mismatch_phrase_matches_text`, `image_text_corpus`,
`build_image_topic_idf`, `visual_energy_score`, `image_is_out_of_season` and
`score_image_for_quote`. Their implementations live in
`mrs_bot_image_scoring.py`. `as_string_list` and `visual_energy_score` are direct
aliases; nine thin wrappers pass current root dependencies on each call,
including `re.sub`, `re.findall`, `TOKEN_STOPWORDS`,
`IMAGE_STRONG_MISMATCH_PENALTY`, `mm_dd_in_window` and the sibling helpers used
inside comprehensions and the historical-reference generator. Configuration
authority remains in the bot. The companion never imports the bot or stores
callbacks. Arguments and results pass through without copying; component order,
strict mismatch exclusion, ordinary rounding versus strict ceiling, and existing
exceptions are preserved. See [bot modularisation](bot_modularisation.md) for
the stage 1 boundary and validation.

The thirteen original-editorial helpers retain their root names, signatures,
defaults and annotations. `original_editorial_numeric` is an alias to
`mrs_bot_original_editorial.py`; twelve adapters pass current root settings,
vocabulary/dimension objects, `_ORIGINAL_EDITORIAL_ANALYSIS_CACHE`, logger and
sibling helpers, including recursive and comprehension lookups. The root remains
the configuration and cache authority. The owner neither imports the bot nor
retains callbacks or a separate cache. Loading preserves expanded path keys,
cache-hit identity before I/O, current image/hash validation and chained errors.
Scoring and selection preserve arithmetic, ordering, basename ties, generated
winner handling, log serialization and the existing selected-row/component copy
boundary. Disabled startup, logging and selection still do no work. See
[bot modularisation](bot_modularisation.md) for stage 2 validation.

Record/input callers retain `Record`, `safe_source_logger`, `record_source_ref`,
`bounded_source_refs`, `record_fingerprint`, `resume_fingerprint_tail`,
`locate_resume_fingerprint_tail`, `resume_boundary_fingerprint_counts`,
`filter_resume_boundary_records`, `iter_records`, `read_records`,
`filter_records_by_time`, `summarize_input_files`, `input_retention_coverage` and
`combine_input_warnings` through `mrs_log_digest`, with their original signatures
and defaults. `Record` is the same frozen class imported from
`mrs_log_digest_records`, with unchanged fields and constructor; there is no
second record type. `LOG_RE`, `SAFE_SOURCE_LOGGER_RE`, `SOURCE_REFERENCE_LIMIT`
and `RESUME_FINGERPRINT_TAIL_LIMIT` belong to that owner and retain digest aliases.

The reference merger, boundary-count decoder, time filter and warning combiner
are direct aliases. Ten thin wrappers supply current digest regexes, tail limit,
record constructor, `datetime.strptime`/`fromtimestamp`, `dt_text`, `parse_dt`,
`safe_source_logger`, `record_fingerprint` and `iter_records` where used. Iteration
remains lazy. Selected input reading preserves log-header/continuation handling,
replacement decoding, source ordinals/indexes, exact fingerprint bytes, duplicate
multiplicity, physical versus timestamp order, numeric rotations/mtime ties,
bounds and stat/read/warning order. Input summaries retain physical first/last
timestamps.

The owner has no upward import, stored callbacks, home/configuration lookup,
state write, clock sample or service initialisation. Discovery/explicit-log
selection, self-test authority, backscan cutoff/window choice, locks and
`run_digest`/CLI remain in the coordinator. Context/cursor implementation belongs
to `mrs_log_digest_context`; stable-byte/strict-JSON primitives belong to
`mrs_log_digest_input_io`. Default project/home resolution and producer source
hash/repository provenance remain anchored to the digest entry point; schema 3,
Markdown and resume persistence are unchanged.

Context/cursor callers retain `read_resume_data`, `save_resume_time`,
`state_context_is_within_window`, `strip_internal_context_markers`, `merge_context`,
`extract_config_pairs`, `merge_context_from_log_backscan`,
`find_latest_config_before`, `parse_partial_state_from_msg` and
`apply_saved_context` through the digest with their original annotations,
signatures and defaults. Config-pair and partial-state parsing are direct aliases;
eight thin wrappers supply current dependencies. `INTERNAL_CONTEXT_KEYS` belongs
to the context owner and retains its root key-set alias; recursive stripping
receives both the current set and current root recursive helper. Annotations use
the shared records owner's `Record`.

Merges retain fill-only behavior, shallow current copies, nested sharing and
annotations; backscan formatting runs only for a truthy timestamp after nonempty
prior context. Config scanning receives the current self-test predicate, record
iterator and config extractor, preserving the `before=None` fast exit, read order,
duplicate identity, timestamp/path/ordinal ordering and repeated extraction. Root
still chooses the logs and cutoff. Partial-state parsing retains its fixed fields,
regexes and permissive JSON/truncation recovery.

Cursor reads use the current strict native parser and diagnostic callback with
the existing warning and exception boundary. Saving receives current cursor,
stripping, fingerprint, timestamp and boundary/tail helpers, `Counter`, tail limit
and a lazy clock callback. It preserves conditional old-cursor reads, historical
fallback when runtime input is unavailable, clean-context copies, spacing sharing,
boundary multiplicity and bounded tails. The clock is sampled only while evaluating
`updated_at`; JSON key order, indentation, Unicode, newline, temporary sibling,
`write_text`/`replace` order and failures remain unchanged. No parent creation or
metadata policy is added.

`apply_saved_context` reads history before attaching cursor metadata, retains
state/config only as historical diagnostics, makes the existing shallow spacing
copy and finally calls the current `refresh_derived`. Its `window_end` remains
unused. Restoration confers no publication authority and does not replace current
runtime/evidence loading. Source/window selection, strict publication checks,
schema/producer identity and `run_digest` application/save ordering stay in root.
The owner has no reverse imports, retained callbacks or dependency container.

Legacy response callers retain `try_parse_response_id_text(msg)` and
`response_post_id_is_canonical_string(msg)` through `mrs_log_digest`, with their
original signatures and return values. The compatibility parser is a direct
import from `mrs_log_digest_legacy_posts`; the canonical parser has a thin digest
wrapper supplying the current `valid_string_public_post_id` from the values
owner. Literal parsing, exception handling, integer display conversion and regex
salvage remain distinct from canonical string publication authority. The new
owner imports the existing shared `Record` from `mrs_log_digest_records`.

Eight named handlers own the remaining raw legacy post/reply observations:
`handle_legacy_quiet_message`, `handle_legacy_quote_image_selection`,
`handle_legacy_generated_image_spacing`, `handle_legacy_quote_image_posting`,
`handle_legacy_meme_posting`, `handle_legacy_created_post`,
`handle_legacy_mention_reply` and `handle_legacy_quote_reply`. The first two return
only the handled flag; the others return it followed by the latest spacing,
pending quote, pending meme, latest created-post evidence, or pending reply and
active provider context, respectively. True corresponds only to an original
outer `continue`. Quiet counter-only matches still fall through; a fetched
hot-post result ends dispatch. Creating an X post ends dispatch even without
quote enrichment. No provider attempt index is changed or passed here.

Supplied pending maps and image rows retain their identities and original
mutation/replacement points. Spacing status/state updates share their new row
with the observation list; a blocked row appends without replacing latest state.
Created-post observation calls the current canonical parser once for the saved
evidence and again after a numeric success event, removing that exact event
identity when noncanonical. Mention/hot-post and quote-reply handlers retain
their separate fallback, underscore filtering, source flags, skip matching,
routine counters and context-reset rules. Current `add_event`, `lit`, response
parsers, record sequence and production flags are supplied only where used.

All eight calls retain their coordinator positions, with the four editorial and
generated-identity observers still between quote/image selection and spacing.
The record loop, production/self-test state switching, strict EVENT router,
source/publication authority, final cap/spacing counters, selected-window and
snapshot decisions, input/resume policy, CLI/defaults, provenance and locking
remain in the digest. There are no reverse imports, stored callbacks or generic
state containers; schema 3, JSON and Markdown are unchanged.

Transaction/media callers retain `parse_x_request_start`,
`classify_x_request_endpoint`, `parse_remote_write_transaction_event`,
`summarise_main_post_receipt_lifecycle`, `is_media_v2_request_failure`,
`is_media_fallback_warning`, `is_media_v1_success`, `is_media_v1_failure`,
`is_main_post_success`, `find_recent_media_path` and
`correlate_media_upload_incidents` through `mrs_log_digest`, with their original
signatures and defaults. Eight are direct aliases from
`mrs_log_digest_transactions`; three wrappers supply the current endpoint
classifier, `short`, `seconds_between`, source-reference/fingerprint/bounding
helpers, recent-media lookup and media predicates. The owner uses the shared
`Record` from `mrs_log_digest_records`. Shared scalar helpers remain with their
existing owners.

`record_x_request_start` shares its new event between the supplied request list
and latest-request source index; `record_remote_write_transaction` retains and
annotates the parser's original dictionary before counting and invoking the
existing receipt callback. `add_receipt_event`,
`add_confirmed_reply_receipt_event` and `add_reply_media_context_event` consume
explicit record/field data, source indexes/classification callbacks, observation
lists, statistics and formatting/source helpers. Field kwargs still override
defaults, followed by the existing source-reference overwrite. The confirmed
builder returns the exact pending dictionary: `written` and `reconciled` replace
it with the truthy identity fields, `removed` fills only absent kwargs from the
pending identity and returns a new empty dictionary, and other kinds retain the
original object. The digest's local adapter rebinds the returned state at the
original call site; pending-lane fallback is unchanged.

`handle_legacy_receipt_message` and
`handle_legacy_reply_media_context_message` retain ordered legacy matching and
return true only at an original `continue` outcome. The coordinator retains
their outer dispatch positions, parser invocation positions, request/source
selection, production/self-test pending-state switching, event insertion and
authority. Self-test receipt/media observations remain visible without becoming
production evidence. Complete helper/handler bodies, chronology, matching
windows, suppression fingerprints, unresolved receipt matching and object
sharing are unchanged. The owner has no reverse imports, stored callbacks,
runtime reads, clock sampling or operational actions; schema 3, Markdown,
CLI/defaults/provenance, locks and resume are unchanged.

Three post-scan functions also belong to `mrs_log_digest_transactions` and are
direct digest imports. `prepare_media_incidents_and_errors` consumes the selected
records, request/transaction observations, prepared snapshot, original errors and
self-test list, and coordinator-prepared self-test/API/restriction times. Current
`correlate_media_upload_incidents`, `parse_dt`, `datetime.strptime` and
`seconds_between` callbacks retain correlation/filter ordering. It returns the
correlator's incident list and a new remaining-errors list; retained errors and
self-test appends share the original error objects. Conditional archive fallback,
exact integer epoch checks, later-request predicates and suppression fingerprints
are unchanged. Restriction-time preparation stays in the coordinator for later
API reporting.

`append_unresolved_reply_receipt_errors` consumes the coordinator's confirmed
receipt and error lists, appending unresolved sending/reconciliation errors in
the original order. Its local pending collections and `clear_latest_reconciliation`
retain self-test exclusion, lane/target/reply identities, reverse latest-match
removal and unmatched cases. Source-reference lists are copied shallowly, keeping
their original nested objects; existing errors and receipt rows are retained.

`prepare_reply_receipt_recovery_reporting` follows the unchanged operational-health
call and returns three explicit lists: durably reconciled receipts, unavailable
receipt status and active snapshot receipts. It consumes the prepared health,
snapshot and receipt data with current `parse_dt`, `_normalise_lane` and
`REMOTE_WRITE_RECEIPT_ROLE_LABELS`. Parsing exceptions, time comparisons, fallback
receipt evidence and nonempty nested snapshot-list sharing are preserved. All
three calls remain at their original positions; scanning/source restoration,
incident classification, report assembly and API counters retain their owners.

Digest cost callers retain `load_openai_cost_cache`,
`estimate_openai_cost_window` and `openai_published_cost_report` in
`mrs_log_digest`. The digest resolves cache-path and clock defaults; the costs
module accepts explicit inputs and has no import-time runtime access.
`prepare_openai_published_cost_report` consumes an already loaded cache without
I/O. The digest's historical `provider_usage` argument remains accepted and
ignored.

Provider usage/cost callers retain `xai_reply_cost_summary`, `xai_usage_totals`,
`_cache_metric_coverage`, `_format_cache_metric_coverage_line`, `int_usage_value`,
`optional_int_usage_value`, `format_usd_ticks` and `format_reported_cost` as
explicit digest imports from `mrs_log_digest_provider_costs`. The unchanged
`USD_TICKS_PER_DOLLAR` and `USD_DISPLAY_QUANTUM` constants belong to that module
and retain their digest aliases, as does the Decimal `ROUND_HALF_UP` constant.
Complete public signatures, defaults and behaviour are
unchanged, including the permissive integer conversion versus strict native
nonnegative integer observation, missing/invalid versus zero cost, nullable
cache-metric coverage, call matching/counting, attribution, ordering, input
identity and mutation behaviour, and Decimal rounding/currency formatting.

Within the same owner, `_candidate_reply_disposition` receives the prepared
`decision`, `outcome`, `failure` and `local_rejection` rows. Its result binds
directly after the existing local-rejection lookup and before call-count
coverage. The five original statements retain eager terminal/writer/pipeline
classification, current owner-global classifier lookup, short-circuits, native
errors and exact status interpretation even when publication wins. Publication,
posting failure, writer-local failure, terminal local outcome, deliberate decline,
pipeline failure, approval and unavailable precedence are unchanged. Evidence
association, target fallback, sorting, coverage, arithmetic and report construction
remain in `xai_reply_cost_summary`. This legacy helper remains importable; schema 3
reports do not call it or restore retired per-candidate cost sections.

Dependency direction is digest → provider costs → values. The module uses the
existing lane normaliser and three shared reason classifiers without changing
precedence; it imports no coordinator, renderer, bot or cache reader and performs
no runtime I/O. Usage parsing/projection, pending-call correlation, `analyse`,
resume state and operational-health/publication authority stay with their current
owners. The published-cost cache module and its coordinator wrappers are unchanged.

Provider-observation callers retain `xai_usage_stage_from_msg`,
`provider_usage_provider_from_msg`, `parse_xai_call_start`,
`parse_xai_usage_from_msg`, `xai_usage_context_from_pending`,
`unknown_xai_usage_context`, `normalise_active_xai_call_attempt`,
`_cache_input_metric` and `summarize_xai_usage_event` through `mrs_log_digest`
with their original signatures/defaults. The first six are direct aliases from
`mrs_log_digest_provider_observations`; three thin wrappers supply the current
lane, stage, cache and integer-conversion helpers. Shared converters remain in
`mrs_log_digest_provider_costs` and `mrs_log_digest_values`; the observation
owner imports the shared `Record` from `mrs_log_digest_records`.

`observe_provider_message` receives the selected record/message, pending
mention/quote dictionaries, active context/index, attempt/usage/error lists,
statistics and explicit current callbacks. At the original pre-EVENT position it
sets Asking-Grok context, parses call starts, creates attempts, parses/matches
usage, mutates the original matched attempt and appends the summariser's original
event, or records the exact malformed-usage fields. It returns the active context
and attempt index without copying. Source predicates, provider/stage/lane/context
matching, missing-provider fallback, optional reasoning effort, timestamps,
ordering, counters and absent/partial/cache values are unchanged.

`observe_provider_error` runs at the later, original raw-error position after X
observation. It receives the selected record/message, active context, API-error
list, statistics, source indexes and current `short`/`record_source_ref` helpers.
It appends the existing xAI error fields and returns the context, clearing it for
the same provider-error and Grok-completion messages. It does not receive or
clear the attempt index. Provider usage parsing remains at its earlier position;
latest-error enrichment still follows this context rebind.

Source switching, resume decisions and cost/report assembly remain in the
coordinator. The owner stores no callbacks,
imports no coordinator and performs no clock, file, home, configuration or
provider access. Schema 3, Markdown, CLI/defaults/provenance, locking and
publication authority are unchanged.

API-health observation and preparation belong to `mrs_log_digest_api_health`,
with seven direct digest imports. The pure
`is_reply_target_eligibility_restriction` and
`is_deleted_or_inaccessible_tweet_403` classifiers retain exact root aliases,
signatures and predicate order. `handle_cooldown_message` observes the counter,
active list and entered event before the unchanged used-history handlers.
`handle_x_api_error` runs after those handlers with the current record, source
request index, pending mention/quote state, restriction flag, shared API/error
lists, statistics, source indexes and named parser/time/formatter/source/403
helpers. It retains source eligibility, the inclusive absolute 300-second
request window, endpoint fallback and pending lane/target selection. Request
status/failed fields mutate the original request after error formatting/source
projection and before 403 classification. Only the deleted/inaccessible 403
returns handled; target-eligibility restrictions continue to provider observation
and `enrich_latest_api_error`. Enrichment mutates the latest error row before the
existing traceback and later observation counters.

`prepare_api_health` runs at the original post-scan position before legacy
strategy-outcome inference. Explicit selected observations, production event
object IDs, prepared publication evidence, restriction times, timeout count and
current Counter/bounded-text/ID/confirmation/time helpers preserve transport-ID
deduplication, literal-lane conflicts, media handoffs, immutable success IDs,
historical durable-only exclusions and exact counter-semantics strings. It
returns `ApiHealthPreparation`, a typed collection of these existing computed
results, retaining original transaction/error rows and Counter objects. It does
not store callbacks or acquire/validate new evidence.

`api_health_report` consumes that result and the current error, restriction,
cooldown and request lists at the original `api_health` report-literal position.
It preserves key order, shared list references and late field evaluation,
including `has_5xx_failures`, sorted Counter conversion and conflict slicing after
intervening callbacks. Existing digest APIs/signatures/defaults, schema 3, JSON
and Markdown are unchanged. Source switching/selection, snapshot/window and
publication authority, placement of strategy inference, report orchestration,
resume, CLI, clocks and locking remain with their existing owners; the API owner has no
reverse imports, stored callbacks, I/O or operational actions.

Digest runtime callers retain `load_current_runtime_state`,
`load_current_runtime_config` and `runtime_control_snapshot` with their original
signatures and result shapes. Their wrappers pass the digest's stable reader,
strict parser and time dependencies into `mrs_log_digest_runtime`. State and
configuration retain native JSON number types; controls use the exact Decimal
parser. The control clock is a callable sampled after document/key/generation
validation and before boolean/time validation. State observation time remains
sampled in the digest after the state loader returns, separately from report
generation time and the file mtime. Analysis orchestration remains in the digest;
prepared current-health overlays belong to `mrs_log_digest_state_reporting`.
Shared `dt_text` and `bounded_exception_status` now live in the
values leaf and remain explicitly importable through the digest.

`shadow_lifecycle_snapshot(project_dir)` is a direct digest alias to the runtime
owner. Its import of `load_lifecycle_register` and `lifecycle_decision_schedule`
remains inside the original `try`, followed by loading the project's
`shadow_feature_lifecycle.json` and computing the schedule. Feature and schedule
lists retain their identity; overdue entries share the schedule rows. Import,
load and schedule failures retain the exact unavailable response. The digest
still decides when and how to use this observation; the move adds no reads,
clock samples or current-state/publication decision.

Prepared-state callers retain `state_list_count`, `state_list_tail`,
`state_list_head`, `summarize_engagement_question_experiment_state`,
`summarize_latest_state`, `current_author_no_reply_strike_progress`,
`refresh_current_health_headline`, `refresh_derived`, `epoch_to_human` and
`epoch_to_london_text` with their original digest signatures. The list head/tail
helpers are direct aliases; thin wrappers supply current digest helpers and
constants for the other functions. The reporting owner holds the two
`AUTHOR_NO_REPLY_PROGRESS_*` limits, five `AUTHOR_EVALUATION_QUARANTINE_*`
evidence-policy labels, `UNKNOWN_INVALID_STATE_FIELD` and
`CURRENT_COOLDOWN_FIELDS`, retaining digest aliases and values. Missing-state
vocabulary and scalar validators retain their values owner; experiment vocabulary
retains its quote-publication owner and is supplied through current digest aliases.

Epoch conversion receives the current `datetime.fromtimestamp`; London conversion
also receives the current `LONDON`. Local conversion retains its original
timezone omission and exception behavior. `summarize_latest_state` calls the
supplied `clock_now` once at entry, before reading state or invoking projection
helpers, exactly as the original unconditional `datetime.now()` call did.
Author progress uses `state_observed_at or generation_time` without sampling a
clock. Unknown/missing/invalid distinctions, strict types, current/prior/legacy
policy handling, expiry/window boundaries, ordering, limits and omissions remain
unchanged. State summaries retain shallow slices and projected-value sharing;
derived/cooldown/headline refreshes mutate the supplied report at the existing
call sites. Loading, saved-context application, resume, backscan and overall
report assembly retain their current owners.

`prepare_headline_and_derived` consumes named prepared statistics, health,
restrictions, media/recovery/receipt/asset observations, state summary, shared
`Record` objects and configuration, plus current `plural_count`, `int_or_none`
and `parse_dt`. Its six-item tuple returns the initial headline, transient timeout
count, three media lists and derived budgets/lane priority. Media lists retain
their original rows. Cooldowns use supplied state/record timestamps without a
new clock sample; the temporary Grok-skip claim retains its original lifetime.
The digest calls it before API preparation at the original analysis position.

`prepare_reply_quality_headline` runs after the digest's existing historical-context
and single-call quality summaries. It consumes events, the initial headline,
single-call quality and current `plural_count`, returning legacy counts, a
replacement headline list and its cooldown-free base. It preserves insertion
positions and every cooldown exclusion without mutating the input list. Mention
control preparation, API report materialisation and report schema assembly remain
at their original coordinator positions; existing reporting functions are unchanged.

`record_mention_backlog` and `record_author_evaluation_quarantine` consume the
selected parsed payload, record timestamp, current validation helpers,
`add_event` and the existing statistics counter. The digest retains the four
backlog and three quarantine dispatch predicates and positions, source tracking
and event insertion. Both existing statistics increments remain: one in
`add_event`, one in the selected handler. `prepare_mention_control_observations`
selects the original emitted event objects and returns its event list, original
Counter and explicit skipped-evaluation sum; the digest attaches that list and
converts sorted counts at the original report site. JSON schema 3, exact Markdown,
CLI/defaults, provenance, locks and self-test isolation are unchanged. The owner
imports no coordinator, renderer, bot or provider, stores no callbacks and has
no generic dependency container.

Digest file/JSON callers retain `file_sha256`, `_stable_file_identity`,
`read_stable_regular_snapshot`, `read_stable_regular_bytes`,
`read_stable_private_json_bytes`, `canonical_atomic_json_bytes`,
`canonical_private_json_bytes`, `_strict_json_object`,
`_strict_native_json_value` and `_strict_native_json_object` with their original
signatures/defaults. Their implementations belong to `mrs_log_digest_input_io`.
The six independent functions remain direct digest aliases. Four thin root
wrappers resolve current sibling helpers on each invocation: the snapshot
supplies `_stable_file_identity` as `stable_file_identity`; the regular/private
byte readers supply `read_stable_regular_snapshot` as `read_snapshot`; the native
object parser supplies `_strict_native_json_value` as `parse_json_value`.
Callbacks are explicit named inputs and are never stored. Standard-library module
imports remain shared, preserving patches to their functions.

The stable reader preserves no-follow flags, path/fd metadata comparisons, private
owner/link/mode checks, size/read bounds, descriptor closure and error order.
`file_sha256` retains its ordinary file read, including symlink following; corpus
parse/hash reads remain separate. `_strict_json_object` retains exact `Decimal`
floats; the native parsers retain ordinary float types, overflow rejection and
recursive UTF-8 string checks. Duplicate/nonfinite/decoding errors, nested values
and object-root checks keep their existing contracts. Atomic encoding uses
`json.dumps`' default ASCII escaping and nonfinite-number policy; private encoding
uses `ensure_ascii=False, allow_nan=False`. Both retain their exact indentation,
sorted keys, UTF-8 bytes and trailing newline. The owner imports no digest or bot
and performs no import-time I/O. `build_digest_contract` and `repository_head_sha`
remain in root: default `__file__`, producer source identity, schema constants and
current repository callback resolution still identify the digest facade.

Digest remote-write callers retain `remote_write_safety_snapshot`,
`reconciliation_archive_snapshot` and the private `_read_readonly_archive_bytes`
with their original signatures. Their thin wrappers supply current digest
dependencies to `mrs_log_digest_remote_write`, including the root control and
archive snapshot callbacks and private archive reader. Patching these root entry
points still affects the next inspection. Safety converts the project path,
then samples `datetime.now` before control/archive inspection. The archive
wrapper supplies `reconciliation_inspection_error`; both snapshots use the
exact-Decimal `_strict_json_object`, not the native-number parser.

Within the remote-write owner, `_observe_remote_write_artifact` implements the
complete artifact observation algorithm. The nested `observe_name(name, kind,
*, receipt_role=None, retirement_source_basename="", retirement_path_phase="")`
keeps its signature and position, forwarding those inputs and the current
`project_dir`, `active_entries`, `read_bytes` and `parse_json_object` references.
The implementation appends to the original list and returns no state; the
adapter retains its `None` result. Missing paths return early, lstat errors append
their existing unbounded reason, and nonregular artifacts are never read. Mode
and size conversion remain outside the broad identity-inspection exception
boundary. Stable reading retains the current 256 KiB limit, followed by hashing,
parsing and identity projection; failures retain fields already populated.

Retirement source/path checks, exact positive-int expected sizes, cleanup
hash/size fallbacks, document phase, canonical source identity and its hash stay
together. OS/stat/hash/regex helpers, constants, identity helpers and
`bounded_exception_status` retain current same-owner lookup. Diagnostic failures
and the final append still propagate in their original order. No callback is
stored or collection copied by this extraction. Configured probing, snapshot
coordination, lazy inspectors, existing grouping copies, blockers and result
construction remain in `remote_write_safety_snapshot`.

The four pure helpers `_remote_write_document_identity`,
`_group_active_remote_write_artifacts`, `_canonical_retirement_source_identity`
and `_safe_relative_project_path`, plus ten file/receipt/retirement snapshot
constants, remain direct digest imports. Stable-file protections, read order,
size bounds, lexical paths, read-only permissions, canonical identities, hashes,
grouping and failure results are unchanged. Protocol, retirement, transport and
media inspectors remain lazy and retain their existing read-only arguments.
Dependency direction is digest → remote-write snapshots → shared values;
callbacks preserve delegation without reverse imports or stored dependencies.
`annotate_remote_write_snapshot_window` retains its digest signature and default
through a thin wrapper supplying current `datetime.strptime` and
`datetime.fromtimestamp`. The owner assigns the selected end before parsing,
catches only `ValueError` there and converts only exact integer epochs. All
relationship/reason strings, the supplied authority flag and mutations of shared
active-entry, component and blocker dictionaries remain unchanged. Annotation
adds no clock sample, read or authority inference. Window selection, analysis,
report assembly and all operational/publication authority remain in the
coordinator. Operational incident reconciliation belongs
to `mrs_log_digest_incidents` and consumes the prepared snapshot.

`observe_error_warning` receives the current record/message, the four shared
error/recovery lists, the four pending maps, prepared visual-description flag,
source indexes and explicit current restriction, rejection, formatting,
fingerprint, source-reference and operational-classification callbacks. It
retains the complete self-test/restriction/receipt/recovery/asset/media predicate
sequence, including the legacy clarification rejection callback immediately
before error routing. That callback remains the root closure, preserving event
identity, source references and production provenance. Suppression order, row
fields/timestamps, list identity and pending pause-lane hints are unchanged.
Only `is_asset_metadata_warning` and `is_handled_reply_restriction` return, as an
explicit pair assigned before provider observation; later asset/X dispatch
retains its original position. Pending maps are read by reference. The observer
adds no read, clock, validation or event authority and stores no callbacks.
Its shared-owner `Record` annotation is imported only under `TYPE_CHECKING`.

Operational-error callers retain `summarise_operational_error_health`,
`classify_operational_error`, `_base_remote_control_key`, `_remote_control_scope`,
`_remote_operation_scope_for_lane` and `_explicit_remote_pause_scope` with their
original digest signatures through six thin wrappers. `_incident_exception_line`,
`_normalise_incident_text` and `_event_time` are direct digest imports with their
complete original definitions. `REMOTE_OPERATION_SCOPE_LABELS`,
`REMOTE_CONTROL_SCOPE_BY_KEY` and `REMOTE_LANE_SCOPE` belong to the incident module
and retain identical digest aliases; the wrappers supply the current mappings.

The wrappers supply current classification, incident-text, event-time and scope
helpers, `bounded_source_refs`, `seconds_between`,
`is_deleted_or_inaccessible_tweet_403` and
`annotate_remote_write_snapshot_window` as named callbacks. The 403 predicate is
supplied by the classifier wrapper. The summary receives `datetime.now` as
`clock_now`, `datetime.fromtimestamp` as `fromtimestamp` and `datetime.min` as
`datetime_min`: both conditional clock samples, all three epoch-conversion sites,
their ordering and the missing-time fallback are unchanged. Shared time/text/lane
and terminal-outcome helpers come directly from `mrs_log_digest_values`.

Evidence preparation stays in the incident owner at three original sequence
positions, with direct named unpacking of the existing results.
`_prepare_pipeline_incident_evidence` receives `events`, `operational` and current
`get_event_time`; it returns `pipeline_failures_by_identity` and
`raw_pipeline_evidence`. Event rows remain shared and raw evidence keeps `id(item)`
keys. Fullmatch/hint rules, exact failure counts, unique nearest causal matching,
no-match branches and callback order are unchanged; `_normalise_lane`, `re` and
builtins retain owner lookup.

`_prepare_remote_ambiguity_evidence` receives `serious`, `events`, already
materialised `remote_write_transactions`, `classify_operational_error`,
`get_event_time` and `seconds_between`. It returns `ambiguity_times`,
`transport_attempts`, `ambiguous_reply_outcomes` and `ambiguous_media_outcomes`.
Both ambiguity-time passes and repeated classifications remain, along with
transport/media filters, causal windows, stable time/transaction ordering, native
field types and timestamp sharing. Lane normalisation retains owner lookup.
`groups` and `stable_root_categories` are still initialised before this call.

`_pause_scope_for_item` implements ordered pause-scope inference in the same
owner. The nested `pause_scope_for_item(item)` keeps its signature and position
as a direct-return adapter, forwarding the original item, `events`,
`transport_attempts`, `explicit_remote_pause_scope`, `get_event_time`,
`base_remote_control_key`, `remote_control_scope` and
`remote_operation_scope_for_lane`. Explicit evidence returns its original tuple
and key list. Missing-time behavior, the inclusive 60-second structured-event
window, distance/text sorting and stable ties, key-before-lane interpretation,
unknown scopes, pending-lane priority and the directional inclusive 10-second
transport fallback are unchanged. `re` retains incident-owner lookup.

Immediately after that adapter, `_group_operational_incidents` receives `groups`,
`operational`, `raw_pipeline_evidence`, `ambiguity_times`,
`stable_root_categories`, `pipeline_failures_by_identity` and the current
`classify_operational_error`, `get_event_time`, `seconds_between`,
`is_subordinate_remote_write_symptom`, `incident_exception_line`,
`matching_ambiguity_identity`, `pause_scope_for_item` and
`normalise_incident_text`. It mutates the existing groups and shared operational
rows, including subordinate/identity/pause annotations, and seeds empty
pipeline-only groups in the original order. Only `pipeline_identity_by_group`
returns, directly bound for later construction with its original identity tuples.
Pipeline evidence priority, exception/root selection, signatures, stable categories,
reverse group traversal and the distinct ambiguity/provider windows retain their
original callback and mutation order. `dt_text`, `_normalise_lane` and `re` remain
current same-owner globals. Group/category allocation, recovery preparation and
the complete per-group incident-construction loop stay in the summary; neither
implementation stores callbacks, returns closures or introduces copies.

`_prepare_recovery_evidence` receives `events`, `receipt_events`, the materialised
`lifecycle` and `confirmed_reply_receipt_events`, and `get_event_time`. After
`pipeline_identity_by_group` and before safety annotation, it returns `event_times`,
`receipt_removed_times`, `successful_restart_times`, `remote_write_success_times`,
`remote_operation_successes` and `terminal_reply_receipts`. Both event passes and
callback order remain, with exact scope vocabulary and shared per-kind scope sets;
generic transport success has only `all_remote_writes` scope. Receipt kind and
self-test filters and deliberate shallow receipt-row copies are unchanged.
`success_scopes` remains local to preparation. These functions add no copies beyond
the existing bodies, stored callbacks, clock samples or acquisition. Iterable
materialisation, classification, grouping, adapters, snapshot authority,
construction and final selection stay in the summary.

Within the incident owner, `_pipeline_recovered_after` and
`_remote_pause_recovery_status` implement pipeline and pause recovery. The nested
`pipeline_recovered_after(identity, last_time)` and
`remote_pause_recovery_status(scope, control_keys, last_time)` retain their
signatures and original positions as direct-return adapters. Pipeline recovery
receives the prepared `events` and current `get_event_time`; lane and terminal
helpers retain incident-module global lookup. Pause recovery receives `safety`,
`events`, the already materialised `lifecycle`, `remote_operation_successes`,
`base_remote_control_key`, `remote_control_scope`, `explicit_remote_pause_scope`
and `get_event_time`. Inputs pass by reference without new copies or casts.
Strict later-time/identity filters, failure exclusion, terminal distinctions,
reason tie-breaking, control hierarchy, unknown-scope short-circuit, ordered
clearance callbacks and scope-matched success retain their exact behavior.
The implementations add no reads, clock samples, authority decisions or stored
callbacks; surrounding summary phases stay put.

Identified transaction recovery uses `_remote_write_recovery_status` in the same
owner. Its nested `remote_write_recovery_status(category, identity, first_time,
last_time)` retains its signature and definition position as a direct-return
adapter. It forwards `identity_snapshot_available`, `active_component_matches`,
`active_remote_components`, `component_is_related_to_selected_window`,
`component_is_relevant_to_category`, `identity_snapshot_explicitly_unavailable`,
the intact `safety`, `terminal_reply_receipts`, `handled_api_restrictions`,
`remote_write_success_times`, `fromtimestamp` and `get_event_time`. The unidentified
active scan retains its position even with unavailable snapshots, after the
matched-active early return. The nested `audit_matches` body, archive fallback,
native-int checks, transaction/target rules, six-hour target fallback, audit ties,
handled-403 window, shared terminal receipt rows and strict later-success rule
are unchanged. `_normalise_lane` and `timedelta` retain incident-owner lookup.

Category recovery uses `_recovered_after`, with the original nested
`recovered_after(category, last_time)` signature and position. It receives
`events`, `event_times`, `receipt_removed_times`, `successful_restart_times`,
`safety`, `get_event_time`, `fromtimestamp` and `clock_now`. Both historical-event
passes, recovery-kind/candidate order, strict timestamps, time/reason ties and
empty results are preserved. Current-clear protocol recovery remains distinct
from authoritative namespace-clear ambiguity recovery. Audit validity, native-int
epochs, fallback and ordering are unchanged. Epoch conversion remains conditional;
`clock_now` is invoked only in the successful protocol-clear branch, independently
of `generated_at`. Both implementations consume current prepared references and
callbacks without new reads, copies, casts, authority or stored dependencies.

The same owner implements ambiguity association in `_matching_ambiguity_identity`
and exact subordinate correlation in `_is_subordinate_remote_write_symptom`.
The nested `matching_ambiguity_identity(raw, item_time)` and keyword-only
`is_subordinate_remote_write_symptom(*, category, raw, item_time)` keep their
original signatures and positions as direct-return adapters. Identity matching
receives `ambiguous_reply_outcomes`, `transport_attempts`,
`ambiguous_media_outcomes` and the supplied `seconds_between`; subordinate
correlation receives `ambiguous_reply_outcomes`, `ambiguity_times` and that same
callback. These are the current prepared references, including comprehension
inputs; `_normalise_lane` and `re` retain incident-owner global lookup.
Missing-time short-circuiting, direct 300-second matching/transport fallback,
transaction regex/first match, nearest unique reply and single-media selection
within 10 seconds, persistent-barrier fallback, candidate/callback order and
stable ties are unchanged. Matching evidence returns by identity without copying.
Subordinate rules retain exact categories/messages and distinct time policies:
receipt evidence uses `0 <= outcome_time - item_time <= 300`, transaction barriers
use `seconds_between <= 5`, and exact lane barriers use
`0 <= item_time - outcome_time <= 5` (durations in seconds). Neither implementation
adds preparation, stored callbacks, reads, clock samples or authority decisions.
Grouping, pause scopes, recovery, construction and final selection stay in place.

`reconcile_current_snapshot_incidents` in `mrs_log_digest_snapshot_incidents`
owns the six local evidence helpers and their complete reconciliation loop.
The incident summary calls it at the original position immediately before sorting,
passing the actual incidents, availability flag, active components, snapshot
evidence and intact safety mapping. Current component/window matching, event-time
and epoch callbacks are explicit inputs, as are the incident owner's current
`dt_text` and `short` references. JSON/hash operations retain the shared standard
library behavior. Unique ledger matching, retirement conflicts, conditional exact
integer epoch conversion and lazy `observed_at` fallback retain their original
order and exceptions. The helper mutates the supplied incidents, preserves nested
evidence sharing and returns no result. It adds no input copies, authority
inference, validation, read, clock sample, returned closure or stored callback.
Earlier preparation, predicates, recovery and incident construction, then later
sorting, selection and the final report expression, stay in the incident summary.

Incident categories/signatures, identity grouping, current/historical resolution,
retirement merging, restrictions versus failures, pause scope and recovery
chronology, authoritative-window decisions, source references, counts, ordering,
labels and exception behaviour are unchanged. The summary retains original error
and event objects, mutates supplied errors and delegates snapshot annotation on
the original nonempty snapshot; existing empty-snapshot fallback semantics and
shallow evidence sharing are retained. It performs no file/home/configuration
access or provider calls. Dependency direction is digest → incidents → snapshot
incidents, with shared values used by the incident owner,
without upward imports, stored callbacks or dependency containers. Snapshot
loading/window annotation, transaction parsing, media-log correlation, event
collection, `analyse` and report assembly retain their existing owners. JSON
schema 3, Markdown, CLI, publication authority and source/self-test isolation are
unchanged.

Durable reply-evidence callers retain `load_confirmed_reply_receipt_evidence`,
`load_historical_reply_history_evidence`, `_confirmed_conversational_receipt_evidence`,
`_valid_durable_ai_reply_draft`, `_valid_canonical_utc_timestamp`,
`_valid_historical_completed_item` and `_valid_historical_failed_item` with their
original digest signatures. Seven thin wrappers supply the current digest
`read_stable_private_json_bytes`, strict native-number `_strict_native_json_object`,
`canonical_atomic_json_bytes` or `canonical_private_json_bytes`,
`datetime.fromtimestamp`, `datetime.fromisoformat`, `LONDON` and text/validator
callbacks as needed. Loader-to-validator, receipt-to-draft and draft/failed-row
UTC validation still delegate through the digest entry points. No clock is sampled.

The pure `_structured_value_sha256`, `_valid_historical_formatter_metadata` and
`valid_conversational_public_reply_text` definitions belong to
`mrs_log_digest_reply_evidence` and remain direct digest imports, as do the four
receipt/history byte-limit and publication-epoch constants. Shared post-ID,
bounded UTF-8 text, hash vocabulary and bounded diagnostics retain their values
leaf owner. Private-file bounds, read/parse/validation order, native bool/int/float
distinctions, exact schemas, timestamp rules, identity bindings, formatter/draft
validation and source-receipt hash reconstruction are unchanged. Encoders retain
their distinct receipt/history byte formats. Valid long and multiline published
text is preserved without applying current generation policy to historical text;
projection copies, shared values and returned evidence identity are retained.

Dependency direction is digest → reply evidence → values, without reverse imports,
stored callbacks, runtime I/O on import or service initialisation. Event collection,
`analyse`, pipeline reconciliation, report assembly
and all publication/recovery authority retain their existing owners. JSON schema 3,
Markdown, CLI, defaults, provenance, clocks, locks and resume behaviour are unchanged.

Public reply-text preparation belongs to `mrs_log_digest_reply_text`.
`_normalised_structured_reply_confirmation` is a direct digest import with its
complete original signature/body. `_public_reply_text_result`,
`_durable_public_reply_text_candidates` and `enrich_published_reply_text` retain
their original digest signatures through three thin wrappers. They supply current
`bounded_source_refs`, `epoch_to_london_text`, normalisation/candidate/result
helpers and `valid_conversational_public_reply_text` from the stage 13 evidence
leaf. Enrichment still delegates through the digest helper entry points; the
candidate wrapper supplies the current conversational validator. Shared UTF-8,
post-ID and hash validators retain their values-module owner.
`PUBLISHED_REPLY_WARNING_LIMIT` belongs to the text module, remains `100` and is a
direct digest alias passed explicitly to enrichment on each call.

Within that owner, `_index_reply_confirmations` receives the current `events`
list, `structured_reply_confirmations`, `confirmed_receipt_evidence`,
`normalise_confirmation` and `epoch_to_london_text` explicitly. Enrichment calls
it immediately after `legacy_identity` and before allocating `enriched_records`,
then consumes its original dictionary directly. Structured confirmations retain
their order, duplicates and normalized object references; receipts fill only
absent reply keys in enumeration order. Each receipt still undergoes integer/epoch
conversion and normalization even when its reply key already exists. Both
`len(events)` calls remain inside each iteration, preserving insertion metadata,
authority flags, `None` short-circuits, native errors and callback order. No
collection is copied or callback retained by this extraction. Legacy preparation,
warnings, the coupled identity/conflict and event-mutation loop, synthesis,
and health aggregation remain coordinated by enrichment.

`_enrich_selected_historical_reply_text` keeps canonical historical evidence
indexing and the complete selected historical-event enrichment phase together in
the same owner. Its eight explicit inputs are `historical_reply_text_evidence`,
`events`, `production_ids`, `consumed_historical_evidence`, `enriched_records`,
`resolve_text`, `bounded_source_refs` and `warn`. The call stays after legacy-record
enrichment and before remaining-historical synthesis. It mutates the current sets
and event rows in place, returns no state and retains no callback. Both indexes
are local; post-ID validation and `SHA256_LOWER_RE` retain current same-owner
lookup. Authoritative `is True`, strict canonical selectors, production/status
filters, ordered cross-parent/reply expansion and consumption before resolution
are unchanged. Candidate defaults, exact unavailable reasons, conflict overrides,
reply-ID mutation, shared references, omission conversion/native errors, warnings
and enriched-ID updates retain their original order. Remaining-historical
synthesis, final event insertion and health aggregation stay in the coordinator.

Prepared structured confirmations retain precedence over receipt-derived
confirmations. Exact long/multiline published text, unavailable versus conflicting
evidence, immutable lane/target/reply identities, duplicates, source bounds and
omission counts, warning caps/order and synthetic-event insertion order are
unchanged. Enrichment mutates the supplied report and original event list/objects;
`production_event_object_ids` still filters by identity. Candidate projections,
shared text/reference values, shallow durable-status copying and historical
section references into the event list retain their original relationships.
Drafts and unconfirmed/nonauthoritative observations acquire no publication
authority. The text module performs no file/home/configuration access, clock
sampling, provider calls or import-time runtime work. Evidence loading retains its
existing owners. Event collection, `analyse`, pipeline reconciliation and report
assembly remain in the coordinator; operational incident reconciliation belongs to
`mrs_log_digest_incidents`. JSON schema 3, Markdown and CLI contracts are unchanged.

Quote-publication correlation belongs to `mrs_log_digest_quote_publication`.
`valid_account_root_publication_identity`, `valid_engagement_confirmation_event`
and `engagement_main_metadata_status` retain their original digest signatures
through thin wrappers supplying current post-ID validation, hash/pair patterns
and experiment vocabulary. The eight `ENGAGEMENT_*` constants formerly beside
those validators retain direct digest aliases, including the shared vocabulary
used by `summarize_engagement_question_experiment_state`. The warning limit retains
its values-module owner and is supplied on each warning call.

`record_main_post_publication`, `record_account_root_publication`,
`record_engagement_confirmation` and `record_engagement_trial_outcome` accept
prepared parsed payloads, record timestamps and named validator/source/evidence
callbacks. Strict parsing, the outer loop, dispatch predicates, source tracking,
event insertion and the daily-meme pending-state update remain in the coordinator
at their original positions. Local adapters retain dynamic current-source checks
and omission accounting while the module owns `add_engagement_correlation_warning`,
`retain_quote_post_evidence`, `note_invalid_quote_post_evidence` and
`correlated_quote_post_fields`. Correlation maps, invalid-evidence sets, warning
lists/keys/counters, current text/reference helpers and the question separator are
explicit arguments. Callbacks are neither stored globally nor bundled in a
dependency container.

`prepare_quote_publication_report` enriches the original production quote events,
assembles confirmed experimental publication rows and sorts supplied trial
outcomes/warnings in place. The returned rows retain projected text/reference
sharing. Duplicate/conflict semantics, canonical public IDs, exact long/multiline
text and hashes, field precedence, missing versus null, source-reference bounds,
warning cap/count/order/omissions and authority/source/self-test filtering are
unchanged. Observed-success counting reads the same prepared correlation evidence
with the same object identities. Overall report assembly, JSON schema 3, Markdown,
CLI, defaults, provenance, clocks, locks and resume retain their existing owners
and behaviour. The reporting module performs no I/O or runtime initialisation
and imports no coordinator, renderer, bot or provider module.

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
digest generation time. Analysis and report assembly stay in the digest; pure
utilisation and runway summaries belong to `mrs_log_digest_image_usage`.
Both snapshot modules have no
import-time runtime effects or imports back into the digest, Markdown or bot.

`generated_post_rate_history` and `load_runway_config` retain their digest
signatures/defaults through thin wrappers into the generated-pool owner. The
rate wrapper supplies current `read_records`,
`try_parse_strict_json_object_from_msg`, the root `GENERATED_BASENAME_RE` alias
and `datetime.now`. The conditional clock sample, supplied timezone stripping,
`days` scan cutoff, fixed 7/30-day output windows, contaminated-second exclusion,
first-post-ID deduplication and coverage/rate types and ordering are unchanged.
No extra source or ID validation is introduced.

`RUNWAY_CONFIG_DEFAULTS` belongs to the generated-pool owner and retains a root
alias; the wrapper passes its current value and `_strict_native_json_object`.
Defaults are shallow-copied, observed values overlay matching keys and existing
local configuration overlays them last. The existence check remains outside
the read/parse exception boundary, with the same error dictionary. There are no
additional reads or clock samples, stored callbacks or dependency containers.

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

Consistency event callers use `record_reply_evidence_unavailable`,
`record_runtime_control_pause`, `record_runtime_control_clear`,
`record_clarification_reply_cap_override`, `record_clarification_reply_used`,
`record_repair_reply_completed`, `record_posting_transaction_state` and
`record_daily_meme_failure`. Each receives the current parsed event, record
timestamp, local statistics and `add_event`, plus only its current bounded
text/list/integer/boolean or string post-ID helpers. Field access/validation
order, defaults, limits and types are unchanged. Generic insertion remains in
the digest; pause/clear, clarification and repair still increment their kind
again after insertion. Returned control-lane lists remain shared with event rows.

`production_consistency_report(events, stats)` returns the same ordered report
dictionary at the original root literal key position, after late API reporting
and the historical-reply counters. It creates a new list of the fixed eleven
kinds with shared rows, followed by five separate sorted counter iterations for
transaction state, obligation state, meme-failure stage, historical runtime
status and unavailable-evidence lane. It does not snapshot these counters early.
The historical-events owner and other report sections are unchanged. Parsed
EVENT predicates, dispatch/source switching, strict confirmed-publication
acceptance and completed historical-anchor validation remain in the digest;
displayed valid IDs confer no publication authority. The owner imports only
standard-library types and stores no callbacks or invocation state.

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
`most_common_with_cutoff_ties` is shared through the values leaf and remains an
explicit digest import. The generated-identity
module imports no digest, renderer, bot or snapshot readers. Import performs no
home lookup, runtime I/O, logging setup or service initialisation. Report assembly,
generated-image spacing/resume state and image-selection algorithms remain with
their existing owners.

Original-editorial callers retain `original_editorial_comparison_key(item)` and
`original_editorial_shadow_summary(events)` as explicit digest re-exports with
unchanged signatures. `record_original_editorial_selection` and
`record_original_editorial_shadow` receive the matched message, timestamp, level,
dedicated observation list, pending-companion counter, statistics, errors, strict
native JSON-object parser, diagnostic formatter and lazy source-reference
callback. Both collections and both counters remain local to `analyse`; the
digest retains the original branch positions and continues after success,
suppression or a parse error. Observations bypass `add_event` and acquire no
generic event counters or provenance, including for self-test sources.

The parser result's `time` and `event_mode` are overwritten before companion
accounting. The comparison key remains the ordered tuple of `quote_hash`,
`line_no`, `selection_phase`, `production_source`, `production_winner` and
`shadow_original_winner`, without coercion. Each selection permits suppression
of one later matching shadow. Earlier shadows remain recorded; repeated
selections and companions retain their multiplicity. Suppressed shadows still
increment both the shadow and companion statistics. Only UTF-8 encoding and
parsing are caught as parse errors; comparison-key failures retain their partial
mutation order, and parseable invalid summary fields still fail downstream.

The summary preserves boolean identity versus truthiness, native numeric types,
integer rank conversions, winner/ranking ties, denominators and the original
objects in `severe_disagreements`. It does not mutate observations. Dependency
direction is digest → original editorial → values; the ranking helper in values
is unchanged. The new module imports no coordinator, renderer, bot or snapshot
reader and performs no I/O or runtime initialisation.

Single-call reply callers retain `single_call_reply_summary(events)` as an
explicit digest re-export with its original signature. The specific handlers
`record_single_call_reply_decision`, `record_single_call_reply_provider_usage`,
`record_single_call_reply_posting_outcome` and
`record_single_call_reply_draft_recovered` accept already parsed fields, the
record timestamp and a keyword-only `add_event` callback. The digest retains the
original branch predicates, precedence, outer parsing and continue flow.
`add_event` still constructs and retains each event dictionary, applies
`max_text`, attributes provenance/production identity and increments statistics.
The summary consumes those same emitted objects at the original analysis point.

Field order, allowlists, defaults, exact integer/boolean distinctions and bounds
are unchanged. An explicitly invalid `recent_conversational_reply_count` does
not fall back to `recent_reply_count`; that alias applies only when the primary
field is absent. Temperature keeps the original native int/float finite check.
Provider attempts retain their missing, malformed, out-of-range and available
observations, with the existing provider status/reset/retry projections. Summary
deduplication, object-identity accounting, retry compliance, token totals,
latencies and averages are unchanged. Posting/recovery observations confer no
new publication authority; confirmed-reply and durable-evidence handling remain
with their existing owners.

Dependency direction is digest → single call → values. The unchanged
`normalise_reply_lane` and `bounded_event_nonnegative_integer_observation` live
in the values leaf and remain explicitly importable through the digest. The
single-call reporting module imports no digest, renderer, bot or operational
`single_call_reply` module and has no shared mutable state.

Legacy reply-reporting callers retain `reply_pipeline_stage_summary`,
`normalise_majority_review_telemetry` and `majority_review_utilisation` as explicit
digest re-exports from `mrs_log_digest_reply_pipeline`. The private
`_valid_majority_review_summary`, `_majority_review_telemetry_for_event` and
`_majority_review_utilisation_counts` remain explicitly importable through the
digest too. The unchanged `MAJORITY_REVIEW_FAMILIES` and
`MAJORITY_REVIEW_SUMMARY_FIELDS` tuples belong to the pipeline module and retain
that same compatibility surface. Raw and parser-sanitised telemetry keep their
distinct presence, empty, malformed and duplicate-family handling, strict native
type checks, validation rules and reviewer-call calculations.

`reply_strategy_summary` and `_no_reply_category` belong to
`mrs_log_digest_reply_strategy` and remain explicit digest imports. The three
shared reason classifiers, `_terminal_local_rejection_outcome`,
`_is_terminal_pipeline_failure` and `_is_writer_local_failure`, live unchanged in
values and remain available through the digest for its cost and health callers.
Signatures, defaults, event dictionary identity, input mutation behaviour,
deduplication, ordering, missing-value treatment, reason precedence and summary
calculations are unchanged. The distinct lane normalisers remain separate.

Dependency direction is digest → reply pipeline / reply strategy → values.
Both reporting modules operate only on supplied observations and import no
coordinator, renderer, bot or runtime reader. The strategy module also owns
`conversational_evidence_fields`, `record_reply_strategy_decision`,
`record_reply_strategy_outcome`, `record_reply_target_terminal` and
`record_reply_strategy_rejection`. The pipeline module owns
`record_ai_reply_pipeline_decision`, `record_ai_reply_pipeline_stage_summary`,
`record_ai_reply_pipeline_effective_outcome`, `record_ai_reply_pipeline_failure`
and `record_ai_reply_pipeline_outcome`. Each handler receives a parsed payload,
the record timestamp and only its required current callbacks. The coordinator's
local evidence adapter supplies the current `bounded_event_string_list`; pipeline
handlers also receive that helper and `normalise_majority_review_telemetry` where
needed. Shared scalar/text/identity validators retain their values-module owner.

`prepare_inferred_reply_strategy_outcomes` receives events, handled restrictions,
current `_normalise_lane`/`parse_dt` and the root `add_event` callback. The original
indexes retain event insertion order, latest-decision lookup and target outcome
deduplication; missing values, parser errors, timestamps and copied fields are
unchanged. It runs between API preparation and the two quality summaries. The
coordinator has already cleared its current source record at this point, so these
inferred observations retain their existing lack of source references and
production event identity.

The strategy owner's `add_or_merge_local_rejection` receives the timestamp and
original kwargs dictionary separately from named lane/target, rejection-map,
text-limit, validator and event-callback dependencies. The digest retains the
nested helper's signature through a thin adapter supplying its current helpers.
Payload keys such as `short` and `max_text` remain ordinary observation fields.
Valid-target fallback matching, lane promotion/map-key replacement, bounded
payload types, fill-only enrichment and existing/new returned row identity are
preserved. Event insertion, statistics and source/provenance still use root
`add_event`; classification and strategy/pipeline dispatch stay in place.

The digest retains `reconcile_reply_pipeline_effective_outcomes(events) -> None`
as a thin wrapper supplying its current `_normalise_lane` to the pipeline owner.
Reconciliation still mutates the same decision/stage dictionaries, with the
original local-rejection/outcome precedence and unknown distinctions. Its
invocation remains caller-controlled; the CLI does not add a reconciliation pass
or restore the retired legacy summary sections. Existing public/private summary,
validator, vocabulary and reason-classifier aliases remain available.

Strict parsing, branch predicates/order, `analyse`, `add_event`, statistics,
the local rejection adapter, source/self-test tracking, publication authority
and report assembly remain in the coordinator. Evidence counts and missing/null/
unknown values, telemetry bounds, callback-return identity, duplicate coalescing,
event order, JSON schema 3 and Markdown are unchanged. No callbacks or mutable
analysis state are stored globally.

Visual-context callers retain `parse_reply_visual_description_event` and
`reply_visual_context_report` as explicit digest imports from
`mrs_log_digest_visual_context`. All six `REPLY_VISUAL_DESCRIPTION_*` constants
move with the parser and retain their digest aliases. The unchanged
`SHA256_LOWER_RE` already belongs to `mrs_log_digest_values`; the visual leaf and
other digest validators share that definition. The visual leaf uses the existing
`_normalise_lane` without merging the distinct lane normalisers.

Complete signatures, defaults and bodies are unchanged, including strict field
allowlists, metadata and retained-analysis bounds, native integer/boolean checks,
status/count/schema distinctions, canonical hashes and malformed-input returns
and errors. Parsing copies retained analysis through the existing JSON round
trip; correlation preserves its original ordering, deduplication and sharing of
retained analysis objects without mutating the supplied observations. Outer
structured JSON parsing, call sites, event insertion, `analyse` and publication
authority remain in the coordinator. JSON schema 3 continues to omit the legacy
visual-context report sections.

Image-usage callers retain `generated_image_utilisation`, `generated_pool_runway`
and `regular_image_usage_summary` as explicit digest imports from
`mrs_log_digest_image_usage`. These accept prepared pool, post-rate,
configuration and event observations with unchanged signatures, defaults and
bodies. Image coverage, legacy metric aliases, percentages, rankings,
current-cycle scheduling/availability calculations and failure reasons are
unchanged. `generated_post_rate_history`, `load_runway_config` and
`RUNWAY_CONFIG_DEFAULTS` belong to `mrs_log_digest_generated_pool`, retaining
digest adapters/aliases. The image-usage owner remains a pure consumer; root
still selects inputs and reference times and assembles reports.

Dependency direction is digest → visual context → values, and digest → image
usage → standard library. Neither new leaf imports the coordinator, renderer,
bot or runtime readers, performs runtime I/O or introduces shared mutable state.
Markdown, CLI, defaults, provenance, clocks, locks and resume behaviour are
unchanged.

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
