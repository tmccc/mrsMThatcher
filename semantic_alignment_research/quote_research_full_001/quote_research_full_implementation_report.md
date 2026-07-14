# Full Quote Research Implementation Report

## Outcome

The immutable 632-quote corpus is fully terminal. It contains 439 strictly valid grounded research packets and 193 permanent, classified failure records. There are no pending, active, transient-failure, or validation-failure records left for an ordinary resume.

The source manifest contains 632 distinct quote IDs and preserves 633 source occurrences. Its SHA-256 is `3dd17a2af366c7ae9afee01daac35426e0406d25ff4163a5a5c918b5d0275461`.

## Pilot Recovery

The original 20-quote pilot recovery completed without repeating its 13 pre-existing valid packets. The recovered view contains 17 valid packets and 3 permanent failures, with no pending work. Known pilot recovery spend was $0.752128.

## Full-Corpus Completion

| Result | Count |
|---|---:|
| Valid packets | 439 |
| Permanent failures | 193 |
| Pending or active | 0 |
| Total | 632 |

Permanent failures remain auditable rather than being discarded:

| Classification | Count |
|---|---:|
| Exhausted validation failure | 152 |
| Exhausted transient failure | 38 |
| Permanent provider/adapter failure | 3 |

The largest final causes were missing usable grounding (101), repeated Vertex `429 RESOURCE_EXHAUSTED` responses (50), and quote-identity changes during normalisation (28). Raw responses, extracted content, validation errors, grounding metadata, and attempt history were retained. The run contains 3,563 lifecycle records, 1,548 raw-response files, and 773 authoritative usage records.

## Transport Behaviour

The Developer API completed 137 valid packets. Its first confirmed provider-wide daily-quota case received the permitted retry; the second 429 activated the durable run-wide pause. The runner did not probe Developer again and routed later unfinished work directly to Vertex.

Vertex completed 302 valid packets. The final direct-to-Vertex count was 465. The Developer pause reason is recorded as `two_429s_for_first_provider_wide_quota_case`.

## Costs

| Transport | Known spend |
|---|---:|
| Gemini Developer API | $8.375554 |
| Vertex AI | $50.305286 |
| Combined | $58.680840 |

Possible ambiguous exposure is separately reported as $0.220368 and is not included in authoritative known spend. Average known cost per valid packet was $0.133669.

The initial expected preflight estimate was $24.307952. Actual spend was $34.372888 higher (141.4% above that estimate), principally because grounded Vertex requests had substantially larger billed inputs and many records consumed bounded second attempts. After live evidence showed the original Vertex $40 ceiling was inadequate, the operator explicitly authorised continuation. The guarded limits were changed to $40 Developer, $65 Vertex, and $75 combined. Actual spend remained $14.694714 below the Vertex ceiling and $16.319160 below the combined ceiling.

## Sources And Validation

All 439 valid packets retain at least one grounded source, giving 100.0% source coverage among valid packets. The final report records 1,485 retained grounded-source references. Strict validation did not fabricate missing data; exhausted invalid responses became permanent failures while their raw evidence remained available.

## Durability

The runner persisted each attempt and terminal result atomically, skipped terminal records on resume, retained transport-specific costs, and handled graceful interruption without replaying completed work. Final resume status skips 439 valid packets and 193 permanent failures and has zero unfinished quotes.

## Tests

Final verification passed:

* `python3 -m py_compile analyse_quote_research_gemini.py semantic_alignment/*.py tools/*.py`
* `python3 -m pytest -q tests/test_quote_research_corpus.py tests/test_quote_research_gemini.py tests/test_semantic_alignment_bakeoff.py tests/test_semantic_alignment_calibration.py tests/test_semantic_alignment_pipeline.py tests/test_logging_isolation.py`: 109 passed
* `git diff --check`

No test made a live provider call.

## Files Changed

Task implementation and outputs are confined to the quote-research CLI/modules, focused tests, the recovered pilot directory, the immutable full-corpus run directory, and the supplied `thatcher_quote_research_project` inputs. Existing unrelated working-tree changes were preserved.

`git diff --stat` is empty because the task files are currently untracked rather than staged. `git status --short` reports 64 pre-existing and task-related untracked entries, including the quote-research implementation and research directories. Nothing was staged, committed, pushed, or deployed.

No production behavior or production state was changed, and the production bot was not stopped, restarted, or signalled.
