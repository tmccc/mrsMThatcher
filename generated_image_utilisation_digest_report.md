# Generated Image Utilisation Digest Report

## 1. Implementation summary

The standalone log digest now includes `## Generated image utilisation`. It reports successful generated-image use for the current active pool, bounded never-observed and longest-unused lists, frequent-image ranking, current-cycle agreement, and a top-10 concentration measure. Production selection and posting code were not changed.

The implementation reuses the existing bounded historical log scan. That scan now returns its already deduplicated successful regular-post records, so utilisation does not perform another full log pass.

## 2. Files changed

- `mrs_log_digest.py`
- `tests/test_generated_image_utilisation_digest.py`
- `generated_image_utilisation_digest_report.md`

No unrelated local changes were modified or discarded.

## 3. New metrics

- `active_generated_images`
- `active_images_used_ever`
- `active_images_never_used`
- `active_pool_ever_used_percentage`
- `active_images_used_in_current_cycle`
- `active_images_unused_in_current_cycle`
- `total_successful_generated_posts_observed`
- `median_successful_posts_per_used_image`
- `maximum_successful_posts_for_one_image`
- `top_10_share_of_successful_generated_posts`

The section also includes bounded tables for the ten most frequently used active images, active images never observed successfully posted, and active images unused longest.

## 4. Data sources

- Active pool basenames: `generated_review_approved_images/`
- Active/quarantined status and current-cycle usage: existing `generated_pool_health_snapshot()` data, including read-only `images_used.json`
- Origin quote hash: existing generated basename format `tg_<origin-quote-hash>.png`
- Successful posting history: structured `EVENT` records where `event=main_post_posted` and `lane=quote_image` in the supplied current/rotated production logs

Candidate, shadow, skip, recovery, failed-post, meme, reply, and receipt-only records are not counted. Post IDs and existing rotated-record keys provide duplicate suppression. This uses the definitive reconciled success event emitted by the existing transactional posting path.

## 5. Definition of “ever used”

In this section, “ever used” means that an active generated basename has at least one definitive successful regular-post event in the bounded production logs available to the digest. It does **not** mean provable account-lifetime use. This limitation is printed immediately above the metrics and tables.

Current-cycle metrics retain the project’s separate established meaning: membership in the current `images_used.json` image cycle. The utilisation values are sourced directly from pool health rather than recalculated.

## 6. Bounded historical coverage

The real read-only run covered `2026-07-07 09:03:47` through `2026-07-11 03:42:40` (approximately 3.8 days). Within that evidence:

- Active generated images: **79**
- Active images observed successfully used: **7**
- Active images not observed successfully used: **72**
- Observed active-pool exercise: **8.9%**
- Successful active generated posts observed: **7**
- Median successes per observed-used image: **1.0**
- Maximum successes for one image: **1**
- Top-10 share: **100.0%**
- Current-cycle active used/unused: **7 / 72**, agreeing with pool health

The top-10 share is 100% because only seven active generated successes exist in the available history and each is a different image. It is not evidence of concentration among a mature history.

## 7. Tests executed and results

Focused pool utilisation, health, and runway tests:

```text
24 passed
```

Existing digest integration smoke tests:

```text
35 passed, 134 deselected in 29.62s
```

Compilation:

```text
python3 -m py_compile mrs_log_digest.py tests/test_generated_image_utilisation_digest.py
PASS
```

Focused coverage includes no images, no usage, one use, repeated use, deterministic ranking ties, active/quarantined separation, never-used ordering, current-cycle agreement, duplicate rotated records, bounded history, missing optional metadata, and compact rendering.

The real digest was run with `--no-state`; it completed in 4.17 seconds. The production log size and mtime were unchanged across that invocation. No production process was started, stopped, restarted, or signalled.

## 8. Git diff --stat

Task-specific working-tree changes:

```text
mrs_log_digest.py                                  | 114 lines changed
tests/test_generated_image_utilisation_digest.py  | 126 lines added
generated_image_utilisation_digest_report.md      | this untracked report
```

The repository-wide `git diff --stat` also shows the pre-existing generated metadata changes and four active-path PNG deletions from the real quarantine transaction. They are unrelated to this implementation and were not altered.

## 9. Git status --short

Task files:

```text
 M mrs_log_digest.py
?? tests/test_generated_image_utilisation_digest.py
?? generated_image_utilisation_digest_report.md
```

The full status additionally contains pre-existing modifications to `generated_image_analysis.json` and `generated_image_identity_dependence_audit.json`, four deleted generated PNG paths, and unrelated untracked development/runtime artifacts. The new digest, focused test, and this report remain unstaged and uncommitted as requested; all pre-existing items remain untouched.

## Safety confirmation

- No production posting behaviour changed.
- No production image, metadata, state, configuration, receipt, or log was edited.
- No quarantine or restore action occurred.
- No X, xAI, or external API call occurred.
- The production bot was not restarted or signalled.
- Nothing was staged, committed, or pushed.
