# Incremental modularisation

Stage 1 starts at `e08d894d9cd39a07ebe4eeb72205baa864d1a2be` (fetched
`origin/master`, 2026-09-05). Production HEAD was the same SHA, with no local
changes or divergence. Work is isolated on `codex/modularisation-stage1` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage1`.

## Responsibilities and useful boundaries

| Module | Responsibilities at the base revision |
| --- | --- |
| `mrsMThatcher2.py` (29,099 lines) | Configuration/bootstrap, scheduling and lane selection, context/media collection, reply integration, quote/image selection, API transports, durable state and transaction recovery. Its largest function is `maybe_reply_to_mentions` (893 lines). |
| `mrs_log_digest.py` (21,332 lines) | Log discovery/parsing and resume cursors; read-only runtime/evidence snapshots; event correlation and health/accounting; report preparation and JSON contract; Markdown; CLI locking, delivery and resume persistence. `analyse` is 5,233 lines and `render_markdown` is 3,073. |

The bot already delegates model-facing conversational decisions, bounded context,
validation and durable drafts to `single_call_reply.py` (2,639 lines). Keep that
boundary: future reply work should reuse it. Historical-context formatting,
corpus admission/outbox handling, engagement analytics, health reporting, and
receipt/media/transport authority also have dedicated modules. The digest reads
selected helpers from those modules without importing the bot; its snapshots
must remain observational.

## Dependencies to respect

- Bot module globals hold configuration, paths, caches, cooldowns, and authority
  state. Importing it reads environment/credentials, configures logging,
  constructs OAuth state and binds the request provider. Extracted code must not
  import it back; inject the specific state, transport or callback it needs.
- `analyse` builds correlated event/pending state and report dictionaries.
  Runtime overlays and derived headline updates mutate that report afterward.
  Render only the prepared report; keep I/O and current-health classification
  outside presentation.
- Digest project defaults use the entry script's `__file__`; resume/output paths
  and locks are resolved by the CLI. The cost cache default is derived from the
  home directory at digest import. Moving these into presentation would couple
  import and deployment location to report output.
- Tests import the digest, patch its functions/constants (including rendering
  failure injection), call `main`, and execute it from foreign directories.
  Preserve those seams. Avoid circular imports through the bot or digest when
  sharing helpers; shared code belongs below both callers.

## Extracted in stage 1

`mrs_log_digest_markdown.py` owns Markdown assembly and named section renderers.
It consumes the prepared report plus a main-post receipt lifecycle summary.
`mrs_log_digest.render_markdown(report)` remains the compatibility wrapper and
computes that summary using the existing analysis helper. The summary is passed
separately, so no field is added to the JSON report. Section order, strings,
formatting, optional-data handling and legacy output are retained.

`mrs_log_digest_values.py` is a small shared leaf for the existing scalar/date/
money conversions and report vocabulary used on both sides. It has no paths,
environment lookups, clocks, I/O or runtime initialisation. Existing helper and
constant names remain explicitly importable through `mrs_log_digest`.
Dependency direction: digest → Markdown and values; Markdown → values.
The renderer never imports the digest, bot, snapshot readers or service modules.

Markdown is the first boundary because most of it already consumes prepared
data. This removes its long-lived local scope without changing the correlated
analysis or runtime. Receipt classification stays in analysis; formatting-only
helpers move with presentation.

| Size (physical lines; functions include their definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 21,332 | 18,039 |
| `analyse` | 5,233 | 5,233 |
| Digest `render_markdown` | 3,073 | 10 (wrapper) |
| Markdown module / assembly function | — | 3,347 / 44 |
| Shared values module | — | 155 |

The bot is unchanged. The largest section renderer is the event-detail group
(401 lines); each section now has its own local scope.

Validation: 357 existing digest/CLI regressions pass before and after, plus four
new tests for exact fixture bytes, input immutability and independent import/
render without runtime reads, writes, network or logging initialisation. All 85
captured offline baseline reports replay exactly. A fixed-clock, temporary-path
CLI check covers Markdown/JSON primary and secondary outputs: Markdown matches
exactly; JSON differs only in the separately verified `producer_source_sha256`,
which must change with the entry script. Analysis/CLI and moved-helper ASTs are
unchanged. The documentation gate passes for 181 modules.

Stage-one test-isolation finding: existing in-process/CLI tests could read the
host's default published-cost cache. The initial run did so read-only. Both
baselines and regressions were subsequently rerun with a temporary
`sitecustomize.py` overriding `Path.home()` for the test processes (inherited via
`PYTHONPATH`), and committed fixtures contain only synthetic cache data. The
baseline emitted three existing third-party `blinker` deprecation warnings.

## Extracted in stage 2

Base: `0de0ed6b3f3e617d61ccc680d58bfb185256b927`, the verified completed
stage-one commit and current fetched `origin/codex/modularisation-stage1` tip
(2026-09-05). Fetched `origin/master` and the clean production checkout remained
at `e08d894d9cd39a07ebe4eeb72205baa864d1a2be`, which lacks stage one. Work is
isolated on `codex/modularisation-stage2` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage2`; stage one is unchanged.

`mrs_log_digest_costs.py` owns cache schema/scope/money validation, UTC-date
segmentation, cumulative-sample boundary accounting and prepared current-day/
selected-window reports. Dependency direction: digest → costs → values.
Costs imports neither digest, Markdown nor the bot. Its import performs no home
lookup, cache/runtime access, logging initialisation or service calls.

The digest retains the original `load_openai_cost_cache` and
`openai_published_cost_report` signatures as wrappers, and explicitly re-exports
the estimator, conversion helper, money-map validator and constants. It resolves
the default/explicit cache path (including `expanduser`) and implicit clock, so
patches to `digest.OPENAI_COST_CACHE_PATH` and `digest.datetime` still work.
The legacy `provider_usage` argument remains ignored. The costs loader receives
the unchanged stable regular-file reader and strict JSON parser as two explicit
callbacks; unrelated snapshot readers remain in the digest. Report preparation
uses the loaded cache and concrete observation time without further reads.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 18,039 | 17,684 |
| Costs module | — | 430 |
| Digest cache loader | 153 | 15 (wrapper) |
| Window estimator | 174 | 174 (re-exported from costs) |
| Digest published-cost report | 50 | 23 (wrapper) |
| `analyse` / digest Markdown wrapper | 5,233 / 10 | unchanged |
| Markdown / values modules | 3,347 / 155 | unchanged |

Before the baseline, the existing pytest bootstrap gained a temporary `HOME`,
created before test imports and inherited by subprocesses. Tests assert the
exact isolated default path before reading, exercise missing and synthetic CLI
caches, and verify explicit path/clock overrides. Production path selection is
unchanged; no external `sitecustomize.py` is required for these tests.

Validation: 208 existing cost/digest/Markdown/CLI regressions pass before and
after; seven new boundary/isolation tests bring the total to 215. The original
23 cost tests replay all 23 captured cache/report results and nine Markdown
renderings exactly. A further 32 synthetic validation cases in UTC and London
match 64 cache observations and 704 window reports, including value types.
Fixed-clock subprocess CLI comparisons from a foreign directory preserve exact
Markdown bytes and every JSON value in both primary/secondary output modes;
only `producer_source_sha256` changes, independently checked against both entry
scripts. These comparisons precede the commit so `repository_head_sha` is also
compared unchanged. All 177 retained digest function ASTs, including stable
readers, analysis and CLI, are unchanged; moved accounting and validation bodies
are unchanged apart from explicit dependencies/default resolution. The
documentation gate passes for 182 modules. No failures or warnings were observed
in this focused baseline or final suite.

Production files, configuration, durable data and running services were not
modified. This stage is a local commit only; it is not pushed, merged or deployed.

## Likely next steps

1. Separate runtime/evidence snapshot readers from log event aggregation, with
   explicit project paths and observation times. Then extract individual
   `analyse` event families with their own pending state; leave cross-event
   incident reconciliation until its evidence inputs are explicit.
2. In the bot, extract bounded reply-context/history/media preparation around
   the existing `single_call_reply` contract, passing state and fetch functions
   explicitly. Keep scheduling and transaction/posting authority with the
   coordinator until their dependencies can be separated coherently.

Legacy multi-stage reply, image-policy and incident presentation remains for old
logs. No editorial, prompt, model, JSON-schema or runtime changes belong here.
