# Reply contract remediation (review items 4, 5, 9 and 10)

The defects were reproduced against the starting HEAD
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`. None of these four findings was
already fixed. All tests used generated data, local doubles, temporary state or
the loopback fake API. No live provider request or chargeable operation was used.

## 4. Declared factual claims after the one model call

The initial remediation at `a4639e7` attempted to determine inventory completeness
by requiring all remaining prose to match a closed conversational grammar. That
rejected natural courtesies, opinions and humour. The follow-up removes that
restriction; this section describes the resulting contract.

The same single model call identifies factual assertions, assesses their support
and chooses whether to reply. The prompt requires an inventory in every reply
kind, prohibits invented facts and unsupported premises, and prefers a useful
non-factual response, clarification or silence when evidence is inadequate.
Non-factual prose has no fixed vocabulary or template requirement.

`single_call_reply_grounding.py` checks declared claim structure, exact ordered
spans, fact-ID correspondence and whole-passage equality. The existing conservative
restriction on context-dependent factual passages remains. These are deterministic
contract checks, not general semantic entailment or reliable detection of omitted
assertions. Neither a subjective prefix nor a reply-kind label establishes support.
A model can still omit or misinterpret a factual assertion; this does not guarantee
hallucination-free replies. No extra model or retrieval call was introduced.

Draft schema 4 still hashes the declared inventory, exact source-record bindings,
UTC context and all reply text. The schema shape/hash is unchanged; the prompt
hash and cache key change. Obsolete unsent drafts fail current validation and use
the established regeneration path. Frozen schema-4 receipt validation preserves
already-started and confirmed `a4639e7` transactions alongside older supported
receipts, without re-evaluating past evidence availability or authorising a repost.
Exact receipt/journal identities and state generation proofs remain mandatory.

The old test claiming local detection of every omitted assertion is explicitly
replaced by a test documenting that limitation. Prefix/extra-clause tests now
exercise declared claims. Natural conversation, mixed factual/conversational
replies, malformed inventories, unknown IDs, span mismatches, changed evidence,
mechanical limits, root adapters and frozen recovery receive focused coverage.
Mocked responses test the contract, not the model's judgement; no paid evaluation
was performed. The earlier test results below remain historical implementation
records, not measurements of the revised model's editorial behaviour.

## 5. Trusted UTC time context

The context builders produced a date and source timestamp, but payload assembly
silently discarded both. The root's general clock also returned ambient local
time.

The root now supplies a dedicated `current_utc_datetime` callback to
`mrs_bot_reply_context.py` and `mrs_bot_quote_reply_cycle.py`.
`canonical_time_context` validates the UTC calendar date and explicit UTC source
timestamp, normalising `+00:00` to `Z`. Missing source times remain explicitly
unknown; malformed or timezone-less supplied values are rejected before a
provider call. `time_context` is included in the model payload and the persisted
draft. A changed UTC date or source time invalidates draft reuse.

Tests cover today/yesterday/elapsed-time questions, malformed timestamps,
canonical offset handling, hash changes, recovery across a calendar boundary and
the real root context path with an ambient Honolulu timezone.

## 9. Complete image validation, request bounds and emoji

The old image gate accepted magic prefixes; the downloader parsed Content-Length
without checking the final byte count. The payload byte limit excluded base64
images and provider JSON overhead. The emoji check rejected every Unicode `So`
character, including ordinary copyright/degree/number symbols.

`mrs_bot_reply_generation.py` now requests identity transfer encoding and checks
exact declared lengths. Short reads are transient failures; overlong bodies and
unsupported encodings are rejected. `single_call_reply_images.py` uses Pillow
container verification and full decoding of every permitted frame, with explicit
JPEG/PNG/GIF/WebP terminator/length checks. It enforces 8,192 pixels per dimension,
16 million pixels per frame, 32 frames and 32 million cumulative decoded pixels.
It does not weaken Pillow's truncation or decompression-bomb protections.

The per-image bound remains 20 MiB. A 32 MiB bound covers the complete encoded
provider JSON request, including ASCII escapes, JSON spacing, base64 expansion
and every image. An early base64-size calculation avoids constructing obviously
oversized requests. Pillow is now an explicit production dependency:
`Pillow>=10.4,<13` in `requirements.txt`.

The emoji detector uses emoji presentation ranges/sequence markers instead of
Unicode category `So`. Ordinary `20°C`, `© Crown copyright`, `™` and `№ 10`
remain valid. Independent review added the omitted coloured-shape/heavy-equals
emoji range. Emoji sequences, regional flags, keycaps and joined pictographs
remain blocked.

Tests cover complete images of every supported type, signature-only/truncated
containers, false MIME types, dimension/bomb limits, short and overlong reads,
two maximum-size images, and exact whole-request limits with Unicode escaping.

## 10. Rate-limit retries and durable accounting

The old transport parsed Retry-After but always slept one second. Its successful
retry retained telemetry metadata but bypassed provider-health/cooldown state.

The transport now parses Retry-After seconds/dates and OpenAI reset-duration
headers. At most one immediate retry is allowed, only for a proved delay of at
most one second. Longer or unknown delays defer the candidate and use the
existing persisted cooldown mechanism. Retry metadata is bounded to seven days;
large delays never become an immediate retry. Ambiguous transport failures
remain non-retryable. Attempt counts and the initial 429 metadata survive a
second transport or envelope failure.

`evaluate_single_call_reply` checks persisted cooldown before image collection
or a provider call. Recovered 429s update provider error history and persist
cooldown before decision telemetry. An independent fault test verifies that a
telemetry exception cannot erase that cooldown. Tests cover short/long/malformed
headers, reset durations, 429→success, 429→timeout, repeated candidates, and a
real state save followed by a new process loading that state under its singleton
lock.

## Draft migration and files

Draft schema version is now **4**. Old version-3 pending drafts fail current
validation and are discarded by the existing recovery path; they are never
reinterpreted as approved new replies. `mrs_bot_legacy_reply_validation.py`
recognises the frozen version-3 shape, old prompt/schema digests, quoted-subject
hash and image provenance solely for already-started/confirmed receipt recovery.
This preserves suppression evidence for posts that may already exist remotely.

Production files changed:

- `single_call_reply.py`, `single_call_reply_validation.py`
- new `single_call_reply_grounding.py`, `single_call_reply_images.py`
- `mrs_bot_reply_generation.py`, `mrs_bot_reply_context.py`, `mrs_bot_quote_reply_cycle.py`
- `mrs_bot_legacy_reply_validation.py`
- narrow UTC-clock/dependency forwarding changes in `mrsMThatcher2.py`
- `requirements.txt`, and the current single-call section of `README.md`

Test fixtures now emit the new response shape and complete generated image
containers. Synthetic approved factual replies carry explicit synthetic evidence.
Old fixture prose that made causal assertions while labelled opinion was replaced
with premise-neutral opinions. Punctuation-only tests allow factual rejection;
they no longer implicitly approve unsupported assertions.

## Verification

The first safety regression run failed all **15** cases before implementation
(false 1873 in six kinds, four image signatures, four ordinary symbols, missing
time context). After implementation and independent review:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_single_call_safety_contract.py \
  tests/test_single_call_transport_safety.py \
  tests/test_independent_reply_security.py
```

Result: **84 passed in 1.38s**. Eight root-adapter/wire-boundary cases were added
after this preceding combined run:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_single_call_reply.py tests/test_single_call_failure_routing.py \
  tests/test_single_call_transport_safety.py tests/test_single_call_safety_contract.py \
  tests/test_bot_reply_generation.py tests/test_bot_reply_context.py \
  tests/test_bot_reply_context_regressions.py tests/test_bot_pending_reply_drafts_regressions.py \
  tests/test_bot_legacy_reply_validation.py tests/test_single_call_reply_provider_trial.py \
  tests/test_independent_reply_security.py tests/test_independent_state_authority_regressions.py
```

Result: **449 passed in 16.38s**. These suites cover the owning modules, root
adapters, pending drafts, legacy validator, provider trial and independent attacks.

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_integration_harness.py \
  -k 'normal_mention or quote_tweet_reply or mentions_pagination_reaches_replyable or single_call or local_validation_failure or direct_quote'
```

Result: **11 passed, 301 deselected in 7.31s**.

The independent state review identified Python scalar-equality ambiguity during
legacy migration and an inode recapture race after commit. The state owner fixed
both. `tests/test_independent_state_authority_regressions.py` verifies the exact
attacks: **5 passed in 0.44s**. The final repository-wide verification is recorded
separately by the integrating agent; these focused results do not represent a
full-suite claim.

The complete subprocess integration suite and the affected quote-cycle,
evaluation-state and duplicate-confirmation suites were rerun after fixture
migration:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_integration_harness.py tests/test_bot_quote_reply_cycle.py \
  tests/test_bot_reply_evaluation_state.py tests/test_bot_combined_review_regressions.py
```

Result: **360 passed in 163.20s**. Subprocess fixtures use complete PNG images,
canonical UTC deadlines and valid scheduler retry intervals. State modifications
between restarts now call the real locked state writer in a temporary directory;
they do not overwrite generation digests. Confirmed duplicate-suppression,
pagination, lane priority, author caps, rollback repair and two-day soak assertions
remain in force. Expected safety-stop exits use status 3. Documentation coverage
passed for **287 modules**, and `git diff --check` passed.
