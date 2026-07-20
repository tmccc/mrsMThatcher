# Independent adversarial bot code review

## Executive verdict

No confirmed production defects remain from this review.

The reviewed code consistently fails closed at the high-risk boundaries examined: reply-evidence loading, structured model-response validation, reviewer approval, persisted-draft validation, receipt reconciliation, ambiguous remote-post handling, state recovery, and image-metadata eligibility.

The uncommitted `mrs_log_digest.py` changes correctly address AI-first usage parsing, native tone reporting, and absent strategy-version labelling. I found no regression in their control flow.

Confidence is moderate rather than high because focused pytest execution was blocked by the read-only environment’s lack of any writable temporary directory.

## Confirmed findings

None.

I did not classify these correct behaviors as defects:

- Semantic quote/image veto is explicitly shadow-only and fail-open because it cannot alter production selection.
- Conversational reply evidence and reviewer failures are fail-closed.
- Invalid or conflicting confirmed-reply receipts block further reply processing.
- Retryable API failures return a checked status and defer another lane cycle rather than marking candidates terminal.
- The wrapper’s restart loop and systemd’s child termination logic are consistent with the deployed process model.

## Residual risks/test gaps

1. **Focused tests could not execute in this review environment**

   Pytest failed before collection because Python could not find a writable temporary directory among `/tmp`, `/var/tmp`, `/usr/tmp`, or the read-only repository. Consequently, the current uncommitted digest tests and relevant committed safety tests were inspected but not executed here.

   Recommended follow-up: run the exact focused selection listed below in an isolated writable test environment.

2. **Confirmed-post recovery still depends on difficult fault-injection branches**

   The source and tests cover receipt-write failure, emergency state-save failure, receipt replay, and receipt-removal failure. These are high-value branches whose correctness depends on filesystem failure ordering. Maintain tests that independently inject failure at every durable write, directory `fsync`, rename, backup rotation, and receipt unlink boundary.

3. **Digest compatibility depends on retained legacy log fixtures**

   The current patch preserves both legacy `xAI usage=...` and AI-first `xAI reply stage=... usage=...` formats and maps legacy `humour_tone` into the displayed `tone`. Regression coverage should retain both formats, mixed-format windows, malformed usage payloads, and incremental-resume boundaries.

4. **Provider-facing semantic research modules were reviewed statically only**

   Production semantic-veto runtime loading and lookup were traced. Provider-bound research/calibration execution was not invoked because external calls were prohibited. Their transport-specific response handling therefore remains outside executable confirmation in this review.

5. **Operational wrapper behavior lacks a focused source-level regression test**

   The tracked systemd tests assert important unit properties, but the deployed wrapper’s environment overrides, child command shape, and clean shutdown interaction are not directly exercised. A network-free subprocess test with a fake bot executable would provide useful coverage.

## Scope and method

Reviewed directly, without consulting any `*report.md` file:

- `mrsMThatcher2.py`
- `reply_strategy.py`
- `reply_evidence.py`
- `historical_context_formatter.py`
- `semantic_quote_image_veto.py`
- Production semantic-alignment runtime and supporting modules
- `mrs_log_digest.py`
- `mrs_engagement_analytics.py`
- `semantic_veto_shadow_health.py`
- Relevant configuration validation and schemas
- Systemd unit sources, installer, and deployed wrapper call path
- Directly relevant reply, recovery, digest, semantic-veto, deployment, and integration tests

The review traced:

- CLI dispatch and production bootstrap
- Lazy and dynamic imports
- Structured model transport and response validation
- Evidence identities and exact-passage validation
- Pending approved-reply persistence
- Mention, hot-post, and quote-tweet arbitration
- Daily/per-author quotas and scheduling
- Confirmed reply and main-post receipt recovery
- Quote/image selection and shadow-veto isolation
- Digest event parsing, correlation, resume handling, and rendering
- Analytics/runtime path containment
- Wrapper and systemd process coupling

Tests were treated as evidence rather than proof. Candidate problems were rejected unless supported by executable evidence or a complete control-flow argument.

## Commands/tests run

Read-only commands included:

```text
git status --short
git rev-parse HEAD
git branch --show-current
git diff --stat
git diff --check
rg --files …
rg -n … mrsMThatcher2.py reply_strategy.py reply_evidence.py …
sed -n … <reviewed files>
nl -ba … <reviewed files>
ls -l /usr/local/bin/runMrsMThatcher2 /usr/local/bin/mrsMThatcher2.py
readlink -f /usr/local/bin/mrsMThatcher2.py
```

A network-free AST parse succeeded for 53 maintained Python files:

```text
PYTHONDONTWRITEBYTECODE=1 python3 - …
AST parsed 53 maintained Python files
```

The following focused test command was attempted:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/test_reply_strategy.py \
  tests/test_historical_context_reply.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_digest_reply_observability.py \
  tests/test_digest_safety_hardening.py \
  tests/test_deployment_assets.py
```

It did not reach collection. Pytest exited because no writable temporary directory was available. No repository cache was created.

`git diff --check` completed without errors.

## Git state

- Branch: `master`
- HEAD: `ddd36ee9768d80d0ce523e69d8eb87db73e6c3e4`

Current working tree:

```text
 M mrs_log_digest.py
 M tests/test_digest_reply_observability.py
 M tests/test_integration_harness.py
?? mrs_log_digest_ai_first_reporting_fix_report.md
```

The untracked report file was deliberately not read.

Because there are no confirmed findings, there is no committed-versus-uncommitted finding attribution. The residual test limitations apply to both the committed production code and the current uncommitted digest changes.

## Operational isolation

- No files were modified or created.
- No repository caches were created.
- No production state, receipts, histories, logs, analytics databases, or secret values were read.
- Local secret-bearing configuration contents were not inspected.
- No network or provider request was made.
- X was not contacted.
- No service was stopped, started, restarted, reloaded, or signalled.
- No commit, push, installation, or deployment operation was performed.

## Parent-session focused test verification

This appendix was added by the orchestrating session after the independent reviewer completed its report. It does not alter the reviewer's findings.

The exact focused test selection recommended by the reviewer was run in the normal project test environment:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tests/test_reply_strategy.py \
  tests/test_historical_context_reply.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_digest_reply_observability.py \
  tests/test_digest_safety_hardening.py \
  tests/test_deployment_assets.py
```

Result:

```text
294 passed in 8.30s
```

No production service or production state was touched by this verification.
