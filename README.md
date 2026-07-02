# MrsMThatcher Bot

## Local Integration Harness

Run the safe local integration tests with:

```bash
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
state, local config, control files, quote lines, images, meme files, and pickle
history. They use `MRS_LOG_FILE` for logs.

Production defaults are unchanged when these environment variables are unset:

- `MRS_BASE_DIR` defaults to `/disks/disk1/etc/mrsMThatcher`
- `MRS_LOG_FILE` defaults to `<MRS_BASE_DIR>/mrsMThatcher.log`
- `X_API_BASE_URL` defaults to `https://api.x.com`
- `X_UPLOAD_BASE_URL` defaults to `https://upload.twitter.com`
- `XAI_API_BASE_URL` defaults to `https://api.x.ai/v1`

Safety guards:

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
- `tests/test_integration_harness.py`
- `tests/fake_api_server.py`
- `tests/fixtures/scenarios/*.json`
- `mrs_log_digest.py`
- `README.md`
- `runMrsMThatcher2_example`

`mrs_log_digest.py` is included because the digest golden tests run the actual
digest script against generated test logs.

## Launcher Example

`runMrsMThatcher2_example` is a sanitized example of the live launcher. It lists
the required X/xAI environment variables with placeholder values and refuses to
start until the placeholders are replaced.

For live use, copy it to a private untracked launcher and edit that copy:

```bash
cp runMrsMThatcher2_example runMrsMThatcher2
chmod +x runMrsMThatcher2
```

The real `runMrsMThatcher2` is intentionally ignored by Git because it contains
live API credentials. Do not commit the real launcher.

## Deployment Smoke Test

Before replacing the live bot, make a timestamped backup of the live script:

```bash
cp /disks/disk1/bin/mrsMThatcher2.py /disks/disk1/bin/mrsMThatcher2.py.$(date +%Y%m%d-%H%M%S).bak
```

Install the new script:

```bash
cp mrsMThatcher2.py /disks/disk1/bin/mrsMThatcher2.py
chmod +x /disks/disk1/bin/mrsMThatcher2.py
```

Compile-check the installed file:

```bash
python3 -m py_compile /disks/disk1/bin/mrsMThatcher2.py
```

Source the live environment, then run the bot self-test:

```bash
source /path/to/live/env
python3 /disks/disk1/bin/mrsMThatcher2.py --self-test
```

Restart the live service using the normal service manager for this host.
After restart, check the live log for startup config and safety markers:

```bash
grep -E "Bot starting|Base dir=|State file=|Log file=|X base=|X upload base=|xAI base=|Config:" /disks/disk1/etc/mrsMThatcher/mrsMThatcher.log | tail -80
```

The deployment smoke test should not use `MRS_TEST_MODE=1`; that mode is only
for isolated local harness runs.
