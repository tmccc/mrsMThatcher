# Reading the bot and digest

Use this guide to follow execution or find the owner of a change. The complete
module and interface inventory is in [python_api.md](python_api.md).

## How to follow an adapter

The two entry modules coordinate execution and retain many small public adapters.
An adapter passes the current configuration, paths, state and callbacks to an
implementation module. Follow its delegated call to read the behaviour; return
to the adapter to see which dependencies production supplies. Keep those
dependencies explicit rather than importing the entry module back into an owner.
Some dependency-free functions are direct aliases instead of adapters.

## Bot startup and scheduling

Start in [mrsMThatcher2.py](../mrsMThatcher2.py):

1. `run_cli()` delegates parsing and dispatch to
   [mrs_bot_cli_execution.py](../mrs_bot_cli_execution.py). The normal command
   calls `production_bootstrap()` and then `main()`.
2. `production_bootstrap()` applies deployment configuration and initialises
   runtime services. Importing the bot alone does not enter this path.
3. `main()` acquires the instance lock, validates the installation, loads state
   and reconciles recovery evidence. `_log_startup_configuration()` contains the
   startup configuration messages; recovery decisions remain in `main()`.
4. Each scheduler tick checks controls and remote-write barriers, then runs due
   historical-context work, reply checks, quotation posts and memes. Start with
   `run_reply_lane_checks_for_tick()`, `_run_due_quote_post_for_tick()` or
   `_run_due_meme_post_for_tick()` for timing and lane selection.

## Three live paths to read first

The root labels operational authority, runtime assembly and compatibility API
sections explicitly. Its private `_..._owner()` functions build current,
invocation-scoped dependency graphs; adjacent public adapters are not evidence
that production returns through the root. Definitions remain in their existing
order where import-time safety or test monkeypatching makes movement risky.

For the normal mention/hot-post path:

1. `run_reply_lane_checks_for_tick()` chooses the lane in
   [mrs_bot_tick_coordination.py](../mrs_bot_tick_coordination.py), then the
   root `maybe_reply_to_mentions()` enters `ReplyAssembly.run_normal` with current
   reply configuration and shared application authorities.
2. [mrs_bot_normal_reply_cycle.py](../mrs_bot_normal_reply_cycle.py) owns
   discovery order, admission, model-call budgets, backlog continuation and
   status mapping. It calls `ReplyContext.build`, `ReplyDrafts.recover` and
   `ReplyGeneration.evaluate` directly; a recovered draft makes no model call.
3. A valid reply is durably drafted, copied into a sending-receipt template,
   and handed to the shared `ReplyCycleDelivery` boundary described below.

For the quote-tweet path:

1. The same tick coordinator selects the quote lane, and root
   `maybe_reply_to_quote_tweets()` enters `ReplyAssembly.run_quote`.
2. [mrs_bot_quote_reply_cycle.py](../mrs_bot_quote_reply_cycle.py) asks its
   `QuoteWatchPosts` owner for watched originals, uses quote discovery, and owns
   candidate ordering, delay/cap checks and quote-lane bookkeeping.
3. It calls `ReplyContext.build_quote`, `ReplyDrafts.recover` and
   `ReplyGeneration.evaluate` directly, then enters the same delivery boundary.
   The public root context, draft and generation helpers are compatibility APIs,
   not steps in either live cycle.

For the posting and durable-state boundary, start with
`ReplyCycleDelivery.deliver` in
[mrs_bot_reply_delivery.py](../mrs_bot_reply_delivery.py). It rechecks target
availability, then `ReplyAssembly.post_with_current_owners` composes send-time
receipt owners and enters `post_conversational_reply_with_durable_identity`. That flow
publishes the sending receipt before `create_post`; `create_post` owns the
transport journal/fence and X-create authority. On confirmed transport,
`ReplyCompletion.finalise` in
[mrs_bot_reply_reconciliation.py](../mrs_bot_reply_reconciliation.py) applies
the confirmed receipt, durably saves canonical state, retires the journal, and
only then removes the source receipt through `ReplyReceipts.remove`. Proved target
rejection is retired by `ReplyReceipts.retire_rejected`, after terminal state is
durable; it validates the exact receipt and claims transport proof before removal.
The root retains the sealed transport, exact source retirement, signal deferral
and global write barrier. `ReplyAssembly` binds current values again for backlog
continuation, send-time construction and nested receipt reads. Ambiguous outcomes
preserve their durable barriers. This is the boundary to read before changing
posting authority or persistence ordering.

Bootstrap composes a fresh `LocalConfiguration` before
`mrs_bot_runtime_configuration.apply_local_config` loads and applies overrides.
The application owner calls `load_overrides` directly; the public
`load_validated_local_config_overrides` adapter remains compatible but is not on
the production application path. `apply_local_config` retains 5 injected
parameters, with the former loader callback replaced by the typed owner. Stable
file inspection, complete validation and exception identity stay in
`LocalConfiguration`; namespace mutation and logging stay in runtime
configuration.

Each OAuth X request similarly receives fresh `XRequestRoutes` and
`XCreateDiagnostics` owners. Request execution calls route origin/prepared-route
classification and create-response classification/emission directly, while the
public route and diagnostic adapters remain available. The implementation entry
point has 39 total parameters (32 injected), down from 41 (34). Authentication,
provider execution, validated error responses, transport authority and pre-send
pause/receipt controls remain explicit boundaries. Route preparation and origin
selection retain the current Requests capability; anomaly bytes, hashes, clock
sampling, canonical logging and exception construction keep their existing
order and scope. Hand-off tests block all obsolete route/diagnostic relays while
exercising the real adjacent owners for confirmed and anomalous responses.

The final root-composition audit deliberately retains callbacks that carry
fresh authority or side-effect timing: runtime-control snapshots, receipt and
journal loading/retirement, durable state/history writes, provider transport,
remote-write barriers and proof-gated removal. The local self-test also keeps
its public loader callback as a non-production diagnostic boundary. Replacing
those edges would require broader ownership or semantic redesign, so they are
not treated as a zero-root-name target.

For a conversational reply, read the lane owner first, then follow the step you
need. A recovered draft can bypass model evaluation.

| Step | Implementation to read next |
|---|---|
| Mention/hot-post orchestration, budgets and quarantine | [mrs_bot_normal_reply_cycle.py](../mrs_bot_normal_reply_cycle.py) |
| Quote-tweet orchestration | [mrs_bot_quote_reply_cycle.py](../mrs_bot_quote_reply_cycle.py) |
| Ordinary quotation eligibility, seasonal weights and used-cycle selection | `QuoteCandidates` in [mrs_bot_quote_candidates.py](../mrs_bot_quote_candidates.py) |
| Current daily-meme calendar, fallback and first-quote anchor | `MemeSchedule` in [mrs_bot_daily_meme.py](../mrs_bot_daily_meme.py) |
| Quote delay selection and schedule persistence | `QuoteSchedule` in [mrs_bot_runtime_state_helpers.py](../mrs_bot_runtime_state_helpers.py) |
| Ambient and conversational daily-cap dates | `ReceiptDates` in [mrs_bot_receipt_primitives.py](../mrs_bot_receipt_primitives.py); receipt-bound timezone conversion keeps its separate policy boundary |
| Meme catalog discovery, cycle selection and image summaries | `MemeCatalog` in [mrs_bot_daily_meme.py](../mrs_bot_daily_meme.py) |
| Ordinary quote and daily meme execution | `QuotePostRunner` in [mrs_bot_quote_posting.py](../mrs_bot_quote_posting.py) and `DailyMemeRunner` in [mrs_bot_daily_meme.py](../mrs_bot_daily_meme.py); [mrs_bot_main_post_assembly.py](../mrs_bot_main_post_assembly.py) binds each operation |
| Shared quote/meme attempt publication, guarded send and durable confirmation | `MainPostPublication` in [mrs_bot_main_post_publication.py](../mrs_bot_main_post_publication.py); lane owners retain rollback, schedule projections and emergency recovery |
| Main-post receipt application, completion and restart replay | `MainPostRecovery` in [mrs_bot_main_post_reconciliation.py](../mrs_bot_main_post_reconciliation.py) |
| Historical-context queue selection, recovery, claims and durable outcomes | [mrs_bot_historical_context_queue.py](../mrs_bot_historical_context_queue.py); its coordinator keeps gates and loop decisions, with local helpers for sending a claim and applying exception/returned outcomes |
| Candidate discovery and durable mention/quote queues (`MentionQueue` owns mention access and retirement) | [mrs_bot_mention_discovery.py](../mrs_bot_mention_discovery.py), [mrs_bot_hot_post_discovery.py](../mrs_bot_hot_post_discovery.py), [mrs_bot_quote_discovery.py](../mrs_bot_quote_discovery.py) |
| Watched and recent original selection | `QuoteWatchPosts` in [mrs_bot_quote_discovery.py](../mrs_bot_quote_discovery.py) |
| Verified tweet lookup, cache refresh and recent own-post index | `TweetLookupCache` in [mrs_bot_tweet_lookup_cache.py](../mrs_bot_tweet_lookup_cache.py) |
| Verified normal context and two-turn quote context | `ReplyContext.build` and `build_quote` in [mrs_bot_reply_context.py](../mrs_bot_reply_context.py) |
| Original-image editorial metadata, concepts and bounded score adjustments | `OriginalEditorial` in [mrs_bot_original_editorial.py](../mrs_bot_original_editorial.py) |
| Quote/image/meme metadata, overrides and verified asset lookup | `AssetMetadata` in [mrs_bot_asset_metadata.py](../mrs_bot_asset_metadata.py) |
| Eligible-image cycles, verified selection and choice diagnostics | `ImageSelection` in [mrs_bot_image_selection.py](../mrs_bot_image_selection.py) |
| Native photo selection, retrieval and byte validation | `ReplyMedia` in [mrs_bot_reply_native_media.py](../mrs_bot_reply_native_media.py) |
| Read, write, quote and provider cooldowns | `ApiCooldowns` in [mrs_bot_api_cooldowns.py](../mrs_bot_api_cooldowns.py) |
| Bounded model requests and retry/error metadata | `ReplyModelTransport` in [mrs_bot_reply_model_transport.py](../mrs_bot_reply_model_transport.py) |
| Reply evaluation, local validation and outcome accounting | `ReplyGeneration` in [mrs_bot_reply_generation.py](../mrs_bot_reply_generation.py), then [single_call_reply.py](../single_call_reply.py) |
| Pending-draft validation, storage, recovery, clearing and receipt-draft checks | `ReplyDrafts` in [mrs_bot_reply_drafts.py](../mrs_bot_reply_drafts.py) |
| Confirmed-reply history recording and selection | `ReplyHistory` in [mrs_bot_reply_history.py](../mrs_bot_reply_history.py) |
| Shared normal/hot-post admission identities and ineligible-target retirement | [mrs_bot_reply_state.py](../mrs_bot_reply_state.py) |
| Receipt validation, send-template preparation, source reconstruction and time binding | `ReplyReceiptValues` in [mrs_bot_reply_receipt_values.py](../mrs_bot_reply_receipt_values.py) |
| Author quarantine strikes, expiry and policy migration | `AuthorQuarantines` in [mrs_bot_author_quarantines.py](../mrs_bot_author_quarantines.py) |
| Terminal evaluation recording and replay-protection retention | `ReplyEvaluations` in [mrs_bot_reply_evaluation_state.py](../mrs_bot_reply_evaluation_state.py) |
| Clarification eligibility and completed repair history | `ClarificationReplies` in [mrs_bot_reply_clarifications.py](../mrs_bot_reply_clarifications.py) |
| Daily reply buckets, author counts and confirmation accounting | `DailyReplyAccounting` in [mrs_bot_daily_reply_accounting.py](../mrs_bot_daily_reply_accounting.py) |
| Durable confirmation, journal retirement and receipt cleanup | `ReplyCompletion` in [mrs_bot_reply_reconciliation.py](../mrs_bot_reply_reconciliation.py) |
| Pre-send availability, delivery and read/write failure routing | `ReplyCycleDelivery.deliver` in [mrs_bot_reply_delivery.py](../mrs_bot_reply_delivery.py); terminal bookkeeping and status mapping stay in each lane |
| Save draft, prepare receipt, send and commit confirmation | [mrs_bot_reply_preparation.py](../mrs_bot_reply_preparation.py), [mrs_bot_reply_delivery.py](../mrs_bot_reply_delivery.py), [mrs_bot_reply_reconciliation.py](../mrs_bot_reply_reconciliation.py) |

For an ordinary quotation post, the root composes one `ImageSelection` graph at
invocation entry. It shares one `AssetMetadata` with `QuoteCandidates`,
`UsedHistory` and `OriginalEditorial`; the history owner also shares that
`QuoteCandidates`. `ImageSelection.choose_pair` performs bounded quotation
retries and calls `QuoteCandidates.choose` and `ImageSelection.choose_matched`
directly. Regular posting uses the same selection owner for the legacy-history
gate and all image-cycle recovery passes. The public metadata, history,
editorial, quote-selection, image-selection and pair-selection adapters remain
available, but are outside this internal path.

This graph binds current paths, policy, clocks, exceptions and external
boundaries on every root invocation without reading files during construction.
`AssetMetadata` remains at 9 constructor dependencies (2 callback-typed).
Direct composition reduces `QuoteCandidates` from 16 dependencies (7
callback-typed) to 13 (3), `UsedHistory` from 12 (5) to 11 (2),
`OriginalEditorial` from 13 (2) to 12 (0), and `ImageSelection` from 20 (11) to
16 (2). The regular-post implementation entry point now has 43 total parameters
(40 injected): the selection owner and the shared main-post receipt/value/cache
owners replace their former root relays. Hand-off tests block the obsolete
relays while exercising real metadata validation, source-verified migration,
editorial comparison, bounded pair recovery and receipt hand-offs.

Daily meme posting similarly receives one invocation-scoped `MemeCatalog` and
`MemeSchedule`: selection, summary construction, same-day checks and fallback
scheduling call those owners directly. Main-post receipt application uses a
current `MemeSchedule` for legacy date projection and quote-anchored fallback;
due-post ticks and reconciled regular-receipt repair call `MemeSchedule` and
`QuoteSchedule` directly. The public catalogue and scheduling adapters remain
available outside these internal paths. `MemeCatalog`, `MemeSchedule` and
`QuoteSchedule` retain 4, 13 and 5 constructor dependencies respectively. The
daily-meme implementation entry point falls from 46 parameters (45 injected)
to 42 (41), and regular receipt application now has 9 (5).

At each quote or meme invocation the root composes one `MainPostReceiptValues`,
one `MainPostReceipts`, one `TweetLookupCache` and one `MainPostPublication`.
Publication, transport preparation, `create_post`, confirmation persistence and
regular/meme reconciliation call those typed owners directly for attempt
validation, payload binding, storage, finalization, materialization and cache
updates. Later operations still use `current()` so policy is rebound at the
same boundaries as before. Public root adapters remain compatible, and the
proof-gated `remove_main_post_attempt`, `remove_regular_post_receipt` and
`remove_meme_post_receipt` functions remain the only retirement authorities.

`MainPostPublication` now has 21 constructor dependencies (down from 23),
`MainPostReceipts` has 17 (down from 19), and `MainPostReceiptValues` and
`TweetLookupCache` remain at 10 and 14. Transport preparation falls from 7 to 6
total parameters, confirmation promotion from 25 to 23, and `create_post` from
38 total parameters (28 injected) to 36 (26). Regular and meme reconciliation
fall from 19 to 18 and 13 to 12 parameters respectively. Hand-off tests make
the obsolete root relays raise while exercising adjacent real owners; transport,
journal, persistence, proof and remote-write boundaries remain explicit.

Conversational receipt composition shares one current `ReceiptDates` across
`ReplyReceiptValues`, `DailyReplyAccounting` and confirmed-state application.
London daily-cap conversion therefore bypasses the public date adapters while
ambient `epoch_date_str` and receipt-bound main-post timezone conversion remain
separate. `DailyReplyAccounting` stays at 2 constructor dependencies but drops
its callback-typed dependency from 1 to 0. Construction remains inert: file
catalogues, clocks, random draws and state saves begin only in invoked methods.
Hand-off tests make obsolete relays raise while exercising real owners, DST/date
conversion, same-day guards, retry scheduling and receipt accounting.

[mrs_bot_reply_cycle_interfaces.py](../mrs_bot_reply_cycle_interfaces.py) describes
the settings and callback groups supplied to both reply lanes, and re-exports
the delivery owner from its behavior module. Its
`PreparedReplyContext` carries canonical context and separately prepared native
media from builders to the lanes; only canonical context enters durable drafts
and receipts. In the normal cycle, `evaluation_record_pruning_pending` tracks
deferred in-memory pruning;
`quarantine_retirements_pending` tracks bookkeeping still needing a durable save.
These are different obligations even when they arise from the same candidate.

The root creates the relevant state owners once per lane invocation.
Private lane helpers use those owners directly;
backlog continuation re-enters the current root callback and receives fresh
owners. Clocks, date reads and durable saves still occur at their original
operation boundaries.

Both reply cycles receive `ReplyContext`, `TweetLookupCache`, `ReplyGeneration`,
`ReplyHistory`, `ApiCooldowns` and `RuntimeControls` directly. Follow
`build`/`build_quote`, `get_cached`/`store`, `evaluate`/`record_result`,
`recovery_replies`, `active`/`record_error` and `lane_paused` in those owners;
the cycles no longer call their root compatibility relays. The hand-off tests in
`tests/test_bot_normal_reply_cycle.py` and `tests/test_quote_pending_candidates.py`
exercise these owners together with
the relays blocked, including current history for recovery and chronological
history for model context. The root still composes current provider, policy and
persistence dependencies at each invocation.

Discovery uses the same hand-off rule. `MentionQueue` receives
`MentionAuthority`; mention discovery receives `TweetLookupCache` and
`ReplyEvaluations`; hot-post discovery receives those two owners plus
`QuoteWatchPosts`, `ApiCooldowns` and `RuntimeControls`; and the quote cycle calls
`QuoteWatchPosts.lookup` directly.
Each composition shares its invocation's `TweetLookupCache` with nested
watched-post selection.
The normal-cycle root composes the two discovery operations from the real module
functions, so public `get_mentions` and `get_hot_post_reply_candidates` adapters
remain compatible but are outside the cycle path. X transport and pagination
remain callbacks, and backlog continuation still re-enters the root to acquire
fresh owners. The corresponding discovery entry points have 20 and 25 total
parameters; mention discovery drops from 20 to 19 injected dependencies, while
hot-post discovery keeps 24 injected dependencies but replaces five callback
relays with typed owners. `MentionQueue` and `QuoteWatchPosts` each retain five
and six constructor fields respectively, with one fewer callback-typed field.

`ReplyGeneration` in turn receives `ReplyMedia`, `ReplyHistory`,
`ReplyModelTransport` and `ApiCooldowns` directly. Its evaluation path calls
`collect`, `for_evaluation`, `call`, `error`, `active` and `record_error` on
those owners; the public root adapters
`collect_reply_images`, `openai_responses_reply_call` and `_openai_api_error`
remain available for compatibility but are not on either production reply path.
The generation constructor has 15 dependencies (5 callback-typed), down from 20
(11 callback-typed). The normal and quote cycle implementation entry points now
have 38 and 34 total parameters respectively (37 and 33 injected dependencies),
so backlog re-entry and lane-local policy remain explicit rather than moving
into generation. Their hand-off tests block
the obsolete relays while exercising actual media and history operations and the
bound model-transport method.

Each reply-cycle root shares one `ApiCooldowns` instance with generation,
delivery and nested hot-post discovery. The normal cycle likewise shares one
`RuntimeControls` instance with hot-post discovery; the quote cycle receives a
fresh control owner. `ReplyCycleDelivery` calls cooldown error routing directly,
while its posting callback still binds send-time dependencies afresh. Runtime
state loading calls `ApiCooldowns.clear_expired` directly, and startup/main-loop
control checks call `RuntimeControls` directly. Public cooldown/control adapters
remain compatible. `require_remote_operation_unpaused`, instance-lock checks,
provider transport and remote-write authorities remain operational boundaries.

Private lane steps distinguish `SkipReplyCandidate` from
`FinishReplyCheck(status)`, and carry `PreparedReplyContext` through preparation
without converting it to an anonymous tuple. Quote admission captures fixed
history for each scan while accumulating newly classified spam authors.
`_ReplyCycleProgress` owns deferred quarantine pruning and flushing; flags clear
only after their corresponding operation succeeds.

Mention receipt continuation is prepared by `mention_receipt_pagination` in
[mrs_bot_mention_authority.py](../mrs_bot_mention_authority.py). Reconciliation
selects explicit or legacy continuation in its private pagination helper before
clearing target drafts. Image collection delegates each bounded transfer to
`ReplyMedia._download_image`; `ReplyModelTransport._decode_response` interprets
provider responses and closes them on both ordinary and unexpected failures.

Fetched mention pages are committed by `_persist_mention_page` in
[mrs_bot_mention_discovery.py](../mrs_bot_mention_discovery.py). The fetch loop
keeps traversal selection, page budgets and cursor recovery. Its explicit
progress record tracks valid rows seen, highest ID, page count and completion;
completion is set after the durable save and before the completion event.

Draft, receipt, delivery and completion composition now follows typed owners all
the way through each lane. `ReplyDrafts` calls `ReplyHistory` and
`ReplyGeneration`; `ReplyReceiptValues` calls `ReplyDrafts`; `ReplyCycleDelivery`
calls `ReplyReceipts`, `ReplyCompletion`, `TweetLookupCache` and receipt values;
confirmed application calls `MentionAuthority`, `MentionQueue`, `ReplyDrafts`,
`TweetLookupCache` and `ReplyHistory`. Receipt-aware global barriers are composed
directly rather than loading through the public receipt adapter. Public root
entry points retain their names and behavior.

`ReplyDrafts` remains at 12 constructor dependencies while callback-typed fields
fall from 5 to 3. `ReplyReceiptValues` remains at 7 and falls from 4 to 3;
`ReplyReceipts` remains 18 (11 callback-typed); `ReplyCompletion` remains 11 and
falls from 6 callbacks to 5; `ReplyCycleDelivery` falls from 18 dependencies
(11 callback-typed) to 17 (6). Confirmed-state application falls from 20 total
parameters to 17 by replacing eight relays with five typed owners. Construction
remains inert, while each root invocation binds current runtime dependencies.

`prepare_sending_template` checks current send authority before receipt I/O and
preserves reviewed nested objects in a fresh outer mapping. Current and frozen
legacy source promotions use the same transaction operation with their separate
validators; journal binding, exact source bytes and replacement authority remain
in delivery. Receipt removal retains its commit-proof gate, and
`ReplyReceipts.current_receipts` still refreshes later operations.
Sending and confirmed receipt publication also share one operation, keeping
retirement, namespace, validation and exclusive-create checks in that order.
`ReplyCompletion` shares receipt-commit recording and durable saving, followed by
journal retirement and receipt removal using the same commit proof. Fresh replies,
restart reconciliation and emergency state fallback keep their distinct exception
and signal-deferral policies around those owned operations.

`TweetLookupCache` owns cache normalization, pruning, storage, verified fetches
and the recent own-post index. Cached context preserves row identity on a hit;
legacy text refresh saves canonical text while media-only refresh stays transient.
Pre-send availability uses a fresh lookup. Root adapters construct current owners;
clocks, provider requests and saves remain inside their original operations.
Cache normalization calls the composed `StateValues` owner directly, and state
candidate validation shares that same value owner with its `TweetLookupCache`.
Lookup tests patch `fetch` on the owner; `restore_tweet_lookup_fetch` restores its
real transport operation when an isolated test server supplies the response.
`ReplyContext` receives a current `TweetLookupCache` owner and calls `prune` and
`get_cached` directly for parent traversal and directly quoted posts. The
context-to-lookup hand-off test in `tests/test_bot_reply_context.py` blocks the
root lookup/pruning relays while exercising cache identity, bounded parent
fetches and transient media refresh. Normal and quote cycles receive this owner
directly; the old root context builders and their method-level helper adapters
are no longer APIs. `ReplyContext` also receives `ReplyMedia` directly and calls
`context` for both normal and quote preparation; the root
`reply_media_context_for_candidate` adapter remains public but is outside both
production reply paths. Its constructor remains at 19 dependencies while its
callback-typed fields fall from 7 to 6.

`ClarificationReplies` receives one current `ReplyContext` and uses its parent,
own-reply and nested tweet-cache operations directly. The obsolete root
`get_immediate_parent_id` and `is_our_auto_reply` method adapters have been
retired; the separate `get_tweet_by_id_cached` cache adapter remains outside
clarification evaluation. Its constructor falls from 9 dependencies (6
callback-typed) to 6 (2 callback-typed). Refresh tests install traps under the
retired names while exercising the real context-to-cache hand-off.

State loading, public candidate normalization and state publication each compose
current `StateValues`, `AuthorQuarantines`, `ReplyEvaluations`,
`TweetLookupCache` and `MentionAuthority` owners at root invocation entry.
Candidate normalization calls them directly in the existing field, recovery and
pruning order; loading passes that composed normalizer directly rather than
re-entering the public root adapter. `save_state` also passes a directly composed
`StateGenerationContext` and `StateBackups` to canonical publication. Rotation
still precedes canonical replacement, while latest-backup publication remains
after the commit proof and retains its distinct failure handling. Public state,
cache and backup adapters remain compatible but are outside these internal
paths.

The candidate-normalization implementation falls from 17 total parameters (13
injected) to 16 (12), replacing six callback/factory dependencies with five
typed owners. `TweetLookupCache`, `StateValues`, `AuthorQuarantines`,
`ReplyEvaluations`, `MentionAuthority`, `StateBackups` and
`StateGenerationContext` have 14, 2, 8, 6, 5, 8 and 5 constructor fields
respectively. The persistence entry point falls from 14 total parameters (12
injected) to 13 (11), replacing three root relays with two owners. Hand-off tests
block the obsolete normalization, cache epoch, backup and generation-context
relays while exercising real loading, validation and durable publication.

Quote discovery saves fetched candidates in `quote_pending_candidates` before
advancing recent-search cursors. Pending work is returned before further search,
even if its original leaves the watch list. The quote cycle keeps young and
retryable candidates queued; terminal markers and confirmed receipt reconciliation
remove handled targets through `mrs_bot_reply_state`. A queued quote with no
usable creation time gets a bounded metadata refresh rather than waiting forever.
The discovery-to-cycle restart, candidate-budget and posting hand-offs are tested
in `tests/test_quote_pending_candidates.py`.
Watched-original selection seeds the recent-own-post index through
`TweetLookupCache` directly on every lookup. Fresh watch-file reads keep their
original timing; the public watched-post adapters remain available for callers
and compatibility tests but are not used by reply-cycle discovery.

For a change to saved-draft behaviour, start with `ReplyDrafts`. Its `store`,
`recover` and `receipt_draft_is_valid` methods call its own `validate` method;
internal draft operations do not return through root adapters. Each validation
acquires current evidence. Storage also checks current confirmed reply history,
so a fresh draft cannot repeat a response posted after its target was created.
Recovery distinguishes an absent draft, an obsolete
draft, a terminal validation failure and a reusable reply with zero model calls.
Confirmation uses `clear_target` to retire drafts across reply lanes;
emergency replay checks use `has_target` with the same lane coverage. Both
operations retain other drafts and require no evidence access or durable save.
`retire_ineligible` logs and clears a rejected target's saved draft before the
reply-state coordinator records the terminal evaluation; each lane still owns
candidate bookkeeping and saving.
`handled_reply_target_ids` returns a fresh set of normal and legacy quote
targets for normal/hot-post admission. It keeps durable ledgers separate and is
not confirmation evidence.
The root's `_reply_draft_owner()` binds current dependencies without loading
evidence. `_reply_cycle_persistence()` creates one owner per cycle invocation
and supplies its bound `recover`, `store`, `clear` and `retire_ineligible` methods alongside the
existing durable-save callback. Root draft functions remain compatibility entry
points. Draft behaviour tests live in `tests/test_bot_reply_drafts.py`; cycle
tests retain budget, save-order and terminal-retirement checks.

For a change to which previous replies influence a candidate, start with
`ReplyHistory`. Its `for_evaluation` operation selects recent replies and prior
same-author interactions using the target's timestamp; `recovery_replies` uses
current confirmed history to validate fresh draft storage and pending-draft
recovery. Both share the owner's
filtering and exclusion rules. `record_confirmation` builds a confirmed record,
removes duplicates and applies retention limits. Reconciliation invokes it after
caching the reply and before confirmation telemetry, and continues to control
durable saves and receipt retirement. The root binds current clocks and limits
through `_reply_history_owner()`; root history helpers remain compatibility
entry points. Direct behaviour tests live in `tests/test_bot_reply_history.py`.

## Digest input, analysis and reporting

Start with `main()` in [mrs_log_digest.py](../mrs_log_digest.py) for arguments,
output paths and locking, then follow its call to `run_digest()`. That function
discovers logs, selects the resume/time window, reads current local snapshots,
calls `analyse()`, overlays current-state reporting, delivers the report, then
saves the resume cursor when enabled and records were selected. Output must
succeed before the cursor advances.

| Question | Implementation to read next |
|---|---|
| How are records, retained input coverage and resume boundaries selected? | [mrs_log_digest_records.py](../mrs_log_digest_records.py) |
| How are files read consistently and JSON parsed strictly? | [mrs_log_digest_input_io.py](../mrs_log_digest_input_io.py) |
| Where do event routing, shared observations and final analysis assembly live? | `analyse()` in [mrs_log_digest.py](../mrs_log_digest.py) |
| How are current state, config and operator controls observed? | [mrs_log_digest_runtime.py](../mrs_log_digest_runtime.py) |
| How are current-state summaries and derived headlines prepared? | [mrs_log_digest_state_reporting.py](../mrs_log_digest_state_reporting.py) |
| How are reply decisions and exact published text reported? | [mrs_log_digest_single_call.py](../mrs_log_digest_single_call.py), [mrs_log_digest_reply_evidence.py](../mrs_log_digest_reply_evidence.py), [mrs_log_digest_reply_text.py](../mrs_log_digest_reply_text.py) |
| How are request/receipt evidence and current barriers reconciled into health? | [mrs_log_digest_transactions.py](../mrs_log_digest_transactions.py), [mrs_log_digest_incidents.py](../mrs_log_digest_incidents.py), [mrs_log_digest_remote_write.py](../mrs_log_digest_remote_write.py) |
| Where are Markdown, JSON and output delivery handled? | [mrs_log_digest_markdown.py](../mrs_log_digest_markdown.py) renders Markdown; `run_digest()` builds JSON and `deliver_report()` writes output in [mrs_log_digest.py](../mrs_log_digest.py) |
| Where are saved context and cursor persistence handled? | [mrs_log_digest_context.py](../mrs_log_digest_context.py) |

`analyse()` maintains separate production and self-test source contexts; resumed
pending observations belong to production. Event-family modules consume supplied
observations and callbacks. Current snapshots are separately labelled evidence,
not a reconstruction of state at an old log timestamp. `--max-text` limits prose
previews after analysis; identifiers and exact reply evidence retain their own bounds.
Receipt lifecycle summaries are prepared in `analyse()` and shared by JSON and
Markdown. Current runtime overlays precede saved-context application and its final
derived refresh.

## Boundaries and older material

Bot controls live in [mrs_bot_runtime_control.py](../mrs_bot_runtime_control.py);
the digest uses the same pure validation rules from
[runtime_control_contract.py](../runtime_control_contract.py). Controls, instance
locking, receipt recovery and [remote-write barriers](../mrs_bot_remote_write_barriers.py)
govern posting. Keep durable saves and receipt retirement in their owning flows.

The digest observes production evidence without posting or repairing bot state.
Its writes serve report outputs, its own resume state and their locks. Follow the
[README](../README.md) for testing and deployment commands.

[bot_modularisation.md](bot_modularisation.md) and
[modularisation.md](modularisation.md) record past extraction stages. Their old
module counts, import behaviour and retired-feature descriptions are historical;
use this guide, the API inventory and current code for today's architecture.
