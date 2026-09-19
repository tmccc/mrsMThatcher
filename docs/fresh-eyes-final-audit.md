# Final independent audit

Reviewed the remediation diff against starting commit
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`, including the source changes, security
assertions and test-fixture migrations. This record covers the review work and
observed focused results; repository-wide acceptance results are recorded by the
integrating agent. The later natural-conversation follow-up removes the closed
grammar; inventory completeness and semantic judgement are model responsibilities,
not guarantees established by this local audit. See the current README contract.

Reviewed boundaries:

- State generation selection, legacy agreement, pending-identity recovery,
  exact receipt digests, composite state/history proofs, and authority checks at
  journal/source retirement mutations. Legacy backup numbering alone cannot
  establish freshness. Reader-version 5 fences prevent unsafe older readers.
- Single-call declared inventory integrity, whole-passage evidence, deictic
  rejection, UTC hash binding, draft-v4 rejection of old pending work, frozen-v3
  receipt recovery, complete image decoding and recovered-429 accounting.
- Public URL syntax, every redirect hop, mixed DNS answers, pinned connections,
  absolute DNS/socket deadlines, discovery ceilings and provider-origin checks.
- Shared review authentication, actual listener identity, Host/Origin checks,
  signed CSRF, bounded request bodies, authenticated POST export, TLS/proxy
  configuration and preservation of the existing review database workflows.
- Attempt-cost reservations across instances/processes, persistent ceiling,
  private no-follow ledger/lock authority and refusal of ambiguous POST retries.
- Added network tests use injected transports, fake sockets, ASGI clients or the
  loopback server. The pytest network guard remains installed; fixture changes
  tighten permissions and use temporary state. No test skip or live-endpoint
  exemption was introduced by the remediation.

One additional defect was reproduced during this pass: a byte-identical ledger
substitution after reservation replacement could pass confirmation without
matching the inode fsynced during staging. The owner added exact staged-inode
comparison through the existing durable-I/O helper and a regression. Replaying
my original temporary-directory probe then produced
`REJECTED_REPLACED_LEDGER`; no provider request was involved.

The complete added-line credential-pattern scan found only the deliberate
credential-bearing URL rejection fixture. Untracked artifacts were also scanned;
no credential-pattern match was found. `git diff --summary` showed no executable
mode changes. Manual review found no added credential logging or API keys.

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_independent_emergency_state_security.py \
  tests/test_image_attempt_budget.py \
  tests/test_independent_state_authority_regressions.py \
  tests/test_independent_reply_security.py \
  tests/test_research_fetch_security.py \
  tests/test_review_request_security.py \
  tests/test_review_fresh_eyes_security.py \
  tests/test_public_source_deadline.py
```

Observed result: **107 passed in 4.47s**, with one Starlette/httpx deprecation
warning. This includes the 17 emergency/legacy-state cases and actual fresh-process
or concurrent-process authority tests. Log: `/tmp/mrs-final-independent-audit.log`.
`python3 tools/check_python_documentation.py` passed for **287 modules**;
`git diff --check` passed.

No concrete unresolved security finding remained in the reviewed changes at the
end of this pass. The factual gate intentionally rejects unsupported paraphrases;
ambiguous legacy state intentionally requires operator reconciliation. No live
X/OpenAI/Gemini service, production state, deployment or chargeable operation was
used during this audit.
