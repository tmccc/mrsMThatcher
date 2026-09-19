# Research, review application and ancillary request remediation

This records review items 7, 8 and 13, plus the related research-image decoding
change. The starting checkout and reviewed commit were both
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`. Work was isolated on
`fix/fresh-eyes-remediation-20260919` in
`/disks/disk1/research/mrsMThatcher-fresh-eyes-remediation-20260919` because the
original checkout is the documented production launch directory. No assigned
finding was already fixed at the starting commit.

All reproductions and verification described here used local fixtures, mocked
HTTP/DNS/socket operations and temporary files. No live X, OpenAI or Gemini
service, live DNS lookup, production state, deployment or chargeable operation
was used. No production checkout files were edited.

## Item 7: public-source fetching and Discovery pagination

The old source-verification and image-research paths passed
`allow_redirects=True` to Requests. Destination validation happened after the
redirect had already been followed. Gemini grounding verification also admitted
any URL containing `grounding-api-redirect`. Executing the original function
with a mocked request reproduced admission of
`https://evil.example/grounding-api-redirect/token` and observed
`allow_redirects=True`. No external request was made.

`public_source_fetch.py` now owns bounded public-source retrieval and reuses the
existing `_pinned_public_get`, address policy and URL validator in
`historical_context_search_research.py`. It checks each destination before
connecting, follows at most five redirects, rejects credentials and non-HTTP(S)
URLs, and rejects every non-public resolved address. Mixed public/private DNS
answers are rejected in their entirety. The actual connection uses a vetted
numeric address while retaining the validated HTTP Host and TLS SNI. Caller
cookies, credentials, environment proxies and ordinary Requests sessions are
not forwarded to public sources.

Google grounding authority is restricted to HTTPS on
`vertexaisearch.cloud.google.com`, with one URL-safe token immediately below
`/grounding-api-redirect/`. Host suffix tricks, credentials, path traversal and
encoded traversal do not qualify. Every resulting redirect still passes the
public-address policy.

The default total deadline is 90 seconds; research page/image callers retain
smaller deadlines where configured. Bodies are capped at 25 MiB globally and
5 MiB for text pages. Declared lengths are checked exactly. Partial or oversized
pages are rejected instead of being accepted as complete evidence.
`public_source_deadline.py` adds a killable, isolated DNS-only subprocess and a
socket reader that checks the same absolute deadline before every receive.
Connection attempts, TLS, HTTP headers, chunk framing and body reads share the
remaining budget. This prevents a peer from renewing an inactivity timeout by
slowly sending bytes. A timeout does not leave an unbounded DNS worker thread.
HTTP body-file reference counting preserves correct `Connection: close`
semantics and closes the exact socket after the last reader retires.

Transport paths, required trailing slashes and signed query ordering are
preserved after validation. Canonical source identity is not used to rewrite
on-wire resource paths. This avoids redirect loops caused by repeatedly
stripping a server-required slash.

Test mode requires explicit offline transport injection. Low-level pinned
transport tests have a separate explicit opt-in and mock all DNS/socket
operations; ordinary test calls cannot silently invoke the DNS helper.

The adapters changed are:

- `historical_context_source_gemini.py`: direct source and grounding verification;
  fixed Google provider POSTs also refuse redirects.
- `historical_context_source_resolution.py`: saved grounding resolution.
- `semantic_alignment/thatcher_image_hunt.py`: grounded-source resolution, source
  pages and downloaded images; fixed Commons API calls also refuse redirects.
- `historical_context_search_research.py`: shared pinned transport deadline and
  transport-path support, and Discovery resource-list ceilings of 100 pages and
  10,000 items. Oversized pages fail before extending the accumulated list.

Independent review found and reproduced three additional grounding path attacks,
a delayed empty-response acceptance and slow-delivery deadline weaknesses. The
new regressions exercise all of those boundaries. The independent reviewer
also found the HTTP body-file lifetime issue; its regression passes after the
socket-reference fix.

No research database/schema conversion is required. Sources formerly accepted
only because of unsafe URLs, automatic redirects or truncated content now fail
closed. Existing result identity policies remain in their owning modules.

## Item 8: one security boundary for both review workflows

The original legacy application returned HTTP 200 for unauthenticated image
access. Its unauthenticated `GET /api/export` also returned 200 and wrote the
export file. Both were reproduced using the original source, TestClient and a
temporary corpus/SQLite database.

The newer application manages reversible quarantine; the legacy application
manages staged swipe decisions. Replacing one workflow with the other would
lose required functionality. Instead, the swipe implementation now lives in
`tools/generated_image_review_app/swipe.py` and uses the same security boundary
as the quarantine application. `tools/generated_image_review/app.py` is a
compatibility launcher/import wrapper.

`tools/generated_image_review_app/security.py` enforces:

- Constant-time comparisons of both credential hashes, including malformed and
  non-ASCII credential handling.
- Signed CSRF tokens bound to an HttpOnly, SameSite=Strict cookie. JSON routes use
  the `X-CSRF-Token` header; HTML forms use the same signed token contract.
- An explicit allowed Host set, duplicate/invalid Host rejection, exact Origin
  checking and rejection of cross-site Fetch Metadata.
- A 1 MiB body ceiling before routes parse JSON/forms, including streamed bodies.
- CSP, framing prevention, content-type protection, no-referrer and no-store
  headers, including rejection responses. TLS cookies receive Secure.
- Actual ASGI listener-address checks in addition to Host checks. A request sent
  to a LAN listener cannot gain loopback privileges by sending `Host: localhost`.
- Credentials plus TLS or an explicitly configured trusted HTTPS reverse proxy
  for non-loopback operation. The launchers disable implicit forwarding-header
  trust. Reverse-proxy backend isolation and Host preservation are documented.

Export is an authenticated, CSRF-protected POST. The browser client sends the
signed token, and the old mutating GET route now returns 405. Loopback-only
local development may omit credentials, while Host, Origin, CSRF and body
protections remain active. Direct TLS uses `--tls-cert`/`--tls-key`;
`--trusted-proxy-origin` explicitly identifies a trusted HTTPS frontend.
`--public-host` supplies the permitted external Host.

The existing SQLite path and schema, initial review, confirmation,
reconfirmation, undo history and override-export format are preserved. There
is **no database migration** and no decisions are copied or discarded. Existing
launcher imports remain available through the compatibility wrapper. Remote
plaintext unauthenticated launch configurations intentionally stop working.

Documentation corrected:

- `tools/generated_image_review/README.md`
- `tools/generated_image_review_app/README.md`
- `generated_image_review_web_app_implementation_report.md`

The independent reviewer exercised the actual ASGI application with a forged
loopback Host on a LAN listener, identified the initial bypass and verified the
fix. Existing complete workflow tests also exercise the compatibility launcher.

## Item 13: ambiguous image requests, cost reservations and provider origins

The original image-generation function retried all Requests transport failures
and 5xx responses. A mocked read timeout reproduced two POST attempts with
`max_retries=1`, although the first attempt might already have been billed.
Only successful generated images contributed to the old run's operational
accounting.

`generate_all_openai_quote_images.py` now refuses automatic retries of ambiguous
transport failures and 5xx responses. Only explicit HTTP 429 rejection is
eligible for its existing bounded retry loop. Provider redirects are disabled.
The owned Session disables environment proxy inheritance.

`AttemptBudget` reserves every prospective attempt before calling `post`,
including attempts ending in 429, timeout, reset, 5xx or unusable output.
Reservations are written to `attempt_cost_reservations.json` with atomic
replacement, file fsync and parent-directory fsync. Failure to reserve prevents
the request. A restart recovers the reserved count and exposure; a changed
per-request estimate or malformed ledger fails closed. The explicit local
estimated-cost ceiling applies to reservations, not just successful images.
The estimate must be positive and finite and the ceiling finite/non-negative.
Actual provider pricing is not queried or represented as newly verified by this
change. An output directory without this new ledger begins its recorded
reservation history when the new implementation is first used; unknowable
historical failed attempts are not invented.

`provider_endpoint_policy.py` restricts credential recipients to expected HTTPS
origins with default HTTPS ports:

- OpenAI: `api.openai.com`.
- X: `api.x.com`, `api.twitter.com`, `upload.x.com`, `upload.twitter.com`.

Credential components, queries and fragments are rejected. Arbitrary API proxy
origins are not supported. Explicit loopback HTTP/HTTPS overrides remain
available only in test mode. The OpenAI owner
`mrs_bot_request_route_values.py` receives test-mode authority explicitly; root
X-origin normalization uses the import-time test-mode authority. The image
script validates its provider endpoint before reserving or sending.

Root adapter changes were coordinated with the root owner. Endpoint-dependent
subprocess tests now use expected HTTPS origins when simulating production
imports; their transport boundaries remain mocked and their original
import-mode/reload assertions remain in place.

## Related image validation

The research image downloader also reuses
`single_call_reply_images.verify_complete_image`, introduced by the reply owner.
It therefore checks MIME/decoded-format agreement, complete containers,
dimensions, frame counts and decoded pixel budgets before saving a download.
Tests reject signature-only/truncated images, false MIME types and excessive
dimensions. The former redundant second full decode was removed.

## Regression coverage and verification

New focused files are:

- `tests/test_research_fetch_security.py`
- `tests/test_public_source_deadline.py`
- `tests/test_review_request_security.py`
- `tests/test_review_fresh_eyes_security.py` (independent reviewer)

Existing owner/adapter coverage was extended in
`tests/test_thatcher_image_hunt.py`, `tests/test_generated_image_review_app.py`,
`tools/generated_image_review/tests/test_app_api.py`,
`tests/test_historical_context_source_roles.py`,
`tests/test_bot_request_route_values.py`,
`tests/test_fail_safe_bootstrap_and_control.py` and
`tests/test_logging_isolation.py`. Redirect fixtures now model each HTTP hop
explicitly, and review workflow fixtures obtain actual signed CSRF tokens.

The old full-suite log's failures in this scope were the three
`test_global_pause_is_rechecked_at_remote_boundaries` variants in
`test_fail_safe_bootstrap_and_control.py`; the complete module is included in
the final owned-suite command below. The broader log contained no failing
research/LAN/image-hunt modules at that snapshot.

Final owned-suite command:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_public_source_deadline.py \
  tests/test_review_fresh_eyes_security.py \
  tests/test_research_fetch_security.py \
  tests/test_review_request_security.py \
  tests/test_thatcher_image_hunt.py \
  tests/test_generated_image_review_app.py \
  tools/generated_image_review/tests \
  tests/test_historical_context_source_roles.py \
  tests/test_historical_context_search_research.py \
  tests/test_historical_context_transport_url_redaction_transition.py \
  tests/test_bot_request_route_values.py \
  tests/test_fail_safe_bootstrap_and_control.py \
  tests/test_logging_isolation.py --disable-warnings
```

Result: **620 passed**, 31 dependency deprecation warnings, **54.47 seconds**.
The output is retained in `/tmp/mrs-research-final-owned.log`. This includes the
two final transport-preservation regressions and all three old full-suite
runtime-boundary failures identified above.

A later full-suite compatibility failure showed that the generic API URL
normalizer also supports xAI's versioned HTTPS base. Its normalizer now accepts
the known `api.x.ai` origin, while credential-bearing OpenAI configuration passes
an explicit `provider="openai"` selector and continues to reject xAI and every
other non-OpenAI recipient. X origin restrictions are unchanged. Eleven new
regressions include a fresh-process OpenAI environment-override rejection.
The clean-environment follow-up command passed **99 tests in 5.74 seconds**:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /tmp/mrs-remediation-clean-20260919/bin/python -m pytest -q \
  tests/test_research_fetch_security.py tests/test_bot_request_route_values.py \
  tests/test_x_write_outcome_conservatism.py::test_xai_versioned_provider_base_remains_supported \
  tests/test_logging_isolation.py --disable-warnings
```

Output: `/tmp/mrs-xai-endpoint-final.log`. The independent runtime owner also
ran the complete X outcome-conservatism suite: **150 passed in 8.74 seconds**.

Independent reviewer command:

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_review_fresh_eyes_security.py tests/test_public_source_deadline.py \
  tests/test_research_fetch_security.py tests/test_review_request_security.py
```

Result before the two final transport-preservation cases: **62 passed**, one
Starlette deprecation warning, 2.08 seconds. The reviewer reported no remaining
defect in those adversarial cases.

Additional completed checks:

```bash
python3 -m compileall -q public_source_fetch.py public_source_deadline.py \
  provider_endpoint_policy.py historical_context_source_gemini.py \
  historical_context_source_resolution.py historical_context_search_research.py \
  semantic_alignment/thatcher_image_hunt.py generate_all_openai_quote_images.py \
  tools/generated_image_review tools/generated_image_review_app
python3 tools/check_python_documentation.py
git diff --check
```

Results: targeted compileall **exit 0**; documentation coverage **passed,
287 modules**; diff whitespace check **exit 0**. Repository-wide compileall,
clean-environment collection and final full-suite results are recorded by the
root remediation report; this owned-suite result is not a claim that the full
repository suite passed.

## Independent cost-reservation concurrency follow-up

The final independent review found that the first `AttemptBudget` implementation
cached its count at construction. Two already-constructed instances could both
reserve request number 1, bypassing a shared ceiling; concurrent processes also
collided on the fixed `.tmp` name. Both deterministic regressions failed before
the fix (`/tmp/mrs-budget-before.log`).

Reservations now hold a permanent, owned, single-link mode-0600 lock using Linux
`flock`. Opening the lock never follows links. Each reservation securely reloads
the actual bounded ledger while holding that lock, validates the configured
per-request estimate, and refuses to silently raise an already-persisted ceiling.
The containing directory must be owned and not writable by other users.
Directory/lock identities are checked again after acquisition and around commit.

The implementation reuses the existing durable I/O primitives for directory
opening, bounded stable reads, strict JSON, staging and exact cleanup. It writes
unique mode-0600 stages through the locked directory descriptor, then atomically
replaces and directory-fsyncs the ledger before any provider call. The final
proof checks the committed inode against the exact fsynced stage, including
rename-compatible identity fields. A byte-identical replacement after publish
was independently reproduced and is now rejected. Missing, changed, linked,
non-private or malformed authority fails closed; a failed reservation never
calls the provider. Reservations visible after an interrupted final fsync remain
counted on recovery, conservatively overcounting instead of risking a free retry.
The lock pathname is never retired while other instances may use the ledger.

The new `attempt_cost_reservations.json.lock` is created beside the existing
ledger. Existing correctly owned mode-0600 ledgers retain every reserved attempt.
A missing ledger beside an existing lock is ambiguous and requires explicit
operator recovery, as does intentionally increasing the recorded ceiling.
No automatic deletion, count reset or permission repair is performed.

```bash
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_image_attempt_budget.py tests/test_research_fetch_security.py
```

Result: **66 passed in 2.72 seconds**, `/tmp/mrs-budget-final.log`. The 18 new
budget tests include two stale instances, simultaneous separate processes, a
fresh Python restart, symlinks, hardlinks, FIFOs, unsafe permissions, lock/ledger
substitution, failures before replacement and after directory fsync, exact
committed inode verification and persisted ceiling/price changes. The existing
ambiguous-POST retry regressions also pass. Tests use only temporary state and
mocked sessions; no billable API request was made.

## Final diff audit

The complete owned diff, including new files, was checked for credential
exposure, implicit live-network calls, unsafe compatibility changes and weakened
failure handling. No credentials were added. Tests use literal dummy values,
not credential files. New runtime network code is invoked only by explicit
research fetches; importing these owners does not send requests, resolve DNS,
create state or initialize mutable service registries. The DNS command passes
arguments without a shell and executes isolated standard-library Python.

No executable-file mode changes were introduced. No database decisions,
production configuration, corpus assets, used histories or bot state were
modified. Remaining wider bot/state/reply changes belong to their respective
owners and are intentionally outside this record.
