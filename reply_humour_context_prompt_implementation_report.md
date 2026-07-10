# Reply Humour Context Prompt Implementation Report

## 1. Executive summary

A targeted shared reply-prompt change now instructs the existing Grok reply call to identify social intent before answering, avoid literal correction of obvious harmless humour, play along briefly when a natural dry response occurs, and return `SKIP` rather than force a joke. The real production failure is included as a compact diagnostic example.

No reply-pipeline behavior, model call count, response schema, skip parser, media handling, budget, spacing, scheduling, posting, or state logic changed.

## 2. Files inspected

- `mrsMThatcher2.py`
- `tests/test_unit_helpers.py`
- `tests/test_integration_harness.py`
- `tests/fake_api_server.py`
- `tests/fixtures/scenarios/normal_mention_reply.json`
- `tests/fixtures/scenarios/quote_tweet_reply.json`
- Current Git status and tracked diff

## 3. Existing reply architecture

Both normal mention/hot-post replies and quote-tweet replies use the same `ask_grok_for_reply(context_text, media_context)` generation function.

- Model for both lanes: `XAI_MODEL`, currently defaulting to `grok-4.3`.
- API shape for both lanes: one `/chat/completions` request with system and user messages, temperature `0.7`, and `MAX_GROK_OUTPUT_TOKENS`.
- Response contract: reply text or exactly `SKIP`.
- Skip parsing: `clean_generated_reply()` followed by a case-insensitive exact `SKIP` check; `SKIP` returns `None`.
- Safety cleanup and reply-length handling are shared.
- The only possible second request remains the existing narrow text fallback after a confirmed multimodal-input rejection. This task added no call or retry.

## 4. Exact normal-mention prompt path

`maybe_reply_to_mentions()` obtains mention and hot-post candidates, applies existing local filters, then calls `build_context_for_grok()`.

`build_context_for_grok()` supplies the available parent chain oldest-to-newest, labels this account's posts and known auto-replies, and identifies the incoming comment. When no chain exists it supplies an incoming standalone comment. The resulting context and `reply_media_context_for_candidate()` output are passed to the shared `ask_grok_for_reply()` function.

## 5. Exact quote-tweet/quote-mention prompt path

`maybe_reply_to_quote_tweets()` applies its existing local filters and calls `build_quote_tweet_context(original_tweet, quote_tweet)`. That context explicitly includes the account's original quoted post and the user's quote-post text. It passes the result and the unchanged quote-tweet media context to the same `ask_grok_for_reply()` function.

## 6. Shared prompt logic

The two lanes have different context builders but share all generation guidance in `ask_grok_for_reply()`. The prompt was therefore changed once in that shared function; no duplicated lane-specific prompt block was introduced.

## 7. Existing humour/sarcasm instructions before the change

The old prompt described the voice as dry, witty, occasionally cheeky and sometimes sarcastic. It allowed a little civil sarcasm when another person was being foolish. It did not ask the model to distinguish serious claims from jokes, teasing, puns, playful understatement, or harmless sarcasm. It also did not tell the model not to explain or pedantically correct a comic premise.

## 8. Root cause

The available parent/account context was sufficient to understand the joke, but the reply contract emphasized defaulting to a civil reply and offered only general wit/sarcasm guidance. With no social-intent check, the model treated `Not her most memorable quote` as a literal mistaken claim and produced the factually accurate but socially poor correction `This wasn't meant as a quote at all.`

The later `AI ain't good at jokes...` account reply was manual iOS activity. It was not treated as generated bot output, a training target, or part of the automatic failure.

## 9. Exact prompt changes

The shared user guidance now says, in substance and in the actual payload:

- consider whether the incoming post is serious disagreement, a genuine question, praise/agreement, abuse/spam, or a joke/tease/pun/sarcasm/light-hearted remark;
- use all supplied context and do not interpret everything as humour;
- do not respond literally to an obvious joke or tease;
- for harmless humour, prefer a brief witty, dry, or self-aware response and play along where natural;
- do not explain the joke or pedantically correct a deliberately comic premise;
- return `SKIP` when no genuinely good response occurs rather than forcing humour.

Only string literals in the existing `user_prompt` changed.

## 10. Why this should help

The model already receives the parent account post, thread/quote context, and incoming text. The missing instruction was how to interpret their social relationship. The new guidance puts that judgment inside the existing generation reasoning and establishes the desired fallback: natural dry participation or no reply, never compulsory comedy or literal correction.

## 11. Preventing overcorrection

The prompt explicitly says to use the whole context and not interpret everything as humour. Serious disagreement, genuine questions, praise, abuse, and spam remain explicit alternative intents. Existing factual, safety, spam, relevance, context-availability, daily-cap, per-author-cap, and spacing rules remain unchanged. Sarcastic political criticism can therefore still receive a substantive response when context warrants it.

## 12. Diagnostic example

The prompt includes:

- account post: `I know some of the images have been a bit odd of late. I'm working on it - bear with me...`
- incoming reply: `Not her most memorable quote`
- interpretation: teasing about the account's usual quotation format;
- undesirable literal response: `This wasn't meant as a quote at all.`
- illustrative natural response: `History may overlook that one.`
- permitted alternative: `SKIP`.

The example teaches the social distinction and does not require a canned response.

## 13. Manual reply handling

The manual `AI ain't good at jokes...` reply was explicitly excluded. It is neither represented as bot output nor suggested as a target response.

## 14. No extra xAI call

No classifier, second-pass reviewer, additional model, recursion, API call, or external service was added. The new regression test captures the request and proves exactly one mocked request is made per lane case.

## 15. Schema and skip semantics

The model, two-message schema, temperature, token budget, and response parsing are unchanged. Exactly `SKIP` remains accepted and returns `None`; the new test exercises this actual path.

## 16. Media context

`reply_media_context_for_candidate()`, `xai_user_content()`, photo ordering/cap, unavailable-media instruction, URL sanitisation, and the narrow multimodal rejection fallback are unchanged. Focused media tests passed for mention, quote-tweet, text-only, external-URL, photo-cap, and fallback cases.

## 17. Tests added or changed

### `tests/test_unit_helpers.py`

Added `test_reply_prompt_handles_obvious_harmless_teasing_without_literal_correction`, parameterised for `mention` and `quote_tweet`. It invokes the actual shared prompt path with a mocked HTTP request and asserts:

- the account post and incoming joke survive in the final payload;
- the social-intent and harmless-humour guidance is present;
- literal correction and joke explanation are discouraged;
- the diagnostic bad response and illustrative better response are present;
- `SKIP` remains allowed and parsed;
- the configured model and two-message schema are unchanged;
- only one request occurs.

Before the production prompt edit, both cases failed at:

```text
assert "joke, tease, pun, sarcasm or light-hearted remark" in prompt
```

Result: `2 failed in 0.76s`, proving both lanes lacked the guidance.

### `tests/test_integration_harness.py`

The scheduler branch-parity helper already masked system prompt prose but compared user guidance byte-for-byte. The intentional user-prompt change caused its only full-suite failure while all operational outputs matched. Its existing normalization now masks only the fixed guidance prefix and retains the lane-specific context beginning at one of three known markers. Scheduler behavior, xAI call count/schema, conversation context, posts, uploads, and state remain compared.

## 18. Test results

Focused reply/media/skip tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_unit_helpers.py -k 'reply_prompt_handles_obvious_harmless_teasing or mention_native_photo_context_reaches_xai or text_only_mention_keeps_plain_xai_content or mention_external_url_without_native_photo or mention_native_photo_context_caps_multiple_photos_in_order or quote_tweet_native_photo_context_reaches_xai or generated_reply_is_safe_enough or xai_error_is_multimodal_input_rejection or multimodal_fallback'
14 passed, 316 deselected in 2.93s
```

Parity plus diagnostic regression:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py::test_scheduler_promotion_differential_fuzz_matches_master_except_allowed_scheduler_delta tests/test_unit_helpers.py::test_reply_prompt_handles_obvious_harmless_teasing_without_literal_correction
3 passed in 49.48s
```

Full suite (run because the prompt is shared across reply lanes):

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
627 passed, 1 skipped, 1 warning in 186.26s
```

The warning is the existing Starlette `TestClient` deprecation warning.

## 19. py_compile

```text
python3 -m py_compile mrsMThatcher2.py
PASS (no output)
```

## 20. git diff --check

```text
git diff --check
PASS (no output)
```

## 21. Production-log isolation

Tests use `/tmp/mrsMThatcher-unit-import/unit-test.log`, pytest temporary base directories, and fake local endpoints. During the full suite, the independently running production bot naturally appended 18,293 bytes (`1365995` to `1384288`). The appended interval was scanned and contained no `pytest`, `pytest-of-`, unit-import path, dummy credential, loopback fake endpoint, fake-API, or test-cycle marker. No production log was truncated, rotated, or edited.

## 22. Files modified

- `mrsMThatcher2.py`: shared prompt text only.
- `tests/test_unit_helpers.py`: two-lane prompt regression.
- `tests/test_integration_harness.py`: narrow parity normalization for fixed user guidance while preserving supplied context comparison.

## 23. Files created

- `reply_humour_context_prompt_implementation_report.md` (this untracked report).

## 24. git diff --stat

```text
mrsMThatcher2.py                  | 10 ++++++++
tests/test_integration_harness.py | 14 ++++++++++++
tests/test_unit_helpers.py        | 48 +++++++++++++++++++++++++++++++++++++++
3 files changed, 72 insertions(+)
```

## 25. git status --short

Task-specific tracked changes:

```text
 M mrsMThatcher2.py
 M tests/test_integration_harness.py
 M tests/test_unit_helpers.py
?? reply_humour_context_prompt_implementation_report.md
```

The repository also retains numerous pre-existing unrelated untracked analysis files, generated corpora, locks, reports, reviews, and deployment backups. They were not modified, staged, or cleaned by this task.

## Safety confirmations

- No X call was made by this task.
- No xAI call was made by this task; all request-shaped tests used fake local responses.
- No external API call was made by this task.
- No test post was created.
- No production state file was changed by this task.
- No receipt was changed by this task.
- No local configuration was changed.
- The live bot was not restarted or signalled.
- Nothing was staged.
- Nothing was committed.
- Nothing was pushed.
