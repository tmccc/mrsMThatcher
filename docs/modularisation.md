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

The stage-four completion-time "local commit only" statement predates its push.
Stage-five setup fetched and verified `origin/codex/modularisation-stage4` at
`a556a7a0cde538499f53e6ee31d87d877296537e` on 2026-09-05.

## Extracted in stage 5

Base: `a556a7a0cde538499f53e6ee31d87d877296537e`, the fetched stage-four
tip. Ancestry checks confirm all four completed stages are included. Fetched
`origin/master` and production HEAD remain at
`e08d894d9cd39a07ebe4eeb72205baa864d1a2be`, lacking those stages. Work is on
`codex/modularisation-stage5` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage5`; existing branches and
worktrees are preserved.

`mrs_log_digest_historical_events.py` owns field projection and family counters
for `historical_context_semantic_gate`, `historical_context_runtime`,
`historical_context_reply`, `historical_context_obligation` and
`historical_context_outbox`. Their original branch positions call specific
helpers. Reply preparation separates rendering and semantic metadata into small
pure helpers; existing bounded integer validation is reused for lengths.
`historical_context_quality_summary`, its verification/confidence count helpers
and associated vocabulary move intact and remain explicitly importable through
the digest.

Dependency direction: digest → historical events → values. The existing pure
post-ID/UTF-8 validators, bounded text/integer/boolean projections, associated
bounds/regexes and `_count_optional` move unchanged to the values leaf, retaining
digest imports for other consumers. Durable-history validation and its distinct
rules are unchanged. The new module has no I/O, import-time runtime effects,
reverse imports, dispatcher or pending-state object. Helpers receive only the
parsed event, timestamp, needed local counter and insertion callback.

The digest retains ordinary/strict parsing, the entire
`historical_context_reply_posted` authority branch, completed/already-completed
canonical-anchor checks and the original emitted event object's identity.
It still owns truncation, source references, generic counts, production
attribution, durable/receipt correlation, public text enrichment and incident
reconciliation. Reply status counting remains after the anchor decision and
uses the pre-truncation projected status. Quality summarisation consumes the
same emitted and filtered events at the original point in analysis. Valid
presentation fields do not confer publication authority. Posting-transaction
handling, report assembly, schema version, snapshots, CLI and persistence are
unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 17,149 | 16,651 |
| `analyse` | 5,233 | 4,979 |
| Historical-events module / largest helper (quality summary) | — | 543 / 96 |
| Values module | 166 | 247 |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation uses Python 3.10.12 / pytest 9.1.1 and the committed temporary-HOME/
subprocess isolation. The pre-edit baseline passes 171 tests: reply
observability, digest, safety and Markdown suites plus the specified publication
authority and resume/source-identity integrations. After extraction, 204 focused
tests pass, including 21 new boundary tests and the nearest shared-validator,
nested-field and durable-history regressions. The documentation gate passes for
186 modules. No existing assertions were changed.

A temporary replay compares 68 complete synthetic analysis reports and Markdown
renderings against stage four, checking every value, type and dictionary/list
order. It covers all five families, formatter versions 1–5, missing/malformed
fields, unknown statuses, interleaved unrelated events, production/self-test
sources, valid/invalid anchors and confirmations, durable-only text, seven
`max_text` values, independent repeated calls and full/resumed windows. New
tests retain the existing very-short-text behavior: confirmation correlation
can add a `confirmed_public_reply` to the historical section without changing
structured family counters or the earlier quality summary.

Nine foreign-directory CLI scenarios (36 invocations across the two revisions
and both primary/secondary output modes) compare exact Markdown bytes, every
JSON value, stderr and saved resume bytes. These cover full/first/resumed windows,
two text limits, physical fingerprint cursors and the existing timestamp fallback.
Two further standalone Markdown/JSON comparisons bring the total to 40 CLI
invocations, including Markdown without durable-evidence JSON loading.
Only `producer_source_sha256` and `repository_head_sha` are exempted from direct
cross-revision equality, after independently checking each against its script
bytes and Git HEAD. The CLI replay is repeated after committing to exercise the
new commit identity. All 165 other retained digest function/class ASTs, ten moved
helper ASTs, unrelated analysis branches and report assembly remain identical.
The full historical suite and live operational calls are outside this focused
validation.

Production files, configuration, logs, durable data, pools and running services
are untouched. Stage five is committed locally only; it is not pushed, merged or
deployed.

The stage-five completion-time "local commit only" statement predates its push.
Stage-six setup fetched and verified `origin/codex/modularisation-stage5` at
`608b5b730f5da2d36f62bc2db52598a31a48e294` on 2026-09-05.

## Extracted in stage 6

Base: `608b5b730f5da2d36f62bc2db52598a31a48e294`, the fetched stage-five
tip, whose parent is `a556a7a0cde538499f53e6ee31d87d877296537e`.
Ancestry checks confirm the completed stage-one through stage-five commits are
all included. Fetched `origin/master` and production HEAD remain at
`e08d894d9cd39a07ebe4eeb72205baa864d1a2be`, lacking those stages. Work is on
`codex/modularisation-stage6` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage6`; no suffix was needed,
and existing branches and worktrees are preserved.

`mrs_log_digest_generated_identity.py` owns the two specific observation
handlers for `GENERATED_IDENTITY_POLICY_SHADOW_RESULT` and
`GENERATED_IDENTITY_POLICY_APPLIED`, plus `generated_identity_shadow_summary`
and `generated_identity_policy_summary`. The digest explicitly re-exports both
summary functions with their original signatures and calls the handlers at the
original branch positions. Substring precedence and both successful/failed
`continue` paths are preserved.

Handlers receive only the message, timestamp, level, dedicated observation list,
local counter, error list, strict native JSON-object parser, text formatter and
lazy source-reference callback. They preserve first-marker splitting, whitespace
stripping, timestamp replacement, list order, parser-result mutation, error
detail/truncation/provenance and exactly-once family counters. The parse exception
boundary still covers only encoding/parsing; invalid summary inputs accepted by
parsing remain later failures. These observations still bypass `add_event`,
without new generic counters or provenance fields, including self-test sources.

Both summary bodies move unchanged: policy relevance, verified causation,
effects without winner changes, legacy unverified differences, invariant
failures, neutral ties/downstream differences, origin-only/penalty counts,
transitions, recovery, denominators, aliases and category/ranking order retain
their definitions. No baseline or shadow winner becomes evidence of publication.
`most_common_with_cutoff_ties` moves unchanged to the values leaf, retaining the
digest import used by original-editorial reporting. Dependency direction:
digest → generated identity → values. The new module imports no coordinator,
renderer, bot or snapshot reader and performs no I/O, home lookup, logging setup
or service initialisation. State remains local to each analysis call.

Original-editorial handling/companion deduplication, generated-image spacing and
resume persistence, snapshots, utilisation/runway, historical-context handling,
publication authority/recovery/reconciliation, report assembly, CLI delivery and
the bot's selection/scoring algorithms and configuration are unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 16,651 | 16,455 |
| `analyse` | 4,979 | 4,959 |
| Generated-identity module | — | 254 |
| Values module | 247 | 267 |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation uses the host Python 3.10.12 / pytest 9.1.1 environment and committed
temporary-HOME/subprocess isolation. The pre-edit baseline passes 145 selected
tests: the relevant identity and original-editorial digest cases, digest,
Markdown and safety suites, and spacing-resume/source-identity/JSON-contract CLI
regressions. Another 32 new observation/summary boundary cases pass against the
unchanged baseline before extraction. The final suite passes 183 tests, including
those 32 and six new import/re-export/handler/foreign-directory CLI cases. The
documentation gate passes for 187 modules. No existing assertions were changed.

A temporary replay uses 116 synthetic interleaved records, both source classes,
legacy/current categories, repeated observations, unknown fields, native numeric
types, ranking ties, original-editorial companions, historical events, spacing
and malformed payloads. Twenty complete reports and Markdown renderings match
exactly across empty/full/first/resumed windows and five text limits. Comparison
checks every value, type and dictionary/list order; independent repeated analysis
calls leave inputs unchanged. Eight schema-invalid observations preserve the
same later summary exception types and details.

Twelve foreign-directory CLI scenarios (24 invocations across both revisions)
cover primary/secondary and standalone Markdown/JSON, full/first/resumed/empty
windows, fingerprint cursors and timestamp fallback. Markdown, stderr and saved
resume bytes match exactly. JSON bytes match after replacing only the two
independently verified producer source-hash and repository-HEAD fields. The
comparison is repeated after committing to exercise the final commit identity.
All 162 other retained digest function/class ASTs and the three moved function
ASTs are unchanged; unrelated analysis branches, setup and report assembly are
also unchanged. The full historical/bot-scoring suites and live operational calls
are outside this focused validation.

Production files, configuration, logs, durable data, image pools and running
services are untouched. Stage six is committed locally only; it is not pushed,
merged or deployed.

## Extracted in stage 7

Base: `f0bb8f8c063a0eef40eb8823b6c75a092aae197e`, verified against the
pushed `origin/codex/modularisation-stage6` tip (superseding stage six's earlier
"local commit only" statement). Work is isolated on
`codex/modularisation-stage7` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage7`.

`mrs_log_digest_original_editorial.py` owns `original_editorial_comparison_key`,
`original_editorial_shadow_summary` and specific handlers for
`ORIGINAL_EDITORIAL_SELECTION_RESULT` and `ORIGINAL_EDITORIAL_SHADOW_RESULT`.
The two existing function entry points remain explicit digest re-exports. The
dedicated observation list and pending-companion counter stay explicit and local
to `analyse`; handlers remain at the same branch positions with the same continue
paths. Dependency direction: digest → original editorial → values. No global
accumulator, dispatcher, runtime I/O or reverse import is introduced.

Encoding/parser exception boundaries, exact diagnostics/counters, lazy source
references, timestamp/event-mode overwrites and parser-result identity remain
unchanged. Observations still bypass `add_event`. Each selection suppresses one
later matching shadow; early shadows and excess companions remain observations.
The six comparison fields, summary truthiness/type distinctions, conversions,
ranking ties and severe-disagreement object identity are retained. Invalid
comparison keys and parseable invalid summary inputs keep their downstream
failures, including the original partial mutation order. The ranking helper in
`mrs_log_digest_values.py` is unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 16,455 | 16,353 (−102) |
| `analyse` | 4,959 | 4,930 (−29) |
| Original-editorial module | — | 185 |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation: 175 existing focused tests pass at the base (171 digest/editorial/
Markdown/safety/stage-six extraction tests and four nearby CLI/resume/contract
regressions). Another 24 boundary cases pass before source extraction. The final
suite passes 203 tests, including four new re-export/import/partial-mutation
cases; 40 unrelated bot-scoring cases are deselected. Runs use
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`, Python
3.10.12 / pytest 9.1.1 and the existing temporary-HOME/network isolation. No
existing assertions were changed. Documentation coverage passes for 188 modules;
`git diff --check` passes.

A single temporary 27-record fixture compares full CLI JSON and Markdown with
the unchanged stage-six worktree, using a fixed clock and synthetic project/home
paths. It exercises companion order/multiplicity, malformed input, a suppressed
companion with invalid summary data, native types, ranking ties, self-test
diagnostics and interleaved identity/historical/spacing events. Markdown bytes
and all JSON bytes match except the independently verified
`digest_contract.producer_source_sha256` and `repository_head_sha` fields.
The two moved function ASTs and 160 other retained function/class ASTs match;
`analyse` differs only in the two handler bodies. Validation is limited to these
synthetic/focused regressions; broad bot suites and live provider calls are not
needed for this extraction. Production and earlier worktrees are untouched;
the existing separate corpus parse/hash reads remain unchanged.

Further digest extraction still has practical value. The next useful boundary is
`single_call_reply_summary` and the four structured branches
`single_call_reply_decision`, `single_call_reply_provider_usage`,
`single_call_reply_posting_outcome` and `single_call_reply_draft_recovered`.
They form an active report family with bounded field projection and a substantial
summary consuming emitted events. Specific helpers can receive `add_event` while
the digest retains parsing, provenance, truncation and publication/recovery
authority. No work on that boundary is included in stage seven.

## Likely next steps

1. Extract the remaining pure reply pipeline/strategy summaries and their
   majority-review helpers, as recommended in stage eight below.
   Leave remote-write/reconciliation snapshots and cross-event incident
   reconciliation until their evidence inputs can be separated coherently.
   An existing corpus-reader finding for later work: parsed content and SHA-256
   come from separate reads; changing that
   binding is a separate behavioural decision, outside these extractions.
2. In the bot, extract bounded reply-context/history/media preparation around
   the existing `single_call_reply` contract, passing state and fetch functions
   explicitly. Keep scheduling and transaction/posting authority with the
   coordinator until their dependencies can be separated coherently.

Legacy multi-stage reply, image-policy and incident presentation remains for old
logs. No editorial, prompt, model, JSON-schema or runtime changes belong here.

## Extracted in stage 8

Base: `134b671ac97561d7736cf03b4268729618e48bc0`, verified against the pushed
`origin/codex/modularisation-stage7` tip. Work is isolated on
`codex/modularisation-stage8` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage8`.

`mrs_log_digest_single_call.py` owns the unchanged 293-line
`single_call_reply_summary` and four specific `record_single_call_reply_*`
handlers for the `single_call_reply_decision`,
`single_call_reply_provider_usage`, `single_call_reply_posting_outcome` and
`single_call_reply_draft_recovered` event branches. This places the active
conversational report family and its bounded observation projection together,
removing 228 lines from the coordinator's long analysis scope.

Handlers receive parsed fields, the timestamp and the coordinator's `add_event`
callback. Branch predicates, precedence, outer parsing and continue flow stay in
`analyse`; event construction, dictionary identity/order, provenance, `max_text`
and statistics stay in `add_event`. Field allowlists/defaults, absent-versus-
invalid aliases, exact type checks, bounds, finite temperature handling, provider
attempt/status observations and summary deduplication/calculations are retained.
Publication/recovery authority and durable evidence remain with their existing
owners; the confirmed-reply rules are unchanged.

Dependency direction: digest → single call → values. The unchanged
`normalise_reply_lane` and `bounded_event_nonnegative_integer_observation` now
live in values. Both helpers and the summary remain explicit digest imports.
There is no dispatcher, global accumulator, reverse import or runtime I/O.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 16,353 | 15,813 (−540) |
| `analyse` | 4,930 | 4,702 (−228) |
| Single-call module / summary | — / 293 | 592 / 293 |
| Values module | 267 | 293 |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation: 182 existing tests pass at the base (179 reply-observability/digest/
Markdown/safety/historical-event tests and three nearby CLI/resume/contract
regressions). Nine added cases pass before extraction; the final suite passes
192 tests, including an independent import/reporting and re-export check. New
behaviour cases cover native numeric types, explicit null/boolean attempts,
bounds, alias precedence and summary consumption of original truncated events.
Existing assertions are unchanged. Runs use
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
existing temporary-HOME/network isolation. The documentation gate passes for
189 modules; `git diff --check` passes.

One temporary 15-record fixture compares complete CLI JSON and Markdown with
the unchanged stage-seven worktree using a fixed clock and synthetic paths.
All values, types and order match; 14,498 Markdown bytes match exactly. The
45,497-byte JSON differs only in `digest_contract.producer_source_sha256`;
both source hashes and each `repository_head_sha` are independently checked
against the source files and Git HEADs (both still at the base during comparison).
The three moved function ASTs, four projected branch bodies and 157 other
retained function/class ASTs match. All surrounding non-import module code is
unchanged. Validation is limited to these focused/synthetic checks; no broad bot
suites or live provider/posting calls were needed. Production and earlier
worktrees are untouched; no merge or deployment is included.

The next useful boundary is `reply_pipeline_stage_summary` and
`reply_strategy_summary`, together with `normalise_majority_review_telemetry`,
`majority_review_utilisation` and their three private validation/count helpers.
These seven pure reporting functions total 863 lines and have few dependencies
beyond shared values, majority-review vocabulary and small reason classifiers.
Grouping them would remove a substantial legacy reporting block while keeping
event parsing, cross-event reconciliation and operational authority in their
current owners. No next-stage implementation is included here.

## Extracted in stage 9

Base: `53936781c791adf4e8d87a086044986bd5c5a187`, verified against the live
`origin/codex/modularisation-stage8` tip. Work is isolated on
`codex/modularisation-stage9` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage9`.

`mrs_log_digest_reply_pipeline.py` owns `reply_pipeline_stage_summary`,
`normalise_majority_review_telemetry`, `majority_review_utilisation`,
`_valid_majority_review_summary`, `_majority_review_telemetry_for_event` and
`_majority_review_utilisation_counts`, plus the unchanged
`MAJORITY_REVIEW_FAMILIES` and `MAJORITY_REVIEW_SUMMARY_FIELDS` tuples.
`mrs_log_digest_reply_strategy.py` owns `reply_strategy_summary` and its private
`_no_reply_category`. The seven reporting functions total 863 lines.

The three shared reason classifiers, `_terminal_local_rejection_outcome`,
`_is_terminal_pipeline_failure` and `_is_writer_local_failure`, move unchanged to
values for the remaining cost/health callers. All four supporting reason helpers
total 48 lines. Every moved entry point, including private helpers and both
constants, remains an explicit digest import. Dependency direction is digest →
reply pipeline / reply strategy → values, with no reverse imports, runtime I/O,
dispatcher or shared mutable state.

Complete bodies, signatures and defaults are unchanged. Event identity and
mutation behaviour, ordering, strict types, missing values, deduplication, reason
precedence, majority-review validation/counting and summary calculations retain
their existing semantics. Parsing, `analyse`, effective-outcome reconciliation,
operational-health reconciliation and publication authority stay in the digest.
JSON schema 3, Markdown, CLI, defaults, provenance, clocks, locks and resume
behaviour are unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 15,813 | 14,884 (−929) |
| `analyse` | 4,702 | 4,702 (unchanged) |
| Reply-pipeline module / stage summary | — / 205 | 437 / 205 |
| Reply-strategy module / strategy summary | — / 466 | 506 / 466 |
| Values module | 293 | 328 |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation: 201 existing tests pass with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`: the
reply-observability, digest, Markdown, safety-hardening, digest-costs and
OpenAI-cost-digest files, plus the integration harness's
`test_digest_json_source_identity_is_stable_across_resume_filtering`,
`test_digest_json_contract_identifies_the_major_versioned_schema_and_retained_roots`
and `test_copied_digest_without_git_still_emits_valid_contract_json`.
Existing temporary-HOME/network isolation is reused; no tests or assertions are
changed. All 11 moved function ASTs and complete source bodies match stage 8,
as do all 147 retained digest function/class ASTs and source bodies. Both
constants, all prior values-module ASTs and all remaining non-import digest
module code are unchanged.

One temporary 12-record fixture compares the complete CLI report against the
unchanged stage-eight worktree with a fixed clock and synthetic paths. The
51,408-byte JSON matches in values, types and ordering except for the independently
verified `digest_contract.producer_source_sha256`; each `repository_head_sha`
is checked against its worktree HEAD (both still at the base during comparison).
All 13,080 Markdown bytes match. Direct legacy pipeline/strategy and remaining
cost summaries also match exactly (11,227 bytes), preserving input event values
and identities. Independent imports/reporting perform no runtime I/O, home
lookup, socket/subprocess calls or logging changes; all digest re-exports retain
owner identity and their type annotations resolve.

The documentation gate passes for 191 modules; `git diff --check` passes.
Validation is limited to these focused tests and synthetic checks; no broad bot
suites or live calls were needed. Production and earlier worktrees/branches are
preserved. No merge, deployment or next-stage implementation is included.

The next useful boundary is the pure legacy provider usage/cost reporting group:
`xai_reply_cost_summary`, `xai_usage_totals`, `_cache_metric_coverage`,
`_format_cache_metric_coverage_line`, `int_usage_value`,
`optional_int_usage_value`, `format_usd_ticks` and `format_reported_cost`
(564 lines). Its dependencies are shared values, counters and Decimal/currency
constants. It removes another substantial reporting block before expanding into
stateful coordination; preserve the distinct usage normalisers and missing-cost
semantics. Keep usage parsing/projection, pending call correlation, resume state
and published-cost cache I/O with their current owners. The smaller pure
`reply_visual_context_report` (253 lines) and image utilisation/runway summaries
are further options after that group.

## Extracted in stage 10

Base: `b1f5cd118f61fd82d11453b98a0d7e956234a99b`, verified against the live
`origin/codex/modularisation-stage9` tip. Work is isolated on
`codex/modularisation-stage10` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage10`.

`mrs_log_digest_provider_costs.py` owns `xai_reply_cost_summary`,
`xai_usage_totals`, `_cache_metric_coverage`, `_format_cache_metric_coverage_line`,
`int_usage_value`, `optional_int_usage_value`, `format_usd_ticks` and
`format_reported_cost`: eight complete functions totalling 564 lines. The
unchanged `USD_TICKS_PER_DOLLAR` and `USD_DISPLAY_QUANTUM` constants move with
them. All eight functions and both currency constants remain explicit digest
imports; the Decimal `ROUND_HALF_UP` alias is also retained.

Dependency direction is digest → provider costs → values, using the existing
lane normaliser and three stage-nine reason classifiers. Complete bodies,
signatures and defaults are unchanged, preserving distinct integer conversions,
missing/invalid versus zero costs, cache-metric coverage, response/target/attempt
matching and deduplication, cost attribution, ordering, input identity/mutation
behaviour, Decimal rounding/currency formatting and reason precedence.

Usage parsing/projection, pending-call correlation, `analyse`, resume state,
publication and operational-health authority remain with their current owners.
The published-cost cache module and coordinator wrappers are unchanged, as are
JSON schema 3, Markdown, CLI, defaults, provenance, clocks, locks and resume
behaviour. The new leaf has no runtime I/O or global mutable state.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 14,884 | 14,315 (−569) |
| `analyse` | 4,702 | 4,702 (unchanged) |
| Provider-costs module | — | 602 |
| `xai_reply_cost_summary` / `xai_usage_totals` | 403 / 56 | unchanged |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation: the same 201 existing tests as stage 9 pass with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`: digest-costs,
OpenAI-cost-digest, reply-observability, digest, Markdown and safety-hardening,
plus the integration harness's source-identity/resume, JSON schema/retained-roots
and copied-digest-without-Git tests. Existing temporary-HOME/network isolation is
reused; no tests or assertions are added or changed. All eight moved function
ASTs and complete source bodies match stage 9, as do all 139 retained digest
function/class ASTs and source bodies, both currency constant definitions and
all remaining non-import digest module code. Existing digest leaf modules are
byte-identical to stage 9.

A temporary 10-record fixture compares the complete CLI report against the
unchanged stage-nine worktree with a fixed clock, synthetic project/log paths
and the existing synthetic published-cost cache fixture. JSON (35,953 bytes)
matches in values, types and ordering except for independently verified
`digest_contract.producer_source_sha256`; both repository SHAs are checked
against their worktree HEADs (still at the base during comparison). All 11,607
Markdown bytes match. Direct provider usage/cost/cache/currency summaries match
exactly (5,605 bytes), preserving input list/event values and identities.
Independent leaf import/reporting has no runtime file/home/network/subprocess
access or logging changes; digest aliases retain owner identity and annotations
resolve. The documentation gate passes for 192 modules; `git diff --check` passes.

Validation is limited to these focused regressions and synthetic comparisons;
no broader bot suites or live calls were needed. Production and earlier
worktrees/branches are preserved. No merge, deployment or following-stage
implementation is included; this session stops at stage 10.

Recommended next boundary: `reply_visual_context_report` (253 lines), a coherent
pure correlation/summary leaf depending only on `Counter`, typing and the shared
`_normalise_lane`. Keep `parse_reply_visual_description_event`, its validation
constants and event insertion in the digest. A separate smaller image-reporting
group is `generated_image_utilisation` (65 lines), `generated_pool_runway`
(56 lines) and `regular_image_usage_summary` (24 lines), totalling 145 lines.
Those consume prepared observations; keep `generated_post_rate_history`,
`load_runway_config`, pool snapshot I/O, clock selection and report assembly with
their current owners. The visual-context report removes the larger cohesive
block without moving stateful coordination; the supervisor selects the next
stage within the authorised ceiling of stage 15.

## Extracted in stage 11

Base: `1eabfbcaaf996b01ad48e1b70b47fccc7c41f538`, verified against the live
`origin/codex/modularisation-stage10` tip. Work is isolated on
`codex/modularisation-stage11` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage11`.

`mrs_log_digest_visual_context.py` owns `parse_reply_visual_description_event`
and `reply_visual_context_report`, together with all six visual-validation
constants. `mrs_log_digest_image_usage.py` owns `generated_image_utilisation`,
`generated_pool_runway` and `regular_image_usage_summary`. All five functions and
six constants remain explicit digest aliases. `SHA256_LOWER_RE` already has the
shared values leaf as its owner; its definition and digest alias are unchanged.

The five complete bodies (530 lines) move unchanged. Strict allowlists, bounds,
native types, hashes, malformed-input behaviour, ordered correlation,
deduplication, payload copying/sharing, image coverage/metric aliases,
percentages/rankings and runway calculations/failure reasons are preserved.
Dependencies are digest → visual context → values and digest → image usage →
standard library. Neither new leaf has runtime I/O or shared mutable state.
Outer structured JSON parsing, event insertion, `analyse`, publication and
operational authority, history/config loaders, snapshot reads, clock selection
and report assembly stay with their existing owners. JSON schema 3, Markdown,
CLI, defaults, provenance, clocks, locks and resume behaviour are unchanged.

| Size (physical lines; functions include definition/docstring) | Before | After |
| --- | ---: | ---: |
| Digest file | 14,315 | 13,764 (−551) |
| `analyse` | 4,702 | 4,702 (unchanged) |
| Visual-context module | — | 430 |
| Image-usage module | — | 162 |
| Visual parser / report | 132 / 253 | unchanged |
| Utilisation / runway / regular-image summary | 65 / 56 / 24 | unchanged |
| `run_digest` / Markdown wrapper | 360 / 10 | unchanged |

Validation: 194 existing tests pass with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`: the six
requested reply-observability, digest, Markdown, safety-hardening, utilisation
and runway files, plus seven integration cases covering source-identity/resume,
JSON schema/retained roots, copied digest without Git, image spacing/resume,
regular-image AI labels and oversized image scores. Existing temporary-HOME and
network isolation are reused; no tests or assertions were added or changed.
All five moved function ASTs and complete source bodies, six constant
definitions, 134 retained digest functions/classes and all retained non-import
module code match stage 10. All earlier digest leaf modules are byte-identical.

A temporary 12-record synthetic fixture reuses the existing pool/quarantine,
post-log and published-cost cache helpers with a fixed clock and temporary
project paths. Complete CLI JSON (39,005 bytes) matches stage 10 in values,
types and ordering except for the independently verified producer source hash;
both repository SHAs match their worktree HEADs (still at the base during
comparison). All 13,565 Markdown bytes and 8,194 direct affected-summary bytes
match. Input values/identities, parser copies and retained-analysis sharing are
preserved. Independent leaf import/reporting passes with runtime file/home/
network/subprocess access blocked, unchanged logging, resolved annotations and
owner-identical digest aliases. The documentation gate passes for 194 modules;
`git diff --check` passes.

These checks cover the changed boundaries and nearest regressions, not broader
bot behaviour. The visual report remains a legacy callable omitted from schema
3 output, so it was compared directly as well as checking the complete report.
Production and earlier branches/worktrees are preserved; no bot/provider calls,
merge or deployment are included. This session stops at stage 11.

Recommended next boundary: the read-only remote-write snapshot group,
`remote_write_safety_snapshot`, `reconciliation_archive_snapshot`,
`_remote_write_document_identity`, `_group_active_remote_write_artifacts`,
`_canonical_retirement_source_identity`, `_safe_relative_project_path` and
`_read_readonly_archive_bytes` (1,314 function lines), with their snapshot
vocabulary. Thin digest wrappers would supply the project directory, stable
byte reader, exact-Decimal strict JSON parser, reconciliation diagnostic
formatter, clock callable and current-control snapshot callback. Preserve clock
sampling at entry before controls/archive inspection, and keep existing protocol,
retirement, transport and media inspectors lazy and read-only. Keep
`annotate_remote_write_snapshot_window`, operational-health reconciliation,
`analyse` and report assembly in the coordinator. This is a substantial coherent
observation boundary; the supervisor selects the next stage within the
authorised ceiling of stage 15. No next-stage implementation is included here.

## Extracted in stage 12

Base: `62e86390382fc849e9ada7295eceda3a19c417f0`, verified against the pushed
`origin/codex/modularisation-stage11` tip. Work is isolated on
`codex/modularisation-stage12` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage12`.
The user has removed the old stage-15 ceiling: historical ceiling references
are superseded, and the sequence continues until further digest modularisation
is no longer sensible. This change implements stage 12 only.

`mrs_log_digest_remote_write.py` owns the seven recommended functions (1,314
original function lines): `remote_write_safety_snapshot`,
`reconciliation_archive_snapshot`, `_remote_write_document_identity`,
`_group_active_remote_write_artifacts`, `_canonical_retirement_source_identity`,
`_safe_relative_project_path` and `_read_readonly_archive_bytes`, together with
ten file/receipt/retirement snapshot constants. They form one read-only
observation boundary. The four pure helpers and constants remain direct digest
imports; three thin wrappers retain the original signatures and supply the
current stable reader, exact-Decimal parser, reconciliation diagnostic formatter,
clock, root control/archive snapshots and private archive-read callback.

Path conversion still precedes the safety clock sample, which remains before
control/archive inspection. Read counts/order, stable-file and size protections,
path/symlink and read-only permission handling, hashes, native/Decimal number
distinctions, error types/messages/statuses, grouping, evidence identity and
fail-closed behaviour are preserved. Existing protocol, retirement, transport
and media inspectors remain lazy and read-only. Dependencies are digest →
remote-write snapshots → values, with no reverse import, stored callbacks or
new mutable state. Window annotation, operational-health reconciliation,
`analyse`, report assembly and all operational/publication authority remain in
the coordinator. JSON schema 3, Markdown, CLI, defaults, provenance, clocks,
locks and resume behaviour are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 13,764 | 12,460 (−1,304) |
| `analyse` | 4,702 | 4,702 |
| Remote-write snapshot module | — | 1,402 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |
| Window annotation | 78 | 78 |

Validation: the focused baseline passed 139 tests. After extraction, 156 tests
pass using `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`
and existing temporary-HOME/network isolation: safety hardening, runtime,
digest and Markdown files, plus 11 integration cases for current snapshots,
media incidents, receipt recovery/source isolation, source identity/resume,
schema and copied-digest contracts. Six added cases cover path/clock/read order,
callback result identity, Decimal parsing, private archive reads, diagnostic and
exception propagation, and import safety. The offline-adoption file's digest
assertions are embedded in a startup/recovery workflow; that workflow was not
run for this extraction.

Four pure function bodies and ten constant definitions are source/AST-identical
to stage 11. Three callback bodies match structurally after only the explicit
dependency substitutions; wrapper signatures are retained. All 127 retained
functions/classes and retained non-import module code match, including analysis,
health reconciliation and report assembly. All earlier digest leaves are
byte-identical. Independent import blocks runtime reads, home lookup, services,
network and subprocess activity, and confirms the inspectors remain lazy.

Two temporary comparison tests pass, replaying the five callback cases against
stage 11 and comparing 21 exact typed snapshots plus seven complete JSON and
Markdown report pairs on the same synthetic project. Existing pool/log,
activation and hash-bound archive fixtures cover unconfigured, paused, active
marker, valid read-only archive, writable evidence, malformed marker/control and
symlink-marker states. JSON sizes range from 34,176 to 45,387 bytes; Markdown
from 11,585 to 13,742 bytes. Only the independently verified producer source hash
differs; repository SHAs match each worktree's HEAD (both at the base during
comparison). Fixture file bytes and modes remain unchanged by inspection and
reporting. No comparison scripts/data are committed. The documentation gate
passes for 195 modules; `git diff --check` passes.

These are focused boundary/regression checks, not broader bot validation. No
production/config/state/log/image-pool changes, bot/provider/posting calls,
service control, merge or deployment are included. Earlier worktrees and
branches are preserved.

Recommended next boundary: durable reply evidence loading/validation. The actual
nine-function group is 784 lines: `load_confirmed_reply_receipt_evidence`,
`load_historical_reply_history_evidence`, `_confirmed_conversational_receipt_evidence`,
`_valid_durable_ai_reply_draft`, `_structured_value_sha256`,
`_valid_canonical_utc_timestamp`, `_valid_historical_formatter_metadata`,
`_valid_historical_completed_item` and `_valid_historical_failed_item`.
It needs explicit project paths, `read_stable_private_json_bytes`, the strict
native-number parser, both canonical JSON encoders, timestamp conversion/ISO
parsing callables and the London timezone, plus the current conversational text
validator (or its pure definition with a direct re-export). Preserve byte limits,
schema/identity checks and source-receipt hash reconstruction; no new clock
sample is needed.

The adjacent text-preparation group is another 876 lines:
`_public_reply_text_result`, `_durable_public_reply_text_candidates`,
`_normalised_structured_reply_confirmation` and `enrich_published_reply_text`.
It consumes prepared evidence and needs explicit `bounded_source_refs` and
`epoch_to_london_text` callbacks plus existing text/identity validators. Its
mutation of report events, production-event object identity, bounded warnings,
conflict handling and synthesis ordering make it a distinct subsequent
boundary. Keep event collection, `analyse`, pipeline reconciliation and report
assembly in the coordinator. Neither candidate is implemented here; the
supervisor selects the next fresh session.

## Extracted in stage 13

Base: `5e872ca0a2af909005bdb2644dc745c1fcf1b0a7`, verified against the pushed
`origin/codex/modularisation-stage12` tip. Work is isolated on
`codex/modularisation-stage13` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage13`.
This implements stage 13 only. The former stage-15 ceiling remains superseded;
continue until further digest modularisation is no longer sensible.

`mrs_log_digest_reply_evidence.py` owns the 784-line durable reply-evidence group:
`_structured_value_sha256`, `_valid_durable_ai_reply_draft`,
`_confirmed_conversational_receipt_evidence`, `load_confirmed_reply_receipt_evidence`,
`_valid_canonical_utc_timestamp`, `_valid_historical_formatter_metadata`,
`_valid_historical_completed_item`, `_valid_historical_failed_item` and
`load_historical_reply_history_evidence`. The eight-line pure
`valid_conversational_public_reply_text` definition moves with them. Three pure
helpers and four receipt/history byte-limit and publication-epoch constants
remain direct digest imports. Seven thin wrappers retain the original signatures.

Wrappers supply current project paths, the stable private reader, strict native
JSON parser, distinct atomic/private canonical encoders, timestamp conversion/ISO
parsing, London timezone and text/validator callbacks. Loader-to-validator,
receipt-to-draft and draft/failed-row UTC delegation remain dynamic. No clock
sample is added. Dependencies are digest → reply evidence → shared values;
there are no reverse imports, stored callbacks or import-time runtime effects.

Private-file limits and read/parse/validation order, native bool/int/float
distinctions, exact schemas, timestamps, lane/target/conversation bindings,
draft/formatter validation, source-receipt hash reconstruction, errors/statuses,
copying/sharing and evidence identity are unchanged. Valid long/multiline
published text retains its existing bounds without being revalidated against
current generation policy. The adjacent four text-enrichment functions, event
collection, `analyse`, pipeline reconciliation and report assembly are unchanged.
JSON schema 3, Markdown, CLI, defaults, provenance, clocks, locks, resume and
publication/recovery authority retain their existing behaviour.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 12,460 | 11,746 (−714) |
| `analyse` | 4,702 | 4,702 |
| Reply-evidence module | — | 873 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |
| Adjacent text-enrichment group | 876 | 876 |

Validation: the affected baseline passed 208 tests; after extraction, 214 pass
using `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with
existing temporary-HOME/network isolation. Selection covers reply observability,
digest, Markdown and safety-hardening files plus 21 integration functions for
durable receipt/history authority, exact text, invalid identities/siblings,
draft exclusion, source isolation, conflicts, text bounds and CLI/resume/schema
contracts. Six added cases cover private reader/parser/encoder order, native
numbers, validator delegation and returned identity, London date rollover and
source-hash reconstruction, shallow-copy sharing, UTC exception identity and
inert imports. The five callback cases also pass against stage 12.

Three pure source bodies/ASTs, four constant definitions and all 120 retained
function/class bodies/ASTs match stage 12. Seven extracted bodies match after
only the explicit dependency substitutions; all wrapper signatures are retained.
Retained non-import module code and every earlier digest leaf are unchanged.
The import check blocks runtime reads/writes, home lookup, directory scans,
network/subprocess activity and service/logging initialisation.

Two temporary comparison tests pass, including ten exact typed loader results
and five complete JSON/Markdown pairs on shared synthetic fixtures with fixed
paths/time: valid v4 conversational evidence with long/multiline text, invalid
receipt shape, completed history, an invalid failed sibling, and hash-bound
completed history with a valid failed sibling. JSON bytes (37,620–63,258) and
Markdown bytes (12,652–16,620) match after only independently verified producer
source/commit provenance substitutions. Fixture bytes/modes remain unchanged.
Comparison scripts/data are not committed. The documentation gate passes for
196 modules; `git diff --check` passes.

These are focused extraction checks, not broader bot validation. No production,
configuration, durable state, logs or image pools are changed; no bot/provider/
posting calls, service control, merge or deployment are included. Earlier
worktrees and branches are preserved.

Recommended next digest boundary: the distinct 876-line public text-enrichment
group, `_public_reply_text_result` (79), `_durable_public_reply_text_candidates`
(124), `_normalised_structured_reply_confirmation` (52) and
`enrich_published_reply_text` (621). It consumes prepared runtime/receipt/history
evidence and report events. Supply `bounded_source_refs` and
`epoch_to_london_text` explicitly, retain conversational text validation from the
new evidence leaf and shared UTF-8/post-ID/hash validators from values, and give
the warning limit a coherent owner. Preserve dynamic helper delegation where
needed, production-event object identity, event mutation, bounded warnings,
conflict handling and synthesis ordering. This remains a useful boundary without
moving event collection, `analyse`, pipeline reconciliation or report assembly.
It is assessed here but left for the supervisor's next fresh session; bot
refactoring remains outside this sequence's current scope.

## Extracted in stage 14

Base: `b28f415f4c2afcd2603db584142a4e94520442cd`, verified against the pushed
`origin/codex/modularisation-stage13` tip. Work is isolated on
`codex/modularisation-stage14` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage14`.
This implements stage 14 only. There is no stage-number ceiling; continue until
further digest modularisation is no longer sensible.

`mrs_log_digest_reply_text.py` owns the complete 876-line public reply-text group:
`_public_reply_text_result` (79), `_durable_public_reply_text_candidates` (124),
`_normalised_structured_reply_confirmation` (52) and
`enrich_published_reply_text` (621). The pure normaliser is a direct digest import;
three thin wrappers preserve the other original signatures. The unchanged
`PUBLISHED_REPLY_WARNING_LIMIT = 100` belongs to the text module and retains its
digest alias, supplied explicitly on each enrichment call.

Wrappers pass prepared evidence, report/event objects, `bounded_source_refs`,
`epoch_to_london_text` and current normalisation/candidate/result callbacks.
Candidate validation uses the current digest alias of the stage 13 evidence
leaf's `valid_conversational_public_reply_text`; shared UTF-8/post-ID/hash helpers
remain in values. No upward imports, stored callbacks or dependency containers
are introduced. The leaf performs no file/home/configuration access, clock sample
or provider calls. Evidence loading, event collection, `analyse`, pipeline and
operational reconciliation, and report assembly retain their existing owners.

Report/event mutation and identity, production-object filtering, source/self-test
isolation, prepared-evidence precedence, exact long/multiline text, unavailable
versus conflicting text, lane/target/reply identities, duplicate/conflict rules,
copying/sharing, bounded provenance and omission counts, warning caps/order,
epoch conversion and synthetic-event insertion order are unchanged. Drafts and
unconfirmed/nonauthoritative observations gain no publication authority. JSON
schema 3, Markdown, CLI, defaults, provenance, clocks, locks and resume are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 11,746 | 10,933 (−813) |
| `analyse` | 4,702 | 4,702 |
| Reply-text module | — | 917 |
| Reply-evidence module | 873 | 873 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: the focused baseline passed 242 tests; after extraction, 246 pass
with `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
existing temporary-HOME/network isolation. Selection covers digest, Markdown,
reply observability, safety hardening, stage 13 evidence and new text-boundary
tests, plus 34 integration functions for exact text, receipt/history authority,
invalid evidence/identities/siblings, draft exclusion, source/self-test isolation,
provenance, conflicts/warning caps, text bounds, event-scan bounds and
schema/source-identity/resume contracts. Three added callback tests check call
order, event identity/mutation, precedence, candidate/reference sharing, shallow
status copying and the dynamic warning limit; all three also pass on stage 13.
The existing inert-import check now covers the text module and its digest aliases,
blocking runtime reads/writes, home lookup, scans, network/subprocess activity and
logging/service initialisation.

The pure normaliser definition and all four moved bodies/ASTs match stage 13
after only explicit callback substitutions. All original digest signatures,
wrapper argument forwarding, 123 retained function/class bodies/ASTs, retained
non-import module code, earlier imports, warning constant and 17 earlier digest
leaves are verified unchanged. Ten representative direct helper results and
direct enrichment match typed values, input/output sharing, event mutation and
synthesis order. Six complete JSON/Markdown report pairs match on shared existing
synthetic fixtures with fixed paths/time: bounded historical provenance, confirmed
reply lanes/draft exclusion, text conflict, structured-only history, completed
durable history and a v4 conversational receipt with long/multiline text. JSON
bytes (35,808–62,520) and Markdown bytes (11,642–15,955) match after only independently
verified producer-source/commit provenance substitutions. Fixture bytes/modes are
unchanged. Temporary comparison scripts/data are not committed. The documentation
gate passes for 197 modules; `git diff --check` passes.

These are focused extraction checks, not broader bot validation. No production,
configuration, durable state, logs or image pools are changed; no bot/provider/
posting calls, service control, merge or deployment are included. Earlier
worktrees and branches are preserved.

Recommended next digest boundary: operational-error/incident reporting, centred
on `summarise_operational_error_health` (1,868 lines). Its adjacent helpers
`_incident_exception_line`, `_normalise_incident_text`, `classify_operational_error`,
`_event_time`, `_base_remote_control_key`, `_remote_control_scope`,
`_remote_operation_scope_for_lane` and `_explicit_remote_pause_scope` add 183 lines
and give incident signatures, categories and pause scopes one practical owner.
Keep existing digest aliases and dynamic helper delegation. The three scope
vocabulary mappings can move with that group, retaining digest aliases.

The summary already accepts errors, events, receipts, lifecycle, remote-write
transactions, handled restrictions, confirmed-receipt events, prepared safety,
generation/window times and current-snapshot authority. Supply the fallback
`datetime.now` and epoch conversion explicitly, preserving their call sites;
pass `bounded_source_refs`, `seconds_between`, `is_deleted_or_inaccessible_tweet_403`
and `annotate_remote_write_snapshot_window` as named callbacks while their shared
owners remain in the digest. Reuse time/text/lane and terminal-outcome helpers
from values. Preserve identity-based grouping, current versus historical
resolution, snapshot mutation and incident ordering. Snapshot loading, window
annotation ownership, event collection and report assembly need not move.
This is a substantial but coherent observation group without operational actions;
it is assessed only, for the supervisor's next fresh session. Bot refactoring
remains outside scope.

## Extracted in stage 15

Base: `38930ab232561d6cf2f8accf96fee2ea5c0f19ac`, verified against the pushed
`origin/codex/modularisation-stage14` tip. Work is isolated on
`codex/modularisation-stage15` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage15`.
This implements stage 15 only. There is no stage-number ceiling; the supervisor
can continue in fresh sessions until further digest modularisation is no longer
sensible. Bot refactoring remains outside scope.

`mrs_log_digest_incidents.py` owns the complete operational incident group:
`summarise_operational_error_health` (originally 1,868 lines),
`_incident_exception_line`, `_normalise_incident_text`, `classify_operational_error`,
`_event_time`, `_base_remote_control_key`, `_remote_control_scope`,
`_remote_operation_scope_for_lane` and `_explicit_remote_pause_scope` (183 adjacent
helper lines). Three pure definitions are direct digest imports; six thin wrappers
retain every other original digest signature. The three unchanged scope mappings
belong to the incident module, retain digest aliases and are supplied as current
named data to the relevant wrappers.

Current incident/classification/scope helpers, `bounded_source_refs`,
`seconds_between`, `is_deleted_or_inaccessible_tweet_403` and
`annotate_remote_write_snapshot_window` are explicitly delegated. Shared scalar,
text, lane and terminal-outcome helpers retain their values owner. `datetime.now`
and `datetime.fromtimestamp` are passed as callbacks; both original conditional
clock samples, all three epoch-conversion sites and `datetime.min` semantics are
preserved. There are no upward imports, stored callbacks or dependency containers.
The module performs no file/home/configuration access or provider calls.

Categories/signatures, grouping and object identity, current/historical resolution
and retirement, restrictions versus failures, pause scopes and recovery chronology,
snapshot mutation and empty-snapshot fallback, authoritative-window decisions,
source references, counts/order/labels and exceptions are unchanged. Snapshot
loading/window annotation, remote-write transaction parsing, media correlation,
event collection, `analyse` and report assembly retain their existing owners.
Publication authority, source/self-test isolation, JSON schema 3, Markdown, CLI,
defaults, provenance, locks and resume are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 10,933 | 8,922 (−2,011) |
| `analyse` | 4,702 | 4,702 |
| Incident module | — | 2,184 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: the affected digest, Markdown, reply-observability and safety-hardening
baseline passed 173 tests. The final focused selection passes 212 tests with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`, using existing
temporary-HOME/network isolation. It includes those four files, 12 new boundary
cases, the existing inert-import check extended to incidents, and 21 selected
integration functions (24 cases) covering media correlation/bounded provenance,
unrecovered fallback/deduplication, receipt recovery/source isolation, current and
historical state, malformed reply confirmations, schema/source identity and resume.
The bot harness was not run. Existing digest cases cover durable incident resolution,
new receipts after reconciliation, unmatched/current barriers and transaction shapes.

All 12 new boundary cases also pass against stage 14. They check conditional clock
and epoch order, dynamic classification/scope/helper/data delegation, source-reference
sharing, original error objects, snapshot mutation before callback failure, and
current versus historical window eligibility with shared retirement/ledger evidence.
The existing import check verifies inert loading and identity of all six direct
function/mapping aliases, rejecting runtime file access, home lookup, scans,
network/subprocess activity and logging initialisation.

All nine moved bodies/ASTs match stage 14 after 54 exact callback/data/clock
substitutions; the three pure definitions match verbatim. Original signatures and
wrapper forwarding, all 117 retained definitions, other coordinator code/imports,
the three mappings and 18 earlier digest modules are verified unchanged. Eight
representative direct incident calls match typed values, ordering, input/output
sharing and mutation. Four complete JSON/Markdown report pairs match on shared
existing synthetic fixtures with fixed paths/time: bounded media provenance,
unrecovered media fallback, self-test receipt removal and a durably resolved reply
ambiguity. JSON bytes (36,288–44,634) match after only the independently verified
producer-source hash substitution; repository HEAD provenance is independently
verified and equal at comparison time. Markdown bytes (12,477–14,315) and fixture
bytes/modes/timestamps match exactly. Temporary comparison scripts/data are excluded.
The documentation gate passes for 198 modules; `git diff --check` passes.

These are focused extraction checks. No production checkout, configuration, durable
bot state, logs or image pools were changed; no bot/provider/posting calls, service
control, merge or deployment occurred. Earlier branches/worktrees are preserved.

Recommended next boundary: structured legacy reply-pipeline observations still
inside `analyse`. `conversational_evidence_fields` (89 lines) and nine handlers
(395 lines) cover `reply_strategy_decision`, `reply_strategy_outcome`,
`reply_target_terminal`, `reply_strategy_rejection`, `ai_reply_pipeline_decision`,
`ai_reply_pipeline_stage_summary`, `ai_reply_pipeline_effective_outcome`,
`ai_reply_pipeline_failure` and `ai_reply_pipeline_outcome`.
`reconcile_reply_pipeline_effective_outcomes` adds 113 lines of related event
mutation. Give this roughly 597-line group practical ownership alongside the
existing reply-pipeline/strategy reporting modules. Supply parsed event objects,
record timestamps, current `add_event` and `add_or_merge_local_rejection` callbacks,
`bounded_event_string_list` and `normalise_majority_review_telemetry`; reuse scalar,
post-ID, lane and terminal-outcome helpers from values. Preserve helper delegation,
emitted-event identity/order and the coordinator's parsing, dispatch and source
isolation. This removes a substantial group from `analyse` without changing
publication authority.

Other substantial groups remain: engagement/quote-publication correlation
(`correlated_quote_post_fields`, 409 lines, its evidence/warning helpers and the
main/account-root/engagement structured handlers), author-progress reporting
(`current_author_no_reply_strike_progress`, 320 lines, plus backlog/quarantine
observations), and transaction/media/receipt observations
(`parse_remote_write_transaction_event`, 149; `summarise_main_post_receipt_lifecycle`,
115; `correlate_media_upload_incidents`, 71, with adjacent helpers). The engagement
group needs prepared identity/correlation dictionaries, strict publication evidence,
validators and source-reference/warning callbacks; author progress needs prepared
state and window times. Assess these as coherent owners, retaining report assembly,
source collection and CLI/resume orchestration where that remains sensible. No
following-stage implementation is included here.

## Extracted in stage 16

Base: `f697b7d9cf1a0c7d53dfbc6fb93533617a9af9d9`, verified against the pushed
`origin/codex/modularisation-stage15` tip. Work is isolated on
`codex/modularisation-stage16` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage16`.
This implements stage 16 only. There is no stage-number ceiling; the supervisor
can continue in fresh sessions until further digest modularisation is no longer
sensible. Broader bot refactoring remains outside scope.

`mrs_log_digest_quote_publication.py` owns the three publication/experiment
validators, warning deduplication/bounds, evidence retention and invalid-evidence
recording, the four structured main/account-root/confirmation/trial-outcome
handlers, `correlated_quote_post_fields`, and post-scan quote-event enrichment
and confirmed experimental publication assembly. Three thin digest wrappers
preserve the original validator signatures and current helper/constant delegation.
Eight unchanged engagement vocabulary definitions retain direct digest aliases;
the existing experiment-state summary consumes those aliases. The warning limit
retains its values-module owner.

Local adapters preserve dynamic source checks, warning omission accounting and
correlation callbacks. Existing correlation maps, invalid-evidence sets, warning
lists/keys/counters, payloads, timestamps, source-reference helpers and validators
are explicit arguments. No upward imports, stored callbacks or dependency
containers were introduced. Strict parsing, dispatch, the outer record loop,
source tracking, event insertion and overall report assembly remain in `analyse`.
The daily-meme pending-state update remains immediately after main-post evidence
handling. Observed-success counting reads the same prepared evidence objects.

Canonical public IDs, exact full/multiline text and hashes, duplicate/conflict
semantics, field precedence and missing-versus-null distinctions, reference
bounds/omissions, warning cap/count/order/omissions and authority/source/self-test
filtering are unchanged. Enrichment mutates the original production events and
sorts the original outcome/warning lists; publication rows retain projected
value/reference sharing. JSON schema 3, exact Markdown, CLI, defaults, provenance,
clocks, locks and resume are unchanged. The new module performs no file,
home/configuration, clock or provider access.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 8,922 | 8,066 (−856) |
| `analyse` | 4,702 | 3,905 (−797) |
| Quote-publication module | — | 1,167 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: 162 tests passed with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`, using the
established temporary-HOME/network isolation. The 156 existing checks cover the
digest, Markdown and safety-hardening files plus 19 selected integration
functions: all 15 requested quote/engagement fixtures, compact experiment state,
JSON schema/source identity and source-isolated resume. Five new boundary cases
check current validator/helper/vocabulary delegation, lazy source references,
warning-limit/duplicate omission accounting, payload/conflict/reference identity
and in-place enrichment/sorting/sharing. The existing inert-import test is
extended by one case to the new module and its eight digest aliases. It rejects
runtime file/home access, scans, network/subprocess activity and logging setup.
No entire bot harness was run.

The three pure validator bodies match stage 15 verbatim. All four moved closures
match after only two lazy source-check substitutions and omission-count returns.
The four handler bodies match after nine record-timestamp and four lazy
source-reference substitutions. Post-scan enrichment/assembly matches its
original AST with only a return added. All original public/local signatures,
eight vocabulary definitions, 118 retained top-level definitions and all other
coordinator text are unchanged after excluding the explicit extraction sites.
Earlier reporting modules are unchanged.

Four complete JSON/Markdown pairs match on shared existing fixtures for ordinary
publication, treatment publication, conflicting/bounded evidence and self-test
source isolation. Producer source hashes and repository HEADs were independently
verified before replacing only the producer-hash field; HEADs were equal to the
base at comparison time. JSON bytes (34,992–41,366) then match exactly; Markdown
bytes (12,464–15,233) and fixture bytes/modes/timestamps match without substitution.
Temporary scripts/data are excluded. The documentation gate passes for 199
modules; `git diff --check` passes.

These checks establish extraction parity on the affected behaviour and selected
fixtures, not an exhaustive bot replay. No production checkout, configuration,
durable bot state, logs or image pools were changed; no bot/provider/posting
calls, service control, merge or deployment occurred. Earlier worktrees and
branches are preserved.

Recommended next boundary remains the structured legacy reply-pipeline group:
`conversational_evidence_fields` (89 lines), the nine strategy/pipeline branches
(395 lines including their dispatch predicates; 386 handler-body lines) and
`reconcile_reply_pipeline_effective_outcomes` (113 lines). This roughly 597-line
group still coherently prepares evidence fields, emits decision/stage/terminal
observations and reconciles the same event objects. Give it ownership alongside
the existing reply-pipeline/strategy reporting modules, supplying current
`add_event`, `add_or_merge_local_rejection`, text/list/telemetry validators and
lane/terminal helpers while retaining parsing, source isolation and event
insertion in the coordinator. Preserve the reconciliation order and local
rejection/outcome precedence. No following-stage implementation is included.

## Extracted in stage 17

Base: `21e7b5ce49b73c19938e780542c835ceba064cd9`, verified against the pushed
`origin/codex/modularisation-stage16` tip. Work is isolated on
`codex/modularisation-stage17` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage17`.
This implements stage 17 only. There is no stage-number ceiling; the supervisor
can continue in fresh sessions until further digest modularisation is no longer
sensible. Broader bot refactoring remains outside scope.

The existing `mrs_log_digest_reply_strategy.py` now owns
`conversational_evidence_fields` and the complete `reply_strategy_decision`,
`reply_strategy_outcome`, `reply_target_terminal` and `reply_strategy_rejection`
projections. The existing `mrs_log_digest_reply_pipeline.py` owns the complete
`ai_reply_pipeline_decision`, `ai_reply_pipeline_stage_summary`,
`ai_reply_pipeline_effective_outcome`, `ai_reply_pipeline_failure` and
`ai_reply_pipeline_outcome` projections, plus
`reconcile_reply_pipeline_effective_outcomes`. The original group comprised an
89-line evidence helper, 386 handler-body lines (395 with dispatch predicates)
and 113 lines of reconciliation. No new module or dispatch framework was needed.

Handlers receive parsed payloads, timestamps and their current event, rejection,
evidence, bounded-list or majority-telemetry callbacks. The local evidence adapter
retains its signature and supplies the current `bounded_event_string_list`;
shared scalar/text/identity validators retain their values owner. The digest
reconciliation API retains its signature and delegates its current
`_normalise_lane`. It remains caller-invoked: the existing report path does not
call it, and this extraction adds no reconciliation pass.

Strict parsing, all branch predicates/positions, `add_event`, statistics,
`add_or_merge_local_rejection`, source/provenance tracking and report assembly
remain in the coordinator. Evidence counts, defaults and missing/null/unknown
distinctions, telemetry bounds, reason classification, duplicate coalescing,
callback-return identity/order and later in-place reconciliation are unchanged.
Public/private aliases, JSON schema 3, Markdown, source/self-test isolation,
clocks, CLI/defaults and resume retain their existing behaviour. The owners have
no runtime I/O, upward imports, stored callbacks or shared analysis state.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 8,066 | 7,552 (−514) |
| `analyse` | 3,905 | 3,486 (−419) |
| Reply-pipeline module | 437 | 916 |
| Reply-strategy module | 506 | 728 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: the baseline passed 183 tests and the final selection passed 184,
using `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
established temporary-HOME/network isolation. Both selections cover
`test_digest_reply_observability.py`, `test_mrs_log_digest.py`,
`test_digest_markdown.py`, `test_digest_safety_hardening.py` and nine digest-only
integration cases: schema/source identity, two source-isolated reply/resume
cases, nested/nonfinite display validation, interleaved reply identity, transport
counter semantics and structured-only confirmation. One new boundary test checks
returned-event identity, unavailable-lane rejection coalescing, original
timestamps/counts and later mutation through the current lane helper. Existing
assertions are retained; no broad bot harness was run.

All nine moved handler ASTs match after only record-timestamp substitution. The
evidence/reconciliation bodies match exactly as ASTs; explicit callback arguments,
original signatures/aliases and existing owner functions were checked. Every
other coordinator byte matches the base after excluding the extraction sites.
Three complete JSON/Markdown pairs reuse `pipeline_digest_lines`,
`digest_event_line` and `write_digest_log` with shared fixed paths/time: published
outcomes (38,699 / 11,652 bytes), local rejection/duplicates (38,317 / 12,076) and
unresolved telemetry (38,840 / 12,025). JSON bytes match after replacing only the
independently verified producer-source hash; independently verified repository
HEADs were equal at comparison time. Markdown, warnings, typed values, order,
relationships and fixture bytes/modes/timestamps match without substitution.
Temporary comparison code/data are excluded. The documentation gate passes for
199 modules and `git diff --check` passes.

These checks establish focused extraction parity, not an exhaustive bot replay.
No production checkout, configuration, durable bot state, logs or image pools
were changed; no bot/provider/posting calls, service control, merge or deployment
occurred. Earlier branches/worktrees are preserved.

Recommended next boundary: prepared runtime-state and author-progress reporting.
Give `current_author_no_reply_strike_progress` (320 lines),
`summarize_latest_state` (92), `summarize_engagement_question_experiment_state`,
`state_list_count`, `state_list_head`, `state_list_tail`, `epoch_to_human` and
`epoch_to_london_text` a coherent state-reporting owner alongside the existing
runtime readers. Include the two backlog/quarantine dispatch bodies and their
`mention_control_events`/counts/skipped-evaluation projection. Supply prepared
state/configuration and statuses, source metadata, generation/observation times,
current clock/conversion helpers, unknown-value and engagement vocabulary,
`AUTHOR_EVALUATION_QUARANTINE_*` policies and author/epoch bounds. Reuse values
validators; pass current event callbacks and statistics and preserve both existing
counter increments. Keep runtime reads, strict parsing, source tracking,
report attachment and CLI/resume decisions with their current owners.

Record/source-input ownership is another substantial candidate: `Record`,
`iter_records`, `read_records`, source-reference helpers, input summaries/coverage
and fingerprint/boundary helpers. Its dependencies on file discovery, physical
ordering, dataclass identity, source indexes and resume multiplicity make it a
less immediate boundary than prepared state reporting. Transaction/media/receipt
observation and report preparation also remain useful: `parse_x_request_start`,
`classify_x_request_endpoint`, `parse_remote_write_transaction_event` (149 lines),
`summarise_main_post_receipt_lifecycle` (115), `correlate_media_upload_incidents`
(71) and adjacent media predicates/path lookup. They need prepared records,
time-window/text bounds, source-reference callbacks and current classifiers,
while retaining request/source collection and publication authority in the
coordinator. These candidates are assessed only; no stage 18 implementation is
included.

## Extracted in stage 18

Base: `474fc0239fc449aceb46c3e5d0e0938000c3785d`, verified against pushed
`origin/codex/modularisation-stage17`. Work is isolated on
`codex/modularisation-stage18` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage18`.
This implements stage 18 only; there is no stage-number ceiling. The supervisor
can continue in fresh sessions until further digest modularisation is no longer
sensible. Broader bot refactoring remains outside scope.

`mrs_log_digest_state_reporting.py` owns the existing `state_list_count`,
`state_list_tail`, `state_list_head`,
`summarize_engagement_question_experiment_state`, `summarize_latest_state`,
`current_author_no_reply_strike_progress`, `refresh_current_health_headline` and
`refresh_derived`, plus both small epoch converters. Digest signatures remain
through aliases or thin wrappers supplying current helpers, prepared data and
vocabulary. The author-progress limits, five evidence-policy labels, invalid-state
label and cooldown-field vocabulary move together with unchanged digest aliases;
experiment vocabulary retains its quote-publication owner.

The verified base samples `datetime.now()` **unconditionally at entry** to
`summarize_latest_state`; the scope description's conditional-fallback wording
does not match that implementation. The supplied `clock_now` preserves that exact
site/order. Epoch converters receive current `datetime.fromtimestamp` and London
timezone inputs. Author progress retains `state_observed_at or generation_time`
and samples no extra clock. Strict types, unknown/missing/invalid distinctions,
current/prior/legacy evidence treatment, expiry chronology, ordering, author/epoch
bounds and omission counts are unchanged. Shallow copying/sharing and in-place
derived/headline mutation retain their original behavior.

The owner also holds both complete structured observation bodies (62 lines):
`mention_backlog_started/progress/completed/reset` and
`author_evaluation_quarantine_started/skip/expired`. Their exact event-name sets,
branch predicates/positions, `add_event` and source tracking remain in `analyse`.
The post-scan projection returns its original event list and Counter plus the
explicit skipped-evaluation sum. Report attachment/count conversion is unchanged.
Each event still increments statistics twice, once through `add_event` and once
through its handler; the projected event count remains one. Loading,
saved-context application, backscan, persistence and report orchestration remain
with their current owners. No validation or behavior cleanup was added.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 7,552 | 6,905 (−647) |
| `analyse` | 3,486 | 3,419 (−67) |
| State-reporting module | — | 897 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **232 passed**, final **239 passed**, using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with the
existing temporary-HOME/network isolation. Selection: `test_mrs_log_digest.py`,
`test_digest_runtime.py`, `test_digest_safety_hardening.py`,
`test_digest_markdown.py`, `test_digest_reply_observability.py`; the 22 digest-only
backlog/quarantine/author-progress/source tests from
`test_digest_reports_backlog_quarantines_and_skipped_pipeline_evaluations` onward
in `test_mention_backlog_author_quarantine.py`; and six digest-only integration
cases for compact engagement state, authoritative metrics, runtime state after
the window, no-`until` state, resume/current-state separation and state-derived
strike provenance. The bot-operation chronology/restart case and the full bot
harness were not run. Six new boundary tests cover current conversion/helpers,
clock order, vocabulary, limits, copying/mutation and event/Counter/source
identity; the existing guarded import test now includes the new owner.

All ten helper bodies match the base ASTs after only explicit clock/conversion
substitutions. Original signatures, nine constants/aliases, both complete handler
bodies (record timestamp substitution only), unchanged dispatch predicates and
post-scan projection were checked, including every explicit callback/data
argument. Every remaining coordinator byte matches after excluding extraction
sites. The documentation gate passes for **200 modules** and `git diff --check`
passes.

Three complete JSON/Markdown pairs reuse `write_digest_log`, `digest_event_line`,
`pipeline_digest_lines` and `digest_author_no_reply_record` with shared fixed
paths/time: fresh author progress (**49,284 / 13,841 bytes**), stale/invalid state
(**45,495 / 12,804**) and saved context/cooldowns (**49,798 / 14,635**). JSON bytes
match after replacing only independently verified producer-source hashes;
independently verified repository HEADs were equal at comparison time. Markdown,
warnings and fixture bytes/modes/mtimes match without substitution. Temporary
comparison code/data are excluded. These are focused extraction checks, not an
exhaustive bot replay. JSON schema 3, Markdown, CLI/defaults, provenance,
locks/resume and self-test/source isolation are unchanged; the owner has no I/O,
upward imports, stored callbacks or dependency container.

Recommended next boundary: transaction/media/receipt observation preparation,
about 414 existing helper lines: `parse_x_request_start`,
`classify_x_request_endpoint`, `parse_remote_write_transaction_event` (149),
`summarise_main_post_receipt_lifecycle` (115), `correlate_media_upload_incidents`
(71), the media predicates and `find_recent_media_path`. Supply prepared records
or their timestamp/text/path fields and current `short`, source-reference,
fingerprint, endpoint and media-classifier callbacks. Preserve chronology,
matching windows, unresolved receipt identity and invocation positions; retain
request/source collection, statistics and publication authority in the digest.

Record/input ownership is also useful: `Record`, `iter_records`, `read_records`,
`filter_records_by_time`, `record_source_ref`, `safe_source_logger`,
`bounded_source_refs`, fingerprint/tail/boundary helpers, `summarize_input_files`
and `input_retention_coverage`. It needs one shared frozen record identity,
explicit log regex, parsers/time conversion, current readers/formatters, source
indexes and reference/tail limits. Preserve rotation ordering, duplicate
multiplicity, warning/read order and inclusive/exclusive resume boundaries; keep
discovery and resume policy with the coordinator. This crosses more input/I/O
ownership than the prepared transaction group.

Provider observations form a separate 187-line helper group:
`xai_usage_stage_from_msg`, `provider_usage_provider_from_msg`,
`parse_xai_call_start`, `parse_xai_usage_from_msg`,
`xai_usage_context_from_pending`, `unknown_xai_usage_context`,
`normalise_active_xai_call_attempt`, `_cache_input_metric` and
`summarize_xai_usage_event`. They need prepared pending contexts/records, current
lane and usage converters, stage classification and cache-metric delegation.
Keep pending-call/source correlation, resume decisions and cost reporting with
their existing owners. These candidates are assessed only; no following-stage
implementation is included.

No production checkout, configuration, durable state, logs or image pools were
changed; no bot/provider/posting calls, service control, merge or deployment
occurred. Earlier worktrees and branches are preserved.

## Extracted in stage 19

Base: `dd64509fe29fdb69c9e358f2c284702a8bd88f92`, verified against the live
`origin/codex/modularisation-stage18`. Work is isolated on
`codex/modularisation-stage19` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage19`.
This implements only stage 19. The supervisor continues in fresh sessions until
further digest modularisation is no longer sensible; there is no stage-number
ceiling, and broader bot refactoring remains outside scope.

`mrs_log_digest_records.py` owns the shared frozen `Record`, `safe_source_logger`,
`record_source_ref`, `bounded_source_refs`, `record_fingerprint`,
`resume_fingerprint_tail`, `locate_resume_fingerprint_tail`,
`resume_boundary_fingerprint_counts`, `filter_resume_boundary_records`,
`iter_records`, `read_records`, `filter_records_by_time`, `summarize_input_files`,
`input_retention_coverage` and `combine_input_warnings`. Both compiled regexes
(`LOG_RE`, `SAFE_SOURCE_LOGGER_RE`), `SOURCE_REFERENCE_LIMIT` and
`RESUME_FINGERPRINT_TAIL_LIMIT` move with unchanged digest aliases. Four helpers
are direct aliases; ten thin wrappers supply current regex/constructor,
parser/time conversion, reader, formatter, logger, fingerprint and tail-limit
inputs. The class's defining module changes; digest and owner expose the same
class, with the original frozen fields, constructor and defaults.

Discovery/explicit-log selection, self-test authority, resume reading/writing,
context/backscan policy, stable-byte/strict-JSON primitives, locks and
`run_digest`/CLI remain unchanged. Producer `__file__`/repository provenance and
project/home defaults remain anchored to the digest. No validation cleanup,
clock resampling or persistence redesign was introduced. The owner reads/stats
only supplied log paths when called; importing it performs no log scans/reads,
home/configuration resolution, state writes or service initialisation. It has no
upward imports, stored callbacks or dependency container.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 6,905 | 6,614 (−291) |
| `analyse` | 3,419 | 3,419 |
| Record/input owner | — | 455 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **221 passed**, final **228 passed**, using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with the
existing temporary-HOME/network isolation. Selection: `test_mrs_log_digest.py`,
`test_digest_safety_hardening.py`, `test_digest_reply_observability.py`,
`test_digest_markdown.py`, `test_digest_historical_events.py` and
`test_generated_image_pool_runway_digest.py`; the guarded import test in
`test_digest_runtime.py`; only the two source-reference/logger tests (three
cases) in `test_mention_backlog_author_quarantine.py`; and nine digest integration
cases covering stable source identity across resume filtering, schema roots,
self-test pending identity across resume, source-isolated confirmed receipts and
quote/meme pending state, resume/current-state separation, generated-spacing
resume and correlated/bounded media source references. Existing safety cases
cover numeric rotations, missing inputs, duplicate cardinality, clock rollback,
resume-tail/boundary multiplicity and source-isolated state/config backscan.
The full bot harness was not run.

Six new boundary tests cover shared frozen type/fields, lazy parser and
constructor delegation, continuation/replacement decoding and parser errors,
current reader/stat/warning order including mtime ties and stat failure,
physical summary endpoints, current source/fingerprint/retention helpers and
tail limits. The existing guarded import case includes the new owner. All prior
assertions remain. Exact moved bodies and ASTs match stage 18 after only explicit
regex, tail-limit, constructor, `strptime` and `fromtimestamp` substitutions;
helper callbacks retain their original body names. Original signatures, class
AST, four definitions, all 19 imports and ten complete wrapper calls were checked.
Every remaining coordinator byte matches after excluding extraction/import
sites. Documentation coverage passes for **201 modules**; `git diff --check`
passes.

Direct record/source/fingerprint/filter results and three complete JSON/Markdown
pairs reuse shared `write_digest_log`, `digest_event_line`,
`pipeline_digest_lines` and `log_line` fixtures with fixed paths/time: physical
and duplicate ordering with missing rotation (**41,042 / 12,292 bytes**), resumed
fingerprint tail (**35,288 / 11,464**) and unmatched-tail timestamp-boundary
fallback (**35,333 / 11,442**). The two initial resume-seeding pairs also match.
JSON bytes match after replacing only independently verified producer-source
hashes; independently verified repository HEADs were equal at comparison time.
Markdown, warning bytes, complete resume files (including multiplicity and tail)
and input bytes/modes/mtimes match without substitution. Temporary comparison
code/data are excluded. These are focused extraction checks, not an exhaustive
replay; schema 3, runtime authority, defaults, source identity and resume behavior
remain unchanged.

Recommended next boundary: transaction/media/receipt observation preparation.
The **414-line** helper group is `parse_x_request_start`,
`classify_x_request_endpoint`, `parse_remote_write_transaction_event`,
`summarise_main_post_receipt_lifecycle`, `is_media_v2_request_failure`,
`is_media_fallback_warning`, `is_media_v1_success`, `is_media_v1_failure`,
`is_main_post_success`, `find_recent_media_path` and
`correlate_media_upload_incidents`. It can use the shared `Record` owner and
explicit current `short`, `seconds_between`, source-reference/fingerprint,
endpoint-classifier and media-predicate callbacks. Preserve correlation windows,
physical chronology, suppression identity and unresolved receipt matching.

To also reduce `analyse`, assess its nested `add_receipt_event` and
`add_confirmed_reply_receipt_event` (**40 lines**) with the adjacent legacy receipt
matching/dispatch group (**146 lines**). Supply the selected record, source
indexes/classification, receipt lists, statistics and current formatting/source
callbacks; pass and return the explicit pending confirmed-receipt state. Keep
production/self-test state switching and dispatch/continue order in the
coordinator. The related request/transaction projection block (**46 lines**) and
`add_reply_media_context_event` with its two legacy match branches (**9 + 33
lines**) are further bounded observations; request/source selection and
publication authority must stay with the coordinator.

Provider observations remain a separate **187-line** helper candidate:
`xai_usage_stage_from_msg`, `provider_usage_provider_from_msg`,
`parse_xai_call_start`, `parse_xai_usage_from_msg`,
`xai_usage_context_from_pending`, `unknown_xai_usage_context`,
`normalise_active_xai_call_attempt`, `_cache_input_metric` and
`summarize_xai_usage_event`. The corresponding call-start/usage observation block
inside `analyse` is **79 lines**, including attempt matching/mutation, errors and
statistics. It needs prepared pending context/record data, current lane/usage
converters, stage/cache helpers and explicit active-attempt state. Keep source
switching, resume decisions and cost reporting with their existing owners.
These candidates are assessed only; no following stage is implemented here.

No production checkout, configuration, durable state, logs or image pools were
changed; no bot/provider/posting calls, service control, merge or deployment
occurred. Earlier worktrees and branches are preserved.

## Extracted in stage 20

Base: `0054a8c6dfa9455e03cbaf8400bf5962af42efe0`, verified against the pushed
`origin/codex/modularisation-stage19`. Work is isolated on
`codex/modularisation-stage20` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage20`.
Only stage 20 is implemented; further stages remain for fresh supervisor-led
sessions while digest modularisation remains useful, without a stage-number ceiling.

`mrs_log_digest_transactions.py` owns the **414-line** helper group identified
in stage 19: `parse_x_request_start`, `classify_x_request_endpoint`,
`parse_remote_write_transaction_event`, `summarise_main_post_receipt_lifecycle`,
`is_media_v2_request_failure`, `is_media_fallback_warning`, `is_media_v1_success`,
`is_media_v1_failure`, `is_main_post_success`, `find_recent_media_path` and
`correlate_media_upload_incidents`. Eight unchanged aliases and three thin
wrappers retain digest signatures/defaults and current endpoint-classifier,
`short`, `seconds_between`, source-reference/fingerprint/bounding, recent-media
lookup and predicate delegation. The owner imports the shared `Record`; shared
scalar helpers remain with their existing owners.

The owner also contains the two receipt builders, the adjacent legacy receipt
handler, request/transaction projections, and the reply-media builder and two
legacy match branches. Explicit records/messages, field dictionaries, source
indexes/classification, lists/statistics and callbacks preserve all bodies,
kwargs/default precedence, pending-lane fallback, counts/order and object
sharing. Request events remain shared with the latest-source index; transaction
events retain the parser's dictionary. Passing field kwargs separately avoids
collisions with the new dependency parameter names.

The confirmed-receipt builder returns the exact current or replacement pending
dictionary. The root closure rebinds it at the original call site: `written` and
`reconciled` replace it with the existing truthy identity fields, `removed`
returns a new empty dictionary after filling only missing kwargs, and other
kinds preserve the original object. Production/self-test state switching,
request/source selection, parser invocation positions, event insertion/authority
and outer dispatch/continue order stay in `analyse`. Self-test observations
remain retained without production authority. No matching window, chronology,
suppression fingerprint, unresolved receipt identity or validation changed.
The owner has no reverse import, stored callbacks, dependency container, I/O,
clock sample or operational actions. Schema 3, Markdown, CLI/defaults/provenance,
runtime authority, locks and resume remain unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 6,614 | 6,012 (−602) |
| `analyse` | 3,419 | 3,201 (−218) |
| Transaction/media/receipt owner | — | 826 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **195 passed**, final **202 passed**, with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
established temporary-HOME/network isolation. Selection: all affected cases in
`test_mrs_log_digest.py`, `test_digest_reply_observability.py`,
`test_digest_safety_hardening.py` and `test_digest_markdown.py`; the guarded import
cases in `test_digest_runtime.py`; and ten selected digest integration cases for
correlated/bounded media references, unrecovered fallback counting, source-isolated
receipt lifecycle, self-test removal without production resolution, source
identity across resume filtering, schema roots, resume/current-state separation,
self-test pending identity across resume and quote/meme pending source isolation.
Existing cases retain transaction shapes, media 503 resolution, unmatched
barriers, later receipt identity, runtime authority and resume assertions.

Six added boundary tests cover current helper delegation, correlation callback
and source order, record/event/source/counter identity, lazy parser/projection
order and source selection, builder field precedence, exact pending-state
replacement/rebinding across sources, and handled/nonmatched dispatch order.
The existing guarded import test includes the new owner. All previous assertions
remain. Exact complete helper bodies, eight aliases, three wrapper calls,
original signatures, three builder bodies/adapters, two projections and both
handlers match stage 19 after only extraction signatures/docstrings/indentation,
the explicit pending-state return/rebind and `continue` → `return True` changes.
All **22** original handled outcomes remain. All 18 imports/wiring were checked;
every remaining coordinator byte matches outside extraction sites and the
removed unused `urlsplit` import. Documentation coverage passes for **202 modules**;
`git diff --check` passes.

Three complete JSON/Markdown pairs reuse existing `write_digest_log`, `record`,
`structured_main_post_lifecycle` and integration fixtures at shared fixed paths
and time: bounded media fallback plus X request/transport/main-post lifecycle
(**47,238 / 13,771 bytes**), confirmed receipt recovery/replay suppression
(**35,868 / 11,538**) and production/self-test receipt isolation
(**36,202 / 12,361**). Direct request/transaction parsing, media predicates/path
lookup/correlation/suppression and receipt lifecycle results also match. JSON
bytes match after replacing only independently verified producer-source hashes;
independently verified repository HEADs were equal at comparison time. Markdown
and input bytes/modes/mtimes match without substitution. Temporary comparison
code/data are excluded. These are focused preservation checks, not an exhaustive
replay; the full bot harness was not run.

Recommended next boundary: the nine provider observation helpers named in stage
19 (**187 lines**) and their call-start/usage block (**79 lines**), using prepared
record/pending context, explicit active-attempt state and current lane/usage,
stage/cache and formatting callbacks. The later raw provider error/context-reset
block (**17 lines**) is related and can use a small adapter to return the updated
active context. Keep source switching, parser/dispatch positions, resume decisions
and cost reporting with their current owners.

Further bounded candidates were assessed without implementing them. Raw X API
error projection (**68 lines**) depends on the selected source request, the
300-second matching window, pending lane/target and shared request mutation;
adjacent rate-limit enrichment/traceback counting is **11 lines**. Post-scan media
ambiguity (**81 lines**), remaining-error filtering (**40**), pending confirmed
receipt lifecycle/error preparation (**88**) and prepared receipt recovery
reporting (**91**) can follow with explicit observations and supplied snapshot/
incident evidence, preserving source authority, reference identity and matching
order. API counters/semantics (**259 lines**) and failure/cooldown summaries
(**38**) are another prepared-report boundary, but validated success selection
and production event identity must remain explicit. These are separate useful
boundaries, not a reason to widen the next extraction into a general parser or
bot refactor.

No production checkout, configuration, durable bot state, logs or image pools
were changed; no bot/provider/posting calls, service control, merge or deployment
occurred. Earlier worktrees and branches are preserved.

## Extracted in stage 21

Base: `271873045266b4d6af4f2343af3f4c4c4414a46b`, independently verified against
the pushed `origin/codex/modularisation-stage20`. Work is isolated on
`codex/modularisation-stage21` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage21`.
Only this stage is implemented; the supervisor selects further useful digest
boundaries in fresh sessions, without a stage-number ceiling.

`mrs_log_digest_provider_observations.py` owns the nine provider observation
helpers (**187 original lines**): `xai_usage_stage_from_msg`,
`provider_usage_provider_from_msg`, `parse_xai_call_start`,
`parse_xai_usage_from_msg`, `xai_usage_context_from_pending`,
`unknown_xai_usage_context`, `normalise_active_xai_call_attempt`,
`_cache_input_metric` and `summarize_xai_usage_event`. Six digest aliases and
three thin wrappers retain the original signatures/defaults and current
stage/lane/cache/integer-conversion delegation. The owner imports the shared
`Record`; scalar converters remain in the existing provider-cost/value owners
and are supplied through current digest callbacks. A separate observation owner
keeps mutation and parsing out of the read-only provider-cost reporting module.

`observe_provider_message` contains the complete **79-line** pre-EVENT span,
from Asking-Grok context setup through call-start parsing/attempt creation,
usage matching/mutation, success counters and malformed-usage recording.
Explicit selected record/message, pending mention/quote state, active context/
index, lists, statistics and current helpers replace implicit local dependencies.
The returned context/index are rebound at the original position. Provider/stage/
lane/context matching, missing-provider fallback, optional reasoning effort,
usage-observed mutation and index clearing, timestamps, exact error fields,
list order and shared objects are preserved. Unknown, partial and cache metrics
retain their existing values and precedence.

Source switching, later context-clearing/error paths, resume decisions and
cost/report assembly remain in the coordinator. There are no extra source
filters, validators, normalisation, stored callbacks, dependency containers,
reverse imports, clock samples, file/home/configuration access or provider calls.
Schema 3, Markdown, CLI/defaults/provenance, locking and publication authority
are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 6,012 | 5,802 (−210) |
| `analyse` | 3,201 | 3,142 (−59) |
| Provider-observation owner | — | 336 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **252 passed**, final **254 passed**, using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with the
established temporary-HOME/network isolation. Selection: `test_mrs_log_digest`,
`test_digest_reply_observability`, `test_digest_markdown`,
`test_digest_safety_hardening`, `test_digest_costs`, `test_openai_cost_digest` and
`test_digest_historical_events`; the existing guarded module-import cases; and
seven selected integration functions (nine cases) for resume/current-state
separation, source identity across resume filtering, schema roots, production/
self-test reply identity, self-test pending resume and quote/meme source isolation.
The legacy-provider resume-clearing regression remains included.

One new boundary test covers current callback delegation, source predicates,
active context/index transitions, shared attempt/event mutation, unmatched and
malformed usage, missing-provider matching, cache precedence and counters. The
existing guarded-import parametrisation includes the new owner. Exact source
checks confirm all nine moved bodies, six aliases, retained signatures, wrapper/
observer wiring and the entire observation span; every remaining coordinator
byte is unchanged. Documentation coverage passes for **203 modules**;
`git diff --check` passes.

Three complete JSON/Markdown comparisons use shared existing `record`,
`write_digest_log`, `digest_event_line` and fixed-time cost fixtures at identical
temporary paths: matched/unmatched usage (**34,815 / 11,396 bytes**), malformed/
partial metrics (**35,046 / 11,396**) and resumed/source-isolated attempts
(**36,230 / 12,913**). Per-record transitions, shared attempt/event identity,
direct parser/context/normalisation/cache projections, usage totals, provider
cost summaries and seed/final resume state also match. These direct checks matter
because schema 3 omits the legacy provider-usage arrays. Each producer hash was
verified independently from source bytes and each HEAD through Git before
substituting only the producer-source hash; both HEADs were the base at comparison
time. Markdown, direct/state results and input bytes/modes/mtimes match without
substitution. Temporary comparison code/data are excluded. This is focused
preservation evidence; no exhaustive replay or full bot harness was run.

Recommended next boundary: raw X error observation (**68 lines**, starting at
`x_error_match`) in the transaction owner, and the related provider error/context
reset span (**17 lines**) in the provider-observation owner, at their existing
positions. X handling needs the selected source request, exact 300-second window,
pending lane/target, shared request mutation, restriction predicates, counters
and source/time/format helpers; retain its early handled-403 `continue` outcome.
The provider adapter must return the updated context. Adjacent rate-limit
enrichment/traceback counting is **11 lines** and depends on the latest API error;
keep its dispatch order explicit and avoid turning these into a general parser.

Other assessed boundaries remain unimplemented: post-scan media ambiguity
(**81 lines**) consumes transactions, request chronology and supplied archive
evidence; remaining-error filtering (**40**) needs suppression fingerprints,
self-test/API times and the five-second restriction window. Pending confirmed
receipt lifecycle/error preparation (**88**, including
`clear_latest_reconciliation`) needs source authority and ordered receipt
identities; prepared receipt recovery (**91**) follows incident classification
and uses supplied incident/snapshot evidence and receipt-role vocabulary.
API counters/semantics (**259**) and failure/cooldown summaries (**38**) require
explicit validated-success selection and production event identity. Legacy
mention/hot-post (**139**) and quote-reply (**92**) section spans need exact
pending-state returns, active-context clearing and event callbacks. The enclosing
quote/image span (**252**) already mixes raw handlers with extracted observer
dispatch; select its remaining raw subgroups separately. Meme handling (**19**)
and created-post identity (**20**) are smaller adjacent spans, with the latter
retaining canonical-response checks and production event-object authority.
Further extraction should justify each boundary, not move all of `analyse` to
reduce its line count.

No production checkout, configuration, durable bot data, logs or image pools
were changed; no bot execution, provider/posting calls, service control, merge
or deployment occurred. Previous branches and worktrees are preserved.

## Extracted in stage 22

Base: `56c73adc475088632c14e196002e3e46f0e2debe`, verified against the pushed
`origin/codex/modularisation-stage21`. Work is isolated on
`codex/modularisation-stage22` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage22`.

Three post-scan groups now belong to `mrs_log_digest_transactions.py`:
`prepare_media_incidents_and_errors` moves media correlation, receipt-bound
ambiguity preparation and error filtering (**128 original lines**, including
error rebinding); `append_unresolved_reply_receipt_errors` moves the pending
sending/reconciliation lifecycle and ordered unresolved errors (**88**);
`prepare_reply_receipt_recovery_reporting` moves the reconciled, unavailable and
active snapshot receipt projections (**91**). Their three direct-import calls
remain at the original positions, with `summarise_operational_error_health`
unchanged between the second and third calls.

Named prepared inputs and current callbacks/mapping preserve archive fallback,
integer epoch checks, request chronology, suppression, error rebinding/identity,
self-test appends, latest receipt matching, ordering, exceptions and nested list
sharing. Timestamp preparation stays in the coordinator, including
`handled_restriction_times` reused by API reporting; other moved temporaries need
no later reads. Scanning/source restoration, incident classification, report
assembly, API counters and legacy observations remain in place. There are no new
time reads, input/snapshot calls, reverse imports or stored callbacks. Existing
APIs/signatures/defaults, schema 3, Markdown, source/self-test and snapshot/window
authority, CLI/provenance, locking and resume are preserved.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 5,802 | 5,531 (−271) |
| `analyse` | 3,142 | 2,868 (−274) |
| Transaction owner | 826 | 1,179 (+353) |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **164 passed**, final **167 passed**, using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with the
existing temporary-HOME/network isolation. Selection: `test_mrs_log_digest`,
`test_digest_reply_observability`, `test_digest_markdown`, `test_digest_incidents`;
four archive/self-test isolation cases in `test_digest_safety_hardening`; the
transaction-owner guarded import in `test_digest_runtime`; and fourteen selected
integration functions covering media fallback/source references, receipt
lifecycle/recovery/source isolation, resume/current-state separation, stable
source identity and schema roots. Three new boundary tests establish current
callback/mapping delegation, error-list rebinding and row identity, reverse
receipt matching/order, shallow source references, post-health snapshot use,
recovery-list identity, nested snapshot-list sharing and parser exceptions.

Exact source checks confirm all three moved bodies (only the supplied `strptime`
name and explicit returns/rebinding differ), unchanged pre-existing owner bytes,
and every surrounding coordinator byte outside the three imports/call replacements.
Documentation coverage passes for **203 modules**; `git diff --check` passes.

Six complete JSON/Markdown comparisons match stage 21 using existing `record`,
media/reply ambiguity, reconciliation, structured main-post lifecycle and
`write_digest_log` fixtures with shared fixed times/paths: unresolved media
(**43,184 / 14,223 bytes**), reconciled media (**43,440 / 14,403**), sending/
promotion/removal/replay lifecycles with unavailable status (**47,671 / 14,537**),
reconciled reply receipts (**44,755 / 14,592**), authoritative active snapshot
(**46,179 / 15,619**) and stale active snapshot (**46,559 / 15,797**). Each producer
hash was independently verified from source bytes and each HEAD through Git;
only the producer-source hash was substituted, with both HEADs still at the base.
Markdown and fixture input bytes/modes/mtimes match without substitution.
Temporary comparison code/data are excluded. These full entry-point comparisons
use prepared snapshot fixtures in place of the snapshot loader; they do not
revalidate filesystem inspection or constitute exhaustive replay. No whole bot
harness was run.

Recommended next boundary: assess raw X/provider errors **together with API
counter reporting**. X observation (**68 lines**) needs source request identity,
the 300-second window, shared request mutation, restrictions and the handled-403
`continue`; provider observation/reset (**17**) returns its current context.
Adjacent rate-limit enrichment/tracebacks (**11**) retain latest-error ordering.
API counters/semantics (**259**) and failure/cooldown summaries (**38**) require
production event-object authority, validated success IDs, literal lane/conflict
accounting, current helpers, restriction times and shared error rows. Keep every
call at its current position and report assembly in the coordinator; existing
transaction/provider owners supply the observation boundaries. Legacy mention/
hot-post (**139**) and quote-reply (**92**) handlers remain another substantive
candidate. None is implemented here; continue in fresh sessions only while
further digest modularisation remains useful.

No production/configuration/state/log/image-pool changes, bot execution, provider
or posting calls, service control, merge or deployment occurred. Previous branches
and worktrees are preserved; stage 22 is the only stage implemented this session.

## Extracted in stage 23

Base: `6f3e9334b9306d4668395932f7fc1cae1efe7acf`, verified against the pushed
`origin/codex/modularisation-stage22`. Work is isolated on
`codex/modularisation-stage23` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage23`.

`mrs_log_digest_api_health.py` now owns raw cooldown observation (**13 original
lines**), X error observation (**68**), latest-error enrichment/traceback counting
(**11**), post-scan API counter/semantics/failure/cooldown preparation (**297**)
and the API report value (**56**). The provider error/context-reset span (**17**)
joins `mrs_log_digest_provider_observations.py`. Every call remains at its
original position: cooldown before used-history handlers, X errors after them,
provider context rebind before latest-error enrichment, preparation before legacy
strategy inference, and API field materialisation within the report literal.

Explicit current helpers and shared inputs retain the absolute inclusive
300-second request window, source eligibility, pending lane/target selection,
request status/failed mutation, restriction/error row identity and deleted-403
early exit. Provider reset leaves the attempt index intact. The specific typed
`ApiHealthPreparation` holds existing computed results; `api_health_report`
retains late Counter conversion, `has_5xx_failures`, conflict slicing and shared
lists after intervening callbacks. Production event-object authority, validated
publication evidence, literal immutable IDs, transport/lane conflict deduplication,
media handoff predicates, durable-only exclusions and counter-semantics strings
are unchanged. Source switching, snapshot/window/publication authority, strategy
inference, report orchestration, resume, CLI/defaults, clocks and locking remain
with their existing owners. No reverse imports or stored callbacks were added.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 5,531 | 5,121 (−410) |
| `analyse` | 2,868 | 2,450 (−418) |
| API-health owner | — | 596 |
| Provider-observation owner | 336 | 369 (+33) |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **213 passed**, final **228 passed**, with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
existing temporary-HOME/network isolation. Selection: `test_mrs_log_digest`,
`test_digest_reply_observability`, `test_digest_markdown`,
`test_digest_historical_events`; runtime owner import guards; three safety cases
for provider resume and self-test input/state isolation; and 23 selected digest
integration functions covering API/transport counters, media/receipt handling,
lane conflicts, source isolation and valid/invalid publication authority. Four
new boundary functions contribute 14 cases for request age/source/parser errors,
current callbacks, handled dispatch exits, shared request/error mutation,
provider context identity/reset with retained attempts, production event-object
identity and report evaluation after callback mutations. The API owner joins
the existing guarded-import test. Existing assertions are retained.

Exact source checks confirm all five moved statement bodies with only handled
returns and explicit Counter/strptime/min substitutions; the report dictionary
has the same AST after replacing named preparation-field access. Every coordinator
byte outside the import/call substitutions is unchanged, preserving existing
APIs/signatures/defaults. Pre-existing provider code is unchanged except its
module docstring. Documentation coverage passes for **204 modules**;
`git diff --check` passes.

Four complete JSON/Markdown comparisons against stage 22 match using the existing
`record`/`write_digest_log` helpers, fixed clocks and shared input paths:
handled/deleted 403s (**34,981 / 12,491 bytes**), 429/503 latest-error enrichment
(**38,753 / 13,015**), provider reset with source isolation (**36,637 / 12,680**)
and conflicting transport/success authority (**39,709 / 12,905**). Raw `analyse`
reports also match, including provider resume details omitted by schema 3.
Producer hashes were independently verified from source bytes and HEADs through
Git; only the producer-source hash was substituted, with both HEADs at the base.
Markdown and fixture input bytes/modes/mtimes match without substitution.
Temporary comparison scripts/data are excluded. These entry-point comparisons
use a prepared unavailable snapshot in place of the snapshot loader; they do not
revalidate filesystem inspection or form an exhaustive replay. No whole bot
harness was run.

Recommended next boundary: raw quote/image observations, as two separate named
handlers at their existing positions. The remaining concrete candidates are
(sizes include boundary comments/blank lines):

- Quote/image selection (**85 lines**, starting at 3401) mutates pending quote
  and regular-image observations and emits through the existing event callback.
  Cycle/publication observation (**69**, at 3585) also rebinds pending quote and
  updates the latest matching regular-image row. Keep the intervening editorial,
  generated-identity and image-spacing dispatch in the coordinator.
- Daily meme observation (**20**, at 3654) can return pending meme plus a handled
  flag, preserving its three ordered matches and event fields.
- Created-post identity (**21**, at 3674) can return the last-created-post row
  and handled flag; retain both response-parser calls, canonical/source flags
  and exact emitted-event identity when removing publication authority.
- Mention/hot-post (**140**, at 3695) and quote-reply (**93**, at 3835) are separate
  later handlers: retain pending/context rebinding, response identity fallback,
  production flags, skip counters and handled exits. Source switching stays in
  the coordinator.

None of these later boundaries is implemented here. Further stages remain useful
without a stage-number ceiling. No production/configuration/state/log/image-pool
changes, bot execution, provider/posting calls, service control, merge or deployment
occurred. Previous branches/worktrees are preserved; only stage 23 is implemented
in this session.

## Extracted in stage 24

Base: `1291a1e099c147bb861ec67094db32af168745ac`, verified against pushed
`origin/codex/modularisation-stage23`. Work is isolated on
`codex/modularisation-stage24` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage24`.

`mrs_log_digest_legacy_posts.py` owns eight named raw observation handlers:
quiet/hot-post/alternating-lane diagnostics (**48 original lines**), quote/image
selection (**85**), generated-image spacing (**53**), quote cycles/text/posting
(**69**), daily meme (**20**), created-post evidence (**21**), mention/hot-post
replies (**140**) and quote replies (**93**). The two companion response parsers
move with created-post evidence: the compatibility parser remains a direct digest
import, and the canonical parser retains its digest signature through a wrapper
supplying the current values-owner validator. The shared `Record` is unchanged.

The **32 original outer continues** become handled returns at the same coordinator
positions. Counter-only quiet observations still fall through. Stateful handlers
return the exact original/replacement pending maps, latest spacing row and active
provider context. Blocked spacing only appends; image enrichment updates the last
matching unenriched row in place. Creating an X post still ends dispatch without
pending enrichment. Both canonical-parser calls, compatibility display parsing,
production flags and event-object authority invalidation are preserved. Normal/
hot-post and quote-reply algorithms retain their distinct matching and resets.

The record loop, source state switching, EVENT router and four interleaved
editorial/identity calls stay in the coordinator, as do provider attempt indexes,
earlier source/receipt/error dispatch and final cap/spacing counters. Current
helpers and only the inputs each handler uses are supplied explicitly. No reverse
imports, stored callbacks, general state/dispatch framework or behavior fixes
were introduced. Existing APIs/defaults, values/types/order/errors, counters,
schema 3, JSON/Markdown, source and publication authority, selected-window/current
snapshot decisions, resume, CLI, provenance and locking are preserved.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 5,121 | 4,645 (−476) |
| `analyse` | 2,450 | 1,990 (−460) |
| Legacy-post owner | — | 688 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **208 passed**, final **236 passed** using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` with the
existing temporary-HOME/network isolation. Selection: `test_mrs_log_digest`,
`test_digest_reply_observability`, `test_digest_markdown`,
`test_digest_historical_events`; runtime owner import guards; three safety cases
for provider resume and self-test state/input isolation; and **19 digest-only
integration functions** covering quote/image selection, spacing/resume, receipts,
reply/publication authority, mixed source identities and noncanonical immutable
success. Eight added boundary functions contribute **27 cases** for handled/
fallthrough dispatch, interleaved observers, object identity, current callbacks,
canonical/display parser distinctions, lane-specific matching/resets and exact
production event identities. The owner also joins the guarded-import test.
Existing assertions are retained. No whole bot harness was run.

Independent text/AST checks verified every moved body and all original function
signatures/defaults. Restoring the original helper and loop spans and removing
the new import reproduces every original coordinator byte; only the explicit
handled returns differ inside the owner. Manual inspection covered each exact
body, including both canonical calls and the reverse image-row loop.
Documentation coverage passes for **205 modules**; `git diff --check` passes.

Five complete JSON/Markdown pairs also match stage 23, using shared existing
fixture setups, `record`/`write_digest_log`, fixed clocks and identical paths:

| Comparison group | Records | JSON bytes | Markdown bytes |
| --- | ---: | ---: | ---: |
| Quote/image/meme, spacing and mixed sources | 24 | 43,755 | 15,815 |
| Mention/hot-post, mixed sources and noncanonical response | 10 | 36,877 | 12,670 |
| Quote-reply, mixed sources and noncanonical response | 10 | 36,873 | 12,503 |
| Pending mention/quote and provider attempt before resume | 3 | 33,653 | 11,378 |
| Resumed mention and quote replies | 6 | 35,534 | 12,132 |

The temporary comparison test passed; raw `analyse` reports, stderr and saved
resume bytes also match. Direct checks retain the provider attempt after reply
context resets, a distinction omitted by schema 3. Producer hashes were verified
from source bytes and HEADs independently through Git; only the producer-source
hash was substituted, with both HEADs at the base. Fixture input bytes, modes and
mtimes remain unchanged across each comparison. The comparisons use a prepared
unavailable remote-write snapshot and do not revalidate filesystem inspection or
form an exhaustive replay. Temporary scripts/data are outside the worktree.

Recommended next boundary: remaining passive structured telemetry field
projections, starting with runtime/reply-control events (`runtime_control_pause`,
`runtime_control_clear`, clarification/repair observations and
`reply_evidence_unavailable`). Named family handlers can join the appropriate
existing owners; posting-state and meme-failure projections are further small
candidates. Keep strict parsing, branch selection/order, source authority and
confirmed-publication acceptance in the coordinator.

Headline/derived preparation is another coherent candidate: initial health and
media/recovery summaries, reply budgets/priority, and later single-call/legacy
headline insertions have distinct evaluation positions. Extract prepared report
values with explicit current inputs rather than moving the whole post-scan body.
Supporting `merge_context`, config-pair/backscan merging and saved-context helpers
may merit ownership together if their pure preparation becomes clearer; log
selection/backscan cutoffs, resume reads/writes and advancement, current-snapshot
authority, CLI/default paths and locks remain appropriate coordination. Thin
compatibility wrappers are intentional. Further work must improve ownership and
clarity, without a stage ceiling or line-count target. No following stage is
implemented here.

No production/configuration/state/log/image-pool changes, bot execution,
provider/posting calls, service control, merge or deployment occurred. Previous
branches/worktrees are preserved; only stage 24 is implemented in this session.

## Extracted in stage 25

Base: `bb361f5fbb1af868a882450920bd0bd0a979f9a9`, verified against pushed
`origin/codex/modularisation-stage24`. Work is isolated on
`codex/modularisation-stage25` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage25`.

The existing state-reporting owner now holds `prepare_headline_and_derived`
(**226 original body lines**) and `prepare_reply_quality_headline` (**53**).
The first returns the headline, transient timeout count, three media lists and
derived budgets/lane priority; media rows remain shared. Supplied state/record
times drive cooldowns without another clock sample. The temporary Grok-skip text,
all claims/order and negative or unavailable budgets are preserved. Finalisation
returns legacy counts, a replacement headline and the cooldown-free base.

The existing strategy owner now holds `prepare_inferred_reply_strategy_outcomes`
(**42 original body lines**) and the local-rejection builder (**54**). Inference
preserves indexing order, latest decisions, missing/error cases, deduplication,
timestamps and copied fields. The nested rejection adapter retains its signature
and passes the original payload dictionary separately from current named helpers,
preserving dependency-name payload keys, lane promotion/map replacement, bounded
types, fill-only enrichment and returned row identity. Root `add_event` still
owns statistics/provenance; post-scan inference still follows source-record
clearing and does not acquire production event identity.

All four calls remain at their original positions. API preparation, both quality
summaries, mention-control preparation, late API materialisation and root schema
assembly retain their order. Existing owner functions and passive EVENT branches
are unchanged. No generic state container, stored callbacks, reverse imports or
framework was introduced; public/private APIs/defaults, schema 3, values/types/
order/errors, shared objects, clocks and source/publication authority are preserved.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 4,645 | 4,308 (−337) |
| `analyse` | 1,990 | 1,649 (−341) |
| State-reporting owner | 897 | 1,221 |
| Reply-strategy owner | 728 | 855 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **220 passed**, final **226 passed**, using
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the existing
temporary-HOME/network isolation. Selection: general digest, reply observability,
Markdown and incidents modules; five state/runtime boundary functions and owner
import guards; two self-test state/input-isolation functions; and **13 digest-only
integration functions** for current/saved state, media, receipt/source isolation,
API counters and reply authority. The existing API late-report callback and
pipeline/rejection-coalescing regressions pass. Four added functions contribute
**six cases** for current callback timing/delegation, list/row/map identity,
cooldown exclusions, inferred outcomes/errors and payload/dependency collisions.
Existing assertions remain intact; no whole bot harness was run.

Exact text/AST checks verified all moved bodies and existing signatures/defaults.
Restoring the four spans and removing the four new imports reproduces every
stage 24 digest byte. Existing owner/test functions remain byte-identical.
`python3 tools/check_python_documentation.py` passes for **205 modules**;
`git diff --check` passes.

Five complete JSON/Markdown pairs match stage 24 using shared existing `record`,
single-call and media/reconciliation fixtures, `write_digest_log`, fixed London
time and identical paths:

| Comparison | Records | JSON bytes | Markdown bytes |
| --- | ---: | ---: | ---: |
| Current health, cooldowns and budgets | 4 | 38,503 | 13,495 |
| Unavailable media health | 7 | 42,291 | 13,924 |
| Resolved media and cleared cooldown/budgets | 8 | 46,609 | 15,593 |
| Single-call outcomes | 6 | 39,792 | 13,395 |
| Legacy restrictions, inferred/existing outcomes and coalescing | 12 | 45,306 | 14,164 |

The temporary comparison test passed; raw `analyse` reports and stderr also
match. Producer source hashes and repository HEADs were independently verified;
only the producer-source hash was substituted, with both HEADs at the base.
Fixture bytes, modes and mtimes remain unchanged. These synthetic comparisons
use prepared remote-write snapshots, do not revalidate filesystem inspectors and
are not an exhaustive replay. Temporary scripts/data are outside the worktree.

Recommended next boundary: the remaining **111-line** passive runtime/reply-control
EVENT group (`reply_evidence_unavailable`, pause/clear, clarification and repair).
Named handlers can join existing state/reply owners while the coordinator retains
parsing, branch order, source tracking and publication acceptance. Posting-state
(**32 lines**) and meme-failure (**34**) projections are further small candidates;
none changed here.

Supporting context preparation is substantive: marker stripping, `merge_context`,
config-pair extraction and backscan merging contain actual merge/annotation rules.
Payload/partial-state and strict JSON parsing are smaller possible boundaries if
ownership clarifies their consumers; keep native-number and Decimal contracts
distinct. Existing input-reader compatibility wrappers are already appropriate.
Stable/authoritative input reads, source selection/switching, backscan cutoffs,
snapshot/window decisions, publication checks, saved-context/resume orchestration,
schema assembly, CLI/default paths and locks remain appropriate coordination.
Further stages should improve ownership and clarity, without moving all of
`analyse`, a stage ceiling or a line-count target. No following stage is implemented.

No production/configuration/state/log/image-pool changes, bot execution,
provider/posting calls, service control, merge or deployment occurred. Previous
branches/worktrees are preserved; only stage 25 is implemented in this session.

## Extracted in stage 26

Base: `7b760898e7176106fc4825789e44880638d22b2a`, verified against pushed
`origin/codex/modularisation-stage25`. Work is isolated on
`codex/modularisation-stage26` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage26`.

`mrs_log_digest_consistency_events.py` owns eight named projections:
`reply_evidence_unavailable`, `runtime_control_pause`, `runtime_control_clear`,
`clarification_reply_cap_override`, `clarification_reply_used`,
`repair_reply_completed`, `posting_transaction_state` and `daily_meme_failure`.
Their **169 original body lines** are unchanged apart from the explicit record
timestamp. Each receives only its actual current field helpers, parsed event,
local counter and insertion callback. Get/validation order, defaults, limits,
types and post-insertion counters are preserved, including the second kind
increments for pause/clear, clarification and repair. Control-lane lists and
emitted event rows remain shared; displayed IDs grant no publication authority.

`production_consistency_report(events, stats)` holds the exact **45-line**
dictionary value at the original report-literal key position. It retains the
eleven-kind subset, new outer list with shared rows, key order and five separate
`sorted(stats.items())` comprehensions, after the preceding API callback and
historical-reply counter observation. No early or consolidated snapshot is made.

Parsed EVENT predicates, dispatch order, source-state switching, classification,
event insertion/provenance, strict `reply_posted` and
`historical_context_reply_posted` authority branches, and completed historical
anchor validation stay in root. Historical-context owner functions, the two tiny
pagination/candidate-skip branches and other report sections are unchanged.
Existing APIs/signatures/defaults, schema 3, clocks, values/types/order/errors,
JSON/Markdown, source/window/resume policy, CLI/defaults, producer identity and
locking are preserved. No dispatcher, state container or stored callback was added.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 4,308 | 4,147 (−161) |
| `analyse` | 1,649 | 1,477 (−172) |
| Consistency-events owner | — | 328 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation: baseline **232 passed**, final **234 passed**, with
`MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest` and the
established temporary-HOME/network isolation. Selection: reply observability,
historical events, general digest and Markdown modules; the two safety-hardening
self-test state/input-isolation functions; and **seven digest-only integration
functions** for receipt source isolation, exact historical text/provenance,
malformed reply confirmation, historical authority, noncanonical conversational
IDs, integer historical IDs and explicit self-test publication authority.
Two added functions cover current callback/payload/timestamp delegation across
invocations, shared lists/rows, original double increments, and late report-time
counter iteration/order/types. All **52** existing observability helpers/tests
remain byte-identical; existing assertions are retained. No whole bot harness ran.

Exact text/AST checks verify all moved bodies, the report expression, call inputs
and existing signatures/defaults. Restoring the nine spans and removing the new
import reproduces every stage 25 digest byte, establishing that the surrounding
`analyse` is unchanged. Existing digest companions remain byte-identical.
`python3 tools/check_python_documentation.py` passes for **206 modules**;
`git diff --check` passes.

Three complete fixed-path/time JSON/Markdown pairs match stage 25:

| Comparison | Records | JSON bytes | Markdown bytes | Observed publications |
| --- | ---: | ---: | ---: | ---: |
| Bounded/malformed control and reply fields | 43 | 70,007 | 15,181 | 0 |
| Historical families, transaction/meme failures | 76 | 133,686 | 26,769 | 1 |
| Mixed sources and valid/invalid confirmations | 13 | 45,551 | 14,219 | 2 |

The temporary comparison test passed, reusing historical fixtures and
`write_digest_log`. Raw `analyse` reports and stderr match too. A direct trace
matches **984** parsed get/validation/counter observations and checks shared
control lists and report rows. Producer hashes and both repository HEADs were
independently verified; only the producer-source hash was substituted, with
both HEADs at the base. Fixture bytes, modes and mtimes are unchanged. These
synthetic comparisons use a prepared remote-write snapshot, do not revalidate
filesystem inspectors and are not an exhaustive replay. Temporary scripts/data
remain outside the worktree.

Recommended next boundary: the shared stable-byte/private-JSON input primitives.
The no-follow metadata/read checks, private-file constraints, canonical encoders
and strict JSON parsers form useful common ownership for runtime, remote-write,
receipt, history and cost readers. Preserve thin digest compatibility wrappers
where current helper lookup matters, and keep the native-number and Decimal
contracts distinct. Source selection, authority decisions and producer identity
remain coordinator responsibilities; moving those for size would not help.

Context helpers are another substantive boundary: marker stripping, saved-context
merging, config-pair extraction and backscan annotation total **81 lines**.
`read_resume_data`, `save_resume_time` and `apply_saved_context` combine persistence,
clock/cursor decisions and refresh ordering; retain that orchestration while
considering only separable context preparation. Existing input-reader and domain
compatibility wrappers are already appropriate.

Substantive companions also remain: prepared post-rate/coverage calculations
within `generated_post_rate_history` (**55 lines** including reading/time selection)
could join image-usage ownership while root retains selection; runway-config
preparation (**20**) and the two reply/API restriction classifiers (**12/14**) are
smaller companions of existing owners. These are candidates for useful ownership,
not a line-count target or a commitment to more stages.

Stage 26 only is implemented here. The supervisor starts a fresh session for each
stage. The user's hard stop after stage **32** for reassessment supersedes older
no-ceiling language in this report; do not start stage 33, and stop earlier if no
sensible digest modularisation remains. Broader bot refactoring is outside scope.
No production/configuration/state/log/image-pool changes, bot execution,
provider/posting calls, service control, merge or deployment occurred. Previous
branches/worktrees are preserved.

## Extracted in stage 27

Base: `adbdd934074cdeead78d4d5c3c3536b14af5c14c`, verified against pushed
`origin/codex/modularisation-stage26`. Work is isolated on
`codex/modularisation-stage27` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage27`.

`mrs_log_digest_input_io.py` owns `file_sha256`, `_stable_file_identity`,
`read_stable_regular_snapshot`, `read_stable_regular_bytes`,
`read_stable_private_json_bytes`, `canonical_atomic_json_bytes`,
`canonical_private_json_bytes`, `_strict_json_object`,
`_strict_native_json_value` and `_strict_native_json_object`: **183 original
function lines**. Bodies are unchanged except for named sibling callbacks.
Six independent functions are direct root aliases. Four thin wrappers retain
exact signatures/defaults and supply the current identity, snapshot or native
value parser at invocation time. Shared standard-library modules remain shared;
there is no reverse import, stored callback, dependency container or new import I/O.

No-follow flags, metadata/owner/link/mode checks, size/read bounds, path/fd
comparisons, descriptor closure and exception type/text/order remain exact.
Ordinary file hashing and separate corpus parse/hash reads are unchanged.
Decimal versus native floats, nested decoding/UTF-8/duplicate/nonfinite failures
and object-root checks retain their distinct contracts. The canonical encoders
retain different ASCII/nonfinite defaults and exact sorted/indented bytes/newlines.
`build_digest_contract` and `repository_head_sha` remain byte-identical in root,
including facade `__file__`, schema constants and current callback resolution.
Reconciliation, runtime/evidence, records/discovery, context/cursor, authority,
window/snapshot, CLI/locking and analysis coordination are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 4,147 | 3,994 (−153) |
| `analyse` | 1,477 | 1,477 |
| Input-I/O owner | — | 236 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation used `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`
with the established temporary-HOME/network isolation. Baseline: **276 passed**
in the eight requested modules (safety hardening, runtime, reply evidence,
corpus/generated pool, costs, original editorial, generated identity and general
digest). Final: **294 passed**: 281 in those modules plus 13 cases from eight
existing digest-only integration functions covering producer identity/copied
facade, current state beyond the window, receipt/history fallback, private JSON
redaction and nonfinite/non-UTF-8 display/state input. No whole bot harness ran.
Three added test functions cover all four sibling delegations across invocations,
shared results, root checks and original callback exceptions; the existing import
safety test adds the new owner. All existing test bodies/assertions are retained.

Exact text/AST review verifies all ten moved bodies, four wrapper calls and
original callable signatures/defaults. Applying only those replacements and the
new import reproduces every current digest byte. All **91** remaining root
functions, including `analyse` and `build_digest_contract`, and all **27** existing
digest companions remain byte-identical. `python3 tools/check_python_documentation.py`
passes for **207 modules**; `git diff --check` passes.

Four complete fixed-path/time JSON/Markdown pairs match stage 26 using existing
integration fixtures and their original assertions:

| Fixture | Records | JSON bytes | Markdown bytes |
| --- | ---: | ---: | ---: |
| Runtime state after the selected window | 6 | 37,658 | 12,980 |
| Version-4 receipt with Unicode/multiline text | 1 | 61,685 | 15,955 |
| Completed historical reply history | 1 | 37,301 | 12,384 |
| Duplicate-key private/runtime/config input | 1 | 62,905 | 16,009 |

Both producer hashes and repository HEADs were independently verified. Only the
producer-source hash was substituted; both HEADs were at the base. Stderr and
fixture bytes/modes/mtimes also match. A second temporary test compares **45**
typed parser cases, **10** encoding cases and **17** file-operation/error traces,
plus real private-file bytes/metadata, ordinary symlink hashing and shared JSON
module patches. It covers distinctions absent from reports, including Decimal
precision/overflow, nested surrogates, exact encoder bytes, read bounds and close
ordering. Both temporary tests passed; scripts/data stay outside the worktree.
These are synthetic comparisons, not an exhaustive filesystem-race or production
replay; existing nearest regressions and exact body review provide the remaining
preservation evidence.

Recommended next boundary: substantive context/cursor ownership. Marker stripping,
context merge, config-pair extraction, backscan annotations and partial-state
parsing total **117 lines**; `read_resume_data`, `save_resume_time` and
`apply_saved_context` add **143**. These eight functions and their marker vocabulary
form a coherent candidate, with current parser, marker/fingerprint helpers,
clock and refresh callbacks supplied explicitly where needed. Preserve missing
versus None merge semantics, truncated-state recovery, backscan timestamps,
retained-history separation from current state, spacing carry-forward and refresh
order. Resume ownership must retain same-timestamp occurrence merging, bounded
physical cursor tails, reset/preserve flags, warning/encoding/replacement behavior
and the existing save-time clock position. `apply_saved_context` currently retains
history without applying its `window_end` argument; extraction must not invent a
new temporal filter or promote history to current authority.

Root should retain production/self-test source selection, latest-config scan and
backscan cutoff/window choice, current-snapshot selection, publication checks,
overall report schema, CLI/locking/delivery and the decision to save only after
successful delivery. Moving context/cursor implementation need not move that
coordinator or duplicate records ownership. This is a useful next assessment,
not a following-stage implementation or a line-count target.

Stage 27 only is implemented. Stop after stage **32** and reassess, or earlier if
no sensible digest modularisation remains; older no-ceiling wording is superseded.
No production/configuration/state/log/image-pool changes, bot execution,
provider/posting calls, service control, merge, deployment or force-push occurred.
Previous branches/worktrees are preserved.

## Extracted in stage 28

Base: `ad8a48a99edb3b09e64415cce41c4faf77f31678`, verified against pushed
`origin/codex/modularisation-stage27`. Work is isolated on
`codex/modularisation-stage28` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage28`.

`mrs_log_digest_context.py` owns the ten specified functions:
`read_resume_data`, `save_resume_time`, `state_context_is_within_window`,
`strip_internal_context_markers`, `merge_context`, `extract_config_pairs`,
`merge_context_from_log_backscan`, `find_latest_config_before`,
`parse_partial_state_from_msg` and `apply_saved_context`, plus
`INTERNAL_CONTEXT_KEYS`. Inclusive physical signature/body spans at the verified
base total **308 lines** (the supplied scope estimate was 302). Two independent
parsers are direct aliases; eight wrappers retain root signatures/defaults and
supply current named helpers/constants. Annotations use the shared `Record` owner.

Bodies remain exact apart from explicit native-parser/diagnostic calls and the
save-time clock callback. Recursive helper/key-set lookup, fill-only merges and
sharing, backscan ordering/deduplication/cutoffs, permissive partial-state parsing,
conditional old-cursor reads, historical fallback, fingerprint multiplicity and
bounded tails are preserved. The clock remains at `updated_at` evaluation; JSON
keys/order/types/bytes and temporary-sibling write/replace behavior are unchanged.

Saved state/config remain historical diagnostics, with the same shallow spacing
copy and final current `refresh_derived` call. `apply_saved_context.window_end`
remains unused. Source/window selection, runtime/evidence loading, publication
checks, schema/producer identity and CLI/locking/delivery/save order stay in root.
There are no reverse imports, retained callbacks, generic dependency container or
new import-time I/O or clock sample.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 3,994 | 3,800 (−194) |
| `analyse` | 1,477 | 1,477 |
| Context owner | — | 407 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation used `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`
with the established temporary-HOME/network isolation. Baseline: **186 passed**
in general digest, safety hardening and runtime. Final: **203 passed**, comprising
195 in those modules and eight existing digest-only integrations for current
state after the window, saved/current separation, future historical state,
spacing carry-forward, resume source identity, schema roots and self-test
pending/quote/meme isolation. Seven added test functions (eight cases) cover
current callbacks, recursive key-set changes, copies/shared results, read and
exception boundaries, backscan ordering, historical-only restoration and late
clock lookup; the existing import-safety test adds the owner. Every pre-existing
test body/assertion is retained. No whole bot harness ran.

Two temporary comparison tests also passed. Six complete fixed-path/time
JSON/Markdown pairs match stage 27:

| Fixture | Records | JSON bytes | Markdown bytes | Cursor bytes |
| --- | ---: | ---: | ---: | ---: |
| Quiet window with future retained history | 1 | 34,392 | 12,669 | 1,130 |
| Resume-boundary seed | 1 | 34,873 | 11,831 | 990 |
| Same boundary without cursor filtering | 2 | 35,796 | 11,743 | — |
| Resumed boundary | 1 | 34,967 | 12,079 | 1,209 |
| Distant-rotation config backscan with quiet heartbeat | 1 | 33,996 | 11,985 | 1,209 |
| Truncated variant of the existing runtime-state fixture | 2 | 33,382 | 11,508 | 1,027 |

Existing quiet/backscan/resume fixtures and assertions were reused. Stderr and
saved-cursor bytes match for both report formats; raw pre-save reports also match.
Producer source hashes and repository HEADs were independently verified; only
source hashes were substituted, with both HEADs still at the base. Fixture
bytes/modes/mtimes and versioned Markdown fixtures are preserved. Seven callback/
sharing/clock cases also pass against the original stage 27 bodies, with a separate
recursive-key-set comparison. These direct checks cover distinctions omitted by
schema 3. Scripts/data remain outside the worktree. This is focused synthetic
preservation evidence, not an exhaustive filesystem-failure or production replay.

Exact text/AST review verifies all ten bodies, eight wrapper calls, original root
signatures/defaults and marker vocabulary; all **85** remaining root functions
(including `analyse`, `build_digest_contract` and `run_digest`), the rest of root's
AST and all **28** existing digest companions are unchanged.
`python3 tools/check_python_documentation.py` passes for **208 modules**;
`git diff --check` passes.

Recommended next boundary: `annotate_remote_write_snapshot_window` (**78 lines**)
is substantive annotation for the existing remote-write owner. Supply current
timestamp parsing/conversion; preserve nested sharing and unknown-time/current/
window distinctions while leaving window and authority decisions in root.

Other useful companions are `generated_post_rate_history` (**55 lines**) and
`load_runway_config` (**20**, plus defaults) for the generated-pool owner, preserving
contamination exclusions, coverage calculations, current readers/parsers and clock
timing. `shadow_lifecycle_snapshot` (**23**) is a small runtime observation suitable
for related runtime-owner work. These implement domain behavior; compatibility
wrappers already serve their purpose. `discover_logs` (**18**),
`resolve_explicit_logs` (**32**), `is_selftest_log_path` (**4**) and
`load_authoritative_state_for_logs` (**22**) coordinate source selection,
production/self-test eligibility and neighboring-state authority, appropriately
together in root. Record mechanics already have an owner. Publication checks,
overall reports and CLI coordination also belong in root; neither wholesale
coordinator relocation nor a line-count target is useful.

Only stage 28 is implemented. Stop after stage **32** and reassess, or earlier if
no sensible digest modularisation remains. No production/configuration/state/log/
image-pool changes, bot execution, provider/posting calls, service control, merge,
deployment or force-push occurred. Previous branches/worktrees are preserved.

## Extracted in stage 29

Base: `847dc58844193f7ae317851b4ad1ddff4398e3a6`, verified against pushed
`origin/codex/modularisation-stage28`. Work is isolated on
`codex/modularisation-stage29` in
`/disks/disk1/research/mrsMThatcher-modularisation-stage29`.

Four domain companions move, totalling **176 lines** at the base, plus runway
defaults. `annotate_remote_write_snapshot_window` belongs to the remote-write
owner; `generated_post_rate_history`, `load_runway_config` and
`RUNWAY_CONFIG_DEFAULTS` belong to the generated-pool owner;
`shadow_lifecycle_snapshot` belongs to the runtime owner. Three thin adapters
retain root signatures/defaults and pass current named dependencies. Lifecycle
and runway defaults retain direct root aliases; the rate adapter explicitly
passes the current root basename regex alias, although its owner was already
the generated-pool module.

Bodies are exact apart from explicit timestamp conversion, conditional clock
and native-parser calls. Selected-end assignment, ValueError-only timestamp
handling, exact-int epochs, every relationship/reason string and supplied
authority flag retain their order and shared nested mutations. Rates preserve
timezone stripping, scan cutoff versus fixed 7/30-day outputs, contaminated
seconds, first-ID deduplication, coverage gaps and types/order. Config retains
its shallow defaults/observed/local overlays and existence/read/error boundary.
Lifecycle keeps its lazy import inside `try`, local path, shared feature/schedule
rows and exact failure response. No reads, clock samples, validation, authority
inference, retained callbacks or generic dependency containers are added.

`discover_logs`, `resolve_explicit_logs`, `is_selftest_log_path` and
`load_authoritative_state_for_logs` remain together in root, with unchanged
implementations and call positions. Image usage remains a pure consumer.
Source/window/resume/publication policy, schema 3, producer/default `__file__`,
context/cursor application, CLI/locking, `analyse` and `run_digest` are unchanged.

| Physical lines | Before | After |
| --- | ---: | ---: |
| Digest file | 3,800 | 3,648 (−152) |
| `analyse` | 1,477 | 1,477 |
| Remote-write owner | 1,402 | 1,486 |
| Generated-pool owner | 263 | 367 |
| Runtime owner | 335 | 362 |
| `run_digest` / Markdown wrapper | 360 / 10 | 360 / 10 |

Validation used `MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`
with the established temporary-HOME/network isolation. Baseline: **252 passed**;
final: **264 passed**. Selection covers corpus/generated pool, runtime, incidents,
safety hardening, general digest, generated runway/utilisation/pool health,
three existing digest-only lifecycle cases and two digest-only integrations for
current state after/at the window end. Six added test functions (12 cases) cover
current callbacks/defaults/regex, conditional clock and timezone handling,
exact epoch types, sharing, mutation/read/error order and lazy import failures.
The existing import guard additionally rejects eager lifecycle loading; all
pre-existing assertions remain. No whole bot harness ran.

Three temporary comparison tests passed. Complete fixed-path/time report pairs
match stage 28, reusing pool/quarantine, log, corpus, lifecycle, protocol and cost
cache fixtures:

| Fixture | Records | JSON bytes | Markdown bytes |
| --- | ---: | ---: | ---: |
| Generated rates and overdue lifecycle, current snapshot | 5 | 44,964 | 13,551 |
| Bounded window with current barrier | 5 | 47,715 | 14,083 |
| Quiet window with malformed config/metadata/lifecycle | 0 | 41,572 | 13,743 |

Raw reports, stderr and direct affected results also match, including types and
dictionary order. Eleven callback/clock/sharing cases pass against the original
stage 28 bodies; direct comparisons additionally cover eight annotated snapshots
and four config results/read traces. These cover distinctions omitted by schema
3. Producer source hashes and repository HEADs were independently verified;
only source hashes were substituted, with both HEADs still at the base. Fixture
bytes/modes/mtimes and versioned fixtures are unchanged. Scripts/data stay outside
the worktree. This is focused synthetic preservation evidence, not an exhaustive
filesystem-failure or production replay.

Exact text/AST review verifies four moved bodies, the defaults, three adapters,
original root signatures/defaults, all **89** remaining root definitions, the
rest of root's AST, every existing function in the three extended owners and
all **26** other digest companions. `python3 tools/check_python_documentation.py`
passes for **208 modules**; `git diff --check` passes.

Recommended next boundary: the contiguous **176-line** initial per-record
error/warning observation group inside `analyse`, from `is_self_test_error`
through error/recovery routing. Its prepared flags, legacy clarification
rejection callback and shared error/recovery lists form one coherent observation
helper. Only `is_asset_metadata_warning` and `is_handled_reply_restriction` need
returning for later consumers. Preserve predicate/exception/callback order and
pending-lane diagnostics. Source switching, publication authority, surrounding
config/state updates and subsequent coordinator dispatch remain separate.
This candidate is assessed here, not implemented.

Substantial complexity remains inside digest-derived owners: the incident owner
is **2,184 lines**, including the **1,884-line**
`summarise_operational_error_health`. Moving code into files does not establish
completion; any further split needs a useful domain boundary of its own.
Only stage 29 is implemented. Stop after stage **32** and reassess, or earlier
if no sensible digest modularisation remains. No production/configuration/state/
log/image-pool changes, bot execution, provider/posting calls, service control,
merge, deployment or force-push occurred. Previous branches/worktrees are preserved.
