# MrsMThatcher Bot

Current default runtime and full-suite platform: CPython 3.12 on Linux (x86-64).
The typed production core retains the previously documented Python 3.10
compatibility target; `mypy-core.ini` checks that target. The complete suite in
this worktree is run with the host's CPython 3.12 interpreter.
Production locking requires Linux open-file-description locks (`F_OFD_SETLK`
and `F_OFD_GETLK`), `flock`, an accessible `/proc/self/fdinfo`, and AF_UNIX
abstract sockets. These protections are mandatory; other operating systems are
not supported for production or the lock integration suite. Live module reload
is refused after bootstrap or lock acquisition; restart through the normal
controlled lifecycle instead.

Runtime-control deadline strings must include an explicit timezone offset, for
example `2030-01-02T03:04:05+00:00` or `2030-01-02T03:04:05Z`. Integer Unix epochs
are also accepted. Timezone-less dates fail closed, independent of host settings.
The one-shot `--test-cycle` and `--test-main-tick` modes return status 3 after a
remote-write safety stop has obtained durable restart authority; successful
no-post outcomes return zero.

## Local Integration Harness

All bot-code test runs have a documentation prerequisite. The root pytest
configuration checks maintained modules and public APIs for the required
docstrings before collecting even a targeted test. Run the same fast gate
directly when editing bot code:

```bash
python3 tools/check_python_documentation.py
```

Run the safe local integration tests with:

```bash
python3 -m pip install -r requirements.txt
python3 tools/check_python_documentation.py
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_integration_harness.py
```

The tests start a fake local API server and run the real bot script with
`--test-cycle`, `--test-post-quote`, and `--test-post-meme`.
All bot state is created under pytest temporary directories. The harness sets:

- `MRS_TEST_MODE=1`
- `MRS_BASE_DIR=<tmp test state dir>`
- `MRS_LOG_FILE=<tmp test state dir>/test.log`
- `X_API_BASE_URL=<fake server>`
- `X_UPLOAD_BASE_URL=<fake server>`
- `OPENAI_API_BASE_URL=<fake server>/v1`

Before collection, pytest also sets a temporary `HOME` inherited by child CLI
processes, isolating the digest's default published-cost cache. Cost tests use
synthetic caches and can still override the digest's path and clock directly.

If development dependencies are installed in the Python user site, preserve that
package location for child CLI processes before running pytest:

```bash
export PYTHONUSERBASE="$(python3 -m site --user-base)"
```

This keeps installed packages available after pytest changes `HOME`; temporary
home, state/cache and network isolation remain in effect.

The complete offline test and research-tool dependency set is recorded in
`requirements-dev.txt`. Install it when running the full repository suite:

```bash
python3 -m pip install -r requirements-dev.txt
python3 tools/check_python_documentation.py
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

Production corpus validation needs only `requirements.txt`. HTML/PDF extraction
and research-provider dependencies remain behind explicit offline retrieval
boundaries and belong in `requirements-dev.txt`. Saved curated evidence accepts
fetch-policy versions v5, v6 and v7 under the same publisher, archive-capture and
content-hash checks; unknown or malformed versions are rejected.

On the four-core production host, the coverage-equivalent fast path uses four
isolated pytest workers:

```bash
python3 tools/check_python_documentation.py
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0
```

Do not run serial and parallel suites concurrently in the same checkout.

## Typed production core

Install `requirements-dev.txt` in a development environment and use its Python
interpreter for the single production-core checker and its contract examples:

```bash
python3 -m mypy --config-file mypy-core.ini
python3 tools/check_core_typing_examples.py
```

`mypy-core.ini` is the exact list of checked production files. It checks the
actual root factories in `mrsMThatcher2.py`, both reply lanes and their assembly,
generation, draft, delivery and completion modules, main-post assembly,
publication, attempt/receipt owners and quote/meme runners, runtime coordination,
and the state loading boundary. The value, runner, publication, runtime
coordinator, reply assembly and main-post assembly modules named in the
configuration's per-module section have strict function and generic checks;
the remaining selected legacy modules have their annotated bodies checked.
The real root factories are checked against receipt, transport, mutation
authority and state-save contracts. Application imports outside that selected
list are followed for their signatures without making unrelated offline,
native media, transport and locking implementations strict. Those specialised
authorities remain runtime boundaries. The configuration does not use `ignore_errors`,
`ignore_missing_imports`, or `follow_imports=skip`.

`mrs_bot_core_contracts.py` describes normalised state, verified reply contexts,
current and historical drafts, and the distinct reply and main-post receipt
shapes. Raw JSON and state-candidate normalisation remain dynamic and preserve
unknown persisted fields. Once default creation or candidate normalisation
succeeds, the same dictionary is carried as `BotState` through runtime
coordination, reply cycles, delivery, completion and main-post orchestration.
Recognised keys that may be absent are optional in that contract. Provider
values remain untrusted objects; draft and receipt validators establish their
internal shapes. `single_call_reply.py` retains the existing `PipelineResult`
dataclass instance while its checked outcome protocols narrow reply-ready,
no-reply, operational-failure, disabled and draft-discarded alternatives. Every
alternative also guarantees the telemetry read by reply generation and decision
logging. The reply and main-post receipt loaders return
tagged outcomes for absence, invalid data and the supported sending,
pending-schedule or confirmed stages. Current materialised main-post receipts
are distinct from smaller historical validated forms.

To add an evaluation outcome, extend `PipelineOutcome`, validate its status and
payload in `checked_pipeline_outcome`, and handle it in
`mrs_bot_reply_outcomes.outcome_disposition`;
the `NoReturn` exhaustiveness check and negative typing fixture catch omissions.
Static shapes describe an in-memory guarantee at one boundary. File rereads,
remote success, source identity and durable retirement still require the
existing runtime validation and transaction proofs.

## Typed digest core

The digest has a separate Python 3.10 check using the development-only mypy
dependency in `requirements-dev.txt`:

```bash
python3 -m mypy --config-file mypy-digest.ini
python3 tools/check_digest_typing_examples.py
```

`mypy-digest.ini` checks the digest coordinator, its input/record, context,
runtime, transaction, reporting, reply and incident modules, and
`mrs_log_digest_contracts.py`. All listed modules require annotated functions
and generic arguments, and mypy checks calls into the selected core. The
contracts and inert analysis-state module add stricter export and extra checks.
This does not make every digest helper strict.

Raw log lines, JSON and saved cursor contents remain dynamic at their readers.
After parsing or the relevant field checks, records, physical input summaries,
source references, current state/config snapshots, restored resume fields and
core report sections have checked shapes. The current state and config tags
preserve their reader-accepted dictionaries without copying them; they do not
claim a complete bot-state schema. `DigestCurrentSnapshots` keeps the tags
distinct, and only the state tag enters the current-state parameters of
`analyse()` and `DigestAnalysis`. `report_section()` reads
the current dictionary for a known section of an analysis-built report. Its
checked keys cover summary, resume context, mention/recovery sections, current
state/config provenance, provider coverage and the known single-call cost field.
Final enrichment and cursor persistence use those typed reads and writes.
Raw indexing of the report's historical extensions remains dynamic, and the
accessor does not validate arbitrary dictionaries. Public rendering, refresh and
cursor helpers still accept partial older reports with their existing missing
and `None` behavior; their limited partial fields do not imply a complete
analysis-built report. The typing examples check mutations after insertion,
known-key spelling and state/config miswiring at both analysis entries; they
do not prove arbitrary raw indexing safe. `DigestAnalysisState`
starts with complete observation collections and source contexts; the
coordinator owns publication correlation and its optional API preparation is
required before report assembly. `run_digest()` carries one selected input and
one report through overlay and delivery to the later cursor commit. Historic
event payloads, optional analytics and compatibility report sections remain
dynamic because their fields vary across old records and report versions.

When running the broad suite through an agent, use a detached session: the suite
can outlast the command session's time limit. Capture output in a log and save
the final exit status, then monitor it until completion. Preserve the test
environment above and confirm that any interrupted run has stopped before
starting another. Progress near 100% is not a completed test result.

Historical research tests use `tests/helpers/historical_corpus.py` to read their
original corpus from a checked-in, hash-verified compressed fixture. Shallow
clones and source archives need no Git history or downloads to run these tests.
See `tests/fixtures/README.md` for the fixture's contents and maintenance. Current
runtime tests use the active quotation collection.

Reusable bot test support lives in `tests/helpers`: `reply_values.py` and
`mention_values.py` provide root-independent data builders, while
`reply_fixtures.py`, `mention_fixtures.py`, `bot_runtime.py` and `bot_fixtures.py`
provide isolated application setup and integration patches. Root-independent
behavioural tests import the value builders directly. Tests needing durable-state
isolation
explicitly import `isolate_bot_runtime`; its autouse behaviour is scoped
to those test modules. Network and production-state guards are defined in
`tests/conftest.py`.

`adapter_assertions.py` shares the ordinary adapter-forwarding checks. Keep
module-specific defaults, callback groups and import-isolation checks local.

Shared mention, single-call, historical-context, receipt and transport fixtures
also live in `tests/helpers`, alongside integration-harness, research and offline
selector support. Provider test modules import the same helpers as their
consumers; tests should not import support from other test modules. Import
fixtures explicitly in each test module so their scope remains visible.

Bot regression tests are grouped by feature in `tests/test_bot_*_regressions.py`,
including asset selection, posting, receipts, reply handling, state and runtime
configuration. Keep feature-specific helpers beside their tests and shared
builders in `tests/helpers`. Use `pytest.mark.allow_loopback_network` on a test
or as its module's `pytestmark` for local fake servers; the network guard
continues to block non-loopback connections.

Digest tests share record, incident, corpus, generated-pool and cost builders in
`tests/helpers/digest_*.py`. Keep digest tests with their feature groups and
import reusable builders from those support modules rather than other test
modules. The independent Markdown expectations remain in
`tests/fixtures/digest_markdown`.

### Manual Fake-Server Run

Start the fake API server with one of the scenario fixtures:

```bash
python3 tests/fake_api_server.py tests/fixtures/scenarios/normal_mention_reply.json
```

The server prints a local URL such as `http://127.0.0.1:12345`. In another
shell, create a disposable test state directory and run one of the test-only
commands:

```bash
export FAKE_API_URL=http://127.0.0.1:12345
export MRS_TEST_MODE=1
export MRS_BASE_DIR=/tmp/mrsMThatcher-test
export MRS_LOG_FILE=/tmp/mrsMThatcher-test/test.log
export X_API_BASE_URL="$FAKE_API_URL"
export X_UPLOAD_BASE_URL="$FAKE_API_URL"
export OPENAI_API_BASE_URL="$FAKE_API_URL/v1"
export X_CONSUMER_KEY=dummy
export X_CONSUMER_SECRET=dummy
export X_ACCESS_TOKEN=dummy
export X_ACCESS_SECRET=dummy
export X_MY_USER_ID=12345
export OPENAI_API_KEY=dummy
export X_BEARER_TOKEN=dummy
```

Run one reply-cycle pass:

```bash
python3 mrsMThatcher2.py --test-cycle
```

Run one quote/image post pass:

```bash
python3 mrsMThatcher2.py --test-post-quote
```

Run one daily meme post pass:

```bash
python3 mrsMThatcher2.py --test-post-meme
```

The test-only commands require `MRS_TEST_MODE=1` before `mrsMThatcher2` is
imported. That authority is fixed for the lifetime of the imported module;
changing the environment later cannot enable or disable a test command. They
use `MRS_BASE_DIR` for state, local config, control files, quote lines, images,
meme files, and used-history JSON files. Legacy pickle history files are no longer automatically
deserialised; if JSON history is missing while a legacy pickle exists, the bot
fails closed until JSON history is restored or migrated manually from a trusted
backup. They use `MRS_LOG_FILE` for logs.

The base-directory, log, primary X API, and OpenAI defaults remain as listed
below. Media upload routing is the exception: `X_UPLOAD_BASE_URL` no longer
defaults to the legacy `https://upload.twitter.com` origin used with
`/1.1/media/upload.json`. When unset, it now inherits the resolved
`X_API_BASE_URL`, and the exact `POST /2/media/upload` request uses that origin.
X reads and `POST /2/tweets` continue to use `X_API_BASE_URL`.

- `MRS_BASE_DIR` defaults to `/disks/disk1/etc/mrsMThatcher`
- `MRS_LOG_FILE` defaults to `<MRS_BASE_DIR>/mrsMThatcher.log`
- exact provider requests are retained under `<MRS_BASE_DIR>/ai-request-records/`
- `X_API_BASE_URL` defaults to `https://api.x.com`
- `X_UPLOAD_BASE_URL` inherits the resolved `X_API_BASE_URL` when unset
- `OPENAI_API_BASE_URL` defaults to `https://api.openai.com/v1`

Safety guards:

- Operational entry points require a successful explicit `production_bootstrap()` call; importing the module alone never enables them.
- Production bootstrap configures the rotating production file log explicitly. Tests must pass `configure_file_logging=False` or an isolated `log_path`; module-owned handlers are replaced and closed on reconfiguration.
- The primary production log rotates at 2,000,000 bytes, retains 100 rotated backups, and therefore has an approximate maximum active-plus-rotations footprint of 202 MB.
- If `MRS_TEST_MODE=1` and `MRS_BASE_DIR` resolves to the production directory or a child of it, the bot aborts before logging or state writes.
- If `MRS_TEST_MODE=1` and `MRS_LOG_FILE` resolves under the production directory, the bot aborts.
- If `MRS_TEST_MODE=1` and any API base URL still points at live X/OpenAI hosts, the bot aborts unless `MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST=I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X` is set deliberately.

Endpoint override convention:

- `X_API_BASE_URL` must be an origin only (scheme, host and optional port), with no path, query, fragment or user information; the bot appends `/2/...` endpoint paths.
- `X_UPLOAD_BASE_URL` must likewise be an origin only. It is used only for the exact literal `POST /2/media/upload`; reads and `POST /2/tweets` continue to use `X_API_BASE_URL`. When unset it inherits the resolved `X_API_BASE_URL`. The bot does not fall back to a second legacy upload endpoint after an uncertain outcome.
- `OPENAI_API_BASE_URL` should include `/v1` when the fake server exposes `/v1/responses`.

Scenario fixtures live in `tests/fixtures/scenarios/`. The fake server implements only the endpoints the bot currently uses:

- `GET /2/users/{id}/mentions`
- `GET /2/tweets/search/recent`
- `GET /2/tweets/{id}/quote_tweets`
- `GET /2/tweets/{id}`
- `POST /2/tweets`
- `POST /2/media/upload`
- `POST /v1/responses`

## Files To Keep Together

The production runtime and its operational support assume these files are
deployed as a coherent set:

- `mrsMThatcher2.py`
- `mrs_bot_image_scoring.py`
- `mrs_bot_original_editorial.py`
- `mrs_bot_asset_metadata.py`
- `mrs_bot_quote_candidates.py`
- `mrs_bot_image_selection.py`
- `mrs_bot_quote_posting.py`
- `mrs_bot_regular_post_completion.py`
- `mrs_bot_daily_meme.py`
- `mrs_bot_legacy_reply_validation.py`
- `mrs_bot_reply_state.py`
- `mrs_bot_reply_drafts.py`
- `mrs_bot_reply_history.py`
- `mrs_bot_reply_generation.py`
- `mrs_bot_reply_model_transport.py`
- `mrs_provider_request_records.py`
- `mrs_bot_reply_cycle_interfaces.py`
- `mrs_bot_reply_preparation.py`
- `mrs_bot_reply_receipt_values.py`
- `mrs_bot_reply_reconciliation.py`
- `mrs_bot_reply_delivery.py`
- `mrs_bot_normal_reply_cycle.py`
- `mrs_bot_quote_reply_cycle.py`
- `mrs_bot_quote_discovery.py`
- `mrs_bot_hot_post_discovery.py`
- `mrs_bot_mention_discovery.py`
- `mrs_bot_mention_authority.py`
- `mrs_bot_reply_evaluation_state.py`
- `mrs_bot_author_quarantines.py`
- `mrs_bot_tweet_lookup_cache.py`
- `mrs_bot_reply_context.py`
- `mrs_bot_reply_native_media.py`
- `mrs_bot_reply_lane_policy.py`
- `mrs_bot_daily_reply_accounting.py`
- `mrs_bot_reply_clarifications.py`
- `mrs_bot_runtime_control.py`
- `runtime_control_contract.py`
- `mrs_bot_api_cooldowns.py`
- `mrs_bot_x_pagination.py`
- `mrs_bot_request_route_values.py`
- `mrs_bot_x_response_diagnostics.py`
- `mrs_bot_tick_coordination.py`
- `mrs_bot_durable_json_io.py`
- `mrs_bot_state_value_normalisation.py`
- `mrs_bot_state_persistence.py`
- `mrs_bot_state_candidate_validation.py`
- `mrs_bot_state_loading.py`
- `mrs_bot_main_post_receipts.py`
- `mrs_bot_main_post_receipt_storage.py`
- `mrs_bot_main_post_attempt_values.py`
- `mrs_bot_main_post_confirmation_persistence.py`
- `mrs_bot_main_post_publication.py`
- `mrs_bot_x_request.py`
- `mrs_bot_post_creation.py`
- `mrs_bot_main_post_reconciliation.py`
- `mrs_bot_historical_context_delivery.py`
- `mrs_bot_historical_context_queue.py`
- `mrs_bot_historical_context_runtime.py`
- `mrs_bot_receipt_retirement.py`
- `mrs_bot_remote_write_barriers.py`
- `mrs_bot_local_config.py`
- `mrs_bot_runtime_configuration.py`
- `mrs_bot_self_test.py`
- `mrs_bot_observability.py`
- `mrs_bot_runtime_service_initialisation.py`
- `mrs_bot_safety_marker_snapshots.py`
- `mrs_bot_remote_write_incidents.py`
- `mrs_bot_instance_lock_checks.py`
- `mrs_bot_installation_lifecycle.py`
- `mrs_bot_transaction_recovery.py`
- `mrs_bot_transport_source_preparation.py`
- `mrs_bot_cli_execution.py`
- `mrs_bot_used_history.py`
- `mrs_bot_receipt_primitives.py`
- `mrs_bot_runtime_state_helpers.py`
- `remote_write_safety_protocol.py`
- `remote_media_upload_receipt.py`
- `remote_write_transport_journal.py`
- `exact_receipt_retirement.py`
- `transaction_mutation_authority.py`
- `x_api_error_semantics.py`
- `single_call_reply.py`
- `single_call_reply_validation.py`
- `reply_evidence.py`
- `reply_factual_evidence.json`
- `historical_context_formatter.py`
- `historical_context_outbox.py`
- `historical_context_packet_corrections.py`
- `historical_context_published_reply_semantic_review.py`
- `historical_context_reply_semantic_gate.py`
- `historical_context_source_curated_evidence.py`
- `historical_context_source_independent_review.py`
- `historical_context_source_openai_manifest.py`
- `historical_context_source_recovery.py`
- `historical_context_source_research_manifest.py`
- `historical_context_source_resolution.py`
- `historical_context_source_roles.py`
- `historical_context_reply_schema.json`
- `shadow_lifecycle.py`
- `shadow_feature_lifecycle.json`
- `semantic_alignment/__init__.py`
- `semantic_alignment/io.py`
- `semantic_alignment/quote_research_schema.py`
- `semantic_alignment_research/quote_research_full_001/corpus_manifest.json`
- `semantic_alignment_research/quote_research_full_001/grounding_sources.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_packet_corrections.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_curated_evidence.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_independent_review.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_openai_research.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_recovery.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_research.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_resolution.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_role_audit.json`
- `semantic_alignment_research/quote_research_full_001/research_packets.json`
- `semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json`
- `mrs_log_digest.py`
- `tools/extract_prospective_conversations.py`
- `openai_cost_cache.py`
- `runMrsMThatcher2`
- `mrs_bot_health.py`
- `mrs_bot_health_monitor.py`
- `deploy/systemd-user/install.sh`
- `deploy/systemd-user/mrsMThatcher.service`
- `deploy/systemd-user/mrs-bot-health-monitor.service`
- `deploy/systemd-user/mrs-bot-health-monitor.timer`
- `deploy/systemd-user/mrs-engagement-analytics.service`
- `deploy/systemd-user/mrs-engagement-analytics.timer`
- `deploy/systemd-user/mrs-openai-cost-cache.service`
- `deploy/systemd-user/mrs-openai-cost-cache.timer`
- `deploy/systemd-user/mrs-prospective-conversations.service`
- `deploy/systemd-user/mrs-prospective-conversations.timer`
- `mrsMThatcher.env.example`

The offline research and benchmark implementation remains versioned with the
project, but is not a production runtime dependency:

- `semantic_alignment/quote_research_gemini.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_quote_image_veto.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `tests/test_integration_harness.py`
- `tests/fake_api_server.py`
- `tests/fixtures/scenarios/*.json`
- `README.md`
- `requirements.txt`
- `requirements-dev.txt`
- `mrsMThatcher.local.example.json`
- `extra_quote_watch_post_ids.example.txt`
- `mrsMThatcher.txt`
- `quote_analysis.json`
- `image_analysis.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `images/t*.jpg`
- PNG files in `images/` (including uppercase and mixed-case extensions)
- `final_posting_queue_top90_as_is/images/*`
- `final_posting_queue_top90_as_is/renamed_png_v3_top90_posting_queue.json`

The rejected quotation/image semantic-veto implementation, manifests and
adjudications remain versioned only as offline research evidence. The
production bot, digest and scheduled operational services do not import or
load them.

`mrs_log_digest.py` is included because the digest golden tests run the actual
digest script against generated test logs.

The tracked image assets are the runtime assets used by `mrsMThatcher2.py`.
Larger source/research meme directories such as `memes/`, `meme_hunt_001/`,
and `meme_shortlist*/` are intentionally ignored.

## Regular Quotation-Image Pool

Regular posts discover the existing original images through the `images/t*`
glob and also discover every file with a case-insensitive `.png` suffix in that
same configured regular-image directory. Overlapping matches appear once. The
legacy `tg_<64-hex-quote-hash>` assets remain excluded. Every candidate still
requires an `image_analysis.json` `path_index` entry, matching content hash and
valid per-image analysis before seasonal and quotation matching can select it.
Used-image history remains basename based; legacy numeric history is migrated
only when the old glob ordering and complete analysed corpus are proven.

Within this regular pool, a case-insensitive `.png` suffix means `image_source`
is `generated`; other selected photographs remain `original`. Quotation
preparation turns `generated` into the existing `made_with_ai=True` value, which
is bound in the prepared main-post attempt and final X request payload. This
filename convention does not classify incoming reply media, unrelated uploads
or daily memes. The retired quote-bound generated-image pool, origin-quote
boost, spacing rule and generated-identity policy remain removed from the bot
runtime. Retired generated-image local configuration keys are ignored.

Existing quote-bound generated images, audit metadata and historical records
are preserved.
The standalone review apps, audit tools and offline future-post simulator remain
available. The simulator's generated-image experiments use dedicated offline
helpers under `tools/`; they do not enable generated-image selection in the bot.
The digest continues to interpret historical generated-image events.

## Historical Context Replies

The optional historical-context stage posts a neutral, corpus-backed threaded reply only
after a regular quotation post has been confirmed. It does not change the quotation,
image selection, schedule, or main-post receipt semantics. When enabled, startup validates
the immutable archive against the counts and packet-file hash declared by its manifest and
final status. The current archive declares 638 completed packets and five unresolved
quotations. The current 630 canonical source records are then filtered to exactly 622
attribution-eligible runtime quotations; five unresolved and three additional
attribution-ineligible records remain unavailable for posting. A quote without an eligible
completed packet receives no context reply. Replies are also suppressed when the
audited evidence would be labelled `Research incomplete` or has no usable public
source. This check applies even when the source or verification sections are
disabled. A supported source without a public URL, or an unknown occasion alone,
does not suppress the reply. The main quotation still posts normally; the context
obligation is closed without a publication retry and the digest records
`skipped_incomplete_research` with reason `incomplete_historical_research`.

Enable it in the ignored `mrsMThatcher.local.json` file:

```json
{
  "historical_context_reply": {
    "enabled": true,
    "maximum_length": 4000,
    "include_meaning": true,
    "include_source": true,
    "include_verification": true
  }
}
```

The posting path supports X long posts and does not impose an application-level
280-character limit. `maximum_length` is an explicit safety ceiling measured using X's
weighted-length rules. The production formatter is `historical_context_reply_schema_v2`:
it uses compact `Context —`, optional `Meaning —`, `Verification —`, and `Source —`
sections. Its deterministic Meaning rule omits only redundant explanation; provenance is
never shortened away. The reviewed corpus fits below 650 weighted characters.

## Single-call Conversational Replies

Mention, quote-tweet and hot-post replies use one production strategy. Existing
deterministic eligibility and scheduling run first; the bot then assembles
bounded conversation context and locally retrieved trusted facts, makes one
OpenAI Responses API call, checks schema, declared evidence and mechanical limits, and passes a
valid reply to the existing durable X-write path. A valid `no_reply` is the
editorial decision. Provider, schema and local-validation failures are
operational failures and do not count as editorial declines.

Schema and local-validation failures record the specific failed rules in
`validation_error_codes`, such as `reply_contains_mention` or
`invalid_reply_length_or_whitespace`. The structured decision event also records the
rejected proposed reply in `rejected_reply_text`, extracted only from the model's
JSON `reply` field or a rejected pending draft. It preserves whitespace and valid
Unicode, retains at most 4,000 characters, and reports the original length in
`rejected_reply_text_character_count`. `rejected_reply_text_status` is `available`,
`truncated` or `unavailable`. Unparseable responses, non-string replies and invalid
Unicode have no recoverable reply text; model reasoning, the full provider
response and exception prose are not logged.

The digest's `single_call_reply.validation_failure_details` groups failures by
rule and shows the latest 40 rejected decisions with time, lane, target ID, rule
details and the same rejected-text fields. Markdown labels this text as rejected
and not published. Counts cover the full selected window. Older events without
text or rule details remain explicitly unavailable; discarded historical replies
cannot be reconstructed. These diagnostics do not create publishable drafts or
change retry, retirement or provider-health decisions.

There is no shadow, fallback, reviewer, secondary provider, claim-audit call,
repair call or separate visual-description call. An image-bearing candidate
supplies up to two locally validated images in the same Sol request.

The single model call must identify every checkable assertion, assess its support
and list it in `factual_claims` regardless of `reply_kind`. An opinion or joke can
still contain facts. The prompt forbids invented facts and accepting unsupported
premises. When evidence is inadequate, it favours a useful non-factual response,
clarification or sensible silence. Natural courtesies, sympathy, opinions,
disagreement and humour have no closed vocabulary or template requirement.
When an account question is still live, a substantive partial answer counts as
conversational progress: the model should acknowledge only what was supplied
and may ask for the important remaining information. The existence of an earlier
question does not itself require a reply, so emoji-only reactions, repetitions
and courtesies after a completed exchange can still end in `no_reply`.
Contributor-named sources remain untrusted and cannot be described as inspected
or verified without matching trusted facts.

Deterministic validation checks the structured inventory, exact ordered claim
spans, fact-ID membership, evidence identity and durable hashes. Declared facts
must still copy a complete trusted passage; context-dependent pronouns and
relative dates in those passages are conservatively rejected. Creation and
recovery use the same passage whitespace normalization, while source-record
hashes still bind the original evidence. These checks do
not establish arbitrary semantic entailment, classify the remaining prose or
detect every omitted assertion. The model remains responsible for meaning and
inventory completeness. Fact IDs, subjective prefixes and reply-kind labels
are not evidence of support. This design does not guarantee hallucination-free
replies. The 270-weighted-character limit and duplicate, link, mention, hashtag
and emoji checks remain mandatory. There is no sentence-count limit; the model
is asked for concise natural prose. The factual inventory supports up to 32
claims, matching the bounded fact budget rather than imposing a sentence ceiling.

One fact slot is reserved for a locally defined account-identity record: the
MrsMThatcher quotation account uses AI-generated conversational replies and is
not Margaret Thatcher. This lets relevant transparency answers cite an honest
local authority through the same claim and source-hash checks as other facts.
It does not establish authorship of another account's posts. Draft recovery
resolves the same record; changes to its source or content invalidate an unsent
draft that relied on it. Previously logged sentence-limit failures remain
readable in the digest.

Draft schema 4 binds the declared claim inventory, source-record hashes and UTC
`time_context` to its hash. A changed UTC date or source timestamp invalidates a
pending draft. The natural-conversation change updates the prompt hash/cache key
without changing schema shape, model or reasoning settings. Obsolete unsent
drafts are discarded through existing regeneration logic. Frozen validation
preserves already-started and confirmed receipts, including schema-4 drafts from
`a4639e7`; a prompt change cannot authorise regeneration or reposting of those
transactions. Exact receipt/journal bindings and durable retirement proofs remain
required. Older supported receipt lifecycles remain recoverable too.

Image downloads require exact declared lengths and complete Pillow decoding,
with limits of 8,192 pixels per dimension, 16 million pixels per frame, 32 frames
and 32 million decoded frame pixels in total. Each image remains bounded to
20 MiB; the complete JSON provider request, including base64 and all images, is
bounded to 32 MiB. Malformed images never reach the provider.

A 429 receives at most one immediate retry, and only when provider metadata
specifies a delay of at most one second. Longer or unknown delays persist a
cooldown and defer the candidate. Recovered 429s also persist provider-health
and cooldown accounting before decision telemetry or another candidate call.

Immediately before the HTTP transport begins, the bot serialises the final
validated application request once, durably stores that exact UTF-8 body in an
owner-private gzip JSON record under `<MRS_BASE_DIR>/ai-request-records/`, and
sends those same bytes. Records include a unique logical `call_id`, body byte
length and SHA-256, target/lane correlation, and non-secret transport metadata.
They include the complete selected private context and inline image data URLs,
so they are private operational data, are not written to ordinary debug output,
and must not be shared as though they were public conversation exports. Ordinary
log rotation does not remove them; they remain until explicit operator cleanup.
A recording failure defers the candidate before any provider attempt and does
not count as provider-health evidence. Capture presence proves preparation, not
provider receipt or success. New pending reply drafts retain the capture
`call_id` inside their validated hash so restart recovery, sending receipts and
final posting telemetry keep the same correlation without another provider
call, including when recovery locally rejects the draft. Schema-4 drafts and
receipts created before this additive field remain
valid with capture identity explicitly unavailable; their historical hashes
are neither rewritten nor guessed.

`mrs_log_digest.py --json` and `--json-output` embed verified complete records
under `provider_requests`; `provider_request_correlations` keeps bounded event
references and `provider_request_coverage` reports complete, historical,
missing, corrupt and unmatched categories over logical calls and physical
attempts. The physical denominator is scoped to the selected log window: for
each identified logical call it uses de-duplicated attempt-start events when
present, otherwise that call's decision-time attempt metadata. Legacy decisions
without capture identity contribute their own supported attempt count; recovery
events contribute no later or lifetime attempts. Use `--request-record-dir`
only when the captures are not beneath the selected `--project-dir`. Markdown
reports coverage and previews only; it does not label a preview as the complete
model input.

Configure it through the ignored local configuration after review:

```json
{
  "single_call_reply": {
    "enabled": true,
    "strategy_version": "single-sol-reply-20260904",
    "model": "gpt-6-sol",
    "timeout_seconds": 180
  }
}
```

The global OpenAI circuit-breaker threshold is configured separately as
`"MAX_OPENAI_ERRORS_PER_WINDOW": 3`. During a controlled upgrade, the loader
accepts the retired `MAX_XAI_ERRORS_PER_WINDOW` name only as an unambiguous
migration alias; do not retain both names.

The request always uses `gpt-6-sol`, reasoning effort `high`, no temperature
override, `max_output_tokens=8192`, `store=false`, strict JSON Schema output and no tools.
The model sees at most 12 visible turns and 12,000 visible-text characters,
eight earlier same-author interactions, 30 recent confirmed conversational
replies, 32 compact trusted facts selected from at most eight evidence packets,
and two images. Cached parent context may extend to 64 verified ancestors, but
new parent lookups remain capped at three per candidate. Same-author
interactions are drawn from the existing confirmed
`ai_reply_history`; they are recorded only after remote confirmation, including
confirmed-receipt recovery.

Only a mechanically valid `reply` creates a pending draft. The compact draft is
bound to the contribution, complete canonical payload, frozen prompt and schema,
model settings, used source records and supplied images. A validated pending
draft is reusable after restart without another provider call. Drafts from old
strategies are never reinterpreted or posted after cut-over.

Production conversational replies are limited to 48 automatic replies per
day and 6 replies per author per day. The quote-tweet lane also retains its
separate 12-reply daily ceiling; that lane-specific limit is not the global
conversational-reply limit.

The following legacy audit remains offline historical tooling; it is not a
selectable production reply strategy:

```bash
python3 reply_strategy.py \
  --state bot_state.json \
  --output semantic_alignment_research/ai_first_reply_strategy_001/legacy_v1_draft_audit.json
```

The older network-free fixture, saved-history and local-retrieval evaluation is
also retained only for research:

```bash
python3 tools/evaluate_ai_first_reply_strategy.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --output-dir semantic_alignment_research/ai_first_reply_strategy_001
```

## Experimental Shadow Lifecycle

`shadow_feature_lifecycle.json` records the purpose, evidence target and current
state of each maintained shadow feature. Hybrid reply retrieval is
`offline_only`: it is absent from the production reply path, while its
deterministic index, replay and evaluation tools remain available. See
[`semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md`](semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md).

The retired generated-identity policy and promoted original-editorial selector
have no outstanding experimental review entries. The original-editorial
selector remains active when `ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING` is true:
its winner replaces the ordinary winner for an original-image selection.

## Historical Context Reply Persistence

Context replies use independent ignored runtime files:

- `historical_context_reply_receipt.json` records an in-flight or confirmed reply;
- `historical_context_reply_history.json` records completed and confirmed-failed outcomes.

The main quote remains successful if context delivery fails. A confirmed context receipt
is reconciled on resume without reposting either the quote or reply. An ambiguous sending
receipt requires operator reconciliation and is not automatically duplicated. The bot's
own context replies are excluded from mention and hot-post reply processing.

Receipts and history retain formatter version, template variant, Meaning decision, length,
verification, source class, and historical confidence. Legacy records without this metadata
remain valid and are treated as formatter v1 by offline analytics.

`mrs_log_digest.py` reports structured context outcomes by status, including completed,
already-completed, failed, skipped and dry-run events.

## Architecture And Python API

Start with the [code reading guide](docs/code_reading_guide.md) for the bot and
digest execution paths and the next implementation file to read.

The maintained module map, side-effect boundaries, active corpus accounting and
PEP 257 documentation policy are recorded in
[`docs/python_api.md`](docs/python_api.md). Check module and public-definition
docstring coverage without additional dependencies. Pytest also runs this as
a mandatory session prerequisite before collecting tests:

```bash
python3 tools/check_python_documentation.py
```

## Local Runtime Files

`mrsMThatcher.local.example.json` is a sanitized example of the optional local
config file. Optional production and shadow features are represented with safe,
source-default-disabled values. To use those settings on a host, copy it to the
untracked runtime name:

```bash
cp mrsMThatcher.local.example.json mrsMThatcher.local.json
```

`extra_quote_watch_post_ids.example.txt` documents the optional extra quote
watch-list format. To use it, copy it to the untracked runtime name and add one
post ID per line:

```bash
cp extra_quote_watch_post_ids.example.txt extra_quote_watch_post_ids.txt
```

Quote discovery combines the five most recent main posts (including memes) and
any extra watched posts in a recent-search query using `quotes_of_tweet_id:`
joined with `OR`, excluding retweets. Results are matched to their original post;
watch-list priority, oldest-first processing within each original, reply delays,
duplicate checks and reply limits still apply. Originals without search results
need no context lookup.

Recent search covers quotes created during the last seven days, even when the
original is older. An outage longer than that window can leave older quotes
undiscovered. There is no `since_id` cutoff, so deferred quotes remain eligible
for later checks. Up to ten IDs fit in each query; larger configured watch lists
use additional batches. `QUOTE_LOOKUP_API_MAX_RESULTS` controls page size, and
each batch gets `QUOTE_LOOKUP_MAX_PAGES_PER_POST` times its number of originals
as its page allowance. An incomplete combined search switches to bounded
per-original recent searches so a busy lower-priority post cannot hide another
watched post's quotes. Pending per-original searches resume on later checks;
these busy checks can require more requests than a complete combined search.
Search continuations are saved separately and discarded when their query changes.
The legacy per-post lookup helper remains available
for diagnostics; the reply cycle does not fall back to the faulty endpoint.

The real `mrsMThatcher.local.json` and `extra_quote_watch_post_ids.txt` are
ignored because they are host-local operational inputs. Generated runtime state
and logs such as `bot_state.json*`, `lines_used.json`, `images_used.json`,
legacy `*.pickle` history artefacts, and `mrsMThatcher.log*` are also
intentionally ignored.

Module imports use source defaults and do not load host-local configuration.
Executable bot entry points call an explicit production bootstrap. An absent
`mrsMThatcher.local.json` permits defaults; if the file exists, any read,
JSON, type, value, or cross-field validation error aborts startup rather than
falling back to defaults.

The optional `mrsMThatcher.control.json` is fail-safe. A transient invalid read
retains the last valid control document. If no valid document has been read,
an existing invalid control file acts as `disable_all` until repaired. A
genuinely absent control file means no runtime pause.
Control timestamps accept integer epochs or documented date/time strings;
booleans, numeric strings, fractional/non-finite values, and out-of-range
epochs invalidate the complete control document.
The bot and digest share these validation rules. If `generation` is present,
it must be a non-negative integer; `null` is invalid. Both use the bot's
existing maximum timestamp of `4102531200`.

Durable bot state uses reader version 5 with an embedded monotonic generation
and content digest. Startup selects the newest complete sealed generation and
repairs replicas; failure to publish `.bak1` cannot invalidate a committed primary.
Legacy state migrates under the singleton/state lock only when its authority is
unambiguous. Conflicting legacy documents require operator reconciliation; the
loader does not guess from backup filenames. Older readers are fenced out.

State and used histories are bounded to 64 MiB of encoded JSON. Saves reject
non-finite numbers, invalid state and over-limit growth before replacing existing
authority. Durable identity collections are retained rather than silently pruned.
Receipts and transport journals are retired only with exact, fsynced state and
history proofs, rechecked at destructive boundaries. Files must be owned by the
runtime UID and never writable by group/others; new files use mode 0600 and state
directories must be owned and protected from other writers. See
[the durability remediation notes](docs/fresh-eyes-state-remediation.md) for the
migration and failure-boundary contract.

Normal operational commands require `bot_state.json` (or a valid configured
backup), `lines_used.json`, and `images_used.json`. They refuse to infer a new
installation from missing files. For a genuinely empty new project directory,
run `python3 mrsMThatcher2.py --initialise` once; it creates the durable set and
an installation marker but does not start posting or call an API.

## Reading long X posts

Mentions, quote searches, watched-post replies and individual post lookups request
`note_tweet` and use its complete text when present. Long-post entities are used
alongside X's implicit reply-recipient mentions, so full-text reading does not
lose direct reply eligibility. Short posts continue to use ordinary `text`.

Complete text is retained in the tweet cache and durable mention queue. External
cache entries and queued mentions saved before this change are refreshed before
reuse, including an earlier question used to recognise a clarification; deleted queued posts are retired and temporary lookup failures remain
retryable. Refreshing old parent context counts against the existing network
lookup budget. Locally authored quote, meme and reply text stays available from
the cache without a migration lookup. Confirmed reply history is preserved.

The existing model-input limits still apply: up to 10,000 characters for the
incoming contribution and 12,000 across the visible conversation. This fixes
X's short excerpt being used as the source; it does not remove those bounds.

## Retired question trial

The quotation question trial has been removed. Ordinary quotation selection uses
all eligible unused quotes, with no experimental reservations or appended
questions. The Home Assistant trial sensor and notification automation are also
retired. Historical measurements remain in the analytics database, and generic
collection continues for already tracked posts.

Before deploying this removal, remove the three retired local configuration keys
(`engagement_question_experiment_enabled`,
`engagement_question_experiment_plan_path`, and
`engagement_question_notification_output_path`). Archive the ignored live plan
and Home Assistant notification document outside the checkout. Preserve saved
bot state and analytics data; the retired state field is inert. Confirm that no
active media, main-post, or transport receipt from the trial remains; retired
trial attempt/receipt versions are rejected rather than replayed.

Remove only the corresponding Home Assistant command-line sensor and automation
from its live YAML, validate the configuration, and reload Home Assistant.

## Log Digest Operation

Digest JSON schema version 4 removes trial state, outcomes and correlations.
Generic quote publication warnings now appear under `quote_publication`;
confirmed historical public text remains available.

`--max-text` limits display previews. It does not truncate identifiers, status
codes or other values used to count and correlate events. Analysis uses validated,
independently bounded fields before previews are shortened, so changing the
display limit does not change failure counts or publication matching. Exact
confirmed public text and bounded rejected-reply diagnostics retain their own
limits.

`mrs_log_digest.py` resolves project metadata through `--project-dir` (the
repository/script directory by default), including when logs are passed by
absolute path from another working directory. `--output PATH` atomically writes
a report; explicit relative output paths resolve against the current working
directory, independently of `--project-dir`. Otherwise output is written and
flushed to stdout. Markdown and JSON both use bounded durable receipt/history evidence. Markdown
shows confirmed public text previews and text-health conflicts; `--verbose-replies`
also includes full exact confirmed text. Snapshot-only evidence does not count as
a new publication in the selected log window. Resume state is
advanced only after complete analysis, rendering, and successful report
delivery.
Supplying the canonical `mrsMThatcher.log` explicitly also includes numeric
siblings such as `.1` and `.2`, but excludes self-test and unrelated files.
Stateful digest runs use a separate nonblocking resume lock. Normal digest runs
do not inventory the retired generated-image pool or calculate its current
runway. The existing pool-health, post-rate, runway and utilisation JSON sections
report `available: false` and `status: "retired"`. Historical generated-image
selection, spacing and identity-policy observations remain available; standalone
analysis helpers can still inspect archived pools explicitly.

`meme_queue_health` reports the current daily-meme inventory under the selected
project directory: candidate images, already-posted filenames in the current
cycle, unposted images, and whether automatic recycling permits another
selection. `exhausted` means every candidate has been posted and recycling is
disabled; `recycling_available` means the next selection may start another cycle.
Missing, empty or uninspectable inputs are reported explicitly. The snapshot is
read-only and never resets meme history. It uses current state and local
configuration plus literal source defaults without importing the bot; the
configuration describes files on disk, not proof that a running process has
reloaded them. Observation times distinguish this snapshot from the selected log
window. Historical exhaustion/no-image messages also appear in `asset_health` and
`events`, and recycling messages appear in `events`. Zero meme publications
alone do not establish exhaustion. JSON headlines and Markdown show the current
availability alongside these historical observations.

Enable automatic meme recycling by setting
`"RESET_MEME_CYCLE_WHEN_ALL_POSTED": true` in the ignored
`mrsMThatcher.local.json`, then use the normal controlled bot restart. At the
next selection after exhaustion, the bot clears the used-filename cycle history
and starts again at the first candidate. The digest never performs this reset;
the tracked default remains disabled.

Each digest invocation also takes a read-only, no-follow snapshot of the active
remote-write protocol: activation pair, ambiguity markers, source-receipt
retirement ledgers, transport journal/fence, media-upload receipt/fence,
operator control generation, and reconciliation archive. X transport failures
are attributed to the exact preceding request endpoint, so `/2/media/upload`
is not reported as a mention or tweet-create failure. Historical ambiguous-write
cascades are grouped into one incident and are marked resolved only when a
later mode-0400 reconciliation audit is hash-bound to its archived evidence and
the current barrier namespace is clear. The digest never reconciles, retires,
or writes any of this protocol state.

Source-image validation failures before media receipt publication are local
preflight failures. Repeated errors for the same lane and image form one
incident; a later successful publication for that image resolves it. The digest
also recognises older mislabelled ambiguity errors when their full traceback
proves this pre-publication source-image failure. Historical error records remain
visible; genuine uncertain uploads still require reconciliation evidence.

An X POST transport timeout is not proof of failure: X may have accepted the
write. A new ambiguous outcome first creates and synchronises
`ambiguous_post_outcome.restart_barrier.json`, then adds the same-inode
compatibility name `ambiguous_post_outcome.json`. Either name blocks further
posting until an operator reconciles the incident; the bot does not claim
exactly-once delivery.

The current transport-authority protocol is enabled by the exact read-only
runtime pair `.mrs_remote_write_safety_protocol_v2` and
`.mrs_remote_write_safety_protocol_v2.activation_audit.json`. Missing,
malformed, replaced, unreadable or mismatched pair state blocks every remote
lane in every process; a bare sentinel is not silently treated as activation.
The pair is cross-revalidated after both stable no-follow reads, so files from
different namespace generations cannot be composed into permission. The v1
sentinel and audit remain part of the inspected namespace only as forbidden
legacy state. A v2 runtime refuses either v1 name, and the stopped activator
durably removes the v1 permission sentinel before its audit and publishes the
v2 audit before the v2 sentinel. Every crash point in that migration therefore
leaves both old and new runtimes unable to write.

Local namespace absence is not accepted as proof that an installation is new:
`--initialise` creates durable state but deliberately leaves every remote lane
disabled. Every installation must then be activated only while the user
service, wrapper and Python child are all stopped. First reconcile every active
ambiguity marker and every transaction object: all four source receipts, the
transport journal/fence, the media receipt/fence, transition or retirement
guard prefixes, and the five fixed retirement auxiliaries for each source
receipt. Create an external canonical clean-state/reconciliation attestation.
That immutable 0400 file must name the exact project path/device/inode, exact
state inventory, exact activator CLI SHA-256 and an operator reconciliation
reference. Run `tools/activate_remote_write_safety_protocol.py` with its exact
SHA-256, the preflight-bound project identity and both explicit confirmations.
The activator validates the attestation's bytes and bindings but does not
pretend to prove the operator's clean-state assertion. It takes the complete
instance-lock boundary and refuses either ambiguity name or any unresolved
exact or prefixed transaction entry. A legacy-only ambiguity marker is never
migrated by the running daemon.

Protocol activation is a one-way runtime compatibility boundary. Never start a
pre-v2 binary against that activated state directory; any rollback must remain
stopped until both protocol generations, every marker and every transaction
barrier have been reviewed under the same lock-bound offline procedure. The
activation pair is mutable
runtime state rather than repository content. When the state directory is also a Git
checkout, record a deployment-local exclusion in that checkout's
`.git/info/exclude`; do not modify or overwrite an unrelated tracked
`.gitignore` change merely to hide it.

Every regular, meme, conversational-reply and historical-context reply source
receipt is a strict, no-follow global remote-write barrier. Duplicate JSON
names, non-finite values, noncanonical bytes, symbolic links, directories,
FIFOs and replacement generations block rather than becoming absence. The lane
validates the exact source identity and bytes while publishing a
payload/source/inode-bound `remote_write_transport_journal.json` and independent
`remote_write_transport_fence.json`. The final transport authority revalidates
and consumes the journal/fence pair. Within the cooperative single-instance,
same-state-owner protocol, every supported writer of reserved transaction names
holds the same instance lock, and destructive transaction mutations are
serialized within the owning process. A mutation already visible at a
documented revalidation boundary stops transport; source loss after
journal/fence publication remains restart-blocking. These guarantees and the
one-path fault-injection cases do not cover a non-cooperating same-UID actor
that creates, replaces or removes a reserved name between a stable identity
check and the following pathname syscall, or that mutates the state directory
outside the lock protocol. Confirmation is recorded in the journal before the
exact bound source inode may be promoted, and confirmed source lineage is
rechecked before histories, schedules or receipt retirement change.

Quote-image and meme media upload uses its own
`remote_media_upload_receipt.json` and `.fence.json` pair. After upload, the
confirmed media handoff and receipt retirement occur only beneath an already
prepared main-post journal/fence. An unproved v2 upload outcome remains
ambiguous and restart-visible; the legacy v1.1 upload helper refuses before
transport and is never an automatic fallback. Raw X, media and provider
transports receive no unbound authority and cannot bypass an unresolved
transaction object.

Image files and their immediate containing directories must be owned by the bot
user and must not permit group or public writes. A source-image failure before
receipt publication raises `MediaUploadPreflightError` and uses the ordinary
local-failure scheduling delay. Failures involving existing receipts or begun
receipt publication retain their remote-write barrier handling.

One narrower stopped recovery exists for an ambiguous media upload before any
tweet-create attempt. `tools/reconcile_remote_write_safety_marker.py
--reconcile-unattached-media-upload` requires the exact reviewed marker hash,
media transaction ID, receipt and fence hashes, and each file's device, inode
and ctime. It accepts only a paired media-only ambiguity marker, canonical
`sending` receipt/fence documents with no remote media ID, and complete absence
of every source receipt and tweet journal/fence name. The operator must
separately attest both that no `POST /2/tweets` request was attempted and that
any possibly accepted but unattached media object is abandoned. The operation
archives the exact receipt/fence inodes and a read-only audit before retiring
the active media pair; it does not remove either ambiguity-marker name. Review
that audit and then invoke the normal marker-reconciliation mode as a separate
offline operation. Any other incident shape remains unsupported and blocked.

A separate stopped recovery exists when authenticated read-only X evidence has
already proved that an ambiguous conversational reply or quote/image main post
was published, but the local transport still contains an `attempting_pair`.
The operator, not the tool, must first review the remote result and retain a
credential-free evidence file. Stop the service and wait for the wrapper and
Python child to exit, then run
`tools/reconcile_remote_write_safety_marker.py` with either
`--adopt-externally-confirmed-reply --check-only` or
`--adopt-externally-confirmed-main-post --check-only` before any applying run.
The evidence must be a current-user-owned regular file which is not group/world
writable. The check acquires the same complete offline lock boundary as apply,
performs no network request, creates no archive, and changes no file identity,
bytes or timestamps.

The command requires exact operator-recorded marker, source receipt, transport
journal and fence hashes, devices, inodes, ctimes and sizes. It also requires
the receipt candidate lane, target and text hash; the transport transaction,
lane and canonical-payload hash; the numeric published post ID and confirmation
epoch; and the evidence pathname and SHA-256. Apply mode additionally requires
both
`--confirm-external-publication-reviewed` and
`--confirm-offline-reconciliation-complete`; check-only still requires the
publication-review acknowledgement because it validates a specifically
attested published outcome. This mode accepts only a sending conversational
source from `mention`, `hot_post_reply` or `quote_tweet`, or an attempting
`regular_post_receipt.json` whose lane is exactly `quote_image`, root target is
empty, and media IDs match the ambiguity marker. It does not accept daily memes,
other ordinary posts or a not-published disposition.

Under one continuously held lock set, apply archives the exact evidence,
publishes a prepared audit, transforms the exact attempting journal/fence into
the ordinary externally-provenanced `confirmed_pair`, publishes a completion
audit, and then invokes the existing marker archival transition with the
restart barrier removed last. An identical rerun can resume after the prepared
audit, confirmed transport or completion audit. It also recognises the sole
exact staging generation left by process loss immediately before or after the
journal `RENAME_EXCHANGE`: check-only reports the precise side of the exchange,
and apply either completes the exchange or retires only the proved displaced
attempting inode. Any extra or conflicting staging generation, post, evidence,
payload or source identity refuses. External-adoption flags, including
`--check-only`, are invalid with the separate unattached-media mode. A
successful return leaves the marker names absent, the exact sending/attempting
source receipt present, and the confirmed transport pair bound to the reviewed
post. It deliberately does not retire the receipt or transport.

Restart and read-only digest verification are a separate reviewed operational
step. Normal startup binds the confirmed transport to the exact source,
promotes the receipt, records the already-published reply or main post, and
retires both through their ordinary lane-owned path without another X create
request. Never manually edit, replace, rename or delete a marker, receipt,
journal, fence or adoption audit to imitate or complete this recovery.

Destructive journal, media and source-retirement helpers do not trust their
caller. Each call requires a narrow `TransactionMutationAuthority` issued from
the exact live instance-lock verifier, and every use re-runs that verifier
before inspecting or mutating the namespace. `TransactionMutationAuthority` is
cooperative admission proof, not an operating-system capability or a
conditional-unlink primitive. It excludes supported competing writers only
together with the shared lock and in-process serialization. Source receipt
retirement uses fixed guard, commit, cleanup and two staging names. Before final
committed cleanup removal, supported hard exits leave an inventoried barrier.
If the final unlink succeeds but its directory fsync is not acknowledged, the
current daemon latches fail closed; a fresh marker-only resumer deliberately
does not infer completion from all-absent state without separate idempotence
authority.

Definitely unsuccessful sending attempts record that disposition in the exact
retirement guards and completion ledger. Restart recovery binds it to the
receipt digest and original file identity, and refuses an overlapping transport
journal. Confirmed posts still require their committed-state proof; legacy
markers without a disposition remain readable but do not acquire failed-attempt
authority. Older binaries reject the extended failed-attempt bindings; preserve
those ledgers during any rollback.

Fail-closed does not imply complete automatic recovery. An exact staging entry
left around an identity-bound exchange, or a partially completed offline marker
archive, can require deterministic operator inspection and rerun. Preserve
every source, journal, fence, guard, commit, cleanup and staging entry until the
reviewed recovery path proves its ownership; never delete a blocker merely to
restore availability.

The safety marker must never be removed while the daemon is running. After an
operator has independently reconciled the remote outcome, stop the service and
wait for its process to exit, record the marker's SHA-256, then use
`tools/reconcile_remote_write_safety_marker.py` with that exact hash and
`--confirm-offline-reconciliation-complete`. The tool refuses while any of the
daemon's shared state-directory, abstract-socket, open-file-description or
BSD-flock boundaries is held and rejects symbolic links. It creates a
no-replace same-inode archive link, makes that archive and its audit receipt
durable while every active barrier still exists, removes the legacy marker
first, and retires the restart barrier last. A successor which alone survives
legacy-marker loss remains an active, reconcilable barrier. A running process
latches immediately when it observes either namespace entry; failed inspection
or durability acknowledgement remains an independent in-memory blocker. Never
remove or replace either active name outside the stopped, lock-bound
reconciler. The archival command records an operator reference but does not
itself determine whether the X outcome has been reconciled.

An abrupt loss of the reconciliation command after its archive or receipt has
become durable remains fail-closed: a surviving active name or extra archive
link still blocks the daemon. The command does not automatically resume that
partially completed archival state; an operator must preserve and review its
archive, receipt and active-name evidence before a separately reviewed
recovery. This is an availability limitation, not permission to delete either
barrier manually.

A legacy-only marker from an older release must remain in place until exact
stopped reconciliation. The running daemon refuses to migrate or acknowledge
it. If the sole legacy name is lost before reconciliation, protocol activation
remains absent, so every later compatible process stays blocked without relying
on process memory. Only the stopped, lock-bound activator may establish the
clean activation pair after all marker and receipt evidence is absent or has
been reconciled and an external hash-bound operator attestation records that
conclusion. Pathname absence alone is not treated as proof that an older marker
was never lost.

The daemon first opens and exclusively locks the state-directory inode. That
kernel file lock is shared across lexical aliases and network namespaces. It
then binds a supplementary Linux abstract-socket singleton derived from the
directory's device and inode and opens `mrsMThatcher.lock` relative to the held
directory without following links. The file identity is held for the process
lifetime using both an open-file-description write lock and BSD flock. Every
non-read remote boundary uses Linux `/proc/self/fdinfo` to prove that each
designated descriptor itself owns its exclusive flock, proves that the
designated file descriptor owns the exclusive OFD lock, checks separate
descriptors are excluded from the file and directory, and revalidates both path
identities and the supplementary socket. Missing or unrecognised `fdinfo`
records fail closed. Focused tests exercise the same-inode exclusion mechanism
through aliases and path replacement; the deployment host must also retain a
readable Linux `/proc` because this development host cannot directly create a
second network namespace. The test bypass applies only to explicit loopback endpoints;
custom external endpoints and the live-endpoint override require the real
lock. Do not rename, replace or hard-link the lock while the daemon is running.
The offline reconciler rejects linked files, a live recorded daemon PID and
raced project, lock and archive names in addition to requiring the same
directory, file and supplementary socket boundaries.

Generated utilisation remains bounded by available structured logs: “ever
used” is not an account-lifetime claim. Rate sections report calendar span,
observed logging time, largest detected gap, and coverage quality; material
gaps make observed runway estimates unavailable rather than falsely precise.

### OpenAI published-cost cache

`openai_cost_cache.py` makes a read-only administration call to OpenAI's
organization Costs API and stores provider-published daily UTC totals at:

```text
~/.local/state/mrsMThatcher/openai-costs/daily_costs.json
```

The private cache retains bounded cumulative samples and refreshes the current
UTC date plus the preceding six dates so delayed or corrected published costs
can settle. `mrs_log_digest.py` remains entirely network-free: it reads this
cache and estimates a selected log window from bracketing sample deltas without
interpolation or model-list-price reconstruction. Sample spacing can include a
small amount immediately outside the requested window, and missing boundaries
produce partial coverage or `unknown`, never a zero-cost fallback. OpenAI
per-call and per-stage rows remain `unknown` because daily cost is not allocated
back to individual calls.

Set the collector-only `OPENAI_COST_PROJECT_ID` when the configured project is
the bot's OpenAI project. The digest can then label that project's sample-delta
estimate as project scoped. With no configured project, reporting is explicitly
organization-wide and is not described as bot-exclusive.

## Launcher And Private Environment

`mrsMThatcher.env.example` is a sanitized example of the private live
environment file. It lists the required X/OpenAI environment variables with
placeholder values.

For live use, copy it to the ignored runtime name, edit that copy, and keep it
readable only by the bot user:

```bash
cp mrsMThatcher.env.example mrsMThatcher.env
chmod 600 mrsMThatcher.env
```

`/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py` is the canonical production
bot script. `runMrsMThatcher2` is the tracked live launcher. It contains no
secrets, sources the ignored `mrsMThatcher.env` file, and runs that
repository-side script from its configured `WORK_DIR` by default. Systemd
invokes the tracked launcher directly at
`/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2`. `MRS_BOT_SCRIPT` is a
test-only launcher hook and is honoured only with `MRS_TEST_MODE=1`; production
fails closed if either the inherited environment or the private env file sets
it. If the Python process exits, the launcher waits 60 seconds
before restarting it; the bot's ordinary scheduling still happens inside the
Python process.

The real `mrsMThatcher.env` is intentionally ignored by Git. Do not commit live
API credentials.

## User Systemd Units

Canonical user units are versioned under `deploy/systemd-user/`. Check the live
installation for drift with:

```bash
deploy/systemd-user/install.sh --check
```

The optional network-free version-7 prospective conversation collector
reconstructs account roots, historical-context replies, and exact same-author
parent paths from the retained production log rotation without touching the
bot or its state. It also retains bounded, content-free reply-photo collection
and visual-analysis metadata for review. Its scheduled private root is
`/disks/disk1/research/mrsMThatcher-prospective-conversations-v7`; the existing
version-6 root remains a read-only migration source. Its operation, privacy
model, registered v6-to-v7 rebuild, validation, manual review packs, and
controlled timer activation are documented in the
[prospective conversation extractor runbook](docs/prospective_conversation_extractor.md).

Install updated units as regular files, prepare non-migration scheduled-task
state, and reload the user manager with:

```bash
deploy/systemd-user/install.sh --install
```

The main service preflight checks the canonical repository-side
`/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py` and tracked launcher, then
starts `/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2` directly. The launcher
executes the same checked bot script by default.

The installer uses atomic per-file replacement and runs `daemon-reload`, but it
does not enable, disable, start, stop, or restart any unit. Its non-mutating
first-v7 installation also leaves the prospective v7 root absent for the
registered rebuild; prospective timer activation is intentionally omitted from
its suggested commands. The non-mutating analytics `status` check always
targets the runtime checkout at
`/disks/disk1/etc/mrsMThatcher`, even when the installer itself is run from a
different source worktree. It reports an initialised database, a valid but
uninitialised database, or a status-command/malformed-output failure distinctly.
For an uninitialised database it prints the initialisation command:

```bash
/usr/bin/python3 /disks/disk1/etc/mrsMThatcher/mrs_engagement_analytics.py initialise \
  --project-dir /disks/disk1/etc/mrsMThatcher
```

The small same-host Home Assistant health facility, including progress and
monitor paths, status meanings, thresholds, timer operations, and its explicit
read-only limits, is documented in the
[bot health runbook](docs/bot_health_home_assistant.md).

The independent, read-only status of scheduled supporting jobs and the website
downloader is documented separately in the
[support health runbook](docs/support_health_home_assistant.md). Supporting-job
problems never change the foreground bot-health sensor.

After installation, enable each desired unit explicitly and separately (enable
the analytics timer only after its database is ready):

```bash
systemctl --user enable mrsMThatcher.service
systemctl --user enable --now mrs-bot-health-monitor.timer
systemctl --user enable mrs-engagement-analytics.timer
systemctl --user enable --now mrs-openai-cost-cache.timer
```

The OpenAI published-cost user timer runs every 30 minutes with up to 60 seconds
of randomized delay. Its oneshot service sources the existing private
`mrsMThatcher.env`; the Admin key is never copied into a unit or the cache.

Systemd supports linked unit files, but the repository is on
`/disks/disk1`, a separately mounted ZFS dataset. A lingering user manager may
scan `default.target` before that dataset is available. In that case a symlinked
unit cannot be read, so its bounded `ExecStartPre` mount wait cannot run. The
live units are therefore deliberate copies in `~/.config/systemd/user/`, with
the tracked installer providing drift detection and reproducible installation.

Boot-before-login operation also requires user lingering. Check it with:

```bash
loginctl show-user "$USER" -p Linger
```

## Routine deployment of an already-tested commit

This is the canonical production procedure when the target commit has already
been reviewed, tested, committed and pushed. A new or untested code change must
complete its appropriate validation before it becomes an approved deployment
candidate. Deployment does not require rerunning the complete test suite.

For a release containing only human-readable documentation changes, inspect the
entire diff from production `HEAD` to the approved target, not just the latest
commit. If every changed file is documentation (not a runtime prompt, input,
configuration or generated data), fetch and fast-forward the clean production
`master` to that pushed commit, then verify the resulting revision and clean
status. Do not pause, stop, restart or reload anything, change runtime controls,
or rerun tests solely for that documentation update. Any code or runtime-data
change in the pending range requires the normal procedure below.

Changes to canonical historical evidence or the generated corpus use the
[historical-context evidence runbook](docs/historical_context_evidence_release_runbook.md)
for content validation.

1. Record the approved commit SHA. Fetch the remote, then confirm that the
   production checkout is clean, on `master`, and that its current commit is an
   ancestor of the approved commit. Preserve ignored and untracked operational
   files. Stop if the update is not a clean fast-forward.
2. Before changing `mrsMThatcher.control.json`, preserve its exact original
   bytes and metadata in a private location, or record that it is absent.
3. From the valid existing control document, or `{}` when it is absent, add the
   supported global `"pause_all": true` control without changing any other
   setting. Write a private temporary file in the same directory, apply the
   intended ownership and mode, make it durable, and publish it with an atomic
   replacement; never edit the live file in place. Stop if the existing control
   document cannot be safely preserved.
4. From a log boundary recorded after that replacement, wait for the running
   bot to acknowledge exactly:

   ```text
   Global runtime control pause is active; all remote-write lanes remain idle
   ```

5. Stop only `mrsMThatcher.service`. Verify that its wrapper and Python child
   have both exited before changing any tracked file; leave every other unit
   running unless a release-specific support-service migration below explicitly
   requires that unit to be quiesced.
6. With the service stopped, make one private, metadata-preserving backup of
   the operational runtime files outside the checkout. This is the single
   quiescent deployment backup; do not take duplicated live and stopped
   snapshots for a routine deployment.
7. Fetch again and update production `master` to the approved commit using
   fast-forward-only Git operations. Never use reset, force, rebase or
   `git clean` in production.
8. Start `mrsMThatcher.service` while the global pause remains active.
9. Confirm successful startup, exactly one wrapper and one Python child, the
   correct instance lock, no traceback and no restart loop. A process started
   with the global pause already active initializes its pause-log
   de-duplication state from that condition, so it does not repeat the step 4
   transition acknowledgement. Do not wait for that line a second time.
   Verify that the live control still contains `"pause_all": true` and confirm
   the paused-start safeguard message:

   ```text
   Global runtime control pause is active; leaving main-post receipts untouched during startup
   ```

   together with `Bot started successfully`. Do not clear the pause until all
   of these checks pass.
10. Atomically restore the exact original control-file bytes and metadata, or
    atomically restore its absence if it was originally absent. Do not
    reconstruct an equivalent JSON document.
11. Monitor normal operation for a few minutes after the original control state
    is restored, including the service topology and logs.
12. If a code rollback is required, preserve the current durable runtime state.
    Never overwrite it with the deployment backup, which may already be stale;
    use a separately reviewed compatibility or migration recovery procedure
    when the earlier code cannot consume the current state.

For the first deployment containing prospective extractor schema version 7,
disable and stop `mrs-prospective-conversations.timer` before step 7 and verify
that no extractor oneshot remains active. Keep it inactive through the checkout
update and follow the ordered v6-to-v7 rebuild, validation, manual oneshot and
corpus inspection in the
[prospective conversation extractor runbook](docs/prospective_conversation_extractor.md).
Enable the hourly timer only after those checks succeed. This exception applies
to that support collector only; it does not authorise stopping other units.

The pause acknowledgement, complete service shutdown, single runtime backup,
fast-forward-only update, paused startup verification, exact control-state
restoration and no-stale-state rollback rule are mandatory safeguards. They do
not weaken the remote-write protocol, receipt handling or state compatibility
rules.

## Deployment Smoke Test

This smoke test is for validating new or changed deployment plumbing. It is not
an additional requirement for the routine procedure above unless the target
commit's validation plan specifically requires it.

The canonical production bot script and launcher are the tracked repository
files. Ensure both are executable:

```bash
chmod +x /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2 \
  /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

The previous `/usr/local/bin/mrsMThatcher2.py` and
`/usr/local/bin/runMrsMThatcher2` symlinks are no longer required and should not
be part of the normal deployment procedure. Systemd uses the tracked launcher
directly.

Compile-check the canonical repository file directly:

```bash
PYTHONPYCACHEPREFIX=/tmp/mrs-pycache python3 -m py_compile /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

Source the live environment, then run the bot self-test:

```bash
set -a
source /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
set +a
python3 /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py --self-test
```

The self-test validates local configuration, credentials and core installation
assets without importing provider research SDKs or loading the quotation research
tree. Operational corpus validation remains fail closed on the first real reply
candidate (and at startup for an enabled historical-context stage).

Restart the live service using the normal service manager for this host.
After restart, check the live log for startup config and safety markers:

```bash
grep -E "Bot starting|Base dir=|State file=|Log file=|X base=|X upload base=|OpenAI base=|Config:" /disks/disk1/etc/mrsMThatcher/mrsMThatcher.log | tail -80
```

The deployment smoke test should not use `MRS_TEST_MODE=1`; that mode is only
for isolated local harness runs.

## Read-Only Engagement Analytics

`mrs_engagement_analytics.py` is an optional standalone collector for fixed-age
metrics on quotation posts and their historical-context replies. It uses an
isolated SQLite database under `engagement_analytics/`, has no X write method,
and never imports or changes the posting loop, receipts, histories, schedules,
or production cooldowns. Live metric reads require an explicit `--execute-read`.

See [`engagement_analytics/README.md`](engagement_analytics/README.md) for the
initialisation, discovery, dry-run, collection, report, export, bounded
backfill, and optional user-level systemd timer commands.
