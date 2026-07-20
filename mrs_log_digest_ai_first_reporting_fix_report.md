# AI-first digest reporting fix

Date: 20 July 2026
Baseline commit: `ddd36ee9768d80d0ce523e69d8eb87db73e6c3e4`

## Executive summary

The digest omitted usage from the deployed AI-first V3 reply pipeline. Production logs AI-first calls as:

```text
xAI reply stage=proposer usage={...}
```

`mrs_log_digest.py` recognised only the legacy form:

```text
xAI usage={...}
```

The parser therefore ignored valid proposer, evidence and reviewer usage records without raising a parse error. Digest totals understated xAI calls, prompt tokens, cached tokens, reasoning tokens, completion tokens and cost.

The defect is corrected. Legacy and AI-first formats are now both supported.

## Changes

### Usage accounting

`parse_xai_usage_from_msg()` now accepts:

- legacy `xAI usage={...}` records;
- AI-first `xAI reply stage=<stage> usage={...}` records.

The existing safe `ast.literal_eval()` parsing, malformed-record reporting and aggregate calculations remain unchanged.

An exact-format regression based on the first observed production V3 usage record proves that the digest now records:

- the successful call;
- mention-lane and target context;
- prompt, cached, reasoning, completion and total tokens;
- source count;
- cost ticks.

### Strategy version reporting

AI-first decision and outcome events lacking `strategy_version` previously defaulted to `ai-first-reply-v2`. This could invent an incorrect historical version. Missing metadata now renders as `unavailable`. Explicit V2 and V3 values remain unchanged.

### Tone terminology

AI-first V3 uses tone metadata for all reply modes. Human-facing Markdown now says:

- `Generated tones`;
- `Published/terminal tones`;
- `tone` in decision and outcome tables.

The existing `humour_tone` machine-readable fields remain available for compatibility. A parallel `tone` event field supplies the accurately labelled Markdown columns.

## Files changed

- `mrs_log_digest.py`
- `tests/test_digest_reply_observability.py`
- `tests/test_integration_harness.py`
- `mrs_log_digest_ai_first_reporting_fix_report.md`

## Validation

Commands run:

```bash
python3 -m pytest -q \
  tests/test_digest_reply_observability.py \
  tests/test_digest_safety_hardening.py \
  tests/test_generated_image_pool_health_digest.py \
  tests/test_generated_image_pool_runway_digest.py \
  tests/test_generated_image_utilisation_digest.py \
  tests/test_integration_harness.py::test_digest_reports_xai_usage_events_and_totals \
  tests/test_integration_harness.py::test_digest_reports_reply_strategy_decisions

python3 -m py_compile mrs_log_digest.py
git diff --check
```

Results:

```text
98 passed in 2.34s
py_compile passed
git diff --check passed
```

The full offline suite was not rerun because the change is isolated to digest parsing and rendering, and the focused tests exercise both legacy and deployed V3 formats.

## Operational impact

- No production selection, posting, reply or safety behaviour changed.
- No production state, receipt, ledger, history or configuration was modified.
- No X or provider call was made.
- No service was stopped, restarted or signalled.
- No deployment, commit or push occurred.
- The corrected reporting will be used automatically the next time the standalone digest command runs from this working tree.

## Conclusion

The confirmed AI-first usage-accounting omission is fixed with backwards compatibility retained. Focused validation passed and no bot restart is required for this standalone reporting change.
