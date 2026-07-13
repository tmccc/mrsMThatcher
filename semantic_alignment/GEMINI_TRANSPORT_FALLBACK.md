# Gemini transport fallback

Research runs use the Gemini Developer API unless Vertex fallback is explicitly enabled.
Fallback is not a general retry mechanism. It is available only after both Developer API
attempts return confirmed HTTP 429 quota/rate-limit responses. Schema failures, refusals,
authentication errors, invalid requests, parser defects, timeouts with uncertain transmission,
and ambiguous billing outcomes never cross transports.

Developer and Vertex use the same model, prompt, response schema, output limit and thinking
budget. Startup aborts if those settings differ. Vertex requires `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, `GOOGLE_GENAI_USE_VERTEXAI=true`, readable Application Default
Credentials, and a successful non-displaying ADC token check.

The Developer and Vertex ledgers are separate. Results retain `logical_provider`, `transport`,
`fallback_used`, `fallback_reason`, model/input/prompt/schema identifiers and parent request
provenance. Comparisons still receive one logical Gemini vote. If both transports somehow
produce valid results, the existing first completed logical result is retained; the second is
not sent by normal resume behavior.

Three consecutive cases with two confirmed daily-quota responses activate a durable
Developer quota pause. Remaining Gemini cases route directly to Vertex for that run. Use
`--probe-gemini-developer-after-quota-pause` only for an explicit operator probe.

Example (environment values are loaded separately and are not printed):

```bash
set -a
source /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
set +a
python3 analyse_semantic_alignment_large.py \
  --run-dir semantic_alignment_research/provider_bakeoff_next \
  execute \
  --providers gemini \
  --execute-gemini \
  --enable-gemini-vertex-fallback \
  --confirm-gemini-cost-limit-usd 10 \
  --confirm-gemini-developer-cost-limit-usd 10 \
  --confirm-gemini-vertex-fallback-cost-limit-usd 3 \
  --confirm-combined-cost-limit-usd 13
```

Without `--enable-gemini-vertex-fallback`, behavior remains Developer API only. Unused
Developer budget never increases the explicit Vertex ceiling. No search, grounding, tools,
code execution or computer use is configured by either transport.

## Status and progress

The execution command prints a resume plan before its first paid request and emits a compact
progress line at most once every ten seconds. Logical Gemini completion is shown separately
from Developer and Vertex completion and spend. Fallback state is one of `disabled`,
`unavailable`, `armed`, or `active`.

Inspect saved state without credentials or provider access:

```bash
python3 analyse_semantic_alignment_xai.py gemini-fallback-status \
  --run-id provider_bakeoff_next
python3 analyse_semantic_alignment_xai.py gemini-fallback-status \
  --run-id provider_bakeoff_next --json
```

`--run-dir /absolute/path` may be used instead of `--run-id`.

## Quota pause and reset

A pause records its reason, UTC activation time, whitelisted provider reset headers and trigger
evidence. A provider reset header is marked confirmed. A configured daily boundary is marked
estimated. Otherwise reset time is `unknown`. An expired estimate is informational: Developer
API remains paused and resume continues direct-to-Vertex until an operator explicitly probes or
clears the pause.

Clear only a demonstrably expired pause, preserving request history and an audit record:

```bash
python3 analyse_semantic_alignment_xai.py gemini-fallback-status \
  --run-id provider_bakeoff_next --clear-expired-gemini-quota-pause
```

The command refuses an active run, an unexpired reset, or an unknown reset time. It makes no
provider call and does not clear case failures. Use `--probe-gemini-developer-after-quota-pause`
on a later explicitly authorised execution to probe Developer API once; no probe is automatic.

## Troubleshooting Vertex availability

An `unavailable` status means fallback was enabled but preflight did not pass. Check that the
project/location/Vertex environment values are loaded, ADC exists, ADC can mint a token, and
Vertex exposes the exact same model/settings. Status inspection itself never needs ADC or API
credentials.
