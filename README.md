# MrsMThatcher Bot

## Local Integration Harness

Run the safe local integration tests with:

```bash
python3 -m pip install -r requirements.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_integration_harness.py
```

The tests start a fake local API server and run the real bot script with
`--test-cycle`, `--test-post-quote`, and `--test-post-meme`.
All bot state is created under pytest temporary directories. The harness sets:

- `MRS_TEST_MODE=1`
- `MRS_BASE_DIR=<tmp test state dir>`
- `MRS_LOG_FILE=<tmp test state dir>/test.log`
- `X_API_BASE_URL=<fake server>`
- `X_UPLOAD_BASE_URL=<fake server>`
- `XAI_API_BASE_URL=<fake server>/v1`

The complete offline test and research-tool dependency set is recorded in
`requirements-dev.txt`. Install it when running the full repository suite:

```bash
python3 -m pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

On the four-core production host, the coverage-equivalent fast path uses three
isolated pytest workers:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  -p xdist.plugin -n 3 --dist=worksteal --max-worker-restart=0
```

Do not run serial and parallel suites concurrently in the same checkout.

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
export XAI_API_BASE_URL="$FAKE_API_URL/v1"
export X_CONSUMER_KEY=dummy
export X_CONSUMER_SECRET=dummy
export X_ACCESS_TOKEN=dummy
export X_ACCESS_SECRET=dummy
export X_MY_USER_ID=12345
export XAI_API_KEY=dummy
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

The base-directory, log, primary X API, and xAI defaults remain as listed
below. Media upload routing is the exception: `X_UPLOAD_BASE_URL` no longer
defaults to the legacy `https://upload.twitter.com` origin used with
`/1.1/media/upload.json`. When unset, it now inherits the resolved
`X_API_BASE_URL`, and the exact `POST /2/media/upload` request uses that origin.
X reads and `POST /2/tweets` continue to use `X_API_BASE_URL`.

- `MRS_BASE_DIR` defaults to `/disks/disk1/etc/mrsMThatcher`
- `MRS_LOG_FILE` defaults to `<MRS_BASE_DIR>/mrsMThatcher.log`
- `X_API_BASE_URL` defaults to `https://api.x.com`
- `X_UPLOAD_BASE_URL` inherits the resolved `X_API_BASE_URL` when unset
- `XAI_API_BASE_URL` defaults to `https://api.x.ai/v1`

Safety guards:

- Operational entry points require a successful explicit `production_bootstrap()` call; importing the module alone never enables them.
- Production bootstrap configures the rotating production file log explicitly. Tests must pass `configure_file_logging=False` or an isolated `log_path`; module-owned handlers are replaced and closed on reconfiguration.
- If `MRS_TEST_MODE=1` and `MRS_BASE_DIR` resolves to the production directory or a child of it, the bot aborts before logging or state writes.
- If `MRS_TEST_MODE=1` and `MRS_LOG_FILE` resolves under the production directory, the bot aborts.
- If `MRS_TEST_MODE=1` and any API base URL still points at live X/xAI hosts, the bot aborts unless `MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST=I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X` is set deliberately.

Endpoint override convention:

- `X_API_BASE_URL` must be an origin only (scheme, host and optional port), with no path, query, fragment or user information; the bot appends `/2/...` endpoint paths.
- `X_UPLOAD_BASE_URL` must likewise be an origin only. It is used only for the exact literal `POST /2/media/upload`; reads and `POST /2/tweets` continue to use `X_API_BASE_URL`. When unset it inherits the resolved `X_API_BASE_URL`. The bot does not fall back to a second legacy upload endpoint after an uncertain outcome.
- `XAI_API_BASE_URL` should include `/v1` when the fake server exposes `/v1/chat/completions`.

Scenario fixtures live in `tests/fixtures/scenarios/`. The fake server implements only the endpoints the bot currently uses:

- `GET /2/users/{id}/mentions`
- `GET /2/tweets/search/recent`
- `GET /2/tweets/{id}/quote_tweets`
- `GET /2/tweets/{id}`
- `POST /2/tweets`
- `POST /2/media/upload`
- `POST /v1/chat/completions`

## Files To Keep Together

The production runtime and its operational support assume these files are
deployed as a coherent set:

- `mrsMThatcher2.py`
- `remote_write_safety_protocol.py`
- `remote_media_upload_receipt.py`
- `remote_write_transport_journal.py`
- `exact_receipt_retirement.py`
- `transaction_mutation_authority.py`
- `reply_strategy.py`
- `reply_evidence.py`
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
- `semantic_quote_image_veto.py`
- `semantic_alignment/__init__.py`
- `semantic_alignment/io.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `semantic_alignment/quote_research_schema.py`
- `semantic_alignment_research/quote_research_full_001/corpus_manifest.json`
- `semantic_alignment_research/quote_research_full_001/research_packets.json`
- `semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json`
- `mrs_log_digest.py`
- `runMrsMThatcher2`
- `deploy/systemd-user/*`
- `mrsMThatcher.env.example`

The offline research and benchmark implementation remains versioned with the
project, but is not a production runtime dependency:

- `semantic_alignment/quote_research_gemini.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
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
- `final_posting_queue_top90_as_is/images/*`
- `final_posting_queue_top90_as_is/renamed_png_v3_top90_posting_queue.json`

`mrs_log_digest.py` is included because the digest golden tests run the actual
digest script against generated test logs.

The tracked image assets are the runtime assets used by `mrsMThatcher2.py`.
Larger source/research meme directories such as `memes/`, `meme_hunt_001/`,
and `meme_shortlist*/` are intentionally ignored.

## Generated Regular-Image Observability

The optional generated-image pool is separate from the original `images/t*.jpg`
pool. Generated images can be selected for quotes other than the quote that was
used to generate them. When the selected quote hash matches the generated
image's origin hash, the selector applies `GENERATED_IMAGE_ORIGIN_QUOTE_BOOST`.

Regular selected-image logs now include whether the image was original or
generated, the final score, whether a generated image matched its origin quote,
and the boost that was applied. `mrs_log_digest.py` reports these as
observational metrics only: regular image selections, original versus generated
counts, generated origin matches, and generated cross-quote selections. There is
a minimum spacing rule: after a generated regular image is selected, two
original-image posts must be completed before another generated image is
eligible. The generated pool remains disabled by source default.

## Historical Context Replies

The optional historical-context stage posts a neutral, corpus-backed threaded reply only
after a regular quotation post has been confirmed. It does not change the quotation,
image selection, schedule, or main-post receipt semantics. When enabled, startup validates
the immutable archive against the counts and packet-file hash declared by its manifest and
final status. The current archive declares 627 completed packets and five unresolved
quotations. The current 619 canonical source records are then filtered to exactly 611
attribution-eligible runtime quotations; five unresolved and three additional
attribution-ineligible records remain unavailable for posting. A quote without an eligible
completed packet receives no context reply.

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

## AI-First Conversational Replies

Mention and quote-tweet replies use one production strategy: a structured AI
proposer, claim-specific local evidence adjudication when the draft contains
facts, and a fresh independent AI reviewer. Only an explicit reviewer approval
can reach the durable posting path. The historical quotation corpus remains
available as factual evidence and for exact quotation verification, but replies
are not assembled from a selected quotation packet.

Configure it through the ignored local configuration after review:

```json
{
  "ai_first_reply_strategy": {
    "enabled": true,
    "strategy_version": "ai-first-reply-v3",
    "proposer_model": "grok-4.3",
    "reviewer_model": "grok-4.3",
    "evidence_model": "grok-4.3",
    "research_corpus_path": "semantic_alignment_research/quote_research_full_001",
    "maximum_model_calls": 6,
    "proposer_timeout_seconds": 60,
    "evidence_timeout_seconds": 60,
    "reviewer_timeout_seconds": 60,
    "proposer_max_output_tokens": 900,
    "evidence_max_output_tokens": 1800,
    "reviewer_max_output_tokens": 900,
    "maximum_revisions": 1,
    "maximum_invalid_response_retries": 1,
    "maximum_claims": 6,
    "maximum_evidence_packets_per_claim": 6,
    "maximum_evidence_passages_per_claim": 24,
    "maximum_reply_sentences": 2,
    "fail_closed": true
  }
}
```

The source corpus is loaded lazily on the first candidate that reaches the reply
pipeline. Its partition and packet-file hash are checked against its own immutable
manifest and final status, then the current archive is filtered to exactly 611
attribution-eligible Thatcher packets. Production packet validation comes from
the dependency-free `semantic_alignment/quote_research_schema.py`; it does not
import the Gemini/Vertex research runner or provider SDK. A failed corpus load is
cached for the process and fails closed before media preparation or an AI call.
Factual claims must be supported by exact, hash-validated local passages. Approved
drafts use schema version 2 and are revalidated against their contribution,
context, models, prompts and source hashes before reuse or receipt reconciliation.
V1 drafts are never migrated or posted.

Audit legacy V1 drafts without credentials, posting, or network access:

```bash
python3 reply_strategy.py \
  --state bot_state.json \
  --output semantic_alignment_research/ai_first_reply_strategy_001/legacy_v1_draft_audit.json
```

Run the network-free fixture, saved-history and local-retrieval evaluation:

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

Generated-image identity-policy processing is `suspended` while the generated
pool is disabled. Re-enabling its shadow requires both the generated pool and
`ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING`; enabling the latter alone
does no audit loading, scoring or telemetry work. The semantic-veto and
original-editorial features remain non-enforcing active shadows.

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

The maintained module map, side-effect boundaries, active corpus accounting and
PEP 257 documentation policy are recorded in
[`docs/python_api.md`](docs/python_api.md). Check module and public-definition
docstring coverage without additional dependencies:

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

Normal operational commands require `bot_state.json` (or a valid configured
backup), `lines_used.json`, and `images_used.json`. They refuse to infer a new
installation from missing files. For a genuinely empty new project directory,
run `python3 mrsMThatcher2.py --initialise` once; it creates the durable set and
an installation marker but does not start posting or call an API.

## Log Digest Operation

`mrs_log_digest.py` resolves project metadata through `--project-dir` (the
repository/script directory by default), including when logs are passed by
absolute path from another working directory. `--output PATH` atomically writes
a report; otherwise output is written and flushed to stdout. Resume state is
advanced only after complete analysis, rendering, and successful report
delivery.
Supplying the canonical `mrsMThatcher.log` explicitly also includes numeric
siblings such as `.1` and `.2`, but excludes self-test and unrelated files.
Stateful digest runs use a separate nonblocking resume lock. The schedule-model
runway is a maximum-throughput minimum: it assumes generated selection whenever
spacing permits.

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

## Launcher And Private Environment

`mrsMThatcher.env.example` is a sanitized example of the private live
environment file. It lists the required X/xAI environment variables with
placeholder values.

For live use, copy it to the ignored runtime name, edit that copy, and keep it
readable only by the bot user:

```bash
cp mrsMThatcher.env.example mrsMThatcher.env
chmod 600 mrsMThatcher.env
```

`runMrsMThatcher2` is the tracked live launcher. It contains no secrets. It
sources the ignored `mrsMThatcher.env` file and then runs
`/usr/local/bin/mrsMThatcher2.py`. If the Python process exits, the launcher
waits 60 seconds before restarting it; the bot's ordinary scheduling still
happens inside the Python process.

The real `mrsMThatcher.env` is intentionally ignored by Git. Do not commit live
API credentials.

## User Systemd Units

Canonical user units are versioned under `deploy/systemd-user/`. Check the live
installation for drift with:

```bash
deploy/systemd-user/install.sh --check
```

Install updated units as regular files and reload the user manager with:

```bash
deploy/systemd-user/install.sh --install
```

The installer uses atomic per-file replacement. It does not enable, start, stop,
or restart any unit. Enable units separately when required:

```bash
systemctl --user enable mrsMThatcher.service
systemctl --user enable mrs-engagement-analytics.timer
systemctl --user enable mrs-semantic-veto-shadow-health.timer
```

The semantic-veto health timer runs daily at 23:35 Europe/London. It validates
the configured shadow manifest and source hashes, records cumulative shadow
status, and makes no network call. Durable snapshots are written to:

```text
~/.local/state/mrsMThatcher/semantic-veto-health/latest.json
~/.local/state/mrsMThatcher/semantic-veto-health/history/YYYY-MM-DD.json
```

Its JSON output is also retained by the user journal:

```bash
journalctl --user -u mrs-semantic-veto-shadow-health.service
```

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

## Deployment Smoke Test

On this host, `/usr/local/bin/mrsMThatcher2.py` is a symlink to the script in
this repository. Before replacing a non-symlink live script on another host,
make a timestamped backup:

```bash
cp /usr/local/bin/mrsMThatcher2.py /usr/local/bin/mrsMThatcher2.py.$(date +%Y%m%d-%H%M%S).bak
```

Install or refresh the live symlink if needed:

```bash
ln -sfn /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py /usr/local/bin/mrsMThatcher2.py
chmod +x mrsMThatcher2.py
```

Compile-check the installed file:

```bash
PYTHONPYCACHEPREFIX=/tmp/mrs-pycache python3 -m py_compile /usr/local/bin/mrsMThatcher2.py
```

Source the live environment, then run the bot self-test:

```bash
set -a
source /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
set +a
python3 /usr/local/bin/mrsMThatcher2.py --self-test
```

The self-test validates local configuration, credentials and core installation
assets without importing provider research SDKs or loading the quotation research
tree. Operational corpus validation remains fail closed on the first real reply
candidate (and at startup for an enabled historical-context stage).

Restart the live service using the normal service manager for this host.
After restart, check the live log for startup config and safety markers:

```bash
grep -E "Bot starting|Base dir=|State file=|Log file=|X base=|X upload base=|xAI base=|Config:" /disks/disk1/etc/mrsMThatcher/mrsMThatcher.log | tail -80
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
