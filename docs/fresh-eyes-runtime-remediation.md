# Runtime, automation and development remediation

The review baseline and starting HEAD were both
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`. Items 6, 11 and 12 were confirmed
against that HEAD; none was already fixed. This note records the implementation
and focused evidence. The final repository-wide verification is recorded in the
main remediation report.

## Item 6: one-shot durable safety barrier

`wait_for_durable_barrier_before_one_shot_exit()` previously allowed ordinary
inspection exceptions to terminate a process whose only safety barrier was in
memory. It now retries ordinary exceptions with a 60-second throttle and stays
alive until durable restart authority is proved. Logging failures are contained;
`BaseException`, including `KeyboardInterrupt`, still reaches the existing
controlled signal path. The same protected logging is used by the one-shot
exception handlers before entering the wait.

Changed owner: `mrs_bot_cli_execution.py`. Regression coverage in
`tests/test_runtime_review_remediation.py` and `tests/test_bot_cli_execution.py`
exercises repeated `OSError`, malformed-marker errors, later recovery,
interruption, logging failure, and the real root adapter. No durable schema
migration is needed.

## Item 11: configuration, lifecycle and automation

* `mrs_bot_runtime_configuration.py` rejects spacing-retry intervals outside
  `0 <= QUOTE_CHECK_SPACING_RETRY_SECONDS <= QUOTE_CHECK_EVERY_SECONDS`.
* `mrs_bot_runtime_state_helpers.py` repairs an old future quote-check epoch to
  the current epoch once. The normal interval then elapses before polling;
  repeated ticks do not keep repairing/saving or immediately polling.
* `mrs_bot_cli_execution.py` returns status 3 for safety-stopped test cycles and
  main ticks after the durable-barrier wait; successful no-post outcomes return
  zero. Existing usage/configuration refusal remains status 2.
* `mrsMThatcher2.py` publishes the first health snapshot only after singleton
  authority is acquired. A real duplicate-process test calls both root startup
  paths and proves the rejected contender cannot change the owner's health
  identity or restart count.
* `mrsMThatcher2.py` refuses `importlib.reload()` after bootstrap or lock
  acquisition starts, before resetting any module globals. A subprocess test
  verifies refusal preserves the exact lock descriptors, socket, bootstrap
  state and health object. Import before lifecycle initialization remains pure.
* `runtime_control_contract.py` requires explicit UTC offsets for ISO deadline
  strings, accepts `Z` on the supported Python 3.10 runtime, and retains explicit
  numeric epochs. Naive timestamps are rejected instead of interpreting the
  host timezone. README documents this contract.

Coverage includes root adapters, configuration boundaries, no-save-loop
regression, success/safety CLI outcomes, both environment timezone settings,
OFD/AF_UNIX locks, real subprocess contention, and reload refusal. Existing
persisted future retry epochs are repaired under the normal state lifecycle;
there is no separate version migration for these runtime fields. A control
file containing an old timezone-less deadline must be corrected to an explicit
offset and otherwise fails closed.

## Item 12: reproducible development setup

`requirements-dev.txt` now includes `openai>=2.0,<3`, which is directly imported
by research modules during test collection. Omitting it was independently
reproduced in a disposable environment: collecting
`tests/test_historical_context_source_roles.py` failed with
`ModuleNotFoundError: No module named 'openai'` after uninstalling only that SDK;
restoring it from downloaded wheels restored collection.

`tools/check_python_documentation.py` now prunes virtual environments,
site-packages, build/dist directories, and ordinary caches when Git metadata is
absent. Source-archive regressions include an in-tree virtual environment while
retaining validation of shipped Python sources. README identifies CPython 3.10
on Linux x86-64 and the required OFD locking, `flock`, `/proc/self/fdinfo`, and
AF_UNIX abstract socket facilities; production lock checks are unchanged.
No executable mode was changed by this work.

All tests use temporary private directories. `tests/conftest.py` creates the
isolated test runtime under umask 077 and restores the original umask on
shutdown. A regression verifies directory mode 0700 and file mode 0600. Shared
integration setup writes private fixture files and explicitly identifies
unsealed old state as version 4, so modern version-5 generation authority is not
fabricated by fixtures.

## Focused verification

The new runtime regression suite initially produced 13 failures and 5 passes,
then passed after implementation. A further private-file fixture regression
was added afterwards. Recorded successful runs:

```sh
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_runtime_review_remediation.py tests/test_bot_cli_execution.py \
  tests/test_bot_runtime_safety_regressions.py tests/test_bot_state_loading.py \
  tests/test_bot_state_persistence.py tests/test_bot_durable_json_io.py \
  tests/test_bot_state_storage_regressions.py tests/test_state_reader_compatibility.py \
  tests/test_bot_tick_coordination.py tests/test_followup_fail_safe_hardening.py \
  tests/test_bot_reply_reconciliation.py tests/test_bot_main_post_reconciliation.py
# 386 passed in 14.85s; /tmp/mrs-runtime-state-proof-final.log

MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_bot_receipt_retirement.py \
  tests/test_bot_main_post_confirmation_persistence.py \
  tests/test_bot_regular_post_completion.py
# 133 passed in 6.82s; /tmp/mrs-runtime-receipts.log
```

The state and receipt suites were updated to assert the new commit proof and
complete-generation contracts. Unsafe historical NaN acceptance and
implementation-specific old callback ordering were replaced with readable
canonical generation, rejection-before-publication, migration, secure proof,
and exact proof propagation checks. No production guard was weakened.

## Clean dependency and collection smoke test

Platform: CPython 3.10.12, Linux x86-64 with OFD and AF_UNIX integration tests
available. A disposable venv without system site-packages was used. Package
wheels were fetched only from the public PyPI distribution service, then all
installation and collection ran without index access:

```sh
python3 -m pip --isolated download --index-url https://pypi.org/simple \
  --only-binary=:all: -r requirements-dev.txt \
  --dest /tmp/mrs-remediation-wheelhouse-20260919
python3 -m venv /tmp/mrs-remediation-clean-20260919
/tmp/mrs-remediation-clean-20260919/bin/python -m pip --isolated install \
  --no-index --find-links /tmp/mrs-remediation-wheelhouse-20260919 \
  -r requirements-dev.txt
/tmp/mrs-remediation-clean-20260919/bin/python -m pip check
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /tmp/mrs-remediation-clean-20260919/bin/python -m pytest --collect-only -q
```

Installation succeeded, `pip check` reported no broken requirements, and the
latest recorded clean collection found 8,568 tests in 5.62 seconds. Logs:
`/tmp/mrs-clean-download.log`, `/tmp/mrs-clean-install.log`, and
`/tmp/mrs-clean-collection-final.log`. The isolated environment installed
OpenAI 2.54.0. The intentional missing-SDK reproduction is logged separately at
`/tmp/mrs-clean-before-collection.log`; the SDK was restored offline afterwards.

## Independent SSRF and LAN review

`tests/test_review_fresh_eyes_security.py` independently reproduced six issues
in the first remediation draft: an embedded app's non-loopback ASGI server
address combined with forged `Host: localhost` bypassed authentication and TLS;
three grounding-redirect path traversal forms were accepted; and an empty body
could complete after the whole-request deadline. All six failed before the
owning agent's fixes. A second inspection identified a `Connection: close`
response-body lifetime bug in the new deadline socket wrapper; its seventh
regression failed before the wrapper's reference-lifetime fix.

```sh
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_review_fresh_eyes_security.py tests/test_public_source_deadline.py \
  tests/test_research_fetch_security.py tests/test_review_request_security.py
# 62 passed, 1 existing Starlette deprecation warning in 2.08s
# /tmp/mrs-independent-security-final.log
```

These tests use ASGI in-process calls and mocked DNS/socket/HTTP transports;
none connects to a live provider or public source. No live X, OpenAI or Gemini
service, production state, deployment, push, or chargeable operation was used.

## Additional owner and adapter verification

A broader runtime sweep completed with 1,012 passes in 62.34 seconds, including
runtime service initialization, scheduler helpers, CLI, ordinary eligibility,
logging isolation, documentation, support/bot health monitors, bootstrap,
OFD/AF_UNIX singleton checks, durable I/O, configuration, state loading/saving,
control deadlines and reply isolation (`/tmp/mrs-runtime-broad-final.log`):

```sh
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_bot_runtime_service_initialisation.py tests/test_bot_runtime_state_helpers.py \
  tests/test_bot_tick_coordination.py tests/test_runtime_review_remediation.py \
  tests/test_bot_cli_execution.py tests/test_runtime_ordinary_eligibility_integrity.py \
  tests/test_logging_isolation.py tests/test_python_documentation.py \
  tests/test_mrs_support_health_monitor.py tests/test_fail_safe_bootstrap_and_control.py \
  tests/test_bot_instance_lock_checks.py tests/test_bot_durable_json_io.py \
  tests/test_runtime_control_contract.py tests/test_bot_runtime_configuration.py \
  tests/test_bot_runtime_config_logging_regressions.py tests/test_bot_state_persistence.py \
  tests/test_followup_fail_safe_hardening.py tests/test_bot_runtime_safety_regressions.py \
  tests/test_bot_state_loading.py tests/test_mrs_bot_health.py \
  tests/test_reply_runtime_isolation.py tests/test_mrs_bot_health_monitor.py \
  tests/test_bot_runtime_control.py
```

The root integration regression
`test_clock_rollback_repairs_quote_epoch_once_then_polls_after_interval` now
checks process restarts at +1 and +899 seconds retain the repaired epoch and do
not poll; +900 seconds performs exactly one quote lookup. Its focused run
passed (1 pass, 311 deselected, 2.83 seconds):

```sh
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_integration_harness.py -k clock_rollback
```

Further inspection found ordinary `open()`/`json.load()` in main-post attempt
and conversational receipt retirement owners. Both now consume the existing
secure `load_receipt_json_no_follow` primitive supplied by their root adapters.
Eight regressions failed before these changes: symlink, hardlink,
group-writable file and writable directory for each path. The owner error
contracts retain missing-file and malformed-document behavior. Real root
adapter tests require an actual generation proof, reject an unrelated receipt,
and verify the exact proof reaches the retirement authority. Sources changed:
`mrs_bot_main_post_receipt_storage.py`, `mrs_bot_reply_delivery.py` and the two
narrow adapters in `mrsMThatcher2.py`.

Emergency regular-post owner coverage now checks the immutable
`RegularPostPersistenceResult`, including exact state/history proof propagation
and withholding proof after any component failure. A regression caught duplicate
normalized image identities in composite proof bytes (`{7, "7"}`); the owning
implementation now deduplicates exactly as the history writer does.

```sh
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_bot_main_post_confirmation_persistence.py \
  tests/test_bot_receipt_retirement.py tests/test_bot_regular_post_completion.py \
  tests/test_bot_main_post_receipt_storage.py tests/test_bot_reply_delivery.py
# 219 passed in 9.91s; /tmp/mrs-runtime-receipts-final.log
```
