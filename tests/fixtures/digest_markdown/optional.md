# MrsMThatcher log digest

Observed event window: `2026-07-25 09:00:00` → `2026-07-25 12:00:00`
Project directory: `unavailable`
Records parsed: `12`

## Headline
Fixed presentation fixture

## Latest state (carried-forward snapshot; age unavailable)
State timestamp: `unavailable` (carried forward from previous digest state; age and staleness unavailable)
Snapshot values only; without a state timestamp their currentness cannot be established.

```text
snapshot_daily_reply_count       = None  date=None
snapshot_daily_quote_reply_count = None  date=None
snapshot_quote_spam_author_count = None
snapshot_x_read_api_cooldown_until = None  none
snapshot_x_write_api_cooldown_until = None  none
snapshot_openai_api_cooldown_until = None  none
snapshot_quote_api_cooldown_until = None  none
snapshot_last_main_post_id       = None
snapshot_last_seen_mention_id    = None
snapshot_mention_backlog_active   = false
snapshot_mention_pending_candidates = None
snapshot_active_author_evaluation_quarantines = None
snapshot_next_quote_post         = None  epoch=None
snapshot_next_meme_post          = None  epoch=None
snapshot_posted_meme_count       = None
```

### Current engagement-question trial state
Compact state snapshot; it is separate from engagement activity observed inside the requested event window.
| field | current value |
| --- | --- |
| experiment_id | unknown (not present in latest snapshot) |
| active_plan_sha256 | unknown (not present in latest snapshot) |
| status | unknown (not present in latest snapshot) |
| current_pair_index | unknown (not present in latest snapshot) |
| active_pair_id | unknown (not present in latest snapshot) |
| next_pair_member_position | unknown (not present in latest snapshot) |
| completed_pair_count | unknown (not present in latest snapshot) |
| confirmed_publication_count | unknown (not present in latest snapshot) |
| treatment_publication_count | unknown (not present in latest snapshot) |
| last_experimental_publication_local_date | unknown (not present in latest snapshot) |
| current_deferral_reason | none |

## Mention backlog and author evaluation quarantine
Active mention backlog: no; active author evaluation quarantines: 0.
Active quarantined author IDs: none.
Observed events: starts=0, progress=0, completions=0, resets=0, quarantine_starts=0, quarantine_skips=0.
Pipeline evaluations skipped by active author quarantine (explicit event counts only): 0.

## OpenAI published-cost cache
OpenAI published cost: unknown
Cache status: **unavailable** (fixture unavailable).
The provider-published estimate is not allocated to individual single-call decisions.

## Historical context reply quality
Operational status: **0 current independent incidents**; **0 resolved legacy receipt/source-role incidents** in the selected log window.
Attempted: **0**; completed: **0**; already completed: **0**; failed: **0**; skipped: **0**; dry run: **0**.
Lengths (raw average / X-weighted average / weighted range): **unavailable / unavailable / unavailable–unavailable**.
Length metadata (raw observed/unavailable; weighted observed/unavailable): **0/0; 0/0**.
Shortened: **0** (metadata unavailable: 0); meaning omitted: **0** (metadata unavailable: 0); source omitted: **0** (metadata unavailable: 0); verification omitted: **0** (metadata unavailable: 0).
Verification labels: none observed
Source classes: none observed
Overall reply confidence: none observed
Formatter versions: none observed
Rendering modes: none observed
Source-role audit versions: none observed
Context rendering: concrete event/date included=0, date-only qualified included=0, omitted because no useful event/date was admitted=0, old generic fallback sentence used=0.

## Current historical-context corpus
Snapshot incomplete: **authoritative files unavailable**.
Completed packets / attribution eligible / attribution ineligible: **unavailable / unavailable / unavailable**.
Ordinary-post cycle / unresolved / historical-context blocked / allowed: **unavailable / unavailable / unavailable / unavailable**.
Source-role policy: **unavailable**; semantic-gate policy: **unavailable**.
Current ledger / gate projection: `unavailable` / `unavailable`.

## Historical context engagement
Unavailable: **analytics database not initialised**.

## Shadow feature lifecycle
Unavailable: **invalid lifecycle register**.

## Single-call conversational replies
**0 candidates evaluated; 0 replies posted; 0 valid editorial no-reply decisions; 0 operational failures.**
One-call compliance: **no_candidates** (0 compliant decisions; 0 violations; 0 incomplete; 0 drafts recovered with no provider call).
Logical/physical call diagnostics: **0 repeated model-attempt candidates; 0 excess usage candidates; 0 authorised pre-execution retries; 0 attempt-count mismatches**. Attempt telemetry: **none observed**.
Strategy versions: **none observed**; models: **none observed**; lanes: **none observed**.
Reply kinds: **none observed**; no-reply reasons: **none observed**.
Operational failure reasons: **none observed**; error categories: **none observed**.
Schema/local-validation failures: **0 / 0**; posting failures: **0**.
Average visible turns / visible characters / same-author interactions / recent conversational replies / trusted facts / supplied images: **unavailable / unavailable / unavailable / unavailable / unavailable / unavailable**.
Provider usage totals: **input=0, cached-input=0, cache-write-input=0, output=0, reasoning=0, total=0** across 0 successful responses.
Provider latency average/max: **unavailable / unavailable ms**; published selected-window cost: **unavailable (unavailable)**.

## Counts
```json
{
  "stats": {
    "mention_replies": 2
  },
  "routine_skip_counts": {}
}
```

## Transient provider observations
None observed in the selected window.

## Current independent errors
None unresolved in the selected window.

## Historical/resolved incident errors
None identified in the selected window.

## Other warnings
None found in selected window.

## On-disk local configuration overrides
On-disk local overrides: **unavailable** (`not read`; source `unavailable`).
Effective live configuration is not established; no digest resume snapshot or historical startup log is presented as current configuration.
