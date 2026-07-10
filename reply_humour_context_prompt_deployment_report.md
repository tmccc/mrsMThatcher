# Reply Humour Context Prompt Deployment Report

## 1. Executive result

**Deployment successful; first live reply-quality observation pending.**

The reviewed shared prompt change was revalidated, committed, pushed to `origin/master`, and loaded by one wrapper-managed replacement Python child. Startup and a subsequent main-loop tick were healthy. No genuine reply opportunity occurred during the bounded monitoring period because reply spacing remained active, so live reply quality and live xAI call-count verification are explicitly pending.

## 2. Pre-deployment state

- Capture timestamp: `2026-07-10T11:18:20+01:00`
- Branch: `master`
- Commit before deployment: `8e850f7 Add generated identity-policy shadow scoring`
- Upstream state: `master...origin/master` (synchronized)
- Wrapper PID: `3631076`
- Python child PID: `2111623`
- Instance lock: `pid=2111623`
- Last main post ID: `2075520733984223417`
- Last regular post epoch: `1783677679`
- Next regular post epoch: `1783685456` (`2026-07-10 13:10:56 BST`)
- Daily reply count: `3`
- Daily quote-reply count: `0`
- Next reply lane priority: `quote`
- Last seen mention ID: `2075519319677538463`
- Generated spacing count: `2`
- Last regular image: `t45.jpg`
- Read cooldown: none
- Write cooldown epoch: `0`
- xAI cooldown epoch: `0`
- Quote-read cooldown epoch: `0`
- Original editorial shadow: enabled
- Generated identity-policy shadow: enabled
- Production receipts: none

The working tree also contained many pre-existing unrelated untracked analysis files, corpora, locks, reports, reviews, and backups. They were preserved and not staged.

## 3. Reviewed-change verification

The current source and diff were inspected directly before deployment.

- The production change was ten string-literal lines inside the existing shared `ask_grok_for_reply()` user prompt.
- It asks the model to distinguish serious disagreement, a genuine question, praise/agreement, abuse/spam, and a joke/tease/pun/sarcasm/light-hearted remark.
- It says to use the whole supplied context and not interpret everything as humour.
- It says not to respond literally to an obvious joke or tease.
- It prefers a brief witty, dry, or self-aware response and playing along where natural.
- It says not to explain the joke or pedantically correct a deliberately comic premise.
- It permits `SKIP` when no genuinely good response occurs.
- The real account-post/incoming-reply example is present.
- `This wasn't meant as a quote at all.` is explicitly marked undesirable.
- The manual `AI ain't good at jokes...` reply is absent from production prompt/source and is not a target response.
- Normal mention/hot-post replies and quote-tweet replies both still call the same helper.
- No classifier, model, request, schema, parser, budget, spacing, scheduling, or state mechanism was added.
- The only potential second request remains the pre-existing text retry after a confirmed multimodal-input rejection.
- `xai_user_content()`, media attachment handling, and multimodal rejection classification are unchanged.
- Exact `SKIP` parsing remains unchanged.

## 4. Pre-deployment validation

### Compile

```text
python3 -m py_compile mrsMThatcher2.py tests/test_unit_helpers.py tests/test_integration_harness.py
PASS (no output)
```

The task text named the two tests without their `tests/` prefix; the current repository paths above were used.

### Focused reply/media/skip tests

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_unit_helpers.py -k 'reply_prompt_handles_obvious_harmless_teasing or mention_native_photo_context_reaches_xai or text_only_mention_keeps_plain_xai_content or mention_external_url_without_native_photo or mention_native_photo_context_caps_multiple_photos_in_order or quote_tweet_native_photo_context_reaches_xai or generated_reply_is_safe_enough or xai_error_is_multimodal_input_rejection or multimodal_fallback'
14 passed, 316 deselected in 2.91s
```

### Parity and diagnostic regression

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py::test_scheduler_promotion_differential_fuzz_matches_master_except_allowed_scheduler_delta tests/test_unit_helpers.py::test_reply_prompt_handles_obvious_harmless_teasing_without_literal_correction
3 passed in 48.19s
```

### Full suite

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
627 passed, 1 skipped, 1 warning in 185.69s
```

The warning is the existing Starlette `TestClient` deprecation warning.

### Diff check

```text
git diff --check
PASS (no output)
```

### Production-log isolation

- Production log before tests: `1424591` bytes
- Production log after tests: `1428841` bytes
- Natural live append: `4250` bytes
- Appended interval scan found no pytest path, pytest temporary path, unit-import path, dummy credential, loopback fake endpoint, fake-API marker, test-cycle marker, or synthetic test reply.
- The production log was not deleted, truncated, rotated, or edited.

## 5. Backup

- Directory: `pre_reply_humour_prompt_deployment_20260710_112259/`
- Manifest: `pre_reply_humour_prompt_deployment_20260710_112259/MANIFEST.txt`
- Files copied with metadata preserved:
  - `mrsMThatcher2.py`
  - `mrs_log_digest.py`
  - `mrsMThatcher.local.json`
  - `bot_state.json`
  - `images_used.json`
  - `lines_used.json`
- Receipt files copied: none existed

## 6. Git

- Staged files:
  - `mrsMThatcher2.py`
  - `tests/test_unit_helpers.py`
  - `tests/test_integration_harness.py`
  - `reply_humour_context_prompt_implementation_report.md`
- Staged diff: `4 files changed, 289 insertions(+)`
- Staged diff check: passed
- Commit: `e60e491 Improve humour handling in reply prompts`
- Push result: `8e850f7..e60e491 master -> master`
- Upstream: `origin/master`
- Final ahead/behind: `0 0`
- No unrelated file was staged or committed.
- Local config was not staged or committed.
- Nothing was force-pushed.

## 7. Restart

- Restart timestamp: `2026-07-10T11:23:40+01:00`
- Signal: one `SIGTERM`
- Signal target: Python child PID `2111623` only
- Wrapper PID before: `3631076`
- Wrapper PID after: `3631076`
- Replacement child PID: `2350386`
- Replacement child start: `2026-07-10 11:24:39 BST`
- Startup success: `2026-07-10 11:24:40 BST`
- Approximate wrapper restart interval: 59 seconds
- New lock: `pid=2350386`

The wrapper was neither signalled nor restarted. No repeated signal was sent, and exactly one replacement child appeared.

## 8. Startup

Relevant newly appended lines:

```text
2026-07-10 11:24:40 INFO acquire_instance_lock - Acquired instance lock /disks/disk1/etc/mrsMThatcher/mrsMThatcher.lock
2026-07-10 11:24:40 INFO main - Bot starting
2026-07-10 11:24:40 INFO main - Images found at startup=69
2026-07-10 11:24:40 INFO validate_original_editorial_shadow_startup - Original editorial shadow scoring enabled. analysis_file=/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json original_items=69 weight=0.32 max_abs_adjustment=4.0
2026-07-10 11:24:40 INFO validate_generated_identity_shadow_startup - Generated identity-policy shadow scoring enabled. audit_file=/disks/disk1/etc/mrsMThatcher/generated_image_identity_dependence_audit.json items=83 policies={'origin_quote_only': 6, 'small_penalty': 9, 'unrestricted': 68} small_penalty=6.0 strong_penalty=15.0
2026-07-10 11:24:40 INFO main - Existing next_quote_post_epoch=1783685456, human=2026-07-10 13:10:56
2026-07-10 11:24:40 INFO main - Bot started successfully
2026-07-10 11:25:40 DEBUG main - Main loop tick. epoch=1783679140
```

The further tick found the mention lane correctly blocked only by the existing one-hour reply spacing (`daily_reply_count=3`, 1221 seconds since last reply, 3600 required). No traceback, prompt/config error, receipt block, duplicate child, unexpected cooldown, or startup failure appeared.

Both shadow systems initialized with their pre-deployment settings and were not changed.

## 9. Deployed prompt verification

The running entry point `/usr/local/bin/mrsMThatcher2.py` resolves to the repository file. Post-restart source inspection confirmed:

- humour categories at lines 6759 onward;
- anti-literal guidance at line 6761;
- anti-pedantry guidance at line 6763;
- skip-rather-than-force fallback at line 6764;
- whole-context/non-overcorrection guard immediately before it;
- the diagnostic account post, incoming tease, and undesirable reply at lines 6764-6766;
- normal mention call site at line 7265;
- quote-tweet call site at line 7947;
- unchanged media composition at line 6782;
- unchanged narrow multimodal fallback at lines 6811 and 6825;
- unchanged exact `SKIP` handling later in the same helper.

No xAI call was made for this verification.

## 10. First live reply observation

**Pending.** No genuine reply opportunity was processed during the bounded monitoring period because the existing reply spacing interval had not elapsed. No mention, reply, or test event was forced.

## 11. Live xAI call-count verification

**Pending.** No live reply-generation decision occurred during monitoring. Static code review and focused tests confirm one normal call, with only the unchanged confirmed multimodal-rejection fallback capable of making a second call.

## 12. Production safety

- No manual X call occurred.
- No manual xAI call occurred.
- No manual external API call occurred.
- No test post was created.
- Production state was not manually edited.
- Receipts were not manually edited.
- Local config was byte-identical to the pre-deployment backup after deployment.
- The wrapper was not restarted or signalled.
- Only the Python child was restarted, with one SIGTERM.
- Original editorial shadow was not altered.
- Generated identity-policy shadow was not altered.
- Media pipeline and multimodal fallback were not altered.
- Logs were not deleted, truncated, or rotated.

The live bot naturally read/wrote its normal state during startup and loop operation; no state was manually altered by deployment tooling.

## 13. Final process state

At approximately `2026-07-10 11:25:51 BST`:

- Wrapper PID: `3631076`
- Python child PID: `2350386`
- Lock: `pid=2350386`
- Last main post ID: `2075520733984223417`
- Last regular post epoch: `1783677679`
- Next regular post epoch: `1783685456` (`2026-07-10 13:10:56 BST`)
- Daily reply count: `3`
- Daily quote-reply count: `0`
- Next reply lane priority: `quote`
- Last seen mention ID: `2075519319677538463`
- Generated spacing count: `2`
- Last regular image: `t45.jpg`
- Read cooldown: none
- Write/xAI/quote cooldown epochs: `0`
- Receipts: none

## 14. Final Git state

```text
e60e491 Improve humour handling in reply prompts
## master...origin/master
```

`git diff --stat` is empty and `origin/master...master` reports `0 0`. There are no modified or staged tracked files. `git status --short` contains only pre-existing unrelated untracked artifacts plus:

```text
?? pre_reply_humour_prompt_deployment_20260710_112259/
?? reply_humour_context_prompt_deployment_report.md
```

The deployment report is intentionally untracked. The implementation report was intentionally committed with the reviewed change.

## 15. Rollback instructions

Rollback is warranted only for an operational regression, not an isolated mediocre probabilistic reply.

1. Confirm the problem is caused by prompt deployment and record current process/state/receipt details.
2. Identify the current Python child freshly with `ps -o pid,ppid,cmd --ppid 3631076`.
3. Restore only `mrsMThatcher2.py` from `pre_reply_humour_prompt_deployment_20260710_112259/mrsMThatcher2.py` after reviewing the diff.
4. Do not restore `bot_state.json`, histories, or receipts; backup runtime files become stale as production continues.
5. Do not alter either shadow configuration or local config.
6. Run `python3 -m py_compile mrsMThatcher2.py` and inspect the restored diff.
7. Send one `SIGTERM` to the current Python child only.
8. Do not signal the wrapper; wait for its normal replacement delay.
9. Verify one new child, matching lock, clean startup, both shadows enabled, restored schedule/state, and a further healthy loop tick.
10. Commit/push any deliberate rollback separately only after review.
