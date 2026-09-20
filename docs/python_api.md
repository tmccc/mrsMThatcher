# Python Architecture And Documentation

For a short route through the current bot and digest, start with the
[code reading guide](code_reading_guide.md). This page is the detailed module,
interface and documentation reference.

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
| `mrs_bot_image_scoring.py` | Quotation/image lexical matching, topic IDF, visual energy, seasonal exclusion and component scoring | Fixed list, corpus, visual-energy and calendar-window helpers are used directly; current tag/phrase callbacks, token policy and penalties retain their existing runtime boundaries |
| `mrs_bot_original_editorial.py` | Original-image editorial metadata, concepts, scoring and selection comparisons | `OriginalEditorial` owns recursive concepts, validation/load/cache, profiles, bounded scores and comparisons. Current vocabulary/cache references and external image inputs bind per call; comparison reuse and row/diagnostic copies preserve existing selection and logging behavior |
| `mrs_bot_asset_metadata.py` | Asset loading, quote overrides, identity checks and current catalog discovery | `AssetMetadata` owns JSON, quote/image/meme loaders, override application and metadata lookups; pure normalization/hash/merge helpers call locally. Current paths, glob, logger, image checksum and exception authority bind without I/O. Preserves JSON-copy semantics, validation order, nested references and native failure boundaries |
| `mrs_bot_quote_candidates.py` | Ordinary quotation eligibility, seasonal weights and used-cycle selection | `QuoteCandidates` owns source preparation, seasonal rules, candidate building and cycle selection with current external metadata/source inputs. Preserves set/candidate references, lazy research access, reset rules and RNG order. The standalone research loader remains shared with preparation tools |
| `mrs_bot_image_selection.py` | Original-image eligibility, selection and quotation-image pair retries | `ImageSelection` owns eligible-cycle resets, checked image selection and choice logging. Pair retries retain the root callback so image policy binds after each quote choice. Metadata/scoring/history/editorial boundaries stay current; migration saves, metadata rechecks, caller references, one-time forced reset, exception scope and shared RNG order are preserved |
| `mrs_bot_quote_posting.py` | Ordinary quotation posting and local recovery | Selects, publishes and applies state through current callbacks. Confirmed persistence and receipt retirement use regular-post completion; separate recovery projections track partial progress |
| `mrs_bot_regular_post_completion.py` | Durable completion shared by live regular posts and receipt replay | Saves protected state, enqueues historical context, retires the transport journal and removes the receipt in order. Caller callbacks preserve lazy event and journal identity reads; failures propagate unchanged |
| `mrs_bot_daily_meme.py` | Daily meme assets, calendar scheduling and transactional posting | `MemeCatalog` owns discovery, summaries and history-reset selection; `MemeSchedule` owns calendar, fallback and quote-anchor operations. Posting keeps current callbacks, optional saves, clock order and bound receipt-replay schedules |
| `mrs_bot_legacy_reply_validation.py` | Frozen draft validation for retired tested-pipeline, AI-first and single-Sol schemas | Owns fixed schemas, strategy definitions and pure hash/claim validators directly; current ID/context validators and size limits remain external. A frozen sending draft cannot authorize a new send |
| `mrs_bot_reply_drafts.py` | Pending-draft keying, validation, storage, typed recovery, lane and target clearing, ineligible-draft retirement, target-presence checks and current receipt-draft validation | `ReplyDrafts` owns internal validation calls and acquires current evidence for each validation. Mutates supplied draft state and emits telemetry; performs no provider calls or durable saves. Construction is inert and retains no caller state |
| `mrs_bot_reply_history.py` | Confirmed reply recording, retention and history selection for evaluation and recovery | `ReplyHistory` owns filtering, deduplication, ordering, context exclusions and history inputs. Evaluation uses the target timestamp; draft recovery uses current time. Mutates explicit caller state without durable saves; construction is inert and captures current limits separately from the fixed recent-reply default |
| `mrs_bot_reply_state.py` | Shared admission identities, quote-target markers, ineligible-target retirement and compatibility aliases | `handled_reply_target_ids` returns a fresh set combining normal and legacy quote targets for admission; this is not confirmation evidence and leaves durable ledgers separate. Owns capped seen/skipped quote lists and the durable replied ledger used directly by reconciliation. Shared retirement clears drafts, then records a terminal evaluation; callers retain persistence |
| `mrs_bot_reply_generation.py` | Typed reply evaluation and outcome accounting | `ReplyGeneration` owns evaluation, health/local classification and decision telemetry; owned steps call each other directly. Coordinates media, history, evidence/pipeline and model transport through current callbacks. Returns the full PipelineResult and preserves cooldown ordering, prior-429 accounting, telemetry and distinct provider-health/candidate dispositions. No import-time runtime work |
| `mrs_bot_reply_model_transport.py` | Bounded model requests, retry metadata and provider error construction | `ReplyModelTransport` owns the existing two-attempt request flow and calls its own retry/error helpers. Preserves proved non-execution rules, short 429 delays, prior rate-limit metadata, request/response references and pause/health/close order. Current runtime dependencies are bound inertly; the API key is excluded from representations |
| `mrs_bot_reply_cycle_interfaces.py` | Typed reply-cycle settings, prepared context/media results and evaluation, persistence and delivery callbacks | Per-invocation values supplied by the coordinator; no I/O |
| `mrs_bot_reply_preparation.py` | Shared validated-draft persistence and sending-receipt construction | Reports draft validation failure before a durable state save; constructs schema-v4 receipts with separate context and draft copies. Lane owners retain validation guards, exact log wording, status mapping, provenance and attempt binding. Local failures propagate before transport handling; no import-time runtime work or retained caller state |
| `mrs_bot_reply_receipt_values.py` | Conversational receipt validation, send-template preparation, source reconstruction, pagination provenance and attempt/confirmation times | `ReplyReceiptValues` owns current/legacy lifecycle dispatch and calls its validation/projection methods directly. Preserves shallow source references and hashes; conservative confirmation uses the supplied clock/logger. Construction is inert; durable I/O and retirement authority remain external |
| `mrs_bot_reply_reconciliation.py` | Confirmed reply state application, shared completion, restart reconciliation and emergency completeness | Both reply lanes share apply, durable save, journal retirement and receipt removal. Application/save errors retain their original types; cleanup errors are wrapped after the save. Restart reconciliation keeps its separate persistence policy and exactly-once accounting; `ReplyCompletion` owns shared durable commit and ordered retirement while fresh, restart and emergency paths keep distinct error policies |
| `mrs_bot_reply_delivery.py` | Conversational reply transport and durable receipt lifecycle | Binds the current receipt-value owner for loading and delivery; writes sending receipts before transport. Current and legacy promotion share exact source binding and replacement checks. Removes or retires exact receipts; lanes own terminal bookkeeping |
| `mrs_bot_normal_reply_cycle.py` | Normal mention and hot-post reply orchestration | Uses fresh author-quarantine, evaluation, clarification and accounting owners alongside existing typed settings, persistence and delivery boundaries. Discovery, backlog continuation, actual-model-call budgets and one-success-per-cycle policy stay explicit. Owner clock calls, quarantine batching, draft persistence and durable retirement retain their ordering; pure clearing and count normalization import from their owners |
| `mrs_bot_quote_reply_cycle.py` | Quote-tweet reply orchestration, eligibility and lane markers | Uses fresh evaluation and accounting owners alongside the shared settings, persistence and delivery contracts. Context construction stays behind the existing callback to ReplyContext. Watch-list and numeric candidate ordering, reply delay, candidate/author limits, one-success-per-cycle policy and durable bookkeeping retain their existing order |
| `mrs_bot_quote_discovery.py` | Watched/recent own-post selection and batched recent-search quote discovery | `QuoteWatchPosts` owns fresh watch-file reads, recent-original selection and priority merging. Discovery retains supplied pagination callbacks, query-specific continuations and bounded per-original fallbacks |
| `mrs_bot_hot_post_discovery.py` | Hot-post discovery, bounded skip marking and normal-lane candidate handoff | Refreshes complete post text before filtering, updates caller caches/markers and saves cursor repairs; eligibility precedes candidate caps |
| `mrs_bot_mention_discovery.py` | Mention discovery, durable queue access and watermark retirement | `MentionQueue` owns recovery, ordered access and monotonic retirement through current authority, sorting and save boundaries. Fetched-page traversal remains explicit; transient lookup failures retain the queue |
| `mrs_bot_mention_authority.py` | `MentionAuthority`: pagination, backlog/reset guards and canonical page ownership | Validation owns normalization and recovery reporting; state loading still prefers usable backups before identity recovery, and persistence remains with callers |
| `mrs_bot_reply_evaluation_state.py` | Terminal evaluation recording, retention and watermark-covered skip pruning | `ReplyEvaluations` owns ledger writes and replay protection using current limits, policy, clock and logger. Preserves separate recording/pruning clock reads and in-place batching; pure watermark/terminal lookup and quarantine-clear compatibility exports remain. Construction is inert; durable saves stay external |
| `mrs_bot_author_quarantines.py` | Mention-author quarantine strikes, expiry, lookup and policy migration | `AuthorQuarantines` owns retention and transitions using current thresholds, policy, clock and logging. Normalization preserves historical migrations without clock access; pure clearing retains surviving record references. Construction is inert and durable saves remain external |
| `mrs_bot_tweet_lookup_cache.py` | Verified tweet lookup, normalization, pruning, cached text/media refresh and recent own-post index | `TweetLookupCache` calls its owned normalization, verification, pruning, storage and fetch operations directly. Current request, clock, policy and persistence boundaries bind inertly. Preserves cache-hit references, legacy refresh saves, transient media copies and fresh pre-send lookup |
| `mrs_bot_reply_context.py` | Verified parent paths and canonical normal/quote contexts with separate media | `ReplyContext` owns lookup policy, path verification, participant roles and both context builders. Quote construction uses owned post/logging operations and local text helpers, preserving independent copies, media references and date/log order. Current visible/incoming limits stay separate from the fixed post default. Construction is inert; returns PreparedReplyContext while only canonical context is persisted |
| `mrs_bot_reply_native_media.py` | Native attachments, photo selection, trusted image retrieval and byte validation | `ReplyMedia` owns selection through collection with distinct context/supplied caps and current MIME policy. Preserves target priority, source references, unresolved metadata, trusted origins, redirect/stream bounds, transient errors, response closing and validator-result identity. Discovery and tweet lookup import pure attachment expansion directly; construction is inert and remote calls use supplied safeguards |
| `mrs_bot_reply_lane_policy.py` | Deterministic target and spam gates | Preserves caller-visible eligibility and spam decisions; re-exports clarification constants, thread IDs and daily author-count normalization for compatibility |
| `mrs_bot_daily_reply_accounting.py` | Daily reply buckets, author counts and confirmed-reply increments | `DailyReplyAccounting` owns resets, canonical dates and confirmation advancement with current date/parser, logger and ID-list helper. Recovery never rolls newer buckets backward; increments retain the caller's prior idempotency decision and receipt-date snapshots. Mutates explicit state without saves; construction is inert |
| `mrs_bot_reply_clarifications.py` | Clarification eligibility, author windows and completed repair records | `ClarificationReplies` owns detection, terminal-thread lookup, confirmation conflicts and recording. Requires cached question and confirmed-reply proof; transient refresh errors defer evaluation. Conflict checking and completion recording retain their separate reconciliation positions, references and event order. Construction is inert; saves and delivery remain external |
| `mrs_bot_runtime_control.py` | `RuntimeControls`: stable snapshots, fail-closed cache results and pause policy | Loading and lane/global decisions call owned snapshot, recovery and boolean operations; the current cache, shared schema validation and runtime clock retain their boundaries |
| `runtime_control_contract.py` | Shared bot/digest control keys, timestamp bound and pure validation | Metadata validation rejects unsupported keys and non-integer generation; value validation preserves exact Decimal checks and the bot's existing timestamp rules. Separate phases let the digest retain its clock boundary; callers retain file I/O, parsing, caching and failure reporting |
| `mrs_bot_api_cooldowns.py` | API cooldown expiry, error-window pruning and rate-limit/repeated-error policy | `ApiCooldowns` owns internal pruning and rate-limit calculations. Preserves separate clock samples, scope routing and ordinary-save ordering; current dependencies are bound per root call without retaining caller state |
| `mrs_bot_x_pagination.py` | Bounded X pagination and cursor-error classification | Calls supplied request/page callbacks; tracks token history, preserves partial results and permits one invalidation/head recovery |
| `mrs_bot_request_route_values.py` | Endpoint normalization, loopback policy, X route classification and strict JSON request values | `XRequestRoutes` owns configured origin and prepared-path classification without sending. Fixed endpoint parsing, exact-route recognition and strict payload encoding use their local implementations; current endpoint configuration and exception authority retain their call boundaries |
| `mrs_bot_x_response_diagnostics.py` | Bounded tweet-create classification, anomaly evidence and error construction | `XCreateDiagnostics` owns classification and canonical JSON/hash emission with fixed local bounds. The module constructs ambiguous outcomes from emitted evidence; transport guards, current emission callbacks and exception authority keep their original timing |
| `mrs_bot_tick_coordination.py` | Reply-lane priority normalization, scheduled arbitration and blocked-tick coordination | Each reply tick reads and repairs its scheduling epochs from caller state before arbitration; callers supply no duplicate epochs. Priority, spacing, separate saves and barrier checks retain their ordering. Blocked ticks recheck durability and preserve retained SIGINT delivery; no retained runtime dependencies or import-time work |
| `mrs_bot_durable_json_io.py` | Canonical receipt bytes, owned state namespace checks, stable reads, atomic JSON publication and receipt creation | No-follow bounded reads; receipt parsing and exclusive creation enforce stricter authority rules than ordinary JSON writes. Durable writes include parent fsync |
| `mrs_bot_state_value_normalisation.py` | `StateValues`: bounded IDs and scalar, epoch, list and map normalization | Nested validation stays in the owner with current diagnostics and epoch policy; higher-level state/receipt policy and persistence use these primitives |
| `mrs_bot_state_persistence.py` | State documents, `StateBackups`, and canonical generation publication | Backup rotation owns stable copying; canonical publication retains reader checks, lock authority, commit proofs and the save-before-latest-backup boundary; load/save use the fixed value-free state summary directly and generation serialization remains in its owner |
| `mrs_bot_state_candidate_validation.py` | State candidate normalization, reader compatibility and meme schedule validation | Validates supplied state and applies recovery/normalization callbacks; handles overflow resets, legacy evaluation retirement and quarantine pruning |
| `mrs_bot_state_loading.py` | Durable state loading and ordered recovery selection | Refuses unsafe namespaces; checks primary/latest equality and usable backups before pending-identity recovery, then durably repairs recovered state; ordinary and pending-identity recovery share the same sealed-generation collision and newest-replica rule |
| `mrs_bot_main_post_receipts.py` | Main-post receipt validation and bound regular/meme schedule materialization | Validates lane/schema/source lineage and canonical hashes with fixed scalar and quote-hash helpers from their owners; replay uses bound calendar/timezone data, including DST transitions |
| `mrs_bot_main_post_receipt_storage.py` | Main-post receipt paths, storage and durable lifecycle transitions | Regular and meme writers share one publication lifecycle with explicit lane paths, schema versions, validators and exception types. Retirement and namespace gates precede validation; durable pending-schedule promotion and exclusive creation preserve their separate error boundaries. Readers retain lane-specific contracts. Storage operations use current lane paths and callbacks |
| `mrs_bot_main_post_attempt_values.py` | Main-post payload identity, meme-plan snapshots, attempts and pending confirmations | Owns fixed payload reconstruction and canonical JSON hashing, used directly by receipt validation and transport preparation; runtime validators, entropy, clock and copy boundaries remain supplied per call |
| `mrs_bot_main_post_confirmation_persistence.py` | Confirmed main-post promotion and protected/emergency persistence | Binds remote confirmation durably before fallible local work; exact-byte/source checks, incident latching and fsync failures maintain barriers |
| `mrs_bot_x_request.py` | Authenticated X requests and response handling | Enforces route/body/pause/authority checks before transport, handles diagnostics and provider health, and closes proof scopes. Bearer transport is read-only |
| `mrs_bot_post_creation.py` | Receipt-bound media upload, public-post creation and confirmed-media handoff | Owns fixed media metadata construction and validation used directly by upload, request and handoff checks; prepares source-bound payloads/journals before requests and preserves pause, rejection, ambiguity and SIGINT boundaries |
| `mrs_bot_main_post_reconciliation.py` | Main-post receipt application, emergency representation and local recovery | Distinguishes authoritative from stale/legacy replay, future schedule repair, image-history identity and schedule idempotence remain explicit; retired spacing fields are left untouched. Regular replay shares the live protected-persistence/outbox/retirement sequence while retaining recovered event fields and native exceptions. Regular precedes meme after the simultaneous-receipt gate; no retries or import-time runtime work |
| `mrs_bot_historical_context_delivery.py` | Historical-context delivery, store factories and interrupted-attempt recovery | Uses worker locks, receipt/source proofs and supplied posting callbacks; recovery updates outbox, history and receipt in that order |
| `mrs_bot_historical_context_queue.py` | Historical-context enqueueing and queue processing | Claims and delivers source-bound work through current callbacks, updates durable outbox/history and maintains the coordinator’s unavailable-reason latch |
| `mrs_bot_historical_context_runtime.py` | Historical-context startup reconciliation and writable preflight | Reconciles exact receipt/outbox lineage under the worker lock; preflight blocks a main post when required context state cannot be persisted |
| `mrs_bot_receipt_retirement.py` | Receipt retirement and exact transport source verification | Owns the fixed confirmed-outbox lineage matcher used directly by startup, queue and recovery; checks exact source bytes/digests, attempt ordinals and durable proofs before guarded journal/source mutations |
| `mrs_bot_remote_write_barriers.py` | Global remote-write barrier checks and eligibility for local recovery | Inspects receipts, journals, markers, media and outbox state; unsafe or unresolved evidence blocks unrelated writes, with bounded exact-source local recovery |
| `mrs_bot_local_config.py` | Strict runtime JSON and local-configuration loading/coercion | `LocalConfiguration` owns bounded stable reads, strict decoding, migration/coercion and complete override validation. Runtime validators remain supplied per call; source defaults, applying overrides and bootstrap remain in the root. Fixed parsing uses local standard-library helpers |
| `mrs_bot_runtime_configuration.py` | Runtime configuration validation, application and credential checks | Owns numeric bounds and cross-field validation for source defaults and coerced local overrides. Applies validated values through root adapters and checks required credentials. Configuration storage and source-default snapshots remain in the root module; no import-time runtime work |
| `mrs_bot_self_test.py` | Local self-test orchestration and result logging | Runs validation and local diagnostics through supplied dependencies; reports failures without entering the production loop |
| `mrs_bot_observability.py` | Logging setup, bounded diagnostics and publication/recovery events | Owns managed-handler marking/removal with a fixed marker; configures current supplied handlers/paths, redacts sensitive values and emits descriptive events without authorizing publication or recovery |
| `mrs_bot_runtime_service_initialisation.py` | Runtime health, lazy reply evidence and historical semantic-gate initialization | Initializes services on explicit calls using coordinator-owned caches and logger; reports unavailable components |
| `mrs_bot_safety_marker_snapshots.py` | Safety-marker snapshots and durability acknowledgement | Performs bounded no-follow reads, validates marker/successor identity and acknowledges exact bytes under the process lock with strict fsync |
| `mrs_bot_remote_write_incidents.py` | Remote-write incident latching and durable marker acknowledgement | Sets process latches before file work; clears uncertainty only after durable marker acknowledgement, before releasing retained SIGINT |
| `mrs_bot_instance_lock_checks.py` | Instance-lock verification and mutation-authority issuance | Verifies singleton socket, directory/file locks, PID and OFD evidence; issued authority revalidates the live lock before mutations; the fixed inode/device socket-name algorithm is shared directly with the offline reconciler |
| `mrs_bot_installation_lifecycle.py` | Installation completeness, initialization and startup ledger recovery | Requires bootstrap/singleton ownership, recovers exact ledger exchanges and initializes durable state/history. Failed initialization rolls back registered paths in reverse order |
| `mrs_bot_transaction_recovery.py` | Local transaction recovery and startup receipt gates | Coordinates global gates, sole-receipt selection and journal inspection; a private historical-lane helper contains exact source/outbox state, with shared bound-transport promotion preserving lane dispatch and native failures. Preloaded receipt identity, independent attempt ordinals, worker-lock rechecks and callback order remain intact; recovery performs no new remote work and retains no authority at import |
| `mrs_bot_transport_source_preparation.py` | Transport source preparation and final receipt gates | Validates and binds current/legacy source identities before transport mutation; checks retirement, journal, receipt and media barriers and generation-bound handoffs |
| `mrs_bot_cli_execution.py` | Command-line dispatch and one-shot execution | Frozen CLI/test-mode validation precedes current bootstrap and dispatch. One-shot startup retains state normalization, health progress, quote preflight, ordered failure handling and durable barrier waits. Reply ticks use the same state-authoritative scheduler as the main loop. CLI parsing, main/bootstrap and self-test diagnostics retain their existing owners; no import-time runtime work |
| `mrs_bot_used_history.py` | `UsedHistory`: durable quote and image history | Owns stable loading, source-verified migration and conditional rewrites; the image normalization algorithm also supports the historical simulator's separate corpus-proof policy |
| `mrs_bot_receipt_primitives.py` | Receipt scalar values, calendars and confirmation times | Validates bounded identities and dates; distinguishes ambient from receipt-bound calendars and uses conservative time fallback after remote success |
| `mrs_bot_runtime_state_helpers.py` | Runtime state defaults, load maintenance and scheduling values | Creates fresh defaults and mutates supplied schedule fields; epoch validation and lazy scheduling use current callbacks |
| `single_call_reply_validation.py` | Shared schema/mechanical validation rule vocabulary and bounded diagnostic normalization | Pure allowlisted rule projection; no bot imports, provider calls or I/O |
| `single_call_reply.py` | Frozen Sol prompt/schema, bounded context and facts, one-call orchestration, mechanical validation and durable-draft validation | One injected OpenAI Responses call; local validation and hashing |
| `reply_evidence.py` | Lexically shortlist validated local passages for compact trusted facts | Local corpus reads only |
| `historical_context_formatter.py` | Canonical research loading, compact context formatting and context-reply persistence | Local state; posting only through an injected callback |
| `shadow_lifecycle.py` | Strict validation for the versioned shadow-feature lifecycle register | Local file reads only |
| `mrs_log_digest.py` | Log-input coordination, aggregation and Markdown/JSON reports | Separate production and self-test source contexts retain pending observations and active provider attempts across interleaved records. Handlers use strictly parsed EVENT fields; compatibility parsing only classifies malformed visual diagnostics. Resume state uses the production context and retains its existing JSON contract. Local log and resume-state reads/writes; no provider calls |
| `mrs_log_digest_context.py` | Historical context, config backscan and digest-cursor persistence | Reads supplied log/cursor paths; saves through a temporary sibling and replacement; explicit current helpers, marker/tail limits, Counter factory, diagnostic and save-time clock; no import-time I/O or publication authority |
| `mrs_log_digest_input_io.py` | Stable file observations, strict native/Decimal JSON parsing and canonical receipt/history encodings | Reads only supplied paths; explicit current sibling callbacks; ordinary file hashing retains its separate read contract; no writes or import-time runtime access |
| `mrs_log_digest_records.py` | Shared frozen records, bounded source references, fingerprints, resume-window selection, prefixed-JSON observation parsing and combined log input/coverage reading | Reads/stats supplied log paths and emits existing missing-input warnings; explicit current regex, constructor, parsers, readers and helpers; no import-time runtime access |
| `mrs_log_digest_legacy_posts.py` | Raw legacy quiet/lane, quote/image, spacing, meme, created-post and conversational reply observations and companion response parsing | Supplied shared records, pending/latest objects, lists, counters, production event identities and current event/literal/ID helpers; explicit handled/state returns; no I/O, clock sample, runtime access or provider/posting actions |
| `mrs_log_digest_transactions.py` | Passive X request, transaction, receipt and media observation preparation, legacy matching, receipt/media correlation, shared reply-receipt lifecycle analysis and post-scan receipt/error reporting preparation | Supplied records, snapshots, health, pending state, lists/statistics and current helper/source callbacks; no I/O, clock sample, runtime access or publication authority |
| `mrs_log_digest_api_health.py` | Passive X/cooldown observation, latest-error enrichment and API counter/failure/report preparation | Supplied records, shared lists, production event identities and current helpers; separate preparation and report materialisation; no I/O, clock sample, source selection or publication authority |
| `mrs_log_digest_markdown.py` | Prepared-report Markdown presentation and section rendering | None; receipt lifecycle analysis is supplied by the caller |
| `openai_cost_cache_contract.py` | Shared cached-cost structure, canonical values and monetary consistency | Pure validation used by the collector and digest; freshness, retention and legacy-format policies remain explicit at their call sites. No file, clock or provider access |
| `mrs_log_digest_costs.py` | Published-cost cache loading, freshness checks, UTC-window accounting and report preparation | Uses the shared cache contract while retaining digest freshness and legacy-format policy. Cache reads only through an explicitly supplied stable reader; paths, observation times and strict JSON parser come from the caller |
| `mrs_log_digest_provider_costs.py` | Pure conversational provider usage totals, cost attribution, cache-metric coverage and currency formatting | None; consumes supplied observations without mutation |
| `mrs_log_digest_provider_observations.py` | Passive conversational provider call/usage/error parsing, context selection/reset, attempt matching and observation projection | Supplied records, pending/active state, lists/statistics and current parser/formatter/converter/source callbacks; returns active context/index and mutates shared attempts; later error observation returns context alone; no I/O, clock sample or runtime access |
| `mrs_log_digest_runtime.py` | Current state/configuration validation, operator pause and feature-lifecycle observations | Uses the shared runtime-control contract with supplied stable readers, strict JSON parsers, file-time conversion and pause clock; lifecycle reading remains lazy and local. No import-time runtime access |
| `mrs_log_digest_state_reporting.py` | Prepared current-state, author-strike, structured headline components, derived, reply-quality and mention-control reporting | Explicit data, current helpers, vocabulary, epoch conversion and observation clock; preparation preserves media rows and supplied state/record times; refreshes supplied reports and uses the supplied event callback and statistics counter; no I/O or import-time runtime access |
| `mrs_log_digest_remote_write.py` | Read-only remote-write barrier identities, grouping, safety, reconciliation archive and window annotations | Explicit paths, readers/parsers, diagnostic formatter, clock, snapshot callbacks and annotation time converters; supplied window/authority flag; lazy read-only inspectors; no import-time runtime access |
| `mrs_log_digest_incidents.py` | Per-record error/warning observation, operational-error classification, incident grouping/resolution, retirement evidence and remote pause scopes | Supplied observations, current helper/annotation callbacks, scope mappings and conditional clock/epoch conversion; preserves error/event identity and snapshot mutation; no file/home/configuration access or provider calls |
| `mrs_log_digest_snapshot_incidents.py` | Prepared current-snapshot and retirement-evidence incident reconciliation | Supplied incident/evidence references and current matching/text/time callbacks; in-place incident enrichment/appends and shallow evidence sharing; no reads, clock samples or provider calls |
| `mrs_log_digest_reply_evidence.py` | Structured reply-publication evidence validation and durable confirmed conversational receipt and historical reply-history loading/validation | Structured observations or explicit project paths, stable private reader, native-number parser, canonical encoders, time/source-reference and validator callbacks; no writes, clock sample or import-time runtime access |
| `mrs_log_digest_reply_text.py` | Exact confirmed public reply-text preparation from prepared runtime/receipt/history evidence | Mutates supplied report/events; explicit source-reference, epoch-conversion and helper/validator callbacks and warning limit; no evidence loading, I/O or clock sample |
| `mrs_log_digest_quote_publication.py` | Quote-publication validation and per-analysis evidence correlation | `QuotePublicationCorrelation` owns quote evidence, warning deduplication/counting/capping and report enrichment. Lazy source, validator and reference callbacks preserve authority and original event/reference sharing; no file, configuration, clock or provider access |
| `mrs_log_digest_corpus.py` | Historical-corpus counts, availability, policies and hashes | Reads the existing research/audit paths under an explicit project directory using supplied strict parsing and file hashing; parsing and hashing remain separate reads |
| `mrs_log_digest_generated_pool.py` | Offline generated-image discovery, metadata/hash validation, curation, used history, post rates and runway inputs; normal digest CLI reports the pool retired | Reads/scans existing pool locations and supplied log/project paths; current strict parsers, hashing, clocks, record reader, basename regex and defaults; no writes or import-time runtime access |
| `mrs_log_digest_historical_events.py` | Historical-context event field projection, family counters and emitted-event quality summaries | Only supplied invocation-local counters and event insertion callbacks are mutated/called; no I/O or import-time runtime access |
| `mrs_log_digest_consistency_events.py` | Passive consistency events, family counters and prepared context-transaction outcomes | Shared preparation classifies resolved pending and outstanding context transactions for JSON and Markdown. Existing raw events and counters remain available; parent matching, terminal-state rules and event order govern classification. No I/O, clock sampling or publication authority |
| `mrs_log_digest_generated_identity.py` | Historical generated-identity policy/shadow observation parsing and summaries | Mutates only supplied observation/error lists, local counters and the parser result's timestamp; strict parser, diagnostic formatter and lazy source-reference callback supplied by caller; no I/O or import-time runtime access |
| `mrs_log_digest_original_editorial.py` | Original-editorial selection/shadow observations, companion deduplication and summary | Mutates only supplied observation/error lists, local statistics/companion counters and the parser result's timestamp and event mode; strict parser, diagnostic formatter and lazy source-reference callback supplied by caller; no I/O or import-time runtime access |
| `mrs_log_digest_single_call.py` | Single-call reply decision, provider usage, posting outcome and recovered-draft observations and summary | Emits only through the supplied `add_event` callback; summary separates attempt compliance from ordinary totals and reads emitted events without mutation; no I/O, publication/recovery actions or import-time runtime access |
| `mrs_log_digest_reply_pipeline.py` | Legacy pipeline observation projections, effective-outcome reconciliation, summaries and strict majority-review telemetry validation/utilisation | Supplied event/rejection callbacks; reconciliation mutates supplied events in place; no I/O |
| `mrs_log_digest_reply_strategy.py` | Legacy conversational evidence fields, strategy observations, inferred outcomes, local-rejection coalescing, summary and no-reply categorisation | Explicit events, rejection map, payload dictionary and current callbacks; summaries read events without mutation or publication authority; no I/O |
| `mrs_log_digest_visual_context.py` | Pure reply visual-description validation and visual-context correlation/reporting | None; validates supplied dictionaries and summarises prepared observations; no publication authority |
| `mrs_log_digest_image_usage.py` | Pure offline generated-image utilisation/runway and historical regular-image selection summaries | None; consumes prepared pool, post-rate, configuration and event observations |
| `mrs_log_digest_values.py` | Shared digest scalar conversions, reason classifiers and report vocabulary | None |
| `mrs_engagement_analytics.py` | Read-only X metrics collection and isolated SQLite reporting | Local SQLite reporting uses the standard library; HTTP/OAuth imports are deferred until a collection request or read client is needed. X reads only with explicit flags; writes only under `engagement_analytics/` |
| `hybrid_reply_retrieval.py` | CLI for local hybrid retrieval experiments and review artefacts | Offline by default; provider-review commands require explicit execution and budgets |
| `semantic_alignment/hybrid_reply_retrieval.py` | Local E5 indexing, lexical-versus-hybrid evaluation and historical replay | Offline research files only; it is not imported by the production bot |

## Entry Points And Dependency Ownership

`mrsMThatcher2.py` coordinates production configuration, bootstrap, scheduling
and lane execution. Importing it does not load private host configuration,
acquire the production lock or enter the posting loop. Operational entry points
require `production_bootstrap()` first.

The bot and digest expose adapters and direct aliases for their implementation
modules. To follow an API, read the adapter for the current configuration, paths,
state and callbacks it supplies, then follow the delegated function. Owners do
not import the coordinator back or retain per-call dependencies. Function
signatures and docstrings in the owning module describe the individual API;
the table above identifies responsibilities and external effects.

The bot keeps configuration, caches, loggers and resource handles in the
coordinator. For example, image scoring uses current vocabulary and penalties,
while original-editorial loading uses the coordinator's analysis cache. Selection
uses the shared production RNG and caller-owned history. Disabled editorial
loading and selection do no work.

The main loop owns lane ordering and global safety checks. Its private
`_run_due_quote_post_for_tick` and `_run_due_meme_post_for_tick` helpers own
lane-specific due-time, pause, cooldown and retry decisions. Confirmed or
ambiguous remote outcomes never schedule an error retry.

## Conversational Reply Preparation And Delivery

`mrs_bot_reply_context` constructs bounded canonical context from verified
parent paths, full target text and declared quote references. Lookup budgets
include legacy text refreshes. Parent chronology and contiguous paths must be
verified before context becomes eligible for evaluation.

`PreparedReplyContext` in `mrs_bot_reply_cycle_interfaces` contains the canonical
`context` dictionary and a separate `media_context` value. The normal context
builder returns `None` for permanent context failure; the quote builder returns
a prepared result. Both reply lanes consume those fields directly. Media is
collected during context preparation, in target-first order with bounded,
deduplicated quote photos. The model and durable draft receive the canonical
context without a private media handoff field. Diagnostic context-summary hashing
uses the historical combined envelope so existing hashes remain comparable.

`PipelineResult` carries the evaluated or recovered reply through the lane.
`mrs_bot_reply_preparation` saves validated drafts and builds sending receipts
with separate context and draft copies. Provenance, attempt binding, budgets,
quarantine and terminal-target bookkeeping belong to the lane owner.

`deliver_prepared_reply` in `mrs_bot_reply_delivery` shares pre-send availability,
posting, confirmed/ambiguous error propagation and retryable API accounting.
`ReplyDeliveryStop` distinguishes terminal from retryable failures; the lane maps
that result to its own status. A sending receipt is durable before transport and
SIGINT deferral. When a target must be retired, its terminal state is durable
before the sending journal is retired. Application and save failures preserve
their own error types; cleanup failures are wrapped after the save.

Current draft validation and frozen legacy recovery validation are separate.
A frozen sending receipt remains a write barrier, never permission to send an
old draft. Confirmation promotion requires exact source lineage and transport
proof. Both reply lanes share durable completion through
`mrs_bot_reply_reconciliation`; restart recovery retains its distinct persistence
policy and exactly-once accounting.

## Digest Records, Resume State And Parsing

`Record` in `mrs_log_digest_records` is the shared frozen record type, also
exported by `mrs_log_digest`. The records owner implements log iteration,
source references, fingerprints, retention coverage and resume-window matching.
`iter_records` is lazy. `read_records_and_summaries` obtains selected records and
raw coverage from one parse of each source; coverage includes records outside the
selected time window and before deduplication. Input summaries report physical
first/last timestamps. Physical records retain the earlier observations needed
for resume matching.

`select_resume_window` returns a `ResumeWindowSelection` containing the selected
physical records, cursor mode, matched-tail length and timestamp-fallback status.
Matching preserves duplicate multiplicity and distinguishes physical order from
timestamp order. Timestamp fallback emits its diagnostic before filtering.
`read_records`, `summarize_input_files` and the bounded source/fingerprint helpers
remain available through the digest entry module.

`mrs_log_digest_context` owns `read_resume_data`, `save_resume_time`,
`apply_saved_context`, context merging and compatibility config backscan helpers.
Merges fill absent values rather than replacing current observations. Saved
state/config are historical diagnostics; normal digest execution loads current
runtime configuration directly. Restored context cannot establish publication
authority or replace current evidence.

Cursor reads use bounded no-follow regular-file inspection and strict native
JSON parsing. An absent cursor starts a fresh analysis silently; corrupt or
unreadable cursors warn and permit a fresh analysis. Saves strip internal markers,
retain bounded fingerprint tails and boundary multiplicity, and sample the clock
for `updated_at`. A private temporary sibling is flushed and fsynced, atomically
replaced, then the parent is fsynced;
parent directories are not created. Output must succeed before the cursor
advances.

`analyse` keeps production and self-test pending observations in separate source
contexts, including active provider attempts. Resume state belongs to the
production context. EVENT records are parsed strictly first; compatibility
parsing only classifies malformed visual-description diagnostics. Handlers use
`add_event` for insertion, statistics and source attribution. Semantic analysis
and public-text enrichment precede display shortening, so `max_text` cannot
change identity, status classification or correlation.

`mrs_log_digest_legacy_posts` handles raw historical quote/image, meme,
created-post and conversational-reply messages. Its handled/state results update
the coordinator's pending observations. `try_parse_response_id_text` permits
compatibility display recovery from older response text;
`response_post_id_is_canonical_string` separately enforces canonical string
identity for publication evidence. Numeric display conversion or salvaged text
alone cannot establish confirmed publication.

## Digest File And Runtime Observations

`mrs_log_digest_input_io` provides stable snapshot readers, strict JSON parsers,
canonical byte encoders and file hashes. Stable readers compare path and
file-descriptor metadata, reject symlinks and enforce bounds; private reads also
check owner, link count and permissions. `file_sha256` is an ordinary file read
that follows symlinks, and corpus parse/hash reads are separate observations.

`_strict_json_object` parses fractional numbers as exact `Decimal` values.
`_strict_native_json_value` and `_strict_native_json_object` retain native number
types and validate strings recursively as UTF-8. Strict parsing rejects duplicate
keys, non-finite numbers, overflow and invalid decoding; object parsers also
require an object root. Receipt and private-history encodings have different
contracts: `canonical_atomic_json_bytes` uses default ASCII escaping and JSON
non-finite handling, while `canonical_private_json_bytes` uses
`ensure_ascii=False, allow_nan=False`. Both produce sorted, indented UTF-8 JSON
with a trailing newline. `build_digest_contract` and `repository_head_sha` belong
to the entry point so producer identity refers to the digest source/repository.

`mrs_log_digest_runtime` owns `load_current_runtime_state`,
`load_current_runtime_config`, `runtime_control_snapshot` and
`shadow_lifecycle_snapshot`. State/configuration use native JSON numbers; controls
use exact Decimal parsing and the shared `runtime_control_contract`. Control
validation rejects unsupported keys and explicit null generation. Its clock is
sampled after document/key/generation validation and before boolean/time checks.
State observation time is sampled after loading, separately from generation time
and file mtime. Lifecycle loading is lazy and local; import, load or schedule
failures produce unavailable observations.

`mrs_log_digest_state_reporting` projects prepared state, author-strike progress,
current health and derived headlines. `summarize_latest_state` samples its supplied
clock once on entry. Author progress uses `state_observed_at or generation_time`
without another clock sample. Unknown, missing and invalid values remain distinct.
`refresh_derived` and `refresh_current_health_headline` mutate the supplied report;
headline components have explicit activity, reply-quality, health, observations
and cooldown ordering. Current health refresh replaces the health/cooldown
components from current evidence without interpreting rendered text.

The CLI's `run_digest` overlays current runtime observations before applying
saved context. `apply_saved_context` performs the final derived refresh when
saved-state loading is enabled, including when the cursor is absent; otherwise the coordinator
refreshes directly. The standalone saved-context API refreshes the supplied report
in place and returns `None`. Historical context stays diagnostic while current
state and receipt evidence determine current health.

## Requests, Receipts And Operational Health

`mrs_log_digest_transactions` observes X request starts, remote-write
transactions, main/reply receipts and media events. It consumes prepared records,
snapshots, lists and callbacks without contacting providers or repairing state.
Request observations are shared with source indexes; receipt handlers return
explicit pending identities. Self-test observations remain visible without
becoming production evidence.

`correlate_media_upload_incidents` and `prepare_media_incidents_and_errors`
correlate upload failures, fallback successes and receipt/archive evidence.
They retain suppression fingerprints and separate remaining errors from handled
media incidents. `append_unresolved_reply_receipt_errors` uses the same lifecycle
scan as `summarise_reply_receipt_lifecycle`, matching lane/target/reply identities
and removing the latest matching pending receipt. Repeated and unmatched events
remain distinguishable.

`summarise_main_post_receipt_lifecycle` and
`summarise_reply_receipt_lifecycle` prepare completed, cleared, unresolved and
unavailable receipt observations. Error reporting and presentation use their
own filters over those transitions. `prepare_reply_receipt_recovery_reporting`
combines prepared health, snapshots and receipt observations into durably
reconciled receipts, unavailable status and active snapshot receipts.

`analyse` adds the prepared summaries under
`main_post_recovery.receipt_lifecycle` and
`confirmed_reply_recovery.receipt_lifecycle` after event/evidence processing.
JSON and Markdown therefore consume the same lifecycle results. The public
`render_markdown` adapter reuses these fields and computes absent summaries for
older or minimal report dictionaries without mutating its input.

`mrs_log_digest_api_health` observes cooldowns and X errors, enriches the latest
API error and prepares `ApiHealthPreparation`. Request association uses an
inclusive absolute 300-second window. Deleted/inaccessible-target 403s and reply
eligibility restrictions have distinct handling. Prepared API counts deduplicate
transport identities and preserve literal-lane conflicts, media handoffs and
historical durable-only exclusions. `api_health_report` materializes those counts
with the current error, cooldown and request lists; it does not acquire evidence.

`mrs_log_digest_remote_write` reads current barrier artifacts and reconciliation
archives. Readers enforce stable metadata, bounded bytes and exact-Decimal JSON.
Artifact identity includes canonical document/source hashes and retirement
source/path bindings. Unsafe or unavailable artifacts remain explicit diagnostic
observations. Protocol, retirement, transport and media inspectors are lazy,
read-only calls. `annotate_remote_write_snapshot_window` annotates the supplied
snapshot using an explicit window and authority flag; it does not infer authority
or sample a clock.

`mrs_log_digest_incidents` classifies warnings/errors, groups operational
incidents and resolves them using prepared evidence. Its summary mutates supplied
error rows and snapshot annotations. Pipeline evidence, remote ambiguity,
transport attempts, pause scopes and recovery evidence have separate preparation
and matching helpers; none performs file or provider access.

Incident matching keeps transaction identity ahead of weaker target/lane
fallbacks. `_snapshot_identity_tokens` combines explicit tokens, document hashes
and retirement hashes qualified by source basename for both sides of a match.
Pause-scope inference prioritizes explicit evidence, then structured
events within 60 seconds, then a directional 10-second transport fallback.
Subordinate receipt evidence permits an outcome up to 300 seconds later;
transaction barriers use a five-second distance, while lane barriers require an
outcome no more than five seconds earlier. Recovery requires suitable identity,
scope, timing and evidence authority, not merely a later unrelated success.

Current-clear protocol recovery differs from authoritative namespace-clear
ambiguity recovery. Reconciliation archives require valid identities and native
integer epochs. Unknown scopes and unavailable snapshots cannot silently prove
resolution. `reconcile_current_snapshot_incidents` in
`mrs_log_digest_snapshot_incidents` enriches/appends incidents from prepared
current components and retirement evidence; unique ledger matching and retirement
conflict checks precede resolution. Snapshot loading, annotation and publication
authority remain distinct from report classification.

## Confirmed Publication Evidence

`mrs_log_digest_reply_evidence` validates durable confirmed conversational
receipts and historical reply history using bounded stable private reads,
native-number JSON and the appropriate canonical byte encoder. Exact schemas,
canonical timestamps, source hashes, draft/formatter validation and identity
bindings determine authority. Historical published text may be long or multiline;
current generation rules are not reapplied to already published text.

Structured publication uses `prepare_structured_reply_confirmation`,
`prepare_structured_historical_publication_evidence` and
`valid_structured_historical_completion_anchor`. Strict fields, production-source
authority, canonical IDs and completed anchors are checked independently of the
durable-file contract. Projecting a valid-looking ID into an event does not
establish publication.

`enrich_published_reply_text` in `mrs_log_digest_reply_text` mutates the supplied
report/event objects using prepared confirmations and durable evidence. Structured
confirmations take precedence; receipt confirmations fill absent reply keys.
Matching binds lane, target and reply identities. Unavailable and conflicting
text have separate results, and conflict handling controls source attribution.
Canonical historical evidence is matched and consumed before unresolved evidence
can synthesize an event. Drafts and unconfirmed observations remain unpublished.

Text enrichment preserves exact confirmed text, source-reference bounds and
omission counts. Warnings are capped by `PUBLISHED_REPLY_WARNING_LIMIT` (100).
The supplied production-event object identities control which rows may be
modified; historical report rows share those enriched events. Enrichment performs
no evidence reads, provider calls or clock sampling.

`mrs_log_digest_quote_publication` owns account-root identity validation and
quote-publication correlation. One `QuotePublicationCorrelation` holds evidence,
conflicts and bounded warnings for each analysis. Its `prepare_report` enriches
production quote events and sorts warnings. The JSON schema is version 4:
`quote_publication` contains publication warnings; retired engagement-question
trial fields/sections are absent. Account-root evidence can still supply exact
text for archived question posts.

## Provider Usage And Costs

`mrs_log_digest_provider_observations` parses call starts, usage and errors,
selects source/lane context and matches attempts. `observe_provider_message`
returns active context and attempt index while updating supplied attempt/usage
rows. Later `observe_provider_error` returns context alone; it does not clear the
attempt index. Missing, partial, malformed and cached usage remain distinct.

`mrs_log_digest_provider_costs` owns usage totals, attribution, cache coverage
and Decimal currency formatting. Permissive integer conversion and strict
nonnegative observation are separate APIs; unavailable cost is not zero cost.
Legacy `xai_reply_cost_summary` remains importable for offline callers, but the
current report omits retired per-candidate cost sections.

`mrs_log_digest_costs` loads the published-cost cache through an explicit stable
reader. `prepare_openai_published_cost_report` consumes an already loaded cache
without I/O. The digest resolves default paths/times and retains its historical,
ignored `provider_usage` argument. `openai_cost_cache_contract` owns shared
structure/value consistency; freshness, retention and legacy-format policy remain
with the collector or digest caller.

## Event-Family Reporting

Historical-context handlers in `mrs_log_digest_historical_events` project event
fields, maintain family counters and prepare quality summaries. The coordinator
validates completed/already-completed publication anchors against the strict
source record before counting the projected status. Durable history correlation
and exact public-text enrichment use the separate evidence owners.

`mrs_log_digest_consistency_events` projects control, clarification, repair,
posting-transaction, unavailable-evidence and meme-failure observations.
`production_consistency_report` reads the final counters and exposes the selected
event rows. Shared context-transaction preparation classifies resolved pending
and outstanding transactions for JSON and Markdown. Event insertion and
family-specific statistics remain separate operations; some historical counters
include both increments and are not unique-event counts.

`mrs_log_digest_single_call` projects decisions, usage, posting outcomes and
recovered drafts through the supplied `add_event`. `single_call_reply_summary`
uses those emitted observations for attempt compliance, token/latency totals and
validation details. Native integer/boolean bounds and finite temperature checks
are strict. An invalid explicit `recent_conversational_reply_count` cannot fall
back to `recent_reply_count`; the alias applies only when the primary is absent.
Posting/recovery telemetry alone confers no publication authority.

`mrs_log_digest_reply_pipeline` and `mrs_log_digest_reply_strategy` provide legacy
observation, summary and reconciliation APIs. Majority-review telemetry keeps
missing, empty, malformed and duplicate-family distinctions. Strategy inference
uses the latest decision and target outcome identities; inferred observations
lack production/source identity. Local rejection coalescing fills absent fields
and promotes valid lane/target keys without replacing existing row identity.
`reconcile_reply_pipeline_effective_outcomes` mutates supplied decision/stage rows
when explicitly called; the CLI does not add a legacy reconciliation pass.

`mrs_log_digest_original_editorial` records selections/shadows and suppresses one
later matching shadow per selection. Its comparison key binds quote hash, line,
phase, production source and both winners. Earlier shadows and repeated
observations keep their multiplicity; suppressed companions still contribute to
shadow/companion counters. Parsing diagnostics cover encoding/JSON errors; a
parseable invalid summary value is not silently coerced to zero. Summaries do
not mutate observations.

Historical generated-identity observers in `mrs_log_digest_generated_identity`
use `parse_prefixed_json_observation` for strict parsing and bounded parse-error
diagnostics. Their winners are observations, not publication evidence. Each
analysis owns its lists/counters; successful observations are separate from the
generic event stream. Malformed input includes bounded escaped text and a lazy
source reference, while summary/schema errors retain their separate failure path.

`mrs_log_digest_visual_context` validates historical visual-description events
and retains correlation helpers for legacy callers. Strict field/metadata bounds,
canonical hashes and native type distinctions govern validation. Current reports
omit the retired visual-context sections; malformed diagnostics remain observable.

## Corpus And Offline Image Analysis

`historical_context_corpus_snapshot` in `mrs_log_digest_corpus` reads corpus/audit
files and reports counts, policy and hashes. Separate parse/hash reads allow
partially loaded counts to remain visible when hashing fails.

`mrs_log_digest_generated_pool` retains explicit offline inventory, curation,
post-rate and runway-input helpers. Normal digest runs do not scan the retired
pool and report `available: false`, `status: "retired"`. Historical image
selection, spacing and identity observations remain readable.

`generated_post_rate_history` excludes contaminated seconds, deduplicates post
IDs and reports fixed 7/30-day windows from the selected scan. `load_runway_config`
starts with a copy of defaults, overlays matching observed values, then local
configuration. Pool clocks are sampled only when an explicit time is absent;
naive times use host-local handling. `mrs_log_digest_image_usage` consumes prepared
pool/rate/configuration observations for offline utilization, runway and regular
image summaries. These analysis helpers do not modify bot state.

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

## Retained Generated-Image Simulation

The standalone review/audit tools and archived assets remain available after
runtime removal. `tools/generated_image_simulation.py` binds historical image
selection settings, cache and hooks to the surviving ordinary bot helpers.
`tools/_generated_image_simulation_identity.py` owns the offline audit and
identity-policy calculations; `tools/_generated_image_simulation_selection.py`
owns mixed-pool metadata, discovery, spacing and candidate recovery. Ordinary
image scoring and original-editorial selection still use the bot's maintained
helpers. The bot does not import these offline modules.

## Reply Validation Diagnostics

`PipelineResult.validation_error_codes` carries an immutable tuple of known
schema and mechanical rule names. `single_call_reply_validation.py` supplies the
shared vocabulary and bounded normalization used by producer telemetry and the
digest, without importing either runtime. Unknown rule values and exception prose
are excluded. Existing error categories, draft validation and failure routing
retain their authority.

`rejected_reply_text_fields` provides the shared bounded projection for rejected
proposed replies. `rejected_reply_text` contains up to 4,000 characters with exact
whitespace and valid Unicode; `rejected_reply_text_character_count` retains the
original character count, and `rejected_reply_text_status` distinguishes
`available`, `truncated` and `unavailable`. The producer extracts only the JSON
`reply` field after model-output validation fails, or the reply from a pending
draft rejected during recovery. Missing, unparseable or invalid-Unicode text is
unavailable. Raw provider responses and model reasoning remain excluded, and
rejected text never becomes `PipelineResult.reply` or a reusable posting draft.

`single_call_reply.validation_failure_details` contains rule counts, counts by
rule category, detail-availability counts, and the latest 40 failed decision
rows. Missing historical details, empty lists, partial lists and malformed
values remain distinguishable; omitted counts expose bounded projection. Each
failed row and its structured decision event includes the rejected-text fields.
Older log events explicitly report unavailable text. Markdown renders the same
prepared diagnostics and labels rejected reply text as not published, separately
from confirmed public reply text.

## Safety Boundaries

- Main-post, meme, context-reply and conversational-reply receipts are separate
  durable transaction barriers.
- An ambiguous X write pauses all posting until an operator reconciles it.
- Generated-image selection and identity-policy processing are absent from the
  bot runtime. Retired configuration keys are ignored; old assets and durable
  records remain readable.
- Hybrid retrieval is an offline-only benchmark and is absent from the
  production reply path.
- The promoted original-editorial selector chooses original images when its
  existing enable flag is set. Its lifecycle review entry has been removed.
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
