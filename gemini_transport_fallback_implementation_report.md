# Gemini Transport Fallback Implementation Report

## 1. Architecture

Added a provider-level `GeminiFallbackWorker` that owns one logical Gemini result while using
separate Developer API and Vertex AI transports and ledgers. Existing Developer-only behavior
remains the default. The implementation reuses the common prompt, schema validator, pricing,
Developer client and Vertex client.

## 2. Fallback Eligibility

Fallback is eligible only after the permitted Developer retry also returns a confirmed HTTP
429. Provider status/message details distinguish confirmed daily-quota exhaustion from a
generic rate limit. HTTP 503/capacity fallback remains disabled.

## 3. Ineligible Failures

No fallback occurs for schema-invalid output, malformed JSON, safety/content refusal, invalid
request, authentication failure, model failure, parser defects, fingerprint mismatch, or any
ambiguous transport/billing outcome. An ambiguous attempt blocks cross-transport execution for
that case.

## 4. Lifecycle And Provenance

Developer attempts remain in `gemini_ledger.json`; Vertex attempts use
`gemini_vertex_fallback_ledger.json`. Each record includes logical provider, transport, case,
model, input hash, prompt/schema versions, logical and transport attempt numbers, timestamps
and lifecycle state. Vertex records link to Developer request IDs where available. Successful
fallback results are marked `completed_via_vertex_fallback` and retain transport provenance.

## 5. Model And Settings Parity

Fallback startup compares exact model slug, response schema, JSON MIME type, output-token
limit, thinking budget, temperature and tool configuration. Any difference aborts before a
Vertex request. The provider-neutral prompt and normalised input hash are shared unchanged.

## 6. ADC Preflight

Explicit fallback execution requires the established project/location environment, Vertex mode,
a readable ADC file, and successful `gcloud auth application-default print-access-token` with
stdout and stderr discarded. Credentials and tokens are never printed or persisted.

## 7. Cost Accounting

Developer known spend, Vertex known spend and each transport's ambiguous exposure are tracked
separately. Every next call is checked against its transport ceiling and the operator-confirmed
whole-run ceiling. Developer headroom cannot enlarge the Vertex ceiling.

## 8. Quota-Aware Pause

Three consecutive cases exhausted by the same confirmed daily-quota classification persist a
run-wide `developer_quota_exhausted` state. Later Gemini cases route directly to Vertex when
fallback is enabled and parity passed. Generic 429s do not activate the global pause. An explicit
probe flag can recheck Developer API; it is never automatic.

## 9. Resume Behaviour

Completed logical cases are skipped. Quota-pause state survives resume. A recovered `sending`
state becomes ambiguous and cannot trigger fallback. Developer and Vertex each permit at most
two transport attempts; a third attempt is structurally impossible.

## 10. Reporting

`gemini_transport_summary.json` and the compatible `gemini_worker_summary.json` report counts
completed by each transport, eligible triggers, direct routing after pause, failures, missing
cases, separate spend/exposure, and a per-case Developer/fallback/Vertex/final-status table.

## 11. CLI Examples

See `semantic_alignment/GEMINI_TRANSPORT_FALLBACK.md`. The CLI supports all-provider and
Gemini-only execution. Fallback requires its own enable flag and confirmed Vertex ceiling.

## 12. Files Changed

* `semantic_alignment/gemini_fallback.py`
* `semantic_alignment/large_bakeoff.py`
* `analyse_semantic_alignment_large.py`
* `tests/test_gemini_transport_fallback.py`
* `semantic_alignment/GEMINI_TRANSPORT_FALLBACK.md`
* this report

## 13. Tests And Results

Python compilation and `git diff --check` passed. The semantic-alignment and production-log
isolation selection passed: **149 passed**. Both Gemini transports were mocked; no test made an
external request.

## 14. Smoke Tests

Isolated tests covered Developer retry success, repeated quota exhaustion followed by Vertex
success, provider-wide quota pause/direct routing, Vertex preflight failure, ambiguous Developer
outcome, Vertex retry exhaustion, durable summaries and resume. No existing research run or
production file was used as a writable target.

## 15. Limitations

The quota classifier relies on confirmed HTTP status and provider error metadata. Generic 429s
remain case-local and require two failures; only strongly identified daily quota failures count
toward the provider-wide pause. Capacity/503 cross-transport fallback is intentionally disabled.

## 16. Git Diff Stat

Tracked diff:

```text
 analyse_semantic_alignment_large.py | 25 ++++++++++++++++++++-----
 semantic_alignment/large_bakeoff.py |  4 ++--
 2 files changed, 22 insertions(+), 7 deletions(-)
```

The fallback implementation and its original tests/documentation now contain 1,174 lines total
after the operational status polish:

```text
semantic_alignment/gemini_fallback.py
tests/test_gemini_transport_fallback.py
semantic_alignment/GEMINI_TRANSPORT_FALLBACK.md
gemini_transport_fallback_implementation_report.md
```

## 17. Git Status Short

```text
 M analyse_semantic_alignment_large.py
 M semantic_alignment/large_bakeoff.py
?? semantic_alignment/gemini_fallback.py
?? tests/test_gemini_transport_fallback.py
?? semantic_alignment/GEMINI_TRANSPORT_FALLBACK.md
?? gemini_transport_fallback_implementation_report.md
```

Unrelated pre-existing untracked artefacts remain present and excluded.

## Safety Confirmation

No external API call was made. No existing research output was regenerated. No production
behavior changed. The bot was not stopped, restarted or signalled. No production state,
configuration, history, receipt, log or metadata was edited. No credential was printed or
stored. Nothing was staged, committed, pushed or deployed.
