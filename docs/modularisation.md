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
