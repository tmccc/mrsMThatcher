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
