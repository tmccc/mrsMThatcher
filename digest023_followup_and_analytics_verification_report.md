# Digest023 follow-up and analytics verification

Generated: 18 July 2026 (Europe/London)

## 1. Executive summary

All three actionable findings were resolved without deploying or restarting the
production bot.

- The irrelevant Burnham reply was caused by lexical retrieval matching an
  incidental `Berlin Wall` list fragment and by validation proving grounding but
  not topical relevance. Production-path validation now requires both.
- The generated-image contradiction was an equal-score tie between two original
  images, not a generated-identity policy change. Future telemetry and the digest
  now distinguish policy-caused changes from policy-neutral baseline differences.
- The engagement correction already existed and was working. It correctly maps
  the stale line-derived identity to the posting-time canonical identity. One
  narrow evidence-mismatch path was hardened so changed registered evidence fails
  closed instead of falling through to generic stale-line handling.
- The full repository suite passed with 1,770 tests and one expected skip. The
  final required focused groups passed 150 tests after the last narrow changes.

Recommendation: the source changes are ready for a separate tested deployment.
The analytics correction is already used by the timer because the oneshot invokes
the project script directly; no main-bot restart was required for its verification.

## 2. Files inspected

The investigation included:

- `mrsMThatcher2.py`, `reply_strategy.py`, `mrs_log_digest.py` and
  `mrs_engagement_analytics.py`;
- the four required focused test modules plus reply, digest, receipt, selector,
  simulator and production-isolation tests in the full suite;
- `mrsMThatcher.log` and rotated logs, especially `mrsMThatcher.log.5`;
- `mrsMThatcher.txt`, the pre-clean-up quotation snapshot, `quote_analysis.json`,
  completed research packets and `historical_context_reply_history.json`;
- `engagement_analytics/quote_identity_corrections.json`, the SQLite database,
  collector state/log, JSON/CSV exports and prior repair report;
- both analytics user units and the live status/journal of all three user units.

No secret value from `mrsMThatcher.env` was displayed or copied.

## 3. Irrelevant researched-principle reply

### Root cause

The incoming Burnham contribution was a multi-point post dominated by an
unsupported actor-specific allegation and broad claims about economic performance.
It also contained the short list fragment `Berlin Wall Down`.

The old path had two independent weaknesses:

1. `ask_grok_for_reply()` passed the complete rendered thread context to lexical
   retrieval rather than making the incoming contribution the primary query.
2. `validate_reply_decision()` checked packet eligibility, research confidence,
   factual grounding and quotation accuracy, but it did not require topical
   alignment among the incoming contribution, selected packet and final reply.

The wall packet was therefore authentic and high-confidence, but irrelevant to
the substantive contribution. Grounding confidence alone allowed it to pass.

### Relevance invariant now enforced

For `principle_reply` and `researched_principle`, the local validator now:

1. uses the actual incoming contribution, not inherited parent text, as the
   primary lexical retrieval query;
2. removes handles and URLs and divides a multi-point contribution into topical
   segments;
3. prevents a short label or list fragment from becoming the sole relevance
   basis when longer substantive segments exist;
4. requires a `principle_reply` to share a concrete topical term or a narrowly
   defined political concept with a substantive incoming segment;
5. requires a `researched_principle` to bridge the same substantive segment,
   selected packet semantic fields and final reply through either sufficient
   concrete lexical evidence or one shared defined concept;
6. rejects the decision locally when the bridge is absent. It does not make a
   second model call and cannot post the rejected draft.

This is deliberately conservative: an authentic but unrelated principle is
worse than `no_reply`. It is not a ban on Andy Burnham or the wall sentence.

### Preserved behaviours and tests

Tests use the exact production Burnham contribution and wall packet and prove
both direct validation and the real `ask_grok_for_reply()` call path reject the
bad result. They also prove:

- a researched free-market/accountability principle with a real topical bridge
  remains valid;
- grounding confidence by itself cannot establish relevance;
- `Leaders serve best when conviction, not popularity, guides their course.`
  remains valid for a contribution about popularity;
- unsupported actor-specific allegations cannot be repeated in
  `principle_reply`;
- the misspelt Berlin Wall question still requires and accepts a direct
  East-to-West first sentence;
- factual grounding, clarification, terminal-thread, author-cap, global budget,
  receipt and duplicate protections remain covered by the passing suite.

The model prompt now states the same invariant, but local validation remains
authoritative.

## 4. Generated-image digest reporting

### Root cause of the contradiction

The structured event at `mrsMThatcher.log:15387` compared:

- deterministic diagnostic baseline: `t34.jpg`, score `31.8`;
- actual production winner: `t45.jpg`, score `31.8`;
- both images: original;
- all generated-identity penalties/exclusions: zero;
- production action: `original_unchanged`.

The producer set `winner_changed_by_policy=true` whenever filenames differed,
even though the actual production selector had resolved an equal-score tie and
the identity policy had done nothing. The summary counted changes only among
policy-relevant events, while the Markdown table displayed every raw true flag.
Those differing predicates caused the contradiction.

### Old and new definitions

| Metric/category | Old definition | New definition |
|---|---|---|
| `winner_changed_by_policy` | Baseline basename differs from production basename | At least one cross-quote generated candidate was penalised/excluded **and** the winner differs |
| `baseline_winner_differs` | Not recorded | Baseline and production basenames differ, regardless of cause |
| `identity_policy_winner_changes` | Not recorded | Count of events satisfying the corrected policy-causal predicate |
| `winner_changes` | Summary-only policy-causal count, despite broader raw rows | Compatibility alias of `identity_policy_winner_changes` |
| policy-neutral baseline difference | Not represented separately | Filename difference without policy relevance |
| equal-score tie resolution | Misrepresented as a policy change | Policy-neutral difference with equal baseline/production scores |
| `active_images_used_ever` | Images seen in bounded structured logs | Retained compatibility alias; no longer shown in Markdown |
| `active_images_used_in_observed_logs` | Not recorded | Images seen in the explicitly reported structured-log interval |
| `active_images_never_used` | Images not seen in bounded logs | Retained compatibility alias |
| `active_images_not_seen_in_observed_logs` | Not recorded | Precise bounded-log complement |
| `active_pool_ever_used_percentage` | Bounded-log percentage | Retained compatibility alias |
| `active_pool_observed_usage_percentage` | Not recorded | Precise bounded-log percentage |

The regenerated check digest reports zero policy-relevant selections, zero
policy-caused winner changes and one policy-neutral equal-score tie. The row is
under `Policy-neutral baseline differences`, and `original_unchanged` no longer
appears under a changed-policy heading.

The utilisation section reports the exact observed structured-log interval and
keeps current-cycle history separate. Existing JSON consumers retain the old
fields temporarily as documented aliases with unchanged values. Pool-health and
runway calculations were not changed. The live configuration remained
`generated_pool_enabled=false`, `generated_pool_allowed=true`.

## 5. Engagement identity evidence

Classification: `incorrect_derived_analytics_record` caused by stale line-number
interpretation after the 13-record quotation clean-up. The two texts differ
substantively; this is not formatting variation, text variation or a hash collision.

| Evidence | Stable locator / time | Stored or observed identity | Authority and recomputation |
|---|---|---|---|
| Posting selection | `mrsMThatcher.log.5:25991-26006`, 14 July 21:01:45 BST | `Capitalism is the moral way of running an economy.`; `e28d24...` | Posting-time exact text hashes to `e28d24...` |
| X success response | `mrsMThatcher.log.5:26043-26052`, 14 July 21:01:46 BST | post `2077121396186992800`, exact public text | Transport confirmation of `e28d24...` |
| Context event | `mrsMThatcher.log.5:26225`, 14 July 21:01:47 BST | parent post plus explicit `e28d24...` | Structured explicit identity |
| Main-post event | `mrsMThatcher.log.5:26227` | post plus zero-based `line_no=599`, no quote ID/text | Position metadata only |
| Durable context history | `historical_context_reply_history.json`, key `2077121396186992800` | explicit `e28d24...`, context post `2077121398795907462` | Highest-precedence durable link |
| Historical source | `quote_attribution_cleanup_001/mrsMThatcher_before.txt:600` | canonical text at former zero-based line 599 | Hashes to `e28d24...` |
| Current source | `mrsMThatcher.txt:600` | `It is free enterprise, which creates wealth, not meddling governments.` | Hashes to distinct `a8de2c...` |
| Canonical packet | completed packet `e28d24...` | exact, high-confidence Fraser Institute quotation | Current canonical identity |
| Correction manifest | `stale-main-post-line-2077121396186992800-v1` | exact post, line, both texts and IDs | Narrow evidence-backed correction |
| SQLite pair | `post_pairs.pair_id=4` | one row under `e28d24...` | Durable analytics identity |
| Exports | `post_pairs.csv:68` and JSON target object | one canonical pair under `e28d24...` | Regenerated from verified database |

Full canonical ID:
`e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b`

Incorrect current-line-derived ID:
`a8de2cdcaa20182b2129e0292e4c98956c770132336c12c7130963d3796876e6`

### Repair status and hardening

The live repair already existed and was active. It validates schema, exact text
hashes, post/source/line scope, eligible canonical packet, classification,
reason and evidence. It cannot cross post IDs or reintroduce an attribution-
excluded quotation. `_merge_identity()` still raises for unregistered explicit
identity conflicts.

One necessary change was made: once a correction is registered for an exact
post/source/line/canonical scope, changed observed text or ID now raises
`IdentityConflict` explicitly. It cannot silently fall through to generic
stale-line handling. A regression test alters the registered current text and
proves fail-closed behaviour.

## 6. Analytics backups and integrity

The timer was stopped before the verification snapshot. Backups are in the
ignored runtime directory:

`engagement_analytics/backups/digest023_verification_20260718T121600Z/`

| File | SHA-256 before controlled run |
|---|---|
| `engagement_analytics.sqlite3` | `fe25d16f916d18dc1ad01e8a2d735290c5abe4c5c9ac4534eb640ba3096de800` |
| `collector_state.json` | `aeec1427b41cc3c36d15abb6857d6dfb1ec7888e3d915b9abf80bb438bc6e0cf` |
| `collector.log` | `6a511efd156c8a7ed0c6e6e5b94e9db4bc060b425c9b0b54a66185a38dbf23a8` |
| `post_pairs.csv` | `78780234f161a783b9f6be321ce2d3426023a77355e17d34bf5f8ab374b82fee` |
| `metric_snapshots.csv` | `82f42db65db06c265ac920e9c758bd859a11c82583b572e5b55d265b6973b1e9` |
| `engagement_export.json` | `4db309bf9ecd0cf9e8719c0d507da12559b8992ea4d50815abaa8909df318a7b` |
| `quote_identity_corrections.json` | `5d8ae2c819278ab1b843494debca2622a143981cfc1cbe036713d60e27c5b00d` |

Before and after the manual service run:

- post pairs: 104;
- target canonical pairs: 1;
- metric snapshots: 543;
- target main/context snapshots: 12;
- duplicate main-post identities: 0;
- duplicate `(post_id, target_age_seconds, revision_number)` keys: 0;
- correction-manifest hash: unchanged;
- collector-state hash: unchanged.

The controlled run had nothing due and made zero X read requests. `sqldiff`
showed that its only logical database changes were the expected
`last_discovered_at` refreshes for the 104 unchanged pairs. No snapshot or
identity row was added, removed or duplicated. Regenerated exports contain the
target exactly once under `e28d24...`; the obsolete `a8de2c...` identity does not
appear for that post.

## 7. Commands and validation

Principal commands included:

```text
systemctl --user cat/status/show mrs-engagement-analytics.service
systemctl --user cat/status/show mrs-engagement-analytics.timer
systemctl --user status/show mrsMThatcher.service
systemctl --user stop/start mrs-engagement-analytics.timer
python3 mrs_engagement_analytics.py discover ... --dry-run
systemctl --user start mrs-engagement-analytics.service
python3 mrs_engagement_analytics.py export ... --format json,csv,markdown
sqlite3 ...
sqldiff backup.sqlite3 live.sqlite3
python3 mrs_log_digest.py ... --no-state
python3 -m py_compile reply_strategy.py mrsMThatcher2.py mrs_log_digest.py mrs_engagement_analytics.py
python3 -m pytest -q
git diff --check
```

Results:

- required final focused groups: **150 passed** in 6.79 seconds;
- full repository suite: **1,770 passed, 1 skipped** in 710.67 seconds;
- expected warnings: one Starlette/httpx deprecation and two BeautifulSoup/lxml
  deprecations;
- `py_compile`: passed;
- `git diff --check`: passed;
- no test wrote live posting state or contacted X.

## 8. Service verification

Before verification:

- analytics timer: enabled and active/waiting;
- analytics oneshot: last exit `0/SUCCESS`;
- main service: active/running, MainPID 1805, child PID 1808, start time
  18 July 12:16:59 BST, `NRestarts=0`;
- failed user services: none.

Controlled manual analytics run at 13:17 BST:

- exit: `0/SUCCESS`;
- discovery: 104 unchanged, 0 inserted, 0 updated;
- collection: `nothing_due`, zero requests;
- no `IdentityConflict`;
- target pair/snapshot integrity unchanged.

The analytics timer was restored at 13:17:48 BST and remained enabled. The first
subsequent scheduled-run result is recorded below after the 13:30 trigger.

First scheduled timer run after restoration, at 13:30:30 BST:

- oneshot exit: `0/SUCCESS` at 13:30:32 BST;
- audited correction: applied to the exact registered post and stale derived ID;
- discovery: 104 unchanged, 0 inserted, 0 updated;
- collection: `nothing_due`, zero requests;
- `IdentityConflict`: none;
- target canonical pairs: 1;
- target main/context snapshots: 12;
- total snapshots: 543;
- duplicate main identities and snapshot keys: 0;
- timer: enabled and active/waiting, next trigger 13:45:25 BST;
- failed user services: none.

The main service retained MainPID 1805, child 1808, its original start timestamp
and `NRestarts=0`. Its journal contains no stop, restart, failure or reload caused
by this work.

## 9. Modified files

Files changed by the digest023 work:

- `reply_strategy.py`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `mrs_engagement_analytics.py`
- `tests/test_reply_strategy.py`
- `tests/test_generated_image_utilisation_digest.py`
- `tests/test_generated_identity_policy_production_scoring.py`
- `tests/test_engagement_analytics.py`
- `digest023_followup_and_analytics_verification_report.md`

The working tree also contains prior uncommitted analytics/systemd work that was
preserved: `.gitignore`, `engagement_analytics/README.md`,
`engagement_analytics/quote_identity_corrections.json`,
`engagement_identity_conflict_report.md` and `systemd_user_service_report.md`.

## 10. Remaining risks

- The topical concept vocabulary is intentionally compact and conservative. It
  may reject a novel but legitimate paraphrase; that is the desired fail-closed
  direction. Validator-rejection telemetry should be reviewed after deployment.
- Historical raw identity-policy events remain immutable. The digest interprets
  legacy events consistently, while deployment is needed for future events to
  emit `baseline_winner_differs` and the corrected causal boolean.
- Deprecated utilisation aliases remain in machine-readable output for
  compatibility. Consumers should migrate to the observed-log names.
- Thirty-three other recent main-post events retain stale line positions but
  have conflict-checked explicit identities. They remain summarised as
  non-authoritative line metadata; the exact registered target is now stricter.

## 11. Operating restrictions confirmed

- No X post was made by this task. The separately running production bot was not
  paused and continued its ordinary operation independently.
- The authorised analytics verification used the existing read-only X collector;
  the controlled manual run made zero requests because nothing was due.
- `mrsMThatcher.service` was not stopped, restarted, reloaded or signalled.
- No production state, receipt, posting history or production log was modified by
  the task.
- Generated images were not activated.
- Hybrid retrieval was not activated or changed.
- Semantic-veto enforcement was not activated or changed.
- No historical production log or engagement snapshot was rewritten or deleted.
- No commit, push, deployment or service reload was performed.
