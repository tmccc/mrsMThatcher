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
| Candidate discovery and durable mention queue | [mrs_bot_mention_discovery.py](../mrs_bot_mention_discovery.py), [mrs_bot_hot_post_discovery.py](../mrs_bot_hot_post_discovery.py), [mrs_bot_quote_discovery.py](../mrs_bot_quote_discovery.py) |
| Verified conversation context | [mrs_bot_reply_context.py](../mrs_bot_reply_context.py) |
| Provider integration and local decision/validation | [mrs_bot_reply_generation.py](../mrs_bot_reply_generation.py), then [single_call_reply.py](../single_call_reply.py) |
| Pending drafts and retirement | [mrs_bot_reply_state.py](../mrs_bot_reply_state.py) |
| Save draft, prepare receipt, send and commit confirmation | [mrs_bot_reply_preparation.py](../mrs_bot_reply_preparation.py), [mrs_bot_reply_delivery.py](../mrs_bot_reply_delivery.py), [mrs_bot_reply_reconciliation.py](../mrs_bot_reply_reconciliation.py) |

[mrs_bot_reply_cycle_interfaces.py](../mrs_bot_reply_cycle_interfaces.py) describes
the settings and callback groups supplied to both reply lanes. In the normal
cycle, `evaluation_record_pruning_pending` tracks deferred in-memory pruning;
`quarantine_retirements_pending` tracks bookkeeping still needing a durable save.
These are different obligations even when they arise from the same candidate.

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
