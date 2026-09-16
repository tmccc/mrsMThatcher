# MrsMThatcher log digest

Observed event window: `2026-07-25 09:00:00` → `2026-07-25 12:00:00`
Project directory: `/fixture/project`
Records parsed: `12`

## Headline
Fixed presentation fixture

## Latest state (stale carried-forward snapshot)
State timestamp: `2026-07-24 09:00:00` (carried forward from previous digest state; stale snapshot age at window end: 1 day 3 hours)
Historical snapshot values only; the counters and schedules below are not current.

```text
snapshot_daily_reply_count       = 7  date=2026-07-24
snapshot_daily_quote_reply_count = 3  date=2026-07-24
snapshot_next_reply_lane_priority = quote-tweet
snapshot_quote_spam_author_count = None
snapshot_x_read_api_cooldown_until = None  none  unavailable
snapshot_x_write_api_cooldown_until = None  none  unavailable
snapshot_openai_api_cooldown_until = None  none  unavailable
snapshot_quote_api_cooldown_until = None  none  unavailable
snapshot_last_main_post_id       = None
snapshot_last_seen_mention_id    = None
snapshot_mention_backlog_active   = false
snapshot_mention_pending_candidates = None
snapshot_active_author_evaluation_quarantines = None
snapshot_next_quote_post         = 2026-07-25 13:00:00  epoch=1784984400
snapshot_next_meme_post          = 2026-07-25 14:00:00  epoch=1784988000
snapshot_next_meme_mode          = fallback  date=2026-07-25
snapshot_posted_meme_count       = None
```

## Mention backlog and author evaluation quarantine
Active mention backlog: no; active author evaluation quarantines: 0.
Active quarantined author IDs: none.
Observed events: starts=0, progress=0, completions=0, resets=0, quarantine_starts=0, quarantine_skips=0.
Pipeline evaluations skipped by active author quarantine (explicit event counts only): 0.

## Daily meme schedule
```text
source                  = state carried forward, state is a stale snapshot (1 day 3 hours old at window end)
enabled                 = True
snapshot_next_meme       = 2026-07-25 14:00:00  epoch=1784988000
snapshot_mode            = fallback  date=2026-07-25
snapshot_anchor          = none recorded in snapshot; snapshot fallback mode retained for diagnosis
```

## Reply budget
```text
source             = state carried forward, state counters are stale snapshot values
snapshot auto replies used  = 7 / 12  snapshot_remaining=5
snapshot quote replies used = 3 / 6  snapshot_remaining=3
```

## Reply lane priority
```text
source                         = state carried forward, state priority is a stale snapshot value
snapshot_next_priority          = quote-tweet
normal_lane_due_checks         = 0
mention_function_entries       = 0
mention_fetch_attempts         = 0
mention_checks_skipped_spacing = 0
mention_checks_skipped_cooldown = 0
quote_lane_due_checks          = 0
priority_flipped_to_quote      = 0
priority_flipped_to_normal     = 0
forced_normal_before_quote     = 0
normal_first_refusal_no_post   = 0
quote_tweet_status_posted      = 0
quote_tweet_status_checked     = 0
quote_tweet_status_spacing     = 0
quote_tweet_status_cap         = 0
quote_tweet_status_cooldown    = 0
quote_tweet_checks_no_post     = 0
```

## OpenAI published-cost cache
Cache scope: **project proj_digest_fixture**.
Cache updated: **2026-08-18 12:30:00 UTC** (age: 0 seconds).
Current UTC-day provider-published total: **US$2** for `2026-08-18`.
Status: **provisional**.
OpenAI selected-window estimate: **US$0.75**.
Method: cumulative provider-published cost delta.
Requested window: **08:05–10:25 UTC**.
Sample coverage: **2026-08-18: 08:00–10:30 UTC**.
The estimate can include a small amount immediately outside the requested log window because samples are collected at intervals.
The provider-published estimate is not allocated to individual single-call decisions.

## Generated image utilisation
Current-cycle history records whether an image is marked used in the live image cycle. Bounded structured-log observations count successful post records retained in the scanned logs. The two measures answer different questions and are not interchangeable.
The active-image inventory is a current filesystem snapshot. The structured-log coverage shown below is a secondary bounded scan and can extend slightly beyond the selected digest event window.
Deprecated machine-readable usage aliases retain the bounded-log values for compatibility and are planned for removal only in a future major digest schema version.
Observed structured-log coverage: `unavailable` to `unavailable`.

```text
active_generated_images                    = 12
active_images_used_in_observed_logs        = 0
active_images_not_seen_in_observed_logs    = 12
active_pool_observed_usage_percentage      = 0.0%
active_images_used_in_current_cycle        = 0
active_images_unused_in_current_cycle      = 12
total_successful_generated_posts_observed  = 0
median_successful_posts_per_used_image     = unavailable
maximum_successful_posts_for_one_image     = 0
top_10_share_of_successful_generated_posts = unavailable
```
Most frequently used active generated images
| image | successful_posts | last_successful_post |
| --- | --- | --- |
| none observed | 0 | never |

Active generated images never successfully posted in observed logs (the same population is therefore also unused longest) (count: **12**; sample: **5**)
| image | origin_quote_hash |
| --- | --- |
| unused-00.png | quote-00 |
| unused-01.png | quote-01 |
| unused-02.png | quote-02 |
| unused-03.png | quote-03 |
| unused-04.png | quote-04 |
7 additional active images omitted from the readable summary.

### Detailed generated-image filename appendix
This optional appendix contains every filename retained in the digest's already-bounded utilisation result.
Never observed:
- `unused-00.png`
- `unused-01.png`
- `unused-02.png`
- `unused-03.png`
- `unused-04.png`
- `unused-05.png`
- `unused-06.png`
- `unused-07.png`
- `unused-08.png`
- `unused-09.png`
- `unused-10.png`
- `unused-11.png`

## Generated image spacing
The pool is intentionally disabled; spacing eligibility is informational and does not activate generated-image selection.
```text
required_original_posts_between = 3
original_posts_since_generated  = 4
generated_pool_enabled          = False
generated_pool_allowed          = True
```
All **2** spacing observations had the same state; first `2026-07-26 12:00:00`, last `2026-07-27 04:00:00`.

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
**3 candidates evaluated; 1 reply posted; 1 valid editorial no-reply decision; 1 operational failure.**
One-call compliance: **passed** (3 compliant decisions; 0 violations; 0 incomplete; 1 drafts recovered with no provider call).
Logical/physical call diagnostics: **0 repeated model-attempt candidates; 0 excess usage candidates; 0 authorised pre-execution retries; 0 attempt-count mismatches**. Attempt telemetry: **available=4**.
Strategy versions: **single-sol-reply-20260904=3**; models: **gpt-5.6-sol=3**; lanes: **mention=1, quote-tweet=1, hot-post=1**.
Reply kinds: **principle=1**; no-reply reasons: **completed_exchange=1**.
Operational failure reasons: **invalid_model_response=1**; error categories: **schema_validation=1**.
Schema/local-validation failures: **1 / 0**; posting failures: **0**.
Average visible turns / visible characters / same-author interactions / recent conversational replies / trusted facts / supplied images: **4.00 / 640.00 / 2.00 / 6.00 / 3.00 / 0.67**.
Provider usage totals: **input=100, cached-input=64, cache-write-input=0, output=20, reasoning=8, total=120** across 1 successful responses.
Provider latency average/max: **1200.0 / 1200 ms**; published selected-window cost: **unavailable (unavailable_during_log_analysis)**.

## Counts
```json
{
  "stats": {
    "mention_replies": 2
  },
  "routine_skip_counts": {}
}
```

## Quote/image posts
| time | post_id | line_no | quote_hash | image_basename | image_no | image_score | made_with_ai | text |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-25 10:00:00 | 12345 |  |  |  |  |  |  | Quoted \| text\nSecond line — café |

## Historical context replies
| time | status | parent_post_id | quote_id | reply_post_id | public_reply_text | public_reply_text_status | public_reply_text_source | public_reply_text_reason | weighted_character_count | verification_label | source_class | overall_reply_confidence | formatter_version | rendering_mode | shortening_applied | reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-25 10:01:00 | completed | 12345 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

Per-reply semantic-review disposition and ledger/projection hashes were not supplied reliably by these events; empty columns are omitted.

## Transactional receipt lifecycle
Routine two-phase receipt write/remove pairs completed: **1**. The write event can be logged at WARNING while still being a normal durable transaction step; it is not an incident by itself.
Completed main-post receipt lifecycles by lane: regular quote/image **1**; daily-meme **0**.
Reconciled main-post receipt removals whose opening write was outside the selected window: **1**.
Stale or unresolved receipt events:
| time | level | lane | kind | post_id | quote_hash | image/file | message |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-25 11:30:00 |  | daily_meme | main_post_receipt |  |  | fixture.png |  |

Confirmed remote posts with local recovery/persistence trouble:
| time | level | where | message |
| --- | --- | --- | --- |
| 2026-07-25 11:30:01 | WARNING | fixture | Confirmed \| awaiting local persistence |

## Transient provider observations
None observed in the selected window.

## Current independent errors
None unresolved in the selected window.

## Historical/resolved incident errors
None identified in the selected window.

## Other warnings
| time | where | message |
| --- | --- | --- |
| 2026-07-25 10:02:00 |  | Long warning: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx… |

## On-disk local configuration overrides
```text
MAX_AUTO_REPLIES_PER_DAY=12
MAX_QUOTE_REPLIES_PER_DAY=6
ENABLE_DAILY_MEME_POSTS=true
```
