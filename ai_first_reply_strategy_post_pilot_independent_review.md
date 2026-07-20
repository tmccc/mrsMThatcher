# AI-first reply strategy: post-pilot independent review

Date: 20 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Scope: independent review of the unactivated AI-first conversational reply strategy and provider pilot

## Executive verdict

The AI-first design has strong fail-closed foundations, and the final three provider pilots passed all of their stated cases. It is **not ready for activation**. This review independently reproduced three material code defects, two operational-classification defects and a current restart blocker. It also found that the pilot did not exercise the production revision cycle and does not yet provide complete repeatability provenance.

No source defect was fixed during this review. The running bot was not restarted or signalled, no production state was written, and no provider call was made.

## Findings

### 1. Critical: the next bot start would fail on the current local configuration

The installed wrapper and Python entry point are symlinks into this working tree. The current ignored local configuration still contains the retired top-level `reply_strategy` key, while the new loader rejects that key at `mrsMThatcher2.py:858-862`.

An isolated `--self-test` with dummy credentials and a temporary log failed before runtime initialisation with:

```text
LocalConfigError: Local config contains retired reply_strategy V1 settings; replace them with the reviewed ai_first_reply_strategy configuration before activation
```

The current child remains healthy because it loaded the prior code before these working-tree changes. A crash, reboot or deliberate restart would make the wrapper repeatedly attempt to start code that rejects the current configuration. Do not restart the service until an atomic, validated configuration cut-over is prepared.

### 2. High: disabling AI-first does not disable the reply lanes and permanently consumes candidates

Source defaults set `ai_first_reply_strategy.enabled=false` at `mrsMThatcher2.py:233-254`. Mention and quote-tweet lane entry checks only inspect the broader reply switches at `mrsMThatcher2.py:8827-8838` and `mrsMThatcher2.py:9748-9759`.

The pipeline maps a disabled strategy to `no_reply/strategy_disabled` at `reply_strategy.py:1291-1297`. Runtime then maps every pipeline result without a reply to a semantic `no_reply` at `mrsMThatcher2.py:8380-8406`, and the caller durably records it as a terminal evaluation at `mrsMThatcher2.py:9153-9169`.

An isolated real-path reproduction showed:

1. the first mention scan made no model call but wrote a terminal `strategy_disabled` decision;
2. a second scan skipped the candidate permanently;
3. the feature's nominally safe disabled default therefore still performs discovery work and consumes reply opportunities.

The lane must stop before candidate retrieval, media preparation, evidence loading or terminal evaluation when AI-first is disabled. Equivalent coverage is needed for mentions, quote tweets and hot-post replies.

### 3. High: persisted factual approval is not bound to all evidence seen by the models

`EvidencePassage.prompt_record()` supplies actor, action/relationship, direction/polarity, date/period and quantity to the evidence model (`reply_evidence.py:99-132`). The factual `source_hash` omits those fields (`reply_evidence.py:309-325`). Persisted drafts retain only evidence IDs and those source hashes (`reply_strategy.py:1087-1124`), while recovery verifies only that each ID exists and its source hash is unchanged (`reply_strategy.py:1247-1250`).

Two independent temporary-repository reproductions confirmed that:

- changing actor, action, direction, date and quantity left both the evidence ID and source hash unchanged;
- an approved persisted draft still passed `validate_persisted_draft()` after those semantic fields were materially changed.

This permits corrected evidence metadata to leave a stale factual approval valid. The evidence identity must bind every prompt-visible semantic field, and the draft/repository schema versions should change accordingly.

### 4. Medium: one declared Thatcher quotation permits additional undeclared corpus wording

The deterministic quotation guard at `reply_strategy.py:881-890` checks for undeclared corpus wording only when `exact_thatcher_wording_used` is false. When it is true, it verifies the declared text but does not reject other detected quotations.

A real-corpus reproduction concatenated the declared authorised wording “the answer is less Socialism.” with the undeclared authorised wording “We Conservatives hate unemployment.” The repository detected two quotation IDs, but `deterministic_reply_error()` returned no error.

The deterministic boundary should require every detected quotation span or ID to correspond to explicitly declared exact wording. Reviewer judgement is useful defence in depth, but should not replace this deterministic attribution invariant.

### 5. Medium: malformed model output becomes a permanent editorial `no_reply`

`call_and_validate()` retries a fully received invalid response once and then returns no result (`reply_strategy.py:1329-1378`). Runtime collapses that operational failure into `status=no_reply` (`mrsMThatcher2.py:8380-8406`), after which the candidate is terminally recorded (`mrsMThatcher2.py:9153-9169`).

An isolated mention-path reproduction using `proposer_invalid` showed that the first attempt terminalised the candidate and the second scan skipped it. The digest consequently reports provider/schema failures among editorial no-reply decisions.

Semantic `no_reply` and operational pipeline failure need distinct outcomes. A bounded retry/backoff ledger may be appropriate, but an invalid provider response must not masquerade as a permanent editorial judgement.

### 6. Medium: the successful pilot did not validate the end-to-end revision path

Each of runs v9, v10 and v11 contained 18 cases, but only 10 were complete proposer/evidence/reviewer pipelines. The other eight called the reviewer directly with deliberately bad drafts (`tools/pilot_ai_first_reply_strategy.py:668-707`). Any reviewer result other than `approve` counts as a pass (`tools/pilot_ai_first_reply_strategy.py:550-556`, `:692`).

Across the three final runs:

| Measure | Result |
|---|---:|
| Cases | 54 |
| Passed | 54 |
| End-to-end pipeline cases | 30 |
| Reviewer-only adversarial cases | 24 |
| End-to-end revisions exercised | 0 |
| Provider calls | 83 |
| Known cost | US$0.2858063 |
| Ambiguous calls | 0 |

The reviewer correctly refused every direct adversarial draft, including direction, relationship, date and quantity defects. However, no provider-backed case tested revision, re-proposal and the second fresh review through the production pipeline. At least one factual and one non-factual forced-revision case should pass that complete path before activation.

### 7. Low: sentence counting rejects valid replies containing “No.”

The abbreviation handling around `reply_strategy.py:715` does not recognise the number abbreviation `No.`. `sentence_count("No. 10 is the answer.")` returns 2, and the valid two-sentence reply `No. 10 is the answer. The point is settled.` is counted as three and rejected under the normal two-sentence limit.

This is a conservative false rejection, not an unsafe acceptance.

### 8. Low: pilot provenance and resume handling are incomplete

- Fixed fixtures use `date.today()` (`tools/pilot_ai_first_reply_strategy.py:490-501`), so byte-identical reruns on another date do not use byte-identical prompts.
- The run manifest's source hashes omit the pilot runner itself and the pure research-schema/formatter code that affects validation (`tools/pilot_ai_first_reply_strategy.py:843-866`).
- A ledger operation is marked completed before its response cache is atomically stored. A reproduced crash-window state with a completed ledger row and no cache caused resume to fail with `FileNotFoundError` (`tools/pilot_ai_first_reply_strategy.py:430-478`).

All 292 existing pilot operations have matching cache files, unique logical IDs and no ambiguous exposure, so no recorded result is presently damaged. Total known pilot spend across all 11 runs is US$1.04802765.

## Positive controls

- Production validation no longer imports the Gemini research SDK. `historical_context_formatter.py` uses the dependency-free `semantic_alignment/quote_research_schema.py`; the offline Gemini runner imports that schema instead.
- Evidence loading is lazy for an eligible reply attempt rather than an unconditional production bootstrap dependency.
- Corpus accounting is derived and validated from local manifests rather than embedded production constants.
- Raw claim identity comparisons between stages are deliberately strict and fail closed. Loosening them to punctuation-insensitive token matching would weaken claim integrity.
- The direct-mention preflight must not be bypassed for hot-post candidates. The correct design is to filter legally ineligible targets before caps or model work, not attempt unauthorised replies.
- Receipt, ambiguous-write, author-cap, clarification and terminal-thread protections remained intact in the focused tests.

## Pilot integrity

Runs v9, v10 and v11 each reported 18/18 passes. Their 83 operations were completed with zero ambiguous outcomes and combined known cost of US$0.2858063. Across all 11 run directories, the ledger contains 292 unique completed operations, zero ambiguous operations and US$1.04802765 known spend. Current core source hashes match the hashes recorded by the final three runs where those files were included.

The headline pilot result is therefore internally consistent, but it is narrower than an activation qualification because of the untested revision path and incomplete provenance described above.

## Validation performed

```text
Focused AI-first, runtime-isolation, provider-pilot, offline-evaluation and digest tests:
151 passed in 3.44s

Representative mention/quote integration, caps, malformed-response and 403 tests:
19 passed, 154 deselected in 24.15s

Persisted-draft, receipt, terminal-evaluation and clarification tests:
33 passed, 378 deselected in 0.49s

Ruff fatal/undefined-name/import checks:
passed

Targeted py_compile:
passed

git diff --check:
passed
```

No full test suite was rerun for this review. The focused suites directly exercised the changed reply, persistence, receipt, digest and pilot surfaces.

## Production isolation proof

Before and after review:

```text
Wrapper PID:       1595442
Python child PID:  1595443
Start time:        Sun 2026-07-19 23:07:45 BST
Restart count:     0
State:             active/running
```

The production state was read with a stable hash check only. There were no pending AI reply drafts, no legacy V1 drafts and no pending regular, meme, historical-context, confirmed-reply or ambiguous-write receipts. No X post, provider request, service operation, production-state write, commit, push or deployment occurred.

## Required order before activation

1. Correct the disabled-strategy lane behaviour and its terminal-evaluation consequences.
2. Bind persisted approvals to the complete prompt-visible evidence record and version the schema.
3. Enforce complete declaration of all detected exact Thatcher wording.
4. Separate operational model/schema failures from editorial `no_reply` outcomes and digest reporting.
5. Add provider-backed end-to-end revision cases and complete pilot provenance.
6. Correct sentence handling for `No.` and add the regression.
7. Independently review and test those changes.
8. Prepare and validate the ignored local configuration migration atomically, then perform a separately authorised controlled activation.

## Final verdict

NOT READY FOR ACTIVATION
