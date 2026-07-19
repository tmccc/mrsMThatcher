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

The test-only commands require `MRS_TEST_MODE=1`. They use `MRS_BASE_DIR` for
state, local config, control files, quote lines, images, meme files, and
used-history JSON files. Legacy pickle history files are no longer automatically
deserialised; if JSON history is missing while a legacy pickle exists, the bot
fails closed until JSON history is restored or migrated manually from a trusted
backup. They use `MRS_LOG_FILE` for logs.

Production defaults are unchanged when these environment variables are unset:

- `MRS_BASE_DIR` defaults to `/disks/disk1/etc/mrsMThatcher`
- `MRS_LOG_FILE` defaults to `<MRS_BASE_DIR>/mrsMThatcher.log`
- `X_API_BASE_URL` defaults to `https://api.x.com`
- `X_UPLOAD_BASE_URL` defaults to `https://upload.twitter.com`
- `XAI_API_BASE_URL` defaults to `https://api.x.ai/v1`

Safety guards:

- Operational entry points require a successful explicit `production_bootstrap()` call; importing the module alone never enables them.
- Production bootstrap configures the rotating production file log explicitly. Tests must pass `configure_file_logging=False` or an isolated `log_path`; module-owned handlers are replaced and closed on reconfiguration.
- If `MRS_TEST_MODE=1` and `MRS_BASE_DIR` resolves to the production directory or a child of it, the bot aborts before logging or state writes.
- If `MRS_TEST_MODE=1` and `MRS_LOG_FILE` resolves under the production directory, the bot aborts.
- If `MRS_TEST_MODE=1` and any API base URL still points at live X/xAI hosts, the bot aborts unless `MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST=I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X` is set deliberately.

Endpoint override convention:

- `X_API_BASE_URL` may be the fake server root or may end in `/2`; a terminal `/2` is normalised away because the bot appends `/2/...` endpoint paths.
- `X_UPLOAD_BASE_URL` may be the fake server root or may end in `/1.1`; a terminal `/1.1` is normalised away because the bot appends `/1.1/media/upload.json`.
- `XAI_API_BASE_URL` should include `/v1` when the fake server exposes `/v1/chat/completions`.

Scenario fixtures live in `tests/fixtures/scenarios/`. The fake server implements only the endpoints the bot currently uses:

- `GET /2/users/{id}/mentions`
- `GET /2/tweets/search/recent`
- `GET /2/tweets/{id}/quote_tweets`
- `GET /2/tweets/{id}`
- `POST /2/tweets`
- `POST /2/media/upload`
- `POST /1.1/media/upload.json`
- `POST /v1/chat/completions`

## Files To Keep Together

The integration harness and golden tests assume these files are versioned or
deployed as a coherent set:

- `mrsMThatcher2.py`
- `reply_strategy.py`
- `historical_context_formatter.py`
- `historical_context_reply_schema.json`
- `semantic_quote_image_veto.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/quote_research_gemini.py`
- `semantic_alignment_research/quote_research_full_001/corpus_manifest.json`
- `semantic_alignment_research/quote_research_full_001/research_packets.json`
- `semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json`
- `tests/test_integration_harness.py`
- `tests/fake_api_server.py`
- `tests/fixtures/scenarios/*.json`
- `mrs_log_digest.py`
- `README.md`
- `runMrsMThatcher2`
- `deploy/systemd-user/*`
- `mrsMThatcher.env.example`
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
image selection, schedule, or main-post receipt semantics. Startup validates the immutable
research archive as exactly 626 completed packets and six unresolved quotations. The
current 619 canonical source records are then filtered to exactly 610 attribution-eligible
runtime quotations; six unresolved and three additional attribution-ineligible records
remain unavailable for posting. A quote without an eligible completed packet receives no
context reply.

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

## Accuracy-First Conversational Replies

Mention and quote-tweet replies can optionally use the completed historical
research corpus. The feature is disabled by default and does not affect main
quote posts or historical context-thread replies. When enabled, the model must
return a structured decision using one of the historical, humour, warm, or
`no_reply` modes. Local validation rejects unsupported factual claims,
unverified quotations in quotation marks, disabled modes, weakly grounded
historical claims, hashtags, repetitive stock lines, and replies over the
existing conversational-reply limit.

Enable it through the ignored local configuration after review:

```json
{
  "reply_strategy": {
    "enabled": true,
    "accuracy_first": true,
    "research_corpus_enabled": true,
    "research_corpus_path": "semantic_alignment_research/quote_research_full_001",
    "completed_packets_only": true,
    "allow_historical_correction": true,
    "allow_historical_context": true,
    "allow_researched_principle": true,
    "allow_humour": true,
    "preferred_humour_tones": ["dry", "wry", "playful", "deadpan", "warm"],
    "maximum_retrieved_packets": 5,
    "minimum_grounded_confidence": "medium",
    "no_hashtags": true
  }
}
```

The historical corpus is validated as 626 completed and six unresolved records,
then filtered to exactly 610 attribution-eligible packets before an enabled
production run starts. Strategy metadata is attached to the confirmed reply
receipt and retained in `reply_strategy_history` after reconciliation.
`mrs_log_digest.py` reports decision mode, confidence, evidence count, and
grounding status without publishing internal quote IDs.

Audit a digest without credentials, posting, or network access:

```bash
python3 mrsMThatcher2.py audit-replies \
  --digest /home/tonym/Dropbox/digest014.md \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --json
```

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
write. Such an ambiguous outcome creates `ambiguous_post_outcome.json` and
blocks further posting until an operator reconciles it; the bot does not claim
exactly-once delivery.

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
