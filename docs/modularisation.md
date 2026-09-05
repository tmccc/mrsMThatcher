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

## Extracted in stage 3

Base: `24b2ee048952e1a0b4bc8bb1c0db04a7adb82b3f`, verified after fetching
`origin/codex/modularisation-stage2` on 2026-09-05. Its parent is the completed
stage-one commit `0de0ed6b3f3e617d61ccc680d58bfb185256b927`; both extractions
are included. Fetched `origin/master` and production HEAD remained at
`e08d894d9cd39a07ebe4eeb72205baa864d1a2be`. Work is isolated on
`codex/modularisation-stage3` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage3`.

`mrs_log_digest_runtime.py` owns `load_current_runtime_state`,
`load_current_runtime_config` and `runtime_control_snapshot`, their state/config
limits, configuration allow-list, control key sets and boolean/epoch validation.
The digest retains the three original signatures as explicit wrappers and
re-exports the moved constants and helpers. Dependency direction: digest →
runtime → values. The new module imports neither digest, Markdown nor the bot;
importing it performs no runtime reads, home lookup or service initialisation.

Stable snapshot/byte readers, file identity and strict JSON parsers stay in the
digest, unchanged, and are passed as explicit callbacks. The stage-two costs
wrapper still receives those same helpers. Only the pure `dt_text` and
`bounded_exception_status` helpers move to the existing values leaf; their
digest entry points remain available. No I/O framework or reverse import is
introduced.

State/config wrappers pass `datetime.fromtimestamp` for the existing host-local
mtime conversions. The state observation clock remains in `run_digest`, sampled
immediately after the state loader returns. Report generation time, that
observation time and bound file metadata remain distinct. Controls receive
`datetime.now` as a callable, retaining its original position after reading,
parsing and allowed-key/generation validation, before boolean/time validation
and expiry comparisons. Missing/invalid behaviour, exact Decimal controls,
native state/config numbers, pause ordering and timezone semantics are retained.
Report assembly, health overlays, strike progress and all other snapshots stay
in the digest; bot runtime behaviour is unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 17,684 | 17,453 |
| Runtime module | — | 335 |
| Digest state / config / control readers | 36 / 39 / 80 | 11 / 12 / 8 (wrappers) |
| Values module | 155 | 166 |
| Costs / Markdown modules | 430 / 3,347 | unchanged |
| `analyse` / `run_digest` / Markdown wrapper | 5,233 / 360 / 10 | unchanged |

Validation uses the existing Python 3.10.12 / pytest 9.1.1 environment and
committed temporary-HOME/subprocess isolation. The focused baseline has 169
passing tests: digest, safety, Markdown, costs, state-observation sequencing and
selected CLI/current-state integration regressions. The same suite plus 11 new
boundary tests passes after extraction (180 total). New tests cover independent
import, read-only access to explicit synthetic files, patched reader/parser/file
time compatibility, and control-clock validation order. The documentation gate
passes for 183 modules. No failures or warnings remain in these suites.

An exact replay compares 184 typed synthetic reader results in UTC and London,
including available/absent/malformed/unstable observations, symlinks, filtering,
numeric distinctions and active/expired/invalid controls. Six fixed-clock CLI
runs from a foreign directory cover both primary/secondary output modes with
valid, absent and invalid runtime files. Markdown bytes match exactly. Every
JSON value is compared, with changed producer source/commit identities checked
independently against each entry script and its Git HEAD. Paths, timestamps and
errors are not normalised. All 173 retained function/class ASTs and four moved
helper ASTs are unchanged; the three reader bodies differ only in explicit
dependencies. The full historical suite and live operational calls are outside
this focused validation.

Production files, configuration, durable data and running services were not
modified. This stage is a local commit only; it is not pushed, merged or deployed.

The stage-three completion-time "local commit only" statement above predates
its push. Stage-four setup fetched and verified
`origin/codex/modularisation-stage3` at
`8077329896f89b6e1e99e5bafe2b92b7c9107af9` on 2026-09-05.

## Extracted in stage 4

Base: `8077329896f89b6e1e99e5bafe2b92b7c9107af9`, the fetched stage-three
tip. Ancestry checks confirm inclusion of stages one, two and three. Fetched
`origin/master` and production HEAD remain at
`e08d894d9cd39a07ebe4eeb72205baa864d1a2be`, lacking these stages. Work is on
`codex/modularisation-stage4` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage4`; existing branches and
worktrees are preserved.

`mrs_log_digest_corpus.py` owns `historical_context_corpus_snapshot`, retaining
the six research/audit paths, count fallbacks, availability, missing/malformed
reporting, policy metadata and hash ordering. Parsing and hashing intentionally
remain separate reads, including retention of parsed counts when hashing fails.
`mrs_log_digest_generated_pool.py` owns `generated_pool_health_snapshot` and the
basename and metadata schema/kind constants. Discovery, validation, warning
order, health/coverage classification, completed quarantine/restore records,
used-image accounting and seven-/thirty-day curation calculations are unchanged.
The existing reader steps remain together; no additional helper split is needed
to establish these observation boundaries.

Dependency direction: digest → corpus; digest → generated pool → values.
The new modules import neither digest, Markdown nor bot, and perform no runtime
reads, directory scans, home lookup, logging or service initialisation at import.
The digest retains both signatures as explicit wrappers and re-exports the moved
constants. Paths, unchanged strict native JSON parsers and `file_sha256` are
supplied explicitly. Pool time dependencies are `datetime.now` and
`datetime.fromisoformat` callbacks, preserving digest patching. The implicit
clock is sampled after active-image validation, before curation reads; explicit
times and host-local timezone handling are retained. `run_digest` still supplies
the selected window end when present, separately from digest generation time.
Post rates, utilisation, runway/config loading, analysis, report assembly and all
other snapshot readers remain in place. No parser/hash implementation is copied.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 17,453 | 17,149 |
| Corpus module / reader | — | 128 / 116 |
| Generated-pool module / reader | — | 263 / 237 |
| Digest corpus / pool readers | 106 / 221 | 7 / 10 (wrappers) |
| `analyse` / `run_digest` / Markdown wrapper | 5,233 / 360 / 10 | unchanged |
| Values / runtime / costs / Markdown modules | 166 / 335 / 430 / 3,347 | unchanged |

Validation uses Python 3.10.12 / pytest 9.1.1 and the committed temporary-HOME/
subprocess isolation. The pre-change baseline passes 117 tests: the six affected
digest/pool/Markdown suites and six nearest integration checks for CLI output,
resume, contract/source identity and current-state/window distinction. The same
suite plus 13 focused boundary tests passes (130 total). Boundaries cover
signature/callback compatibility, separate parse/hash observations and hash
failures, implicit-clock sequencing, UTC/London interpretation, independent
import and read-only access to explicit synthetic files. The existing corpus
fixture setup is shared with the new tests; expected outputs are unchanged.
The documentation prerequisite passes for 185 modules. No failures or warnings
were observed.

A temporary replay compares 135 typed snapshots over identical synthetic files:
available, missing, malformed and fallback corpus data; healthy/empty pools;
discovery, metadata, identity and hash problems; completed/failed quarantine and
restore records; used history; and explicit/implicit clocks in UTC and London.
It compares all values, types, dictionary/list order, warnings, hashes and
timestamps without broad normalisation. Six fixed-clock foreign-directory CLI
runs cover both primary/secondary Markdown/JSON modes with available, malformed
and missing observations, including explicit window end, last-record end and
implicit pool time. Markdown bytes and stderr match exactly. Every JSON value
is compared, with producer source hash and repository HEAD checked independently
against each script and Git revision. The replay is also checked after committing
so the new commit identity is exercised. All 174 retained digest function/class
ASTs are unchanged; both extracted bodies differ only in explicit dependencies
and documentation. The full historical suite and live operational calls are
outside this focused validation.

Production files, configuration, data and running services are untouched. This
stage is committed locally only; it is not pushed, merged or deployed.

## Likely next steps

1. Extract a bounded `analyse` event family next, starting with historical-context
   event aggregation and passing its counters/pending state explicitly. Leave
   remote-write/reconciliation snapshots and cross-event incident reconciliation
   until their evidence inputs can be separated coherently. An existing corpus-reader finding for later
   work: parsed content and SHA-256 come from separate reads; changing that
   binding is a separate behavioural decision, outside these extractions.
2. In the bot, extract bounded reply-context/history/media preparation around
   the existing `single_call_reply` contract, passing state and fetch functions
   explicitly. Keep scheduling and transaction/posting authority with the
   coordinator until their dependencies can be separated coherently.

Legacy multi-stage reply, image-policy and incident presentation remains for old
logs. No editorial, prompt, model, JSON-schema or runtime changes belong here.
