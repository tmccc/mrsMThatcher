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

For a conversational reply, read the lane owner first, then follow the step you
need. A recovered draft can bypass model evaluation.

| Step | Implementation to read next |
|---|---|
| Mention/hot-post orchestration, budgets and quarantine | [mrs_bot_normal_reply_cycle.py](../mrs_bot_normal_reply_cycle.py) |
| Quote-tweet orchestration | [mrs_bot_quote_reply_cycle.py](../mrs_bot_quote_reply_cycle.py) |
| Candidate discovery and durable mention queue (`MentionQueue` owns access and retirement) | [mrs_bot_mention_discovery.py](../mrs_bot_mention_discovery.py), [mrs_bot_hot_post_discovery.py](../mrs_bot_hot_post_discovery.py), [mrs_bot_quote_discovery.py](../mrs_bot_quote_discovery.py) |
| Watched and recent original selection | `QuoteWatchPosts` in [mrs_bot_quote_discovery.py](../mrs_bot_quote_discovery.py) |
| Verified tweet lookup, cache refresh and recent own-post index | `TweetLookupCache` in [mrs_bot_tweet_lookup_cache.py](../mrs_bot_tweet_lookup_cache.py) |
| Verified normal context and two-turn quote context | `ReplyContext.build` and `build_quote` in [mrs_bot_reply_context.py](../mrs_bot_reply_context.py) |
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
| Save draft, prepare receipt, send and commit confirmation | [mrs_bot_reply_preparation.py](../mrs_bot_reply_preparation.py), [mrs_bot_reply_delivery.py](../mrs_bot_reply_delivery.py), [mrs_bot_reply_reconciliation.py](../mrs_bot_reply_reconciliation.py) |

[mrs_bot_reply_cycle_interfaces.py](../mrs_bot_reply_cycle_interfaces.py) describes
the settings and callback groups supplied to both reply lanes. Its
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

Receipt loading and delivery bind one current `ReplyReceiptValues` owner for
validation and in-memory projections. `prepare_sending_template` checks current
send authority before receipt I/O and preserves reviewed nested objects in a
fresh outer mapping. Current and frozen legacy source promotions use the same
transaction operation with their separate validators; journal binding, exact
source bytes and replacement authority remain in delivery. Root compatibility
entry points and receipt-removal commit-proof checks are unchanged.
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
Lookup tests patch `fetch` on the owner; `restore_tweet_lookup_fetch` restores its
real transport operation when an isolated test server supplies the response.

For a change to saved-draft behaviour, start with `ReplyDrafts`. Its `store`,
`recover` and `receipt_draft_is_valid` methods call its own `validate` method;
internal draft operations do not return through root adapters. Each validation
acquires current evidence. Recovery distinguishes an absent draft, an obsolete
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
current confirmed history to revalidate a pending draft. Both share the owner's
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
