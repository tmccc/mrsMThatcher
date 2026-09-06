# Bot modularisation

## Stage 1 — quotation/image scoring

Baseline: `af5eda7c163a8174ec1365060aa923d21787e7bd` (2026-09-06).
Before editing, HEAD, master and origin/master matched that commit; the worktree
was clean on `codex/bot-modularisation-stage1`. This is the first stage of the
runtime bot sequence. The completed digest sequence and its report are separate.

### Initial runtime map

Static inspection of the entry point's 592 top-level definitions and imports
identified these working families. The examples describe existing boundaries,
not a commitment to extract every row.

| Family | Representative root responsibilities and existing companions |
|---|---|
| Entry, configuration and control | `parse_cli_mode`, `production_bootstrap`, local-config validation, `load_control`, installation checks; `mrs_bot_health` reports progress |
| Lock and remote-write authority | Instance locks, barriers, request preparation and `x_request`; `remote_write_safety_protocol`, `remote_write_transport_journal`, `transaction_mutation_authority`, `x_api_error_semantics` |
| Durable state and recovery | Atomic JSON, used histories, main/reply receipts and reconciliation; `remote_media_upload_receipt`, `exact_receipt_retirement`, historical-context outbox companions |
| Quote/image metadata and candidates | Analysis loading, quote hashes and eligibility, image discovery/migration, candidate construction, spacing and `choose_matched_unused_image` |
| Quotation/image scoring | Tag/token matching, image text corpus, IDF, visual energy, seasonal exclusion and score components; now `mrs_bot_image_scoring` |
| Editorial and generated identity policy | Original-editorial concepts/profiles/adjustments, metadata validation, generated identity audit loading and policy/shadow selection |
| Publishing and conversational replies | Quote/meme posting, mention/quote-tweet discovery, context construction and durable reply dispatch; `single_call_reply`, `reply_evidence`, `historical_context_formatter` and related semantic/source companions, `engagement_question_experiment` |
| Scheduling and operational entry points | Quote/meme scheduling, reply-lane checks, `main`, self-test/test commands and `run_cli`; tracked launcher and health monitor |

### Extraction and compatibility

The contiguous eleven-helper family at baseline lines 18167–18417 computes
matches from supplied analysis dictionaries. Its shared lexical/scoring purpose
and lack of I/O make it a coherent boundary. File size alone did not determine
the scope.

`mrs_bot_image_scoring.py` owns the original bodies in their original order.
The root retains all eleven names, signatures, defaults and return annotations.
`as_string_list` and `visual_energy_score` are dependency-free aliases. Nine
small wrappers supply current root callbacks/values on every call:

- `re.sub`, `re.findall` and `TOKEN_STOPWORDS` for lexical helpers;
- the current sibling helpers, including references in comprehensions and the
  historical-reference generator;
- `mm_dd_in_window` for season checks, and
  `IMAGE_STRONG_MISMATCH_PENALTY` for strict exclusion.

Configuration authority stays in the coordinator. The companion has no reverse
bot import, stored callbacks, runtime configuration access, I/O or cache.
Wrappers preserve input and result references. Scores, component insertion order,
strict exclusion, ordinary round versus strict ceiling, inclusive/wrapped season
windows, exception handling and existing plural stripping remain unchanged.
Discovery/history migration, random-state handling, editorial/identity policy,
publishing, bootstrap, credentials, locks, receipts and scheduling were not edited.
README's deployment companion list and `docs/python_api.md` include the new file.

### Validation

Disposable evidence is confined to `/tmp/mrs-bot-stage1-FZSMvS`. The exact 36
existing pytest node IDs are in `selected-tests.txt` there; parametrization
expands them to 41 cases. Selection was made after inspecting the test bodies:

| Existing test file | Selected nodes | Coverage |
|---|---:|---|
| `test_unit_helpers.py` | 16 | IDF, energy, strict/ordinary matching, seasonal image availability, original/generated selection, origin boost, spacing, unused-image ranking; guarded bootstrap/selection rejects the retired observer import |
| `test_quote_image_selection_harness.py` | 2 | Import safety and score-cache component/reference behavior |
| `test_original_editorial_shadow_scoring.py` | 5 | Scoring determinism, original/generated winners, adjusted score retention and tie-breaking |
| `test_generated_identity_policy_shadow_scoring.py` | 6 | Existing scores, origin boost, exclusion without mutation, saved RNG, editorial tie and spacing-permitted candidates |
| `test_backtest_original_editorial_matching.py` | 2 | Production baseline score/components and repeated-run determinism |
| `test_simulate_regular_post_futures.py` | 1 | Isolated bot import without inherited OpenAI environment |
| `test_fail_safe_bootstrap_and_control.py` | 3 | Foreign-cwd import, explicit/idempotent bootstrap and six guarded entry points |
| `test_integration_harness.py` | 1 | `test_quote_image_post_uploads_media_records_state_and_schedules_meme`: actual worktree bot, loopback fake API, media/post assertions and temporary used-history/schedule state |

Before editing: documentation passed for **209 modules**; **41 tests passed in
5.80s**. After extraction: documentation passed for **210 modules**; the same
selection plus eight new tests in `tests/test_bot_image_scoring.py` produced
**49 passed in 5.61s**. Existing assertions were preserved. Added cases cover
current configuration/regex/sibling callbacks, nested lookups, import safety and
argument/result identity. No broad suite was run.

Both runs used repository conftest isolation: temporary HOME/state, dummy
credentials, dead external proxies, denied external sockets and explicitly
permitted loopback fake APIs. No production environment was sourced. Commands:

```bash
python3 tools/check_python_documentation.py
mapfile -t stage1_tests < /tmp/mrs-bot-stage1-FZSMvS/selected-tests.txt
TMPDIR=/tmp/mrs-bot-stage1-FZSMvS PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage1_tests[@]}" \
  tests/test_bot_image_scoring.py
```

The baseline command omitted only the new test file. Documentation commands also
used this TMPDIR and `PYTHONDONTWRITEBYTECODE=1`. Logs are
`baseline-documentation.txt`, `baseline-pytest.txt`, `current-documentation.txt`
and `current-pytest.txt` in the evidence directory.

Independent `verify_stage1.py` uses `git show` and compiles only the original
pure AST definitions and current root wrappers into private namespaces; it does
not import the bot runtime. `comparison.txt` records:

- All **11 original bodies reconstructed byte-for-byte**, reversing only four
  explicit dependency renames. All root signatures/defaults/return annotations
  match. All **581 unaffected root definitions** match in AST and source; the
  entire remaining root text matches after removing the new companion import.
- **6 exact scoring tuples** and ordered components match: all components
  `65.625`; strict exclusion `-10000.0`/ineligible; unmatched history `47.625`;
  invalid quality values skipped `63.625`; two missing-analysis fallbacks `0.0`.
  Caller values/container identities and IDF-subclass truthiness/get calls match.
- **19 season cases**, **11 helper results** (including the five-token
  round/ceiling distinction) and **3 malformed-input exception type/argument
  comparisons** pass. Assertions confirm the intended paths ran.
- `git diff --check` passed.

`dependencies.txt` includes globals found recursively in nested symbol tables;
`root-definition-map.txt` retains the static baseline definition inventory.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 29,099 / 1,141,183 | 28,933 / 1,132,837 |
| `mrs_bot_image_scoring.py` | absent | 312 / 12,579 |

The root loses 166 lines and 8,346 bytes. Combined source grows by 146 lines and
4,233 bytes for explicit dependency signatures, wrappers and documentation.

### Recommended next scope

Consider the pure original-editorial calculation family:
`original_editorial_numeric`, `original_editorial_concepts`,
`original_editorial_quote_concepts`, `original_editorial_image_concepts`,
`original_editorial_avoid_concepts`, `original_editorial_quote_dimension_profile`
and `original_editorial_shadow_score`. These share vocabulary/profile arithmetic
and can be reviewed separately from metadata loading, policy application and
winner/RNG handling. Review their recursive helper lookups and current weight/cap
configuration before extracting them. This recommendation is for a fresh
supervisor-selected invocation; stage 1 implements none of it.

## Stage 2 — original-editorial analysis, scoring and selection

Baseline: `f24883c9b4d66aba1db1d5b64a468cb91b307254` (2026-09-06).
The worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage2`
started clean on `codex/bot-modularisation-stage2`; HEAD and
`origin/codex/bot-modularisation-stage1` matched this verified parent.

### Extraction and compatibility

`mrs_bot_original_editorial.py` owns the complete thirteen-function family from
parent lines 18315–18523 and 18972–19168: numeric validation; concepts, quote/image
and avoid concepts; quote dimension profiles; item validation and analysis
loading; startup validation; shadow scoring/results/logging; and winner
application. The existing runtime map above remains applicable.

All root names, signatures, defaults and annotations remain. Numeric validation
is a direct alias; twelve concise adapters pass current root configuration,
vocabulary/dimension references, cache, logger and sibling callbacks. Recursive,
comprehension and nested-helper calls retain their original order. Standard
library imports remain ordinary imports. The owner has no reverse bot import,
retained callbacks, configuration authority, separate cache or import-time work.

The bodies preserve numeric errors/chaining, profile arithmetic and rounding,
invalid-editorial early return, conditional defaults, caps/affinities/penalties,
field ordering and log serialization. Loading retains expanded path keys,
cache-hit identity before I/O/discovery, strict integer schema checks, current
image/hash callbacks, complete dimensions/original coverage and no cache entry
on failure. Selection retains basename ties, generated-winner and same-object
baseline returns, and the selected-row/component shallow-copy boundary. Disabled
startup/logging/selection still perform no loading, scoring or logging.

The stage 1 scoring owner is byte-identical. All other runtime modules and root
statements, generated-identity policy, discovery/selector orchestration and RNG
code are unchanged. README's companion list and `docs/python_api.md` describe
the new owner. The completed digest report is unchanged.

### Validation

Evidence is confined to `/tmp/mrs-bot-stage2-8xv6CI`. `selected-tests.txt` records
26 file/node arguments: the full original-editorial test file, all eight stage 1
adapter/import tests, nine nearby unit selector/bootstrap regressions, six
generated-identity score/tie/spacing interactions, two selection-harness
import/cache tests, two editorial-backtest baseline/determinism tests, the safe
simulation import, three guarded bootstrap nodes, and one existing fake-server
quote/image posting integration. Parametrization expands these to 82 cases.
Existing test assertions were unchanged; no broad suite was run.

Before editing: documentation passed for **210 modules**; **82 passed in 5.74s**.
After extraction: documentation passed for **211 modules**; **90 passed in
5.97s**, including eight new tests in `tests/test_bot_original_editorial.py`.
These add import safety, current mutable vocabulary/dimensions and recursive
callbacks, ordered helper calls, lazy conversion/defaults, cache path/identity,
current validation/hash callbacks, failure non-insertion, disabled branches and
selection reference/copy boundaries.

Both pytest runs retained conftest's temporary HOME/state, dummy credentials,
dead proxies, denied external sockets and explicit loopback fake-server policy.
No production environment was sourced, private production state/configuration
or credentials read, live API/provider calls made, or service controlled.
Commands (the baseline omitted only the new test file):

```bash
TMPDIR=/tmp/mrs-bot-stage2-8xv6CI PYTHONDONTWRITEBYTECODE=1 \
  python3 tools/check_python_documentation.py
mapfile -t stage2_tests < /tmp/mrs-bot-stage2-8xv6CI/selected-tests.txt
TMPDIR=/tmp/mrs-bot-stage2-8xv6CI PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage2_tests[@]}" \
  tests/test_bot_original_editorial.py
TMPDIR=/tmp/mrs-bot-stage2-8xv6CI PYTHONDONTWRITEBYTECODE=1 \
  python3 /tmp/mrs-bot-stage2-8xv6CI/verify_stage2.py
git diff --check
```

`baseline-documentation.txt`, `baseline-pytest.txt`, `current-documentation.txt`
and `current-pytest.txt` retain the actual results. Adapted `verify_stage2.py`
uses immutable `git show` source, isolated AST namespaces and the existing
data-only fixtures; it never imports the bot runtime. `comparison.txt` records:

- All **13 moved bodies** match byte-for-byte after reversing only ten explicit
  dependency names. All root signatures match. Restoring the thirteen definitions
  and removing the import reconstructs the **entire parent root exactly**;
  **577 unaffected definitions** match in source and AST.
- An exact ordered profile and three full score/detail tuples match:
  **5.93688888888889, 2.0, -2.0**, including both tension terms, affinity ceiling,
  penalties, uncapped arithmetic and positive/negative caps. Six numeric errors
  match exception types, messages and causes.
- Synthetic valid/cached metadata preserves result/analysis identity and callback
  order; cached startup does no I/O. Four invalid files (boolean schema, stale
  hash, missing dimension, missing original) match errors/causes and leave no
  cache entry. Disabled branches and invalid-editorial conversion remain lazy.
- Changed/unchanged original winners, generated winners, basename ties and no
  original candidates match ordered payloads/types, exact JSON logs, reference
  and copy boundaries, callback counts and unchanged RNG state. Assertions verify
  each intended path occurred. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 28,933 / 1,132,837 | 28,686 / 1,119,849 |
| `mrs_bot_original_editorial.py` | absent | 497 / 22,065 |
| `mrs_bot_image_scoring.py` | 312 / 12,579 | 312 / 12,579 |

The root loses **247 lines / 12,988 bytes**. Combined runtime source grows by
**250 lines / 9,077 bytes** for the explicit dependency signatures, adapters and
owner documentation.

### Recommended next scope

Review the adjacent generated-identity audit input family:
`generated_identity_numeric`, `configured_generated_image_paths`,
`validate_generated_identity_audit_item`, `load_generated_identity_audit` and
`validate_generated_identity_shadow_startup`. These share audit schema, corpus
hash and cache responsibilities; review startup's current policy-enable
predicates alongside them. Candidate/winner and saved-RNG handling remain a
separate supervisor decision. Stage 2 implements only the editorial family.

## Stage 3 — generated-image identity audit and policy helpers

Baseline: `acbcb31f29ac4ad4f965b8505ab49baf364504b5` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage3` started clean on
`codex/bot-modularisation-stage3`; HEAD and remote
`origin/codex/bot-modularisation-stage2` matched the verified parent. The initial
runtime map above remains applicable.

### Extraction and compatibility

`mrs_bot_generated_identity.py` owns the eleven specified functions from parent
lines 18404–18847: numeric validation, audit item validation/loading, startup
validation, candidate policy rows, shadow results, policy selection, private RNG
replay, applied results and both policy loggers. Two aliases retain the numeric
and private-choice root names; nine adapters supply current settings, schema and
policy sets, root cache, logger and sibling callbacks without copying them.
All root signatures, defaults and annotations remain identical.

Numeric type/range checks, audit schema/hash/origin/coverage errors and chaining,
expanded cache keys and same-object hits before I/O remain unchanged. Failed
validation never inserts a cache entry. Discovery and audit loading remain lazy;
disabled startup branches retain their distinct logs. Penalties are converted
at the original branch points. Selection preserves caller order and shallow
candidate copies with shared nested components/analysis. Counterfactual tie
lists, private RNG replay, causation/mismatch flags, float/None fields, rounding,
payload order and diagnostic bounds retain their original behaviour. Both JSON
log formats and the shadow logger's catch-and-log boundary are unchanged.

`configured_generated_image_paths`, both policy-enable predicates, all
`GENERATED_IDENTITY_*` settings/schema/policy sets, enable flags and
`_GENERATED_IDENTITY_AUDIT_CACHE` authority stay in the root. The owner uses
ordinary standard-library imports and has no reverse bot import, retained
callbacks, separate cache or import-time work. Both prior owners are
byte-identical; discovery, actual selector/random-choice calls, editorial
behaviour, publishing, bootstrap and durable state/transport are unchanged.
README's companion list and `docs/python_api.md` include the new owner; digest
documentation is unchanged.

### Validation

Evidence is confined to `/tmp/mrs-bot-stage3-9hHtMO`. `selected-tests.txt` records
29 file/node arguments: both complete generated-policy files, all stage1/stage2
adapter/import tests, nearby generated-pool discovery and selector/editorial
interactions, selection-harness/backtest/import regressions, guarded bootstrap
and the fake-server quote-image posting integration. Parametrization produces
107 baseline cases. Existing assertions are unchanged; no broad suite was run.

Before editing: documentation passed for **211 modules**; **107 passed in
6.14s**. After extraction: documentation passed for **212 modules**; **115 passed
in 6.14s**, including eight new tests in `tests/test_bot_generated_identity.py`.
These cover import safety, current cache/configuration/helper references, lazy
loading/conversion and flags, shallow-copy boundaries, tied callbacks/RNG and
exact logger serialization/failure handling. Conftest retained temporary
HOME/state, dummy credentials, dead proxies, external-network denial and
explicit loopback fake APIs. No production environment was sourced, private
state/configuration/credentials read, provider called, or service controlled.

Exact commands (the baseline omitted only the new test file):

```bash
TMPDIR=/tmp/mrs-bot-stage3-9hHtMO PYTHONDONTWRITEBYTECODE=1 \
  python3 tools/check_python_documentation.py
mapfile -t stage3_tests < /tmp/mrs-bot-stage3-9hHtMO/selected-tests.txt
TMPDIR=/tmp/mrs-bot-stage3-9hHtMO PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage3_tests[@]}" \
  tests/test_bot_generated_identity.py
TMPDIR=/tmp/mrs-bot-stage3-9hHtMO PYTHONDONTWRITEBYTECODE=1 \
  python3 /tmp/mrs-bot-stage3-9hHtMO/verify_stage3.py
git diff --check
```

Logs are `baseline-documentation.txt`, `baseline-pytest.txt`,
`current-documentation.txt`, `current-pytest.txt` and `comparison.txt`.
Adapted `verify_stage3.py` uses immutable `git show` source, private AST
namespaces and existing data-only fixtures; it never imports the bot runtime:

- All **11 moved bodies** match byte-for-byte after reversing eleven explicit
  dependency names. Restoring the eleven definitions and removing the import
  reconstructs the **entire parent root exactly**; **578 unaffected definitions**
  match in source and AST. Both prior owners and existing tests are unchanged.
- Six complete ordered policy rows and eligible candidates match, including
  all actions, exact scores, origin boost and shared nested references. Seven
  numeric errors match types/messages/causes. Cache success, callback order and
  three schema/stale-hash/coverage failures match, with no failed insertion.
- Neutral, exclusion-caused and zero-penalty tied cases match complete ordered
  payloads, logs, callback order and RNG state, including missing-state and
  tie-count mismatches. Four native missing/empty-input errors and both payloads'
  sorted 12-item diagnostic bounds match. Intended branches are asserted.
- Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 28,686 / 1,119,849 | 28,408 / 1,103,252 |
| `mrs_bot_generated_identity.py` | absent | 482 / 23,694 |
| `mrs_bot_image_scoring.py` | 312 / 12,579 | 312 / 12,579 |
| `mrs_bot_original_editorial.py` | 497 / 22,065 | 497 / 22,065 |

The root loses **278 lines / 16,597 bytes**. Combined runtime source grows by
**204 lines / 7,097 bytes** for explicit dependency signatures, adapters and
owner documentation.

### Recommended next scope

Consider the read-only generated-image source/spacing helpers:
`generated_image_origin_quote_hash`, `image_selection_observability`,
`generated_image_spacing_required`, `original_posts_since_generated_image`,
`generated_images_allowed_by_spacing` and `filter_generated_images_by_spacing`.
They share basename classification and spacing calculations; inspect their
current configuration/callback lookups and same-object filter return. Keep
state updates, persistence and selector orchestration outside that proposed
boundary. The supervisor chooses the next scope in a fresh invocation; stage3
implements none of it.

## Stage 4 — asset metadata and catalog

Baseline: `768285697aed472fd4f3f56ec469be5d5438492c` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage4` started clean on
`codex/bot-modularisation-stage4`; HEAD and remote
`origin/codex/bot-modularisation-stage3` matched that parent. The initial runtime
map above remains applicable.

### Extraction and compatibility

`mrs_bot_asset_metadata.py` owns the sixteen selected helpers: JSON loading;
quote whitespace/hash, recursive merge, overrides, analysis loading/lookup and
source validation; image analysis loading/merging/lookup; original/configured
generated image discovery; generated-basename origin identity; and meme indexing.
Bodies retain parent order from lines 12723–13131, 18408–18423, 19674–19710 and
20859–20887. Two aliases and fourteen explicit adapters retain every root name,
signature, default and annotation. Current root helper/configuration/logger and
`StaleImageMetadata` references are supplied per call, including recursive merge
and quote normalisation/hash callbacks. The existing root `glob` seam remains;
other standard-library dependencies use ordinary imports.

JSON coercions/copy boundaries, exact override matching, ordinary schema
equality, lazy fallbacks, sorted discovery/merge order, collisions, nested
references and original log/error boundaries remain unchanged. Meme indexing
retains default encoding and filename/path-basename/output-filename assignment
order; source-hash warnings remain nonfatal.

Eligibility, seasonal candidates, history migration, spacing/state updates,
publishing and persistence stay in the root, including the excluded research,
quote-lines and file/current-image hash helpers, all exceptions and configuration/
cache authority. The three earlier owners are byte-identical. The new owner has
no reverse bot import, stored callbacks, cache/state object or import-time work.
README and `docs/python_api.md` include it; digest documentation is unchanged.

### Validation

Evidence is confined to `/tmp/mrs-bot-stage4-wxIg8q`. `selected-tests.txt` records
51 file/node arguments covering metadata/overrides/hash/discovery/stale images,
meme context, nearby selector/editorial/generated-policy interactions, all
stage1–3 adapter/import tests, selection-harness identity/cache checks, production
consistency identity, real-selector simulation, ordinary eligibility whitespace
integrity, guarded bootstrap and loopback fake-server quote-image/meme posting.
Existing assertions are unchanged; no complete broad suite was run.

Before editing: documentation passed for **212 modules**; **77 tests passed in
11.09s**. After extraction: documentation passed for **213 modules**; the same
selection plus eleven new tests in `tests/test_bot_asset_metadata.py` produced
**88 passed in 11.04s**. Added cases cover import safety, current adapters and
recursive callbacks, lazy loading, schema equality, ordered discovery/logging,
references and exception boundaries. Conftest retained temporary HOME/state,
dummy credentials, dead proxies, denied external network and loopback fake APIs.
Production environment/private state/configuration/credentials and service were
untouched; no live provider calls occurred.

```bash
TMPDIR=/tmp/mrs-bot-stage4-wxIg8q PYTHONDONTWRITEBYTECODE=1 \
  python3 tools/check_python_documentation.py
mapfile -t stage4_tests < /tmp/mrs-bot-stage4-wxIg8q/selected-tests.txt
TMPDIR=/tmp/mrs-bot-stage4-wxIg8q PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage4_tests[@]}" \
  tests/test_bot_asset_metadata.py
TMPDIR=/tmp/mrs-bot-stage4-wxIg8q PYTHONDONTWRITEBYTECODE=1 \
  python3 /tmp/mrs-bot-stage4-wxIg8q/verify_stage4.py
git diff --check
```

The baseline omitted only the new test file. Logs are `baseline-documentation.txt`,
`baseline-pytest.txt`, `current-documentation.txt` and `current-pytest.txt`.
Adapted `verify_stage4.py` uses immutable `git show` source and private AST
namespaces, never a live bot import. `comparison.txt` records:

- All **16 moved bodies** match byte-for-byte after reversing nine configuration
  names. All root signatures/defaults/annotations match. Restoring the definitions
  and removing the import reconstructs the **entire parent root exactly**;
  **571 unaffected definitions**, other root statements, all three prior owners,
  existing tests and digest documentation are unchanged.
- Four exact whitespace/UTF-8 hash cases and three generated-basename results;
  partial overrides/warning order, JSON coercions/copy boundaries/native error;
  metadata references, stale/hash-failure messages/context and nonfatal source
  warning; ordered discovery/merge results, collisions, nested references and
  error chaining; meme indexing/reference/default-encoding/error boundaries all
  match. Assertions confirm the intended branches ran.
- Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 28,408 / 1,103,252 | 28,224 / 1,094,191 |
| `mrs_bot_asset_metadata.py` | absent | 432 / 15,942 |
| `mrs_bot_image_scoring.py` | 312 / 12,579 | 312 / 12,579 |
| `mrs_bot_original_editorial.py` | 497 / 22,065 | 497 / 22,065 |
| `mrs_bot_generated_identity.py` | 482 / 23,694 | 482 / 23,694 |

The root loses **184 lines / 9,061 bytes**. Combined runtime source grows by
**248 lines / 6,881 bytes** for explicit dependency signatures, adapters and owner
documentation.

### Recommended next scope

Consider the remaining read-only image source/spacing helpers:
`image_selection_observability`, `generated_image_spacing_required`,
`original_posts_since_generated_image`, `generated_images_allowed_by_spacing`
and `filter_generated_images_by_spacing`. They share source classification and
spacing calculations; retain current root callbacks/settings and the same-object
filter return. Keep state updates, persistence and selector orchestration outside
that boundary. The supervisor selects the next scope in a fresh invocation;
stage4 implements none of it.

## Stage 5 — ordinary quotation eligibility and candidates

Baseline: `92d666f9961aa37c2716402ae81eb7ad5a3e0ecf` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage5` started clean on
`codex/bot-modularisation-stage5`; HEAD and remote
`origin/codex/bot-modularisation-stage4` matched that parent. The existing runtime
map remains applicable; inspection focused on this family and its callers.

### Extraction and compatibility

`mrs_bot_quote_candidates.py` owns the thirteen requested helpers for seasonal
windows/weights, source/hash preparation, canonical ordinary eligibility and
candidate/cycle selection. Bodies retain their parent order. `mm_dd_in_window`
and `weighted_random_choice` are aliases; eleven explicit adapters retain all
root names, signatures, defaults and annotations, supplying current root helpers,
configuration and logger on each call.

Window/type handling, arithmetic/clamping, source blank/duplicate/exclusion order,
first matching duplicate, counters, metadata/season-status/candidate references
and exact logs remain unchanged. Weighted choice uses the shared production RNG
stream, including nonpositive-total choice, `>=`, final fallback and native
errors. Source loading retains default encoding, validation order and the root
clock. Canonical eligibility keeps its deferred formatter import, core/schema/
rule/count/alias validation, exact source/packet hashes and attribution partition;
it remains uncached and independent of context-only sidecars. Cycle resets clear
the caller's existing set at the original points and preserve fallback ordering.

The four previous owners are byte-identical. Configuration, exception authority,
durable histories/persistence, image matching/spacing, research implementation,
publishing and scheduling remain outside the owner. There is no reverse bot
import, retained callback, new cache/state abstraction or import-time runtime
work. README and `docs/python_api.md` include the companion; digest documentation
is unchanged.

### Validation

Evidence is confined to `/tmp/mrs-bot-stage5-tRaXvC`. `selected-tests.txt` records
36 file/node arguments: all five ordinary-eligibility integrity tests, the
fourteen requested seasonal/cycle/duplicate/history/missing-analysis/bootstrap
unit nodes, disabled experiment RNG and context-semantic independence, relevant
harness canonical/alias/history/cache cases, real-selector futures/RNG cases,
all four prior owner adapter/import files, guarded bootstrap and loopback
fake-server quote-image posting. Existing assertions are unchanged; no broad
suite or duplicate behavioural comparison suite was added.

Before editing: documentation passed for **213 modules**; **81 passed in 13.61s**.
After extraction: documentation passed for **214 modules**; **88 passed in
14.05s**, including seven new tests in `tests/test_bot_quote_candidates.py`.
They cover import safety, callbacks/settings, RNG/reference boundaries, ordering,
default encoding, loader errors and reset/exclusion identity. The initial run had
87 passes and one failure in 13.81s: a new assertion expected another Python
version's empty-choice message. Comparing with native `random.choice([])` fixed the
test without changing runtime code (`current-pytest-initial.txt`).

Both runs retained repository conftest isolation: temporary HOME/state, dummy
credentials, dead external proxies, denied external sockets and explicit
loopback fake APIs. No production environment was sourced or live provider
called; production code, configuration, credentials, durable state and service
were untouched.

```bash
TMPDIR=/tmp/mrs-bot-stage5-tRaXvC PYTHONDONTWRITEBYTECODE=1 \
  python3 tools/check_python_documentation.py
mapfile -t stage5_tests < /tmp/mrs-bot-stage5-tRaXvC/selected-tests.txt
TMPDIR=/tmp/mrs-bot-stage5-tRaXvC PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage5_tests[@]}" \
  tests/test_bot_quote_candidates.py
TMPDIR=/tmp/mrs-bot-stage5-tRaXvC PYTHONDONTWRITEBYTECODE=1 \
  python3 /tmp/mrs-bot-stage5-tRaXvC/verify_stage5.py
git diff --check
```

The baseline omitted only the new test file. Logs are `baseline-documentation.txt`,
`baseline-pytest.txt`, `current-documentation.txt`, `current-pytest.txt` and
`comparison.txt`. The compact verification script adapts only stage 4's source
equivalence check, using immutable `git show` text and ASTs without importing the
bot: all **13 moved bodies** match byte-for-byte after reversing eight explicit
dependency names; all root signatures/defaults/annotations match. Restoring the
definitions and removing the import reconstructs the **entire parent root
exactly**, including **572 unaffected definitions** and other root statements.
All four prior owners, existing tests and digest documentation are unchanged.
Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 28,224 / 1,094,191 | 27,959 / 1,083,964 |
| `mrs_bot_quote_candidates.py` | absent | 481 / 17,457 |
| `mrs_bot_image_scoring.py` | 312 / 12,579 | 312 / 12,579 |
| `mrs_bot_original_editorial.py` | 497 / 22,065 | 497 / 22,065 |
| `mrs_bot_generated_identity.py` | 482 / 23,694 | 482 / 23,694 |
| `mrs_bot_asset_metadata.py` | 432 / 15,942 | 432 / 15,942 |

The root loses **265 lines / 10,227 bytes**. Combined runtime source grows by
**216 lines / 7,230 bytes** for explicit dependency signatures, adapters and
owner documentation.

### Recommended next scope

Consider the remaining read-only image source/spacing helpers:
`image_selection_observability`, `generated_image_spacing_required`,
`original_posts_since_generated_image`, `generated_images_allowed_by_spacing`
and `filter_generated_images_by_spacing`. Keep state updates, persistence and
selector orchestration outside that boundary. The supervisor chooses the next
stage in a fresh invocation; stage 5 implements none of it.

## Stage 6 — image selection and generated-image spacing

Baseline: `0086d48f43a955574a785893b8e1e7a7c766adc8` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage6` started clean on
`codex/bot-modularisation-stage6`; HEAD and remote
`origin/codex/bot-modularisation-stage5` matched that parent. The existing runtime
map guided focused inspection of the selected functions and affected tests.

### Extraction and compatibility

`mrs_bot_image_selection.py` owns all fourteen requested functions: eligible
image availability; source observability; spacing validation, allowance,
filtering, counters, receipt reflection and logs; `choose_matched_unused_image`;
`choose_regular_quote_image_pair`; and `choose_engagement_question_image`.
Fourteen explicit adapters retain every root name, signature, default and
annotation and supply current helpers, configuration, logger and exception
classes. The central selector's callback dependencies remain explicit.

Bodies retain their original order. Strict integer validation, caught/default
conversion, counter caps/resets, source classification and exact logs remain
unchanged. Disabled/allowed filtering returns the original set. Cycle resets and
counter updates mutate caller objects. Legacy normalization clears/updates the
caller set and invokes the current root save callback **before** checking for
remaining legacy entries, including the original save-failure boundary.

Selection preserves catalog order/index identity, metadata rechecks, distinct
stale/global/quote-specific/unsafe/exhaustion exceptions, seasonal and spacing
exclusions, eligible-only resets and persistent last-image boundaries. Scoring,
origin boost arithmetic, component copy boundaries, candidate references,
identity filtering before ties/editorial selection, shared RNG state/choice,
shadow/applied callback order and sorted five-item diagnostics are unchanged.
Pair retries retain initial exclusions, attempt limits, phase names, the one-time
reset cleared in `finally` and exhaustion arguments. Fixed experimental quotes
restore images only in their existing quote-specific mismatch handler.

All five previous owners are byte-identical. `current_image_sha256`, exception
classes, durable history/receipt/persistence implementations, publishing and
configuration authority stay root. The owner has no reverse bot import, retained
callbacks, dependency framework, new state/cache abstraction or import-time work.
README and `docs/python_api.md` include the companion; digest documentation is
unchanged.

### Validation

Evidence is confined to `/tmp/mrs-bot-stage6-d9Y0zi`. `selected-tests.txt` records
**71 file/node arguments**, including **48 targeted unit-helper nodes**, all five
previous owner adapter/import files, the complete generated-identity production
and shadow scoring files and original-editorial scoring/selection file, affected
harness cases, six futures nodes (spacing transitions, RNG, candidate capture,
receipt selection state and safe import), fixed-quote mismatch restoration,
guarded bootstrap and loopback fake-server quote/image posting. Unit coverage
includes seasonal/stale/missing metadata, unsafe migration, origin boost,
spacing expiry/counters, cycle recovery and last-image fallback, global failures
without quote retries, failed-post history/counter restoration, all four spacing
receipt reconciliation/reapply nodes, and observer removal.

Before editing: documentation passed for **214 modules**; **232 passed in
15.63s**. After extraction: documentation passed for **215 modules**; **243
passed in 15.75s**, including nine new tests expanding to eleven cases in
`tests/test_bot_image_selection.py`. They address current callbacks, references,
import safety and exception/order gaps; existing assertions are unchanged.
An initial selection-file generation error started unselected collection; it was
interrupted before tests ran (`aborted-collection.txt`). The corrected commands
require a nonempty selection. No broad test suite or duplicate behavioural
comparison suite was run.

Both completed runs retained conftest's temporary HOME/state, dummy credentials,
dead external proxies, denied external network and explicit loopback fake APIs.
No production environment was sourced, live provider called or service
controlled; production installation, durable state and credentials were untouched.

```bash
TMPDIR=/tmp/mrs-bot-stage6-d9Y0zi PYTHONDONTWRITEBYTECODE=1 \
  python3 tools/check_python_documentation.py
set -e
mapfile -t stage6_tests < /tmp/mrs-bot-stage6-d9Y0zi/selected-tests.txt
(( ${#stage6_tests[@]} > 0 ))
TMPDIR=/tmp/mrs-bot-stage6-d9Y0zi PYTHONUSERBASE=/home/tonym/.local \
  MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider "${stage6_tests[@]}" \
  tests/test_bot_image_selection.py
TMPDIR=/tmp/mrs-bot-stage6-d9Y0zi PYTHONDONTWRITEBYTECODE=1 \
  python3 /tmp/mrs-bot-stage6-d9Y0zi/verify_stage6.py
git diff --check
```

The baseline omitted only the new test file. Documentation/pytest logs use the
`baseline-` and `current-` prefixes. `comparison.txt` records the compact
structural check adapted from stage 5: all **14 moved bodies** match exactly
after reversing six explicit dependency names; all root signatures match.
Restoring definitions and removing the import reconstructs the **entire parent
root byte-for-byte**, including **569 unaffected definitions** and all other
statements. Five prior owners, existing tests and digest documentation are
unchanged. The comparison uses immutable `git show` source and ASTs, never a bot
runtime import. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 27,959 / 1,083,964 | 27,720 / 1,074,171 |
| `mrs_bot_image_selection.py` | absent | 581 / 23,074 |
| `mrs_bot_image_scoring.py` | 312 / 12,579 | 312 / 12,579 |
| `mrs_bot_original_editorial.py` | 497 / 22,065 | 497 / 22,065 |
| `mrs_bot_generated_identity.py` | 482 / 23,694 | 482 / 23,694 |
| `mrs_bot_asset_metadata.py` | 432 / 15,942 | 432 / 15,942 |
| `mrs_bot_quote_candidates.py` | 481 / 17,457 | 481 / 17,457 |

The root loses **239 lines / 9,793 bytes**. Combined runtime source grows by
**342 lines / 13,281 bytes** for explicit dependencies, adapters and owner
documentation.

### Recommended next scope

Review quotation/image used-history normalization and legacy migration checks,
including catalog completeness and source identity checks. Keep durable I/O and
receipt authority separate when deciding that boundary. The supervisor chooses
the next stage in a fresh invocation; stage 6 implements none of it.

## Stage 7 — ordinary quotation posting orchestration

Baseline: `ea616adbd7b15038fa015d73d9d08b3ec13b3734` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage7` started clean on
`codex/bot-modularisation-stage7`; HEAD and the verified remote stage 6 branch
matched that parent. Inspection reused the runtime map and followed the named
workflow, its callers and affected tests.

### Extraction and compatibility

`mrs_bot_quote_posting.py` owns the complete original 711-line
`post_random_quote` function. Its body is byte-identical, without dependency
renames or branch/recovery refactoring. One root adapter preserves the public
signature, defaults and annotations and passes 76 current dependencies:
callbacks, configuration, logger, root exception/type authorities and the
existing `engagement_question_trial` module. Standard-library `random` shares
the existing stream. No callback retention, reverse import, dependency bag,
provider client, new state/cache or import-time runtime work was introduced.

The initial barrier, preflight clock/reconciliation and history snapshots retain
their order. Experiment invalidation/defer/fallback, reservations and canonical
versus public text remain intact. The nested upload validator retains its
captured objects and all revalidation points. Three ordinary selection phases,
random draws, upload shapes, payload/recovery fields, receipt handoff and SIGINT
deferral are unchanged. Exact-body verification also preserves BaseException
versus Exception handling, all six conditional `locals()` keys, pre-confirmation
history restoration, pending-schedule/emergency recovery, context disposition,
experiment evidence, event order and final receipt retirement.

All six previous owners are byte-identical. Configuration, exception/type
authority, bootstrap/CLI, transaction/transport/receipt/persistence implementations
and scheduling helpers stay root. README's companion list and the Python API
table include the new owner; digest documentation is unchanged.

### Validation

Evidence: `/tmp/mrs-bot-stage7-jMhZ3U`. The validated, nonempty
`selected-tests.txt` contains **138 file/node arguments**, expanding to **268
existing cases**: 81 unit-helper nodes (117 cases), outcome conservatism,
unwritable-outbox preflight, engagement plan/binding/revalidation helpers and
bot-review cases, cross-lane ambiguity/pending-receipt/SIGINT barriers,
bootstrap/control and one-shot entry points, all six previous owner files,
19 regular-lane adversarial transport/restart cases, and the loopback fake-server
successful quote/image posting test. Unit coverage includes the three selection
phases, AI flags, history restoration/invalid IDs, persistence and hard-death
boundaries, emergency component/backup/fsync/latch failures, bound schedules,
context disposition and no-second-post replay. Existing assertions and fixtures
are unchanged; the huge unit file and broad README suite were not run wholesale.

Before editing: documentation passed for **215 modules**, and **268 passed in
44.17s**. After extraction: documentation passed for **216 modules**, and **275
passed in 46.48s**. Four new tests expand to seven cases in
`tests/test_bot_quote_posting.py`: guarded import/shared RNG, current adapter
dependencies and argument/result/error identity, preflight/snapshot references,
and ordinary/control/treatment posting through existing helpers with real
binding/revalidation and local transaction persistence. They check closure object
identity, all three validator calls, upload shape, random draw order and
publication/context/retirement ordering. An initial new-test run failed because
the existing receipt-isolation fixture was not imported; importing that fixture
fixed all three failures without changing runtime code. The standalone corrected
new-test run passed **7 cases in 2.59s**.

All runs used conftest's temporary HOME/state, dummy credentials, dead external
proxies, denied external network and explicitly allowed loopback fake APIs.
Disposable scripts, fixtures and test TMPDIR stayed under the evidence directory.
No production environment was sourced, live provider called, production state or
credentials accessed, service controlled, or deployment performed.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage7-jMhZ3U PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py"
mapfile -t stage7_tests < "$TMPDIR/selected-tests.txt"
(( ${#stage7_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider "${stage7_tests[@]}" tests/test_bot_quote_posting.py
python3 "$TMPDIR/verify_stage7.py"
git diff --check
```

Baseline omitted only the new test file and was preceded by successful selected
collection (**268 cases**, `selected-collection.txt`). Logs use `baseline-` and
`current-` prefixes. `comparison.txt` records the compact structural check adapted
from stage 6: exact moved body, identical public signature, unshadowed conditional
locals, explicit adapter references, standard-library-only imports, and the
**entire parent root reconstructed byte-for-byte**, including **582 unaffected
definitions**. Six prior owners, existing tests and digest documentation are
unchanged. Comparison uses immutable `git show` and static source/ASTs; it never
imports or runs the bot. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 27,720 / 1,074,171 | 27,091 / 1,048,165 |
| `mrs_bot_quote_posting.py` | absent | 812 / 36,376 |

The root loses **629 lines / 26,006 bytes**. Combined runtime source grows by
**183 lines / 10,370 bytes** for explicit dependencies, the adapter and owner
documentation. The six prior owners retain their stage 6 sizes.

### Recommended next scope

Consider daily meme posting orchestration (`post_next_meme`) as the next coherent
workflow boundary, retaining root transaction, receipt, persistence and scheduling
authority. The supervisor chooses the next scope in a fresh invocation; stage 7
implements none of it.

## Stage 8 — daily meme selection, scheduling and posting

Baseline: `e19b8b36088f15868f2f32ba38ed1b00410a317d` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage8` started clean on
`codex/bot-modularisation-stage8`; HEAD matched the verified remote stage 7
parent. Inspection reused the runtime map, supervisor dependency/candidate aids,
and the named functions, direct callers and indirect receipt/scheduler tests.

### Extraction and compatibility

`mrs_bot_daily_meme.py` owns all eighteen requested functions: filename recovery,
cache summary, catalog/cycle selection, meme calendar/schedule producers and
updates, stage logging, post-ID validation and the complete `post_next_meme`
workflow. Their **720 original definition lines** retain byte-identical bodies
and original order, without dependency renames or branch splitting. Seventeen
explicit root adapters and the regex-only `original_meme_filename` alias preserve
all public signatures, defaults and annotations. Adapters supply current root
callbacks, settings, logger and exception authority; ordinary standard-library
imports preserve the shared RNG and ambient human-readable datetime logs.

The original regexes, missing-analysis fallback, metadata composition, catalog
extensions/order and first-unused/cycle-reset-save behavior remain intact.
Calendar boundaries, timezone/DST handling, same-date guards, version/mode/anchor
fields, conditional random draws, early returns and optional saves are unchanged.
Stage operation results and original exceptions, stage names and 500-character
event reasons remain intact. Posting retains closure/state/path/item references,
barriers and reconciliation order, prepared transport/upload handoff, SIGINT
handling, pending-schedule promotion and local recovery, emergency completeness
and backup checks, distinct state/receipt failure events and journal-before-receipt
retirement. The three conditional `locals()` names remain unshadowed.

All seven prior owners, including ordinary quotation posting, are byte-identical.
Shared date/state helpers, the asset metadata loader, configuration, exceptions,
`json_file_matches` and durable state/receipt/transport implementations remain in
the root or their existing owners. No reverse import, retained callback, new
state/cache/schema, provider client or retry policy was introduced. README's
companion list and Python API table include the owner; digest documentation is
unchanged.

### Validation

Evidence: `/tmp/mrs-bot-stage8-o4aRNb`. The validated nonempty
`selected-tests.txt` contains **128 file/node arguments** across 15 files,
expanding to **258 existing cases**. It includes 71 unit-helper nodes (106 cases),
all requested daily-meme atomic-save/ID/midnight/replay/SIGINT/hard-death/remote-
outcome/promotion/emergency/backup/latch/retirement families, calendar/version/
mode/anchor tests and actual ordinary-post schedule interactions. Other selected
cases cover outcome conservatism (3), context-outbox isolation (2), pending-receipt
fsync/recovery and cross-lane barriers (18), bootstrap/control and fail-safe
initialisation (42), all seven prior owners (60), 19 daily-meme adversarial
transport/restart cases, and eight loopback integrations: successful meme upload,
missing ID, midday quote/restart/single meme, midnight fallback, missing assets,
ordinary quote scheduling, reply-priority isolation and read cooldown.

Before editing: documentation passed for **216 modules**; selected collection
found **258 cases in 4.48s**; **258 passed in 53.23s**. After extraction:
documentation passed for **217 modules**; **267 passed in 53.63s**. The nine new
cases in `tests/test_bot_daily_meme.py` also passed separately in **2.20s**. They
add guarded import/shared-library checks, current adapter callback/configuration
and argument/result/error identity, exact regex callback order, metadata/path/
state references, cycle-save failure order, DST fallback short-circuiting,
conditional RNG/save order, stage error identity/truncation and posting closure/
prepared-transport references. Existing tests and assertions were unchanged.

Both runs used conftest isolation: temporary HOME/state, dummy credentials, dead
proxies, denied external sockets and explicitly permitted loopback fake APIs.
Disposable scripts, fixtures and test TMPDIR stayed under the evidence directory.
No production configuration, credentials or state were accessed, production bot
run, provider called, service controlled or deployment performed.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage8-o4aRNb PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py"
mapfile -t stage8_tests < "$TMPDIR/selected-tests.txt"
(( ${#stage8_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider "${stage8_tests[@]}" tests/test_bot_daily_meme.py
python3 "$TMPDIR/verify_stage8.py"
git diff --check
```

Baseline omitted only the new file and followed successful selected collection;
selection failure or an empty list stops before pytest. Logs use `baseline-` and
`current-` prefixes. `comparison.txt` records the compact stage 7 structural check
adapted for eighteen functions: exact moved bodies, explicit adapters/alias,
unchanged signatures and conditional locals, standard-library-only imports,
**565 unaffected root definitions** and the **entire parent root reconstructed
byte-for-byte**. Seven prior owners, existing tests and digest documentation also
match the immutable parent. Comparison uses static source/ASTs and `git show`;
it never imports or runs the bot. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 27,091 / 1,048,165 | 26,581 / 1,030,150 |
| `mrs_bot_daily_meme.py` | absent | 961 / 34,246 |

The root loses **510 lines / 18,015 bytes**. Combined runtime source grows by
**451 lines / 16,231 bytes** for explicit dependency signatures, adapters and
owner documentation. The seven prior owners retain their stage 7 sizes.

### Recommended next scope

Consider confirmed conversational reply history/context selection:
`recent_confirmed_account_replies`, `recovery_comparison_account_replies`,
`recent_same_author_account_interactions` and their private filtering/sorting
helpers. Keep durable history writes, draft handling, provider calls and reply
dispatch in their current owners. The supervisor chooses the next scope in a
fresh invocation; stage 8 implements none of it.

## Stage 9 — retired reply-draft validation and fixed schemas

Baseline: `e6a61aed1e93589452994e6f8658dba7aa16c550` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage9` started clean on
`codex/bot-modularisation-stage9`; HEAD matched the pushed stage 8 parent.
Inspection reused the runtime map and supervisor's
`legacy-validation-dependencies.json`, whose twelve function dependency lists
and seventeen-constant inventory matched static symbol/source inspection.

### Extraction and compatibility

`mrs_bot_legacy_reply_validation.py` owns the contiguous compatibility block
between `ai_reply_receipt_draft_is_valid` and
`mention_pagination_provenance_is_valid`: twelve functions covering hashing,
timestamps, multi-model context, tested-pipeline drafts, AI-first v3 claims and
drafts, single-Sol schemas 1/2 and family dispatch. All **527 original function
definition lines** retain byte-identical bodies and original docstrings, including
absent private-helper docstrings. The **17 constant definitions / 188 lines**
retain their exact names, initializers, types and order without duplication.

Eight explicit root adapters and four function aliases preserve
names, signatures, defaults and annotations. All root constants directly alias
the owner objects. Adapters pass current root constant references, sibling/helper
callbacks, `MAX_TRUSTED_FACTS`, `MAX_SUPPLIED_IMAGES` and
`SINGLE_CALL_MAX_IMAGE_BYTES` on every call. The owner imports only standard
libraries and constructs fixed strings/frozensets; it performs no file,
environment, provider or RNG work and retains no callbacks or mutable state.

Compact sorted non-ASCII JSON hashing, strict UTF-8/error boundaries, lowercase
hash matching, UTC parsing, exact fields and historical prompt/model contracts,
optional repair/retrieved-count/schema-2 author fields, integer/boolean versus
equality-only checks, unsigned payloads, identity/context binding, ordered visible
conversation, claims/evidence/assessment constraints and dispatch short-circuiting
are unchanged. Native malformed-input errors still reach the existing outer
receipt boundary. Current draft validation, lifecycle wrappers, receipt loading,
promotion/reconciliation/transport, `bound_visible_conversation`, configuration
and exception authority stay in their existing locations. Frozen validation
grants no outbound authority and cannot make legacy drafts current pending drafts.
All eight prior owners and existing tests/fixtures are byte-identical; README's
companion list and the Python API table include the owner. Digest docs are intact.

### Validation

Evidence: `/tmp/mrs-bot-stage9-UK74KZ`. Validated `selected-tests.txt` contains
**20 nonempty unique file/node arguments across 12 files**, expanding as follows:

| Existing selection | Cases |
|---|---:|
| Entire `tests/test_legacy_conversational_reply_recovery.py`, using all four existing data fixtures | 31 |
| Seven requested nearest current unit-helper regressions | 7 |
| All eight previous owner adapter/import test files | 69 |
| Foreign-cwd import, explicit/idempotent bootstrap and six guarded entry points | 8 |
| `tests/test_integration_harness.py::test_normal_mention_reply`, with loopback fake APIs | 1 |

Before editing: documentation passed for **217 modules**, selected collection
found **116 cases in 3.96s**, and **116 passed in 8.24s**. After extraction:
documentation passed for **218 modules**, selected collection found **131 cases
in 3.97s**, and **131 passed in 9.15s**. The **15 new cases** in
`tests/test_bot_legacy_reply_validation.py` cover guarded import and alias identity,
current dependency/argument/result/error references, four-case dispatch and
short-circuiting, nested context callbacks, visible-conversation references,
current size limits, and native TypeError/IndexError/UTF-8 boundaries. They reuse
the existing recovery helpers and register the `isolated_recovery_paths` autouse
fixture. Existing assertions, fixtures and sending barriers were preserved.

Both runs retained conftest temporary HOME/state, dummy credentials, dead proxies,
denied external sockets and explicit loopback fake APIs. All disposable scripts
and test TMPDIR stayed under the evidence root. No production configuration,
credentials or state were read, production bot run, provider called, service
controlled or deployment performed. No broad suite was run.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage9-UK74KZ PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py"
mapfile -t stage9_tests < "$TMPDIR/selected-tests.txt"
(( ${#stage9_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider --collect-only "${stage9_tests[@]}" tests/test_bot_legacy_reply_validation.py
python3 -m pytest -q -p no:cacheprovider "${stage9_tests[@]}" tests/test_bot_legacy_reply_validation.py
python3 "$TMPDIR/verify_stage9.py"
git diff --check
```

Baseline omitted only the new file. Selection generation and collection succeeded
before each run; failure or an empty list stops before execution. Logs use
`baseline-` and `current-` prefixes. `comparison.txt` records the reused compact
stage 8 structural check, extended for the seventeen constants: exact moved
bodies/initializers, correct direct aliases and current-dependency adapters,
**570 unaffected root definitions**, and **the entire parent root reconstructed
byte-for-byte** by restoring constants/functions and removing the new import.
All other root statements, eight prior owners, existing tests and digest docs
match the immutable parent. Comparison uses static source/ASTs and `git show`,
without importing the bot. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 26,581 / 1,030,150 | 25,995 / 1,011,096 |
| `mrs_bot_legacy_reply_validation.py` | absent | 820 / 29,885 |

The root loses **586 lines / 19,054 bytes**. Combined runtime source grows by
**234 lines / 10,831 bytes** for explicit dependency signatures, adapters, aliases
and owner documentation. The eight previous owners retain their stage 8 sizes.

### Recommended next scope

Consider the confirmed conversational history/context selection family:
`recent_confirmed_account_replies`, `recovery_comparison_account_replies`,
`recent_same_author_account_interactions` and their filtering/sorting/exclusion
helpers. Keep durable history writes, draft validation, provider calls and reply
dispatch in their existing owners. The supervisor chooses the next scope in a
fresh invocation; stage 9 implements none of it.

## Stage 10 — current reply drafts and confirmed-history queries

Baseline: `b9814d493ef0b096f4bb962ad573498828b8ca89` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage10` started clean on
`codex/bot-modularisation-stage10`; HEAD matched the pushed stage 9 parent.
Inspection reused the runtime map and supervisor's fourteen-function dependency
inventory and nineteen direct test candidates, then inspected generation,
receipt reconciliation and mention/quote-tweet callers and nearby regressions.

### Extraction and compatibility

`mrs_bot_reply_state.py` owns fourteen functions: `pending_ai_reply_draft_key`,
`validate_current_ai_reply_draft`, `store_pending_ai_reply`, `pending_ai_reply`,
`clear_pending_ai_reply`, `_confirmed_conversational_history_rows`,
`_confirmed_history_sort_key`, `recent_confirmed_account_replies`,
`_reply_context_history_excluded_post_ids`, `recovery_comparison_account_replies`,
`_same_author_confirmed_history_rows`, `recent_same_author_account_interactions`,
`_reply_target_epoch` and `ai_reply_receipt_draft_is_valid`. Their **433 original
definition lines** retain byte-identical bodies and original docstrings,
including the absent `_reply_target_epoch` docstring. The fixed
`CONVERSATIONAL_REPLY_HISTORY_LANES` frozenset retains its exact three-line
definition; the root name directly aliases the same object.

Eleven explicit adapters and three function aliases retain root names,
signatures, defaults and annotations. Adapters pass current root helpers,
lane-set reference, limits, logger, exception/result classes and configuration
on each call. The sole internal original-argument signature difference is that
the owner's `recent_confirmed_account_replies` requires `limit` explicitly:
the root always forwards it, preserving the original definition-time default
while the body reads the current root cap. There is no dynamic sentinel.

Recovery preserves current evidence lookup, caller context/recent-list
references, validation/type/identity checks, deep copies, insertion-order
eviction and malformed-container handling. Evidence unavailability propagates
without retiring the draft; local validation failure retires it and emits the
same zero-model-call result; obsolete/invalid errors retire without inventing
an evaluation. Warnings, metadata and exception boundaries are unchanged.
History queries retain original row references, filters, strict integer/epoch
bounds, numeric sorting, stable duplicate replacement, exclusions, exclusive
upper bounds and age-cutoff inclusion. Recovery's final string-ID tie order,
whitespace differences, same-author zero-cap slice semantics, timezone parsing
and receipt text equality are unchanged.

The owner imports only standard libraries, constructs the fixed frozenset and
retains no callbacks or mutable state. It performs no import-time file,
environment, provider or RNG work. Logging/result recording implementations,
generation, media collection, transport, evaluation/quarantine/pruning,
durable history writes, state persistence/normalisation and current/legacy
receipt lifecycle/reconciliation authority stay in their existing locations.
All nine previous owners, existing tests/fixtures and digest docs are unchanged.
README's companion list and the Python API table include the new owner.

### Validation

Evidence: `/tmp/mrs-bot-stage10-p9Vu7v`. Validated `selected-tests.txt` contains
**48 unique, nonempty file/node arguments across 13 files**, covering all
nineteen direct candidates (the legacy candidate is covered by its full file):

| Existing selection | Cases |
|---|---:|
| Entire `tests/test_legacy_conversational_reply_recovery.py` | 31 |
| 33 selected unit-helper nodes: draft/evidence recovery, confirmed history, three generation exclusion guards, receipt validation/clearing and mention/quote-tweet reconciliation/restart paths | 37 |
| All nine previous owner import/adapter files | 84 |
| Foreign-cwd import, explicit/idempotent bootstrap and guarded entry points | 8 |
| Loopback `test_normal_mention_reply` and `test_author_cap_context_survives_restart_in_newer_target_prompt` | 2 |

The author-cap restart case guards retained context reaching later generation
exactly once; it is an indirect context/generation check, not a confirmed-history
fixture. Before editing: documentation passed for **218 modules**, collection
found **162 cases in 4.06s**, and **162 passed in 11.33s**. After extraction:
documentation passed for **219 modules**, collection found **178 cases in
3.97s**, and **178 passed in 11.70s**. The **16 new cases** in
`tests/test_bot_reply_state.py` cover import/alias identity, current dependency
and argument/result references, fixed default/current cap, deep-copy boundaries,
current exception/result classes, exact recovery telemetry/warnings, ordering
and native receipt/time error boundaries. They reuse `unit_reply_context`,
`unit_approved_reply`, the current evidence and confirmed-receipt helpers, and
register the existing `isolate_regular_post_receipt` autouse fixture.

Both runs retained conftest temporary HOME/state, dummy credentials, dead
proxies, denied external sockets and explicit loopback fake APIs. Disposable
scripts, fixtures and test TMPDIR stayed under the recorded evidence root.
No production configuration/state was read, production bot run, provider called,
service controlled or deployment performed. No broad suite was run.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage10-p9Vu7v PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py" --current
mapfile -t stage10_tests < "$TMPDIR/current-selected-tests.txt"
(( ${#stage10_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider --collect-only "${stage10_tests[@]}"
python3 -m pytest -q -p no:cacheprovider "${stage10_tests[@]}"
python3 "$TMPDIR/verify_stage10.py"
git diff --check
```

Baseline omitted `--current` and used `selected-tests.txt`; the current list
adds only the new test file. Selection generation and collection succeeded
before execution; failures or empty lists stop the command. Logs use
`baseline-` and `current-` prefixes. The compact stage 9 structural proof was
adapted for fourteen functions, one constant and the internal required limit.
`comparison.txt` proves exact moved bodies/constant, direct aliases, current
dependency forwarding, unchanged root signatures/defaults and **564 unaffected
root definitions**. Restoring originals and removing the one new import
reconstructs **the entire immutable parent root byte-for-byte**, preserving all
other statements. All nine prior owners and unrelated files match the parent.
The comparison uses static source/ASTs and `git show`, without runtime imports.
Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 25,995 / 1,011,096 | 25,724 / 1,002,016 |
| `mrs_bot_reply_state.py` | absent | 536 / 18,244 |

The root loses **271 lines / 9,080 bytes**. Combined runtime source grows by
**265 lines / 9,164 bytes** for explicit dependency signatures, adapters and
owner documentation. All nine previous owner sizes are unchanged.

### Recommended next scope

Consider bounded reply-media preparation, beginning with `_safe_reply_image_url`
and `collect_reply_images`. Keep transport implementation, provider orchestration,
receipt lifecycle and persistence authority in their current owners. The
supervisor chooses the next scope in a fresh invocation; stage 10 implements
none of it.

## Stage 11 — reply generation and its image/provider boundary

Baseline: `13a73ae1a72474024eaa350ee61881436aae34ed` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage11` on
`big-nas-2`, user `tonym`, started clean on `codex/bot-modularisation-stage11`;
HEAD matched the pushed stage 10 parent. Inspection used the supervisor's
eleven-function dependency inventory and eighteen direct test candidates,
plus current mention/quote-tweet callers and generation/recovery regressions.

`mrs_bot_reply_generation.py` owns `log_ai_reply_posting_outcome`,
`_safe_reply_image_url`, `collect_reply_images`,
`_definite_connection_failure_before_transmission`, `_openai_api_error`,
`_openai_retry_metadata`, `_is_openai_provider_health_failure`,
`_is_terminal_candidate_local_failure`, `openai_responses_reply_call`,
`_record_single_call_result` and `generate_single_call_reply`. The **574 original
definition lines** have byte-identical bodies in original function order.
Eleven explicit adapters preserve root signatures/defaults/annotations,
including deferred request/result-class annotation strings, and pass current
root callbacks, settings, application classes and the requests object per call.
The three fixed definitions retain their exact **30 lines**, initializer order
and types: `_REPLY_IMAGE_MIME_TYPES` is still a mutable set; the provider-health
and terminal-local category collections remain separate frozensets. Root names
directly alias the owner objects and adapters forward current root references.

Image origin/shape/identity rules, pause checks, request arguments, MIME/byte
bounds, chunk filtering, exception causes, response closure and final validation
remain exact. Responses transport retains its two-attempt policy, proved
pre-transmission retry, first-429 closure/sleep and metadata carry-through,
second-429 precedence, health progress, latency rounding and error boundaries.
Retry-After parsing retains the current clock, Mapping check, finite numeric or
aware HTTP-date parsing, ceiling and inclusive seven-day bounds. Generation
keeps images before history/preparation, caller references and exclusions,
the current pipeline/evidence/config/transport, decision/usage/evaluation order
and provider-health accounting. Recovered valid decisions remain successful;
image/local failures retain zero-call/local handling without invented cooldowns.

The owner imports only standard libraries, constructs the fixed sets and does
no import-time file/environment/provider/RNG work or retained callback setup.
Root exception/result authorities, all ten previous owners, the actual pipeline
and evidence implementation, draft/history adapters, cooldown persistence,
terminal evaluation/pruning, posting/receipts and durable state are unchanged.
README's companion list and the Python API table include the owner; digest
documentation and existing tests/fixtures are unchanged.

Evidence: `/tmp/mrs-bot-stage11-kXeIsd`. Validated baseline selection contains
**48 unique nonempty file/node arguments across 16 files**, covering all eighteen
direct candidates. It includes the full failure-routing file (**27 cases**),
all ten previous owner test files (**100**), guarded bootstrap/pause (**12**),
selected unit generation/recovery (**16**), durability transport guard (**1**),
current pipeline/recovery (**11**) and ten loopback integration nodes (**13**).
Integration covers normal mention and quote-tweet replies, native image success
and failure, provider/malformed envelopes, terminal local rejection and restart
context. No entire broad unit/integration suite was run.

Before editing: documentation passed for **219 modules**, collection found
**180 cases in 4.83s**, and **180 passed in 21.51s**. After extraction:
documentation passed for **220 modules**, collection found **191 cases in
4.81s**, and **191 passed in 22.13s**. The **11 new cases** cover guarded import,
constant aliases/current dependencies, request/result/reference identity,
response cleanup and original causes, retry and generation/logging order, and
metadata fallback. They reuse `FakeHttpResponse` and its image bytes,
`FakeRepository`, `enabled_config`, `raw_decision`, `response_envelope`, the
current pipeline and the registered `isolate_regular_post_receipt` fixture.
Conftest retained temporary HOME/state, dummy credentials, dead proxies, denied
external sockets and explicit loopback APIs. Disposable scripts, fixtures and
test TMPDIR remained under the evidence root. Production configuration/durable
state and service were not operated on; no deployment or live provider call ran.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage11-kXeIsd PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py" --current
mapfile -t stage11_tests < "$TMPDIR/current-selected-tests.txt"
(( ${#stage11_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider --collect-only "${stage11_tests[@]}"
python3 -m pytest -q -p no:cacheprovider "${stage11_tests[@]}"
python3 "$TMPDIR/verify_stage11.py"
git diff --check
```

Baseline omitted `--current` and used `selected-tests.txt`; the current list adds
only the new test file. Selection generation and nonempty validation succeeded
before both collection and execution. Logs use `baseline-`/`current-` prefixes.
`comparison.txt` verifies exact moved bodies/constants, aliases and dependency
forwarding, signatures and **564 unaffected root definitions**. Restoring the
original definitions/aliases and removing the one new import reconstructs the
**entire immutable parent root byte-for-byte**, including all other statements.
All ten previous owners and unrelated files match the parent. The comparison
uses static source/AST and `git show`, without importing the bot. Documentation
and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 25,724 / 1,002,016 | 25,290 / 986,011 |
| `mrs_bot_reply_generation.py` | absent | 727 / 26,262 |

The root loses **434 lines / 16,005 bytes**. Combined runtime source grows by
**293 lines / 10,257 bytes** for explicit dependency signatures, adapters and
owner documentation. All ten previous owner sizes remain unchanged.

Next useful domain: consider the bounded terminal reply evaluation query,
recording and pruning helpers, keeping lane-cycle/quarantine policy and durable
persistence authority explicit. The supervisor selects the next scope in a
fresh invocation; stage 11 implements none of it.

## Stage 12 — conversational reply receipt values and validation

Baseline: `b3456fc1427cd1aba1792d6238b6f582ef0831d4` (2026-09-06).
On `big-nas-2` as `tonym`, worktree
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage12` started clean on
`codex/bot-modularisation-stage12`, matching the pushed stage 11 parent.
Inspection used the supervisor's eleven-function dependency inventory and forty
direct test candidates, plus transport/source-lineage, receipt lifecycle,
emergency recovery, counter/watermark and mention/quote-tweet callers.

`mrs_bot_reply_receipt_values.py` owns
`mention_pagination_provenance_is_valid`,
`_conversational_reply_receipt_is_semantically_valid`,
`conversational_sending_receipt_from_confirmed`,
`confirmed_reply_receipt_is_semantically_valid`,
`sending_reply_receipt_is_semantically_valid`,
`_legacy_confirmed_reply_receipt_is_semantically_valid`,
`_legacy_sending_reply_receipt_is_semantically_valid`,
`bind_conversational_reply_attempt_time`,
`_confirmed_reply_receipt_from_sending`,
`_reply_confirmation_epoch_after_remote_success` and
`conversational_reply_confirmation_epoch`. Their **375 original definition
lines** retain byte-identical bodies and docstrings. Eleven explicit adapters
preserve root names, signatures, defaults and annotations, supplying current
validators, JSON/date/clock helpers, legacy versions, logger and exception class
on each call. Current and legacy dispatch, including reconstructed-source
validation, continue through root callbacks.

Exact pagination/schema/lane/identifier restrictions, current context/author and
quote binding, frozen legacy exceptions, clarification constraints and native
error/short-circuit order are unchanged. Source projection/reconstruction retain
shallow outer copies, immutable AIReply and nested references, exact removed
fields, lowercase hashes of canonical sending bytes and quote-lane date rules.
Current confirmed receipts may still omit source hashes; legacy v4 recovery
still requires them. Attempt binding, confirmation-time coercion, rollback
clamping/warning and existing version comparisons are unchanged. Legacy
validation grants no new sending authority.

No constants, schemas, retained callbacks, reverse imports, configuration loader
or provider clients were added. Import uses only standard libraries, with no
file/environment/provider/RNG work. Shared integer/epoch/date/JSON and draft
validators, `InvalidConfirmedReplyReceipt`, durable receipt load/write/promotion/
removal/retirement, state application/reconciliation/emergency handling, counters,
watermarks and `post_conversational_reply_with_durable_identity` remain in their
existing locations. All eleven prior owners and existing tests/fixtures are
unchanged. README's companion list and the Python API table include the owner;
digest documentation is unchanged.

Evidence: `/tmp/mrs-bot-stage12-RxmvhG`. Validated baseline selection contains
**62 unique nonempty file/node arguments across 17 files**, covering all forty
direct candidates. It runs the full legacy recovery file (**31 cases**), all
eleven prior owner files (**111**), selected current unit receipt/restart/
adversarial cases (**65**), source-lineage/promotion (**3**), replaced-source
rejection (**1**), guarded bootstrap (**8**) and normal mention/quote-tweet
loopback integrations (**2**). The existing same-post-ID/different-source and
unchanged-state/source-byte rejection fixtures are reused without alteration.

Before editing: documentation passed for **220 modules**, **221 cases** collected
in **4.87s**, and **221 passed in 14.10s**. After extraction: documentation passed
for **221 modules**, **239 cases** collected in **4.93s**, and **239 passed in
14.83s**. The **18 new cases** cover guarded import, current dependencies,
argument/result references, family callback/error order, shallow copies, exact
hash inputs, attempt-time ordering and current clock/exception identity. They
reuse the current receipt helpers, all four legacy fixture families and the
registered `isolate_regular_post_receipt` autouse fixture. The first run had
**237 passes and two new-test failures**: those fixtures incorrectly supplied
string attempt epochs. Correcting them to the existing strict-integer contract
resolved both; runtime code and existing assertions/barriers were unchanged.

All runs retained temporary HOME/state, dummy credentials, dead proxies, denied
external sockets and explicit loopback fake APIs. Disposable scripts/fixtures
and test TMPDIR stayed under the evidence root. Production remained on
`master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per worktree metadata;
production configuration/state, live providers and service controls were not
used. No broad suite, live bot run, deployment or next-stage work was performed.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage12-RxmvhG PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py" --current
mapfile -t stage12_tests < "$TMPDIR/current-selected-tests.txt"
(( ${#stage12_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider --collect-only "${stage12_tests[@]}"
python3 -m pytest -q -p no:cacheprovider "${stage12_tests[@]}"
python3 "$TMPDIR/verify_stage12.py"
git diff --check
```

Baseline omitted `--current` and used `selected-tests.txt`; the current list adds
only the new test file. Generation and nonempty validation precede pytest and
failures stop execution. Logs use `baseline-`/`current-` prefixes, with the first
post-extraction failure retained in `current-initial-pytest.txt`.
`comparison.txt` checks exact bodies/docstrings, signatures/defaults/annotations,
the parent dependency inventory and explicit current-root forwarding. Restoring
original definitions and removing the single import reconstructs the **entire
immutable parent root byte-for-byte**, including **564 unaffected definitions**
and all other statements. All eleven prior owners, unrelated files and digest
documentation match the parent. This comparison uses source/AST and `git show`,
without importing the bot. Documentation and `git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 25,290 / 986,011 | 25,025 / 976,419 |
| `mrs_bot_reply_receipt_values.py` | absent | 469 / 17,402 |

The root loses **265 lines / 9,592 bytes**. Combined runtime source grows by
**204 lines / 7,810 bytes** for explicit dependencies, adapters and owner
documentation. All eleven previous owner sizes are unchanged.

Next useful domain: consider bounded terminal reply evaluation queries,
recording and pruning, retaining lane/quarantine policy and durable persistence
authority in the root. The supervisor selects the next scope in a fresh
invocation; stage 12 implements none of it.

## Stage 13 — confirmed reply state application and reconciliation

Baseline: `6a7f87f470de8bcb464a218ab1623ca21e421647` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage13` started
clean on `codex/bot-modularisation-stage13`, matching the verified pushed stage
12 parent. Inspection used the supervisor's five-function dependency inventory,
all thirty-five direct test candidates and actual posting/recovery callers.

`mrs_bot_reply_reconciliation.py` owns `_valid_iso_date`,
`_advance_reply_counters_to_confirmation_date`, `apply_confirmed_reply_receipt`,
`reconcile_confirmed_reply_receipt` and
`confirmed_reply_emergency_representation_is_complete`. All **419 original
definition lines** retain byte-identical bodies/docstrings. Five explicit root
adapters preserve names, signatures, defaults and annotations and pass current
callbacks, settings/limits, paths, datetime, logger and exception authorities.
Standard-library-only import performs no file/environment/provider/RNG work;
no callbacks, configuration loader, state/schema or framework were added.

Canonical date round trips and native catches, forward-only counter resets,
confirmation/authority checks and schema4 advancement before clarification
conflict retain their original order. Pagination base/page ownership, recovery
events, deep-copied continuation and reset guards still delay watermark
advancement. Queue removal, in-place draft clearing, lane identifiers, exactly-once
counters, author updates and cache callbacks retain their reference boundaries.
History keeps its original mapping expansion, clock/cutoff, deduplication,
strict epochs, numeric sorting/cap and nested references; clarification records
and events retain idempotency. Reconciliation verifies lineage before mutation,
saves state durably before journal retirement/removal and preserves distinct
failure causes/logs. Emergency completeness retains its existing predicates and
narrow confirmation-error catch without adding outbound authority.

`update_last_seen_mention_id`, `mark_mention_seen_if_applicable`, mention/reset/page
authority, receipt values/validation and durable I/O, transport implementation,
posting and exception definitions stay in their existing locations. All twelve
earlier owners and existing tests/fixtures are unchanged. README's companion
list and the Python API table include the new owner; digest documentation is
unchanged.

Evidence: `/tmp/mrs-bot-stage13-3rzvyO`. The validated nonempty baseline selection
contains **61 file/node arguments across 19 files**: all twelve owner test files
(**129 cases**), full legacy conversational recovery (**31**), affected unit
posting/restart/adversarial cases (**39**), mention authority/reset/page cases
(**5**), lineage rejection (**1**), outcome/commit ordering (**2**), guarded
bootstrap (**8**) and normal mention/quote-tweet loopback integrations (**2**).
The huge unit/integration/backlog files were selected only by affected nodes.
Coverage includes midnight/clock rollback, stale backups, exactly-once counters,
clarification terminal state, draft clearing, persistence failures and SIGINT.

Before editing: documentation passed for **221 modules**, **217 cases** collected
in **4.83s**, and **217 passed in 14.22s**. After extraction: documentation passed
for **222 modules**, **233 cases** collected in **4.93s**, and **233 passed in
15.27s**. Ten new tests expand to **16 cases**, covering guarded import, current
dependencies, state/reference/order contracts and partial-failure boundaries.
They reuse current receipt builders and active-page helpers with the registered
`isolate_regular_post_receipt` autouse fixture. The initial run had **232 passes
and one new-test failure**: its assertion expected draft-map replacement;
correcting it to the existing in-place clearing contract resolved the failure.
Runtime bodies and existing assertions/barriers were unchanged.

```bash
set -euo pipefail
export TMPDIR=/tmp/mrs-bot-stage13-3rzvyO PYTHONUSERBASE=/home/tonym/.local
export MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 tools/check_python_documentation.py
python3 "$TMPDIR/select_tests.py" --current
mapfile -t stage13_tests < "$TMPDIR/current-selected-tests.txt"
(( ${#stage13_tests[@]} > 0 ))
python3 -m pytest -q -p no:cacheprovider --collect-only "${stage13_tests[@]}"
python3 -m pytest -q -p no:cacheprovider "${stage13_tests[@]}"
python3 "$TMPDIR/verify_stage13.py"
git diff --check
```

Baseline omitted `--current` and used `selected-tests.txt`; the current list adds
only the new test file. Generation/nonempty validation preceded each pytest
invocation. Logs retain the baseline, initial failure and successful current run.
Temporary HOME/state, dummy credentials, dead proxies, denied external sockets
and explicit loopback fake APIs remained active, with disposable material under
the evidence root. Production remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd` per worktree metadata; its configuration,
state, credentials and service were not operated on. No live provider call,
deployment or next-stage work ran.

`comparison.txt` verifies exact moved bodies/docstrings, dependency inventory,
current-root forwarding and signatures/defaults/annotations. Restoring the five
definitions and removing one import reconstructs the **entire immutable parent
root byte-for-byte**, including **570 unaffected definitions** and all unrelated
statements. All prior owners and unrelated files match the parent. This uses
static source/AST and `git show`, without importing the bot. Documentation and
`git diff --check` passed.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 25,025 / 976,419 | 24,687 / 962,579 |
| `mrs_bot_reply_reconciliation.py` | absent | 505 / 20,783 |

The root loses **338 lines / 13,840 bytes**; combined runtime source grows by
**167 lines / 6,943 bytes** for explicit dependency signatures, adapters and owner
documentation. All twelve previous owner sizes remain unchanged.

Next useful domain: review `terminal_reply_evaluation`,
`record_terminal_reply_evaluation` and `prune_reply_evaluation_records`, retaining
mention-quarantine policy and durable persistence authority in their existing
locations. The supervisor chooses the next stage in a fresh invocation.

## Stage 14 — conversational reply delivery and receipt lifecycle

Baseline: `d7a5d69d574384a613800502e34b92514f96b590` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage14` started
clean on `codex/bot-modularisation-stage14`, matching the verified pushed stage
13 parent. Inspection used the supervisor's eight-function dependency inventory,
all fifty-six direct test candidates and the indirect namespace, promotion,
source-lineage, recovery and mention/quote-tweet callers.

`mrs_bot_reply_delivery.py` owns `load_confirmed_reply_receipt`,
`write_confirmed_reply_receipt`, `write_sending_reply_receipt`,
`promote_sending_reply_receipt`,
`_promote_legacy_sending_reply_receipt_from_confirmed_transport`,
`remove_confirmed_reply_receipt`,
`retire_proved_rejected_conversational_reply_receipt` and
`post_conversational_reply_with_durable_identity`. All **559 original definition
lines** retain byte-identical bodies/docstrings. Eight explicit root adapters
preserve names, signatures, defaults and annotations, passing current callbacks,
paths, JSON module, logger and exception authority on each call. Import uses
only the standard library, performs no file/environment/provider/RNG work and
retains no callbacks or configuration.

The move preserves no-follow loading and validation order, namespace/retirement
checks before publication, exact current/legacy confirmed-source binding,
projection/validation/replacement order, and the original `open`/`json.load`
removal design. Proved rejection still claims before removal and records
ambiguity before native interrupts or errors with their original causes.
Delivery retains schema4/lane restrictions, shallow templates and reviewed
string references, ordinary transport text, durable sending before SIGINT
deferral, distinct remote-error handling, journal identity/time confirmation,
canonical fallback and backup completeness, guard retention/release conditions,
and journal-before-receipt retirement. Normal return keeps the original objects.

Shared I/O primitives, journals/source binding and mutation authority,
`create_post`, runtime barriers, SIGINT implementation, persistence, receipt
values and state application/reconciliation stay in their existing locations.
All thirteen previous owners and existing tests/fixtures remain unchanged;
README and the Python API table add the companion. Digest documentation is
unchanged. No I/O strategy, retry policy, configuration, state/schema or
transaction authority changed.

Evidence: `/tmp/mrs-bot-stage14-htmwFN`. The validated nonempty baseline has
**87 file/node arguments across 21 files**: all thirteen prior owner files
(**145 cases**), full legacy conversational recovery using all four data fixtures
(**31**), affected unit cases (**64**), write outcomes (**20**), lineage (**4**),
namespace (**9**), guarded bootstrap (**8**), four conversational crash/restart
nodes (**4**) and loopback integrations (**5**). These include normal mention
and quote-tweet delivery, both restart duplicate-suppression paths, target
rejection, generic403/invalid-envelope outcomes, SIGINT recovery, exact-source
ABA/replacement and cleanup fsync barriers. Large unit/integration files were selected only by affected
nodes; no complete broad suite ran.

Before editing: documentation passed for **222 modules**, **290 cases** collected
in **5.15s**, and **290 passed in 24.34s**. After extraction: documentation passed
for **223 modules**, **310 cases** collected in **5.09s**, and **310 passed in
25.73s**, on the first run. Twelve new tests expand to **20 cases** for guarded
import, current dependencies, receipt references/operation order, shallow
`AIReply` delivery, pause/rejection error causes, canonical backup fallback and
retained SIGINT when receipt/marker durability is lost. They reuse
`unit_sending_v4_reply_receipt`, `install_receipt_bound_x_request_stub` and the
real transaction authorities, with both relevant autouse isolation/reset
fixtures registered. Existing assertions and barriers were preserved.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage14-htmwFN/run_selected.sh baseline  # before editing
bash /tmp/mrs-bot-stage14-htmwFN/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage14-htmwFN/verify_stage14.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The runner sets `PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1` and an evidence-root
`TMPDIR`; selection generation, nonempty validation and successful collection
precede `python3 -m pytest -q -p no:cacheprovider` with explicit selected nodes.
Temporary HOME/state, dummy credentials, dead proxies, denied external sockets
and explicit loopback fake APIs remain active. Production remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd` per worktree metadata; its private
configuration/state/credentials and service were not accessed or operated on.
No live provider calls, production deployment/restart or next-stage work ran.

`comparison.txt` verifies exact moved bodies/docstrings, signatures/defaults/
annotations, dependency inventory and current-root forwarding. Restoring the
eight definitions and removing one import reconstructs the **entire immutable
parent root byte-for-byte**, including **567 unaffected definitions** and every
unrelated statement. All thirteen previous owners, existing tests/fixtures and
unrelated files match the parent. Verification uses static source/AST and
`git show`, without importing the bot. Documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 24,687 / 962,579 | 24,306 / 948,149 |
| `mrs_bot_reply_delivery.py` | absent | 703 / 29,657 |

The root loses **381 lines / 14,430 bytes**; combined runtime source grows by
**322 lines / 15,227 bytes** for explicit dependency signatures, adapters and
owner documentation. All thirteen previous owner sizes remain unchanged.

Next useful domain: review `terminal_reply_evaluation`,
`record_terminal_reply_evaluation` and `prune_reply_evaluation_records`, keeping
mention-quarantine policy and durable persistence authority in their existing
locations. Supervisor review precedes any next stage in a fresh invocation.

## Stage 15 — normal mention and hot-post reply cycle

Baseline: `4525efff67f722383953a637647b6662d66389d5` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage15` started
clean on `codex/bot-modularisation-stage15`, matching the verified pushed stage
14 parent. Inspection used the supervisor's 88-reference inventory and all 49
direct test candidates, plus indirect scheduler/control, backlog, recovery and
local mention/hot-post/restart callers.

`mrs_bot_normal_reply_cycle.py` owns the complete `maybe_reply_to_mentions`
definition: **893 original definition lines**, including its **887-line body**
and unchanged docstring. One explicit root adapter preserves the name, signature,
defaults and annotations and supplies all **88 current root dependencies** on
every invocation, including the recursive root callback, copy module, logger,
configuration, application classes and exceptions. The owner imports only the
standard library, does no runtime I/O on import and retains no dependencies.

The unchanged body preserves daily reset/reconciliation before the write
barrier; control/cooldown/cap/spacing and discovery failure order; candidate
sorting, direct skips and nested quarantine batching/nonlocals; context/media,
clarification and evidence routing; recovered drafts and actual-model-call
budget/refunds; draft-before-send durability and pagination provenance; distinct
delivery errors, terminal retirement and confirmed receipt cleanup. Backlog
continuation follows queue drainage and remaining budget, calling the current
root callback with the original state, current evaluation count and
`_skip_hot_post_fetch=True`. One successful reply still ends the cycle.
Discovery/pagination, watermark/counter/quarantine policy, pipeline/evidence,
context/media, persistence, reconciliation and delivery implementations remain
in their existing locations. All fourteen earlier owners, existing tests and
digest documentation are unchanged; README and the Python API table add the owner.

Evidence: `/tmp/mrs-bot-stage15-RASNa4`. The validated nonempty baseline selection
has **95 file/node arguments across 13 files**: six relevant prior reply-owner
files (**96 cases**), selected mention backlog/quarantine (**30**), single-call
failure routing (**7**), unit recovery/delivery/scheduling (**37**), ambiguity
barriers (**21**), proved-rejection outcomes (**5**), guarded bootstrap (**8**)
and local loopback integrations (**24**). All 49 supplied direct nodes are
included. Large unit, integration and backlog files were selected by affected
nodes; no complete broad suite ran.

Before editing: documentation passed for **223 modules**, **228 cases** collected
in **4.43s**, and **228 passed in 30.05s**. After extraction: documentation passed
for **224 modules**, **236 cases** collected in **4.46s**, and **236 passed in
30.13s**, on the first run (`current-initial-pytest.txt`). Five new tests expand
to **8 cases**: guarded import, current dependency/argument/result/error forwarding
on later invocations, current-root recursive continuation with unchanged state
identity and model-call/refund/cap values, durable reconciliation before the
barrier even when disabled, and distinct native context/generation error routing.
They reuse the active-page, editorial-outcome, canonical-context and schema4
receipt helpers with `isolate_regular_post_receipt` registered as autouse;
real local persistence and transaction barriers remain active.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage15-RASNa4/run_selected.sh baseline  # before editing
bash /tmp/mrs-bot-stage15-RASNa4/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage15-RASNa4/verify_stage15.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The runner requires successful selection generation, a validated nonempty list
and nonempty collection before `python3 -m pytest -q -p no:cacheprovider` with
explicit selected arguments. It sets `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONDONTWRITEBYTECODE=1` and evidence-root `TMPDIR`. Temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
fake APIs remain active. The successful affected run is retained; tests were not
repeated after documentation-only edits.

`comparison.txt` proves exact body/docstring/signature/dependency forwarding,
nested closures/nonlocals and recursive callback keywords. Restoring the original
definition and removing one import reconstructs the **entire immutable parent
root byte-for-byte**, including **574 unaffected definitions** and all unrelated
statements. All fourteen previous owners and unrelated tracked files match the
parent. These checks use static source/AST and `git show`, without importing the
bot. The final documentation gate and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 24,306 / 948,149 | 23,514 / 917,584 |
| `mrs_bot_normal_reply_cycle.py` | absent | 1,004 / 41,594 |

The root loses **792 lines / 30,565 bytes**; combined runtime source grows by
**212 lines / 11,029 bytes** for explicit dependency declarations/forwarding and
owner documentation. Prior owner sizes are unchanged.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials or service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: supervisor review of the complete `maybe_reply_to_quote_tweets`
cycle as a separate orchestration boundary. Any next stage requires supervisor
review and a fresh invocation.

## Stage 16 — quote-tweet reply cycle, eligibility, context and markers

Baseline: `6042ef6d4f2a3e901bc34416977af55ba79d9d38` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage16` started
clean on `codex/bot-modularisation-stage16`, matching the verified pushed stage
15 parent. The supervisor's eight-function inventory and fifteen direct test
nodes were used alongside indirect quote-lane and local loopback coverage.

`mrs_bot_quote_reply_cycle.py` owns **892 original definition lines**: the
complete `maybe_reply_to_quote_tweets` (**723 definition lines; 722 body lines**)
and seven eligibility,
profile, canonical-context and marker helpers. Seven explicit root adapters
retain signatures/defaults/annotations and pass current dependencies on each
call, including **78** for the cycle; the dependency-free profile formatter is
an alias. All original bodies and docstrings are byte-identical. The owner
imports only standard-library types and retains no callbacks or runtime state.

Compatibility preserves retryable missing timestamps, structured retweet/direct
quote precedence and native malformed-profile errors; target-first text budgets,
independent context copies and media-before-summary order; bounded 2000-entry
markers versus the durable replied ledger; both daily resets and reconciliation
before barriers/controls; discovery, cooldown, sorting and candidate-limit order;
zero-call failures consuming the quote candidate budget; draft reuse and
pre-send availability; distinct retirement/defer/error paths; durable state
before journal/receipt cleanup and one success per cycle. Watch-list/own-post
lookup, quote discovery, shared context/media/evidence, counters, pipeline,
persistence, reconciliation and delivery remain in their existing locations.
All fifteen earlier owners, existing tests/fixtures and digest docs are unchanged.
README's companion list and the Python API table add the new owner.

Evidence: `/tmp/mrs-bot-stage16-4uQu46`. Before editing, documentation passed for
**224 modules**, **181 cases** collected in **4.48s**, and **181 passed in 37.59s**.
The validated nonempty selection contains **55 file/node arguments across 12
files**: six relevant prior owners (**89 cases**), supplied/nearby unit context,
media, scheduler and receipt recovery (**28**), ambiguity barriers (**21**),
proved quote rejection (**1**), guarded bootstrap (**8**), single-call failure
routing (**9**) and local loopback integrations (**25**). All fifteen supplied
nodes and actual parametrized quote fixtures, per-author caps and cross-lane
spacing cases are included. No broad suite or whole enormous test file ran.

After extraction, documentation passed for **225 modules**, **191 cases**
collected in **4.43s**, and **191 passed in 38.27s** on the first run
(`current-initial-pytest.txt`). Ten new contracts cover guarded import, current
dependency/argument/result/error forwarding, eligibility/profile native behavior,
context references/budgets/copies/media order, marker mutation boundaries, real
confirmed reconciliation before a disabled lane, numeric candidate caps despite
zero model calls, and reused-draft durability/context/pre-send availability.
They reuse the quote scenario, local evidence/draft/receipt validation helpers
and registered autouse `isolate_regular_post_receipt`; barriers remain active.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage16-4uQu46/run_selected.sh baseline
bash /tmp/mrs-bot-stage16-4uQu46/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage16-4uQu46/verify_stage16.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The runner requires successful selection generation and nonempty validation and
collection before pytest. It uses `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONDONTWRITEBYTECODE=1`, evidence-root `TMPDIR` and
`python3 -m pytest -q -p no:cacheprovider` with explicit selected arguments.
Temporary HOME/state, dummy credentials, dead proxies, denied external sockets
and explicit loopback APIs stay in effect. The passing run is retained; tests
were not repeated after documentation-only edits.

`comparison.txt` verifies exact bodies/docstrings, signatures and dependency
forwarding. Restoring all eight originals and removing one import reconstructs
the **entire immutable parent root byte-for-byte**, including **567 unaffected
definitions** and unrelated statements. Static `git show`/AST checks also verify
all fifteen previous owners and unrelated files without importing the bot.
The final documentation gate and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 23,514 / 917,584 | 22,771 / 887,943 |
| `mrs_bot_quote_reply_cycle.py` | absent | 1,050 / 42,552 |

The root loses **743 lines / 29,641 bytes**; combined runtime source grows by
**307 lines / 12,911 bytes** for explicit dependency declarations, forwarding
and owner documentation. Prior owner sizes remain unchanged.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: quote-tweet discovery and watched own-post lookup, subject
to supervisor review and a fresh invocation.

## Stage 17 — watched own-post selection and quote discovery/pagination

Baseline: `5ad078342de911c730ed225e1b1fa99f67f8a71b` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage17` started
clean on `codex/bot-modularisation-stage17`, matching the verified pushed stage
16 parent. The supervisor's six-function inventory and fourteen direct test
candidates were used with affected state, watched-post, hot-post and quote-cycle
callers and actual loopback scenarios.

`mrs_bot_quote_discovery.py` owns **435 original definition lines** across
`quote_repeated_cursor_suppression_record`,
`normalise_quote_repeated_cursor_suppressions`, `load_extra_quote_watch_post_ids`,
`build_quote_lookup_post_ids`, `get_recent_own_post_ids_for_quote_lookup` and
`get_quote_tweets_for_post`. Six explicit root adapters preserve signatures,
defaults and annotations, forwarding respectively **4, 3, 3, 5, 2 and 14** current
dependencies. All original bodies/docstrings, nested pagination closures and
nonlocal variables are byte-identical. Import uses only the standard library,
performs no runtime work and retains no callbacks, configuration, client or state.

Compatibility retains exact cursor-record types, hashes, epoch/backoff/expiration
boundaries, canonical copies, reverse sorting/cap and discarded-change accounting;
fresh UTF-8 watch reads, original comments/isdigit/deduplication/cap/error behavior;
recent seeding before extras, watched-post priority, logging and original slicing.
Discovery retains token canonicalization, expiry inspection before pruning,
durable matching-token cleanup before requests, active-suppression head fetches,
one expiry probe, renewal only on repetition and changed/finished cursor clearing.
Callback order, partial results, hashed-only cursor diagnostics, final token-map
references without an added save, media-before-author expansion, includes-user
identity, original data-list return and native errors remain exact.

Shared `x_paginated_get`, request/authentication, numeric ID validation, recent
own-post cache seeding, media attachment, durable persistence and reply cycles
remain in their existing locations. Discovery acquires no write authority; state
cleanup uses the current root save callback. All sixteen earlier owners, existing
tests/fixtures and digest docs are unchanged. README's companion list and the
Python API table add the owner.

Evidence: `/tmp/mrs-bot-stage17-PbURdD`. Before editing, documentation passed for
**225 modules**, **145 cases** collected in **4.45s**, and **145 passed in 19.46s**.
The validated nonempty baseline contains **44 file/node arguments across 10
files**: seven relevant prior owners (**99 cases**), all fourteen supplied direct
candidates plus state normalization, fresh-head quote processing, shared paginator
invalid/repeated-cursor and hot-post tests (**30**), guarded bootstrap (**8**), and
local loopback quote/watch/hot-post discovery and reply scenarios (**8**).

After extraction, documentation passed for **226 modules**, **155 cases**
collected in **4.44s**, and **155 passed in 19.85s** on the first run
(`current-initial-pytest.txt`; **45 arguments across 11 files**). Ten new contracts
cover guarded import, current dependency/argument/default/result/error forwarding,
watch-file parsing/rereads/read failures, lookup ordering/references/logs,
normalization boundaries/copies/accounting, paginator callback arguments,
media/author/data identity, native malformed-result errors and cleanup-save failure
before requests. Tests register the existing autouse `isolate_regular_post_receipt`
fixture; old assertions and guards are unchanged.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage17-PbURdD/run_selected.sh baseline
bash /tmp/mrs-bot-stage17-PbURdD/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage17-PbURdD/verify_stage17.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The reused runner requires successful selection generation, nonempty validation
and collection before pytest. It uses `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONDONTWRITEBYTECODE=1`, evidence-root `TMPDIR` and explicit selected arguments
to `python3 -m pytest -q -p no:cacheprovider`. Temporary HOME/state, dummy
credentials, dead proxies, denied external sockets and explicit loopback APIs
remain in effect. No broad suite or whole enormous test file ran. The actual
passing run is retained; tests were not repeated after documentation-only edits.

`comparison.txt` verifies exact bodies/docstrings, signatures and dependency
forwarding using immutable `git show` and AST/symbol-table checks. Restoring the
six originals and removing one import reconstructs the **entire parent root
byte-for-byte**, including **533 unaffected definitions** and unrelated statements.
It also verifies all sixteen earlier owners and unchanged unrelated files without
importing the bot. The final documentation gate and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 22,771 / 887,943 | 22,412 / 875,616 |
| `mrs_bot_quote_discovery.py` | absent | 511 / 17,978 |

The root loses **359 lines / 12,327 bytes**; combined runtime source grows by
**152 lines / 5,651 bytes** for explicit dependencies, forwarding and owner docs.
The new focused test file has **331 lines / 15,654 bytes**.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: supervisor review of `get_hot_post_reply_candidates`, keeping
shared pagination, request/authentication, persistence and reply-cycle authority
in their current locations. Supervisor review precedes a fresh invocation.

## Stage 18 — hot-post discovery and candidate handoff

Baseline: `1541bd5520c57f152f8a405a0a13f8f877bf69bc` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage18` started
clean on `codex/bot-modularisation-stage18`; the parent matched the verified
remote stage 17 branch. The supervisor's scope/dependency review and all fourteen
curated direct/indirect test nodes guided the extraction and affected selection.

`mrs_bot_hot_post_discovery.py` owns **482 original definition lines** across
`get_hot_post_reply_candidates`, `mark_hot_post_reply_skipped`,
`maybe_mark_hot_post_reply_skipped` and `dedupe_reply_candidates`. Four explicit
root adapters retain names, signatures, defaults and annotations, passing
**26, 3, 1 and 2** current dependencies. Original bodies/docstrings and the nested
invalid-cursor closure are byte-identical. No constants move; standard-library
imports perform no runtime work or retained dependency setup.

Compatibility preserves early flags/watch failures, local pruning/check-map
copies, full-rescan cadence, cursor-only durable clearing before retry and native
request-error propagation. Eligibility still precedes the candidate cap; pending
outcome/clear, terminal evaluation, skip marker and terminal event order remain
exact. Candidate annotations/cache handoff preserve references; soft watermarks
and continuation remain conservative. Skip IDs/records retain bounds, native
malformed-state errors and in-place versus trimmed-map identity. Merging retains
first mention/unique hot objects, annotation precedence and deep copies only for
missing mention references, preventing a second model decision for duplicates.
Watched-ID reading, shared pagination/authentication, eligibility, pending drafts,
terminal evaluation, cache/persistence, mention authority/watermarks and normal
reply-cycle authority remain in their current root/owners. All seventeen earlier
owners, existing tests/fixtures and digest docs are unchanged.

Evidence: `/tmp/mrs-bot-stage18-dgpSxi`. Before editing, documentation passed for
**226 modules**, **48 cases** collected in **4.14s**, and **48 passed in 17.38s**.
The validated nonempty baseline has **25 file/node arguments across 8 files**:
all fourteen supplied nodes, five relevant prior owners (**24 cases**), guarded
bootstrap (**8**) and two indirect normal-cycle draft/restart recovery callers.
The loopback scenarios cover full rescan after restart, watermark edges,
duplicate one-model-call/one-post behavior, pagination/truncation/final unusable
pages, optional search failure and the quote breaker.

After extraction, documentation passed for **227 modules**, **59 cases**
collected in **4.22s**, and **59 passed in 17.74s** on the first run
(`current-initial-pytest.txt`; **26 arguments across 9 files**). Nine new tests
expand to eleven cases covering guarded import, current dependencies/defaults,
early exits, draft/terminal/media/cache/save order and identity, cursor-save and
request error boundaries, marker bounds/native errors, handoff callbacks and
merge/copy identity. They reuse the existing bot/draft fixtures and register
autouse `isolate_regular_post_receipt`; transaction and network guards remain active.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage18-dgpSxi/run_selected.sh baseline
bash /tmp/mrs-bot-stage18-dgpSxi/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage18-dgpSxi/verify_stage18.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The reused runner requires successful selection generation, nonempty validation
and collection before pytest. It supplies `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`,
evidence-root `TMPDIR` and explicit arguments to
`python3 -m pytest -q -p no:cacheprovider`. Temporary HOME/state, dummy credentials,
dead proxies, denied external sockets and explicit loopback fake APIs remain in
effect. No broad suite or whole enormous test file ran. The actual passing run
is retained; unchanged tests were not repeated after documentation-only edits.

`comparison.txt` checks exact bodies/docstrings, signatures and current dependency
forwarding using immutable `git show`, AST and symbol tables. Restoring the four
originals and removing one import reconstructs the **entire parent root
byte-for-byte**, including **535 unaffected definitions** and unrelated statements.
It verifies all seventeen earlier owners and all unrelated tracked files without
importing the bot. The final documentation gate and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 22,412 / 875,616 | 21,997 / 859,029 |
| `mrs_bot_hot_post_discovery.py` | absent | 556 / 21,756 |

The root loses **415 lines / 16,587 bytes**; combined runtime source grows by
**141 lines / 5,169 bytes** for explicit dependencies, forwarding and owner docs.
The new focused test file has **365 lines / 17,784 bytes**. README's companion
list and the Python API table add the owner.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: supervisor review of reply-context/media preparation around
`build_context_for_reply_ai` and `reply_media_context_for_candidate`, keeping
provider, cache/persistence and reply-cycle authority in their current locations.
Supervisor review precedes a fresh invocation.

## Stage 19 — durable mention discovery, queue access and watermark retirement

Baseline: `502acd64e7286428b587c6004c1f9783fcc191b6` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage19` started
clean on `codex/bot-modularisation-stage19`, matching the verified remote stage
18 parent. The supervisor's scope notes, five-function dependency inventory and
all twenty-four supplied test nodes guided this extraction.

`mrs_bot_mention_discovery.py` owns **392 original definition lines** across
`pending_mention_candidates`, `remove_pending_mention_candidate`, `get_mentions`,
`update_last_seen_mention_id` and `mark_mention_seen_if_applicable`. Four explicit
root adapters pass **4, 24, 1 and 3** current dependencies; dependency-free removal
is a root alias. Original bodies/docstrings and root signatures, defaults and
annotations are unchanged. The owner imports only standard-library types and
retains no callbacks, configuration, clients or state or import-time runtime work.

Compatibility preserves bounded queue authority/recovery before provider work,
legacy cursor migration and durable reset guards, nested page callback copies,
cache/provenance references and final-page pending/watermark atomic saves.
Repeated tokens retain persisted candidates until queue drain; invalid-cursor
and failed-reset boundaries, exact continuation budgets and head-refetch guards
remain unchanged. Queue returns retain sorted record references; retirement
keeps removal, truncation and legacy watermark order, including native numeric
and shape errors. Authority normalization/validation/reset primitives and the
continuation exception, shared paginator/request/authentication, cache/persistence,
terminal/quarantine evaluation and reply cycles stay in their current locations.

Evidence: `/tmp/mrs-bot-stage19-UP2HnN`. Before editing, documentation passed for
**227 modules**, **65 tests** collected in **3.85s**, and **65 passed in 10.57s**.
The validated nonempty selection has **42 file/node arguments across 8 files**:
the twenty-four supplied nodes (**30 cases**), four relevant prior owners
(**15**), guarded bootstrap (**8**), indirect normal-cycle/reconciliation/restart
callers (**7**) and loopback mention pagination/restart integrations (**5**).
It retains final-page atomic commit, repeated-token retained-page, failed-reset
consistency, restored-cursor head-guard and legacy orphaned-candidate recovery.

After extraction, documentation passed for **228 modules**, **74 tests** collected
in **3.88s**, and **74 passed in 10.89s** on the first run
(`current-initial-pytest.txt`; **43 arguments across 9 files**). Eight new tests
expand to nine cases for import safety, current dependencies and exception
authority, queue recovery/save/return references, early queue access, real page
media/cache/copy/commit order, retirement identity and native error boundaries.
They reuse existing mention/provider fixtures and register autouse
`isolate_regular_post_receipt`; transaction and network barriers remain active.

```bash
set -euo pipefail
bash /tmp/mrs-bot-stage19-UP2HnN/run_selected.sh baseline
bash /tmp/mrs-bot-stage19-UP2HnN/run_selected.sh current-initial
PYTHONDONTWRITEBYTECODE=1 python3 /tmp/mrs-bot-stage19-UP2HnN/verify_stage19.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_documentation.py
git diff --check
```

The reused runner requires successful selection generation, nonempty validation
and collection before pytest. It uses `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`,
evidence-root `TMPDIR` and explicit arguments to
`python3 -m pytest -q -p no:cacheprovider`. Temporary HOME/state, dummy credentials,
dead proxies, denied external sockets and explicit loopback fake APIs remain in
effect. No broad suite or whole enormous test file ran; the actual passing run
is retained without repeating unchanged tests after documentation-only edits.

`comparison.txt` checks exact moved bodies/docstrings, signatures and current
dependencies against immutable `git show`, AST and symbol tables. Restoring the
five originals and removing one import reconstructs the **whole parent root
byte-for-byte**, including **534 unaffected definitions** and unrelated statements.
All eighteen prior owners, existing tests/fixtures and digest docs are unchanged;
README and the Python API table only add the companion. Final documentation and
`git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 21,997 / 859,029 | 21,661 / 844,281 |
| `mrs_bot_mention_discovery.py` | absent | 469 / 19,592 |

The root loses **336 lines / 14,748 bytes**; combined runtime source grows by
**133 lines / 4,844 bytes** for dependencies, adapters and owner documentation.
The focused test file has **300 lines / 14,571 bytes**.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: supervisor review of reply-context/media preparation around
`build_context_for_reply_ai` and `reply_media_context_for_candidate`, keeping
provider, cache/persistence and reply-cycle authority in their current locations.
Supervisor review precedes a fresh invocation.

## Stage 20 — durable mention queue authority and normalization

Baseline: `2801943c6eb9da15953b065695a0a8fa15cea9fc` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage20` started
clean on `codex/bot-modularisation-stage20`, matching the verified pushed stage
19 parent. The supervisor's scope notes, inventory and all 33 curated test nodes
were used, with normal-cycle, state-recovery and guarded-bootstrap callers read.

`mrs_bot_mention_authority.py` owns **494 original definition lines** across
`normalise_mention_pagination`, `normalise_mention_backlog_reset_guard`,
`active_mention_backlog_reset_guard`, `normalise_mention_backlog`,
`canonical_mention_pending_candidates`, `_emit_mention_authority_recovery`,
`_reset_mention_candidate_authority`, `validate_pending_mention_candidate_authority`
and `mention_pagination_has_canonical_page_ownership`. Seven explicit adapters
pass **3, 2, 4, 2, 2, 10 and 4** current root dependencies; the dependency-free
active-guard/reset helpers are aliases. Original bodies/docstrings and root
names/signatures/defaults/annotations remain exact; no constants or classes moved.

Compatibility preserves strict field/type/bound checks, overflow reset after
other validity checks, shallow pending copies, original active-guard references,
recovery capture versus immediate warning/event order, strict loader preference
for usable backups, deduplication and authority-failure precedence, reset/head-guard
mutations, unchanged watermarks and exact canonical page ownership, including
native callback errors. Shared bounded ID/provenance primitives, terminal policy,
state loading/persistence, receipts, discovery and cycles remain in existing
locations. The standard-library-only owner adds no saves/provider work and retains
no callbacks, configuration, clients, state or import-time runtime work.

Evidence: `/tmp/mrs-bot-stage20-0ZfNnJ`. Before editing, documentation passed for
**228 modules**, **91 tests** collected in **4.08s**, and **91 passed in 11.66s**.
The nonempty baseline selection has **51 file/node arguments across 10 files**:
all 33 supplied nodes, the stage 19 affected selection and its owner contracts.
It covers malformed normalizers, stale traversal, corrupt-primary/usable-backup
loading, receipt recovery without page ownership, shared loader/writer token caps,
stale queue disposal, guarded bootstrap, normal-cycle callers and five loopback
pagination/restart integrations, including crash/final-page commits, repeated
tokens, failed resets and head-refetch guards.

After extraction, documentation passed for **229 modules**, **101 tests** collected
in **4.05s**, and **101 passed in 12.38s** on the first run
(`current-initial-pytest.txt`; **52 arguments across 11 files**). Nine new tests
expand to ten cases for import safety, current dependencies/defaults/limits,
container/queue/guard references, recovery logging/error order, deduplication
before native errors, failure precedence/reset order and ownership/path boundaries.
They reuse existing mention fixtures and register autouse
`isolate_regular_post_receipt`; old assertions and barriers remain intact.

The reused `run_selected.sh` ran `baseline` and `current-initial`, requiring
successful selection generation, nonempty validation and collection before
`python3 -m pytest -q -p no:cacheprovider`. It sets
`PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1` and evidence-root
`TMPDIR`, retaining temporary HOME/state, dummy credentials, dead proxies, denied
external sockets and explicit loopback fake APIs. No broad suite or whole enormous
test file ran; the passing run was retained after documentation-only edits.

`verify_stage20.py` / `comparison.txt` verify exact bodies, signatures and current
dependencies using immutable `git show`, AST and symbol tables. Restoring the nine
originals and removing one import reconstructs the **whole parent root byte for
byte**, including **529 unaffected definitions** and all unrelated statements.
All nineteen prior owners, existing tests/fixtures and digest docs are unchanged;
README/API only add the companion and this report is appended.
`python3 tools/check_python_documentation.py` and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 21,661 / 844,281 | 21,273 / 829,824 |
| `mrs_bot_mention_authority.py` | absent | 562 / 20,480 |

The root loses **388 lines / 14,457 bytes**; combined runtime source grows by
**174 lines / 6,023 bytes**. The focused test file has **286 lines / 15,339 bytes**.

Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service
operations, live provider calls, deployment/restart or next-stage work ran.
Next useful domain: supervisor review of reply-context/media preparation around
`build_context_for_reply_ai` and `reply_media_context_for_candidate`, keeping
provider, cache/persistence and reply-cycle authority in their current locations.
Supervisor review precedes a fresh invocation.

## Stage 21 — reply evaluation retention and author quarantine

Baseline: `a8992f6d3b1bdb235347010fb4d5b5d533f8e492` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage21` started
clean on `codex/bot-modularisation-stage21`, matching the verified pushed stage
20 parent. Supervisor scope/dependency notes and all 44 curated nodes were used;
normal/quote cycles, discovery, state recovery and guarded bootstrap were inspected.

`mrs_bot_reply_evaluation_state.py` owns **510 original definition lines** across
`completed_mention_watermark_covers_target`,
`prune_completed_mention_quarantine_evaluations`, `prune_reply_evaluation_records`,
`author_no_reply_epoch_limit`, `prune_author_evaluation_quarantines`,
`active_author_evaluation_quarantine`, `record_qualifying_author_no_reply`,
`clear_author_evaluation_quarantine_history`, `normalise_author_evaluation_quarantines`,
`terminal_reply_evaluation` and `record_terminal_reply_evaluation`.
Eight explicit adapters pass **3, 6, 1, 6, 2, 8, 8 and 2** current dependencies;
the dependency-free watermark, clear-history and terminal-lookup helpers are
aliases. Original bodies/docstrings and root signatures/defaults/annotations
remain exact; no constants or classes moved.

Compatibility retains recent/bad-epoch replay protection while bounding evictable
mention quarantine skips, cutoff/tie ordering, live threshold-sized strike history,
strict qualifying flag/current policy, exact old-policy migrations, expiration
and event-before-assignment order, shallow copies versus in-place terminal batches,
returned record references and native failures. Shared mention authority/discovery/
watermark commits, configuration, clock/log/event implementation, persistence and
orchestration stay in existing locations. The standard-library-only owner retains
no callbacks/configuration/clients/state and adds no saves, provider calls or
import-time runtime work.

Evidence: `/tmp/mrs-bot-stage21-9Ybdxu`. Before editing, documentation passed for
**229 modules**, **97 tests** collected in **4.37s**, and **97 passed in 15.21s**.
The validated baseline has **78 explicit nodes across 12 files**, including all
44 supplied nodes and relevant prior-owner, loader/receipt/discovery/cycle/bootstrap
regressions and seven loopback normal/quote/pagination/restart integrations.
It covers all four terminal retention/cap/tie/replay cases, configured threshold
101/cap 404, quarantine reload/expiry, real f909 writer migration, old active
quarantine without live strikes, completed-watermark pruning and incomplete-backlog
volume bounding.

After extraction, documentation passed for **230 modules**, **110 tests** collected
in **4.48s**, and **110 passed in 15.95s** on the first run
(`current-initial-pytest.txt`; **79 file/node arguments across 13 files**).
A concrete missing durable-batch reload contract was then added using the existing
normal-cycle/queue fixtures, real state writer/loader and transaction barriers:
**1 passed in 2.20s** (`durable-batch-pytest.txt`). Thus **111 distinct affected
cases passed**. The **13 new tests / 14 cases** cover import safety, current root
dependencies, copy/record references, strict flags, log/event/assignment order and
native errors, plus two quarantine skips persisted in one durable batch and
reloaded with the incomplete traversal and watermark intact. Imported autouse
`isolate_regular_post_receipt` remains registered; existing assertions are unchanged.

The reused `run_selected.sh` and supplemental selection require successful
selection generation, a validated nonempty list and collection before pytest.
Runs use `PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`
and evidence-root `TMPDIR`, retaining temporary HOME/state, dummy credentials,
dead proxies, denied external sockets and explicit loopback fake APIs. No broad
suite or whole enormous test file ran; passing runs were retained after docs edits.

`verify_stage21.py` / `comparison.txt` verify exact bodies, signatures and current
dependencies with immutable `git show`, AST and symbol tables. Restoring the eleven
originals and removing one import reconstructs the **whole parent root byte for
byte**, including **525 unaffected definitions** and all unrelated statements.
All twenty prior owners, existing tests/fixtures and digest docs remain unchanged;
README/API only add the companion and this report is appended. Documentation and
`git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 21,273 / 829,824 | 20,882 / 815,842 |
| `mrs_bot_reply_evaluation_state.py` | absent | 596 / 22,061 |

The root loses **391 lines / 13,982 bytes**; combined runtime source grows by
**205 lines / 8,079 bytes**. The focused test file has **376 lines / 19,064 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production configuration/state/credentials, service action,
live provider call, deployment/restart or next-stage work occurred.
Next useful domain: supervisor review of reply-context/media preparation around
`build_context_for_reply_ai` and `reply_media_context_for_candidate`, keeping
provider, cache/persistence and cycle authority in their existing locations.
Supervisor review precedes a fresh invocation.

## Stage 22 — tweet lookup, cache and recent own-post index

Baseline: `fd51f03ab515a398d7cf3f710a40201b6c9d7029` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage22` started
clean on `codex/bot-modularisation-stage22`, matching the verified pushed stage
21 parent. The supervisor's scope/dependency notes and all 55 curated nodes were
used; normal/quote cycles, state recovery, receipt application, discovery/context
and guarded bootstrap callers were inspected.

`mrs_bot_tweet_lookup_cache.py` owns **348 original definition lines** across
`normalise_tweet_cache_entry`, `normalise_tweet_cache`, `prune_tweet_cache`,
`record_recent_own_post`, `seed_recent_own_post_ids_from_cache`, `cache_tweet`,
`_verified_tweet_lookup_row`, `get_tweet_by_id`,
`reply_target_is_available_immediately_before_send` and `get_tweet_by_id_cached`.
Ten explicit adapters retain root names/signatures/defaults/annotations and pass
**2, 2, 4, 2, 3, 6, 1, 5, 4 and 7** current dependencies. Original implementation
bodies/docstrings are unchanged; no constants or classes moved.

Compatibility preserves permissive scalar/reference normalization, pruning before
cached reads/writes, stable cap order and original records, own-post seeding and
fallback, exact direct identity checks, fresh pre-send lookup and permanent-target
versus global errors. Media refresh merges into a deep copy without replacing the
cached record or saving; a miss saves canonical state before copying/decorating
the return. Native failures, clock/log/callback authority and mutation/save order
remain intact. Shared state epochs, request/authentication/error classification,
media attachment, configuration, persistence and orchestration remain in existing
locations; `get_immediate_parent_id` stays root for context. The standard-library
owner retains no callbacks/configuration/clients/state and adds no persistence,
validation policy or import-time runtime work.

Evidence: `/tmp/mrs-bot-stage22-GYrhdL`. Before editing, documentation passed for
**230 modules**, **101 tests** collected in **4.17s**, and **101 passed in 16.14s**.
The validated baseline has **75 explicit nodes across 15 files**, including all
55 supplied nodes, relevant prior-owner and guarded-bootstrap tests, indirect
loader/regular/meme/reply receipt/discovery/context callers, deleted-target no-write
regressions and nine loopback normal/quote/pagination/posting/restart integrations.

After extraction, **115 tests** collected in **4.22s** and **115 passed in 16.20s**
(`current-final-pytest.txt`; **76 file/node arguments across 16 files**).
The **12 new tests / 14 cases** cover import safety, current dependencies,
normalization/copy/reference boundaries, pruning and own-post index order, direct
media attachment, cache-hit/media-refresh identity and no-save behavior, and a
real isolated canonical save before return copying plus provider/save errors.
The initial run had **114 passes and one new-test assertion failure**: its
`cache_tweet` dependency count was corrected from seven to the inventory's six;
implementation and existing assertions were unchanged. The imported autouse
`isolate_regular_post_receipt` fixture remains registered.

`run_selected.sh` requires successful selection generation, a validated nonempty
list and collection before pytest. Runs use `PYTHONUSERBASE=/home/tonym/.local`,
`MRS_TEST_MODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`,
`-p no:cacheprovider` and evidence-root `TMPDIR`, retaining temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
fake APIs. No broad suite or whole enormous test file ran. The passing run was
preserved after documentation-only edits; final documentation and diff checks pass.

`verify_stage22.py` / `comparison.txt` verify exact bodies/signatures/dependencies
against immutable `git show`, AST and symbol tables. Restoring the ten originals
and removing one import reconstructs the **whole parent root byte for byte**,
including **523 unaffected definitions** and all unrelated statements. All 21
prior owners, existing tests/fixtures and digest docs are unchanged. README/API
only add the companion and this report is appended.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 20,882 / 815,842 | 20,661 / 808,138 |
| `mrs_bot_tweet_lookup_cache.py` | absent | 446 / 14,326 |

The root loses **221 lines / 7,704 bytes**; combined runtime source grows by
**225 lines / 6,622 bytes**. The focused test file has **346 lines / 17,207 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production configuration/state/credentials, service action,
live provider call, deployment/restart or next-stage work occurred.
Next useful domain: supervisor review of reply-context/media preparation around
`build_parent_chain`, `build_context_for_reply_ai` and
`reply_media_context_for_candidate`, keeping cache/persistence, provider and cycle
authority in their existing locations. Supervisor review precedes a fresh invocation.

## Stage 23 — verified reply-context construction

Baseline: `3082adea58c5fd89567018c6aeaf9beb63d4a958` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage23` started
clean on `codex/bot-modularisation-stage23`, matching the verified pushed stage
22 parent. The supervisor's scope notes, fourteen-function inventory and all 40
supplied nodes informed the extraction; normal/quote cycles, clarification,
state recovery, guarded bootstrap, native media and actual integration callers
were inspected.

`mrs_bot_reply_context.py` owns **446 original definition lines** across
`get_immediate_parent_id`, `clean_text_for_reply_context`, `tweet_context_text`,
`trim_context_text`, `build_parent_chain`, `is_our_auto_reply`,
`_reply_context_post`, `_log_single_call_context_summary`,
`_directly_quoted_tweet_for_reply_context`, `_direct_quote_id`,
`_quoted_post_for_reply_context`, `_parent_path_is_contiguous`,
`_parent_path_is_chronological` and `build_context_for_reply_ai`.
Thirteen explicit adapters supply **49 current root dependencies**; the pure
`_direct_quote_id` is an alias. Root names/signatures/defaults/annotations and
original implementation bodies/docstrings remain exact. Only the owner's
`_reply_context_post.maximum_chars` becomes required: the adapter explicitly
forwards the original root definition-time `MAX_VISIBLE_TEXT_CHARACTERS` default.
No constants or classes moved.

Compatibility retains parent reference validation and lookup/depth budgets,
actual contiguous suffixes and verified chronology (excluding cached observation
timestamps), the own immediate auto-reply check, bounded incoming contribution,
declared target/ancestor quotes, native media before structural summary, metadata,
deep copies and original references. Canonical JSON options/fallback bytes,
prose/URL-free summary hashes and native error boundaries are unchanged.
Cache/lookup/pruning, media preparation, canonical bounds/schema/exceptions,
parsing, configuration, clock/logging and orchestration remain in existing
locations. The standard-library-only owner retains no callbacks, configuration,
clients or state and performs no import-time runtime work.

Evidence: `/tmp/mrs-bot-stage23-ykwtqr`. Before editing, documentation passed for
**231 modules**, **82 tests** collected in **4.22s**, and **82 passed in 13.50s**.
The validated baseline contains **65 explicit nodes across 11 files**, including
all 40 supplied nodes, prior quote/normal cycle and generation contracts, stage
22 cache/media tests, recovery and guarded-bootstrap checks, context/media/evidence
regressions and eight loopback normal/quote/pagination/restart integrations.

After extraction, documentation passed for **232 modules**, **96 tests** collected
in **4.18s**, and **96 passed in 14.26s** on the first run (`current-pytest.txt`;
**66 arguments across 12 files**). The **10 new tests / 14 cases** cover guarded
import, current adapters/defaults/references/errors, the rebound-config fixed
default, parent order/identity, quote container distinctions, cached chronology,
usable suffix/raw ancestor quote preservation, media/copy/metadata order and
canonical rejection/encoding versus native failures. Existing fixtures and native
media/bounding implementations are reused; the imported autouse
`isolate_regular_post_receipt` fixture remains registered.

`run_selected.sh` uses `set -euo pipefail`, successful selection generation,
validated nonempty arguments and collection before pytest. Both runs use
`PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1` and
`python3 -m pytest -q -p no:cacheprovider`, with TMPDIR under the evidence root.
Temporary HOME/state, dummy credentials, dead proxies, denied external sockets
and explicit loopback fake APIs remain active. No broad suite or whole enormous
test file ran; the passing run was retained after documentation-only edits.

`verify_stage23.py` / `comparison.txt` prove exact moved bodies, signatures and
dependencies using immutable `git show`, AST and symbol tables. Restoring the
fourteen originals and removing one import reconstructs the **whole parent root
byte for byte**, including **519 unaffected definitions** and every unrelated
statement. All 22 prior owners, existing tests/fixtures and digest docs remain
unchanged; README/API only add the companion and this report is appended. Final
documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 20,661 / 808,138 | 20,362 / 797,987 |
| `mrs_bot_reply_context.py` | absent | 576 / 18,855 |

The root loses **299 lines / 10,151 bytes**; combined runtime source grows by
**277 lines / 8,704 bytes**. The focused test file has **303 lines / 15,869 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production configuration/state/credentials, service action,
live provider call, deployment/restart or next-stage work occurred.
Next useful domain: supervisor review of native reply-media preparation around
`attach_media_to_tweets`, `candidate_native_photo_media` and
`reply_media_context_for_candidate`, preserving the existing cache, image fetching,
canonical context and provider authorities. Supervisor review precedes a fresh
invocation; this stage launches no successor.

## Stage 24 — native reply-media attachment and bounded photo selection

Baseline: `8a24214a7ec6420f19d54d63141b167b69af9bf1` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage24` started
clean on `codex/bot-modularisation-stage24`, matching the verified pushed stage
23 parent. The supervisor's `stage24-scope-notes.md`, three-function dependency
inventory and all 25 curated nodes informed this extraction; normal/quote cycles,
discovery, context, lookup, state recovery and guarded bootstrap were inspected.

`mrs_bot_reply_native_media.py` owns **187 original definition lines** across
`attach_media_to_tweets`, `candidate_native_photo_media` and
`reply_media_context_for_candidate`. The attachment helper is a dependency-free
root alias; two explicit adapters supply the current root photo cap, candidate
callback and logger (one and three dependencies). Root names, signatures,
defaults and annotations and original implementation bodies/docstrings remain
exact. No constants or classes moved.

Compatibility preserves original expansion records attached in place, later-key
precedence, duplicate order and no-clear behavior; declared, unresolved and
unclassified accounting; malformed versus absent attachment metadata; photo URL
and key conversions and the existing cap, including its native edge behavior.
Target photos precede quotes, target duplicates survive, quote keys deduplicate,
incomplete metadata yields unavailable, and expected counts, fresh selected
records, log fields/levels and return dictionaries remain exact. Discovery,
context, pipeline and actual image fetching/validation retain authority. The
standard-library-only owner retains no callbacks, configuration, clients or state
and adds no import-time runtime work, network/provider/image work or saves.

Evidence: `/tmp/mrs-bot-stage24-9cjho1_q`. Before editing, documentation passed
for **232 modules**, **50 tests** collected in **3.99s**, and **50 passed in
7.09s**. The validated baseline has **41 explicit nodes across 13 files**,
including all 25 supplied nodes, prior context/lookup/discovery/reply owners,
state recovery and guarded bootstrap. Existing loopback tests verify one
multimodal request with no URL payload/image-byte logs, and a photo-fetch 404
with zero model/post calls, terminal operational failure and no breaker/author
strike; hot-post attachment and target/direct-quote priority regressions pass.

After extraction, documentation passed for **233 modules**, **55 tests**
collected in **4.00s**, and **55 passed in 7.30s** on the first run
(`current-pytest.txt`; **46 explicit nodes across 14 files**). Five new contracts
cover guarded import, current adapters/defaults/references/errors, attachment
identity and fresh photo records, target duplicates/quote deduplication and
current callback/log order, and incomplete metadata counts. They reuse the
existing `image_case` fixture and explicitly register the imported autouse
`isolate_regular_post_receipt` reset fixture; existing tests/assertions are intact.

`run_selected.sh` uses `set -euo pipefail`, successful AST-validated nonempty
selection and collection before pytest. Both runs use the required installed
user dependencies, test mode, disabled plugin autoload/bytecode/cache provider,
and TMPDIR under the evidence root. Existing temporary HOME/state, dummy
credentials, dead proxies, denied external sockets and explicit loopback fake
APIs remain active. No broad suite ran; the passing run was retained after the
documentation-only edits.

`verify_stage24.py` / `comparison.txt` prove exact bodies, signatures and
dependencies against immutable `git show` using AST and symbol tables. Restoring
the three originals and removing one import reconstructs the **entire parent
root byte for byte**, including **529 unaffected definitions** and every
unrelated statement. All **23 prior owners**, existing tests/fixtures and digest
docs remain unchanged; README/API only add the companion and this report is
appended. Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 20,362 / 797,987 | 20,200 / 792,625 |
| `mrs_bot_reply_native_media.py` | absent | 214 / 7,152 |

The root loses **162 lines / 5,362 bytes**; combined runtime source grows by
**52 lines / 1,790 bytes**. The focused test file has **205 lines / 9,933 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production configuration/state/credentials, service action,
live provider call, deployment or restart was involved.
Next useful domain: supervisor review of shared tweet ID parsing/ordering and
direct reply eligibility around `parse_tweet_id`, `valid_tweets_sorted_by_id`
and `reply_target_is_directly_eligible`. Supervisor review precedes any further
stage; this invocation implements only stage 24 and launches no successor.

## Stage 25 — reply-lane counters, clarification eligibility and deterministic gates

Baseline: `5a5ed3087b4224eb0f78e899fb8ce95f6a0f56a1` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage25` started
clean on `codex/bot-modularisation-stage25`, matching the verified pushed stage
24 parent. The supervisor's scope/dependency review and all 31 supplied test
nodes informed the extraction; normal/quote cycles, reconciliation, state
recovery and guarded bootstrap callers were inspected.

`mrs_bot_reply_lane_policy.py` owns **12 functions / 205 original definition
lines**: both daily resets, three daily author counter helpers, thread ID and
terminal/recent clarification checks, clarification tokens/context, direct
target eligibility and deterministic spam filtering. Exactly three fixed
clarification regex/stopword definitions (**14 lines**) move with unchanged
initializer source, types and order. Root constants initially share owner
objects; ten explicit adapters pass current root dependencies, including rebound
regexes/stopwords. `daily_author_reply_counts` and `clarification_thread_id`
remain dependency-free aliases. Root names/signatures/defaults/annotations and
implementation bodies/docstrings are preserved.

Compatibility retains current cap-date sampling and logging before reset;
permissive legacy count cleaning/fallback, fresh mapping identity and increment
before capped-ID helper failure; terminal-thread membership and the strict
clarification window; cached confirmed-reply/question proof, original question
text and correction/restatement token gates. Own-author precedence, structured
entities suppressing text fallback, ordered spam patterns, exclamation/ratio
thresholds, raw logging, native errors and mutation order remain exact.
Configurable `SPAMMY_PATTERNS` and other settings, persistence/save points,
context/lookup, own-reply identity and pipeline authority stay in existing
locations. The standard-library-only owner retains no dependencies or runtime
state and performs no import-time file/environment/provider/RNG work.

Evidence: `/tmp/mrs-bot-stage25-Ihn7Hr8F`. Before editing, documentation passed
for **233 modules**, **67 tests collected in 3.90s**, and **67 passed in 18.21s**
(`baseline-pytest.txt`; **53 explicit nodes across 10 files**). The selection
includes all 31 supplied nodes, receipt/reconciliation/context/history owners,
guarded bootstrap and actual indirect cap, clarification and spam cases.
Loopback regressions cover both daily caps across midnight/restart, per-author
caps in both lanes, deterministic rejection before model calls, cross-lane
receipt recovery and normal/quote priority after restart. Existing tests retain
timezone-independent resets, exact 24-hour expiry, durable clarification replay
protection, author caps and terminal threads after later cap reset.

After extraction, documentation passed for **234 modules**, **79 tests collected
in 3.94s**, and **79 passed in 18.39s** on the first run (`current-pytest.txt`;
**64 explicit nodes across 11 files**). Eleven new tests (12 cases) cover guarded
import, current dependency/reference/error forwarding, shared fixed objects and
rebinding, legacy counts, reset/increment order, cached clarification proof and
current exception/token authority, terminal/window rules and deterministic gate
ordering. They reuse existing mention/confirmed-receipt helpers, cache and
reconciliation implementations and explicitly register the imported autouse
`isolate_regular_post_receipt` fixture; existing fixtures/assertions are intact.

`run_selected.sh` requires successful AST-validated nonempty selection and
collection under `set -euo pipefail`. Both runs use installed user dependencies,
test mode, disabled plugin autoload/bytecode/cache provider and TMPDIR under the
evidence root. Temporary HOME/state, dummy credentials, dead proxies, denied
external sockets and explicit loopback fake APIs remain active. No broad suite
ran; passing tests were retained after documentation-only edits.

`verify_stage25.py` / `comparison.txt` prove exact moved bodies, signatures and
dependencies against immutable `git show` using AST/symbol tables. Restoring
original definitions and removing the one new import reconstructs the **entire
parent root byte for byte**, including **519 unaffected function definitions**
and all unrelated statements. All **24 prior owners**, existing tests/fixtures
and digest docs are unchanged; README/API only add the companion and this report
is appended. Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 20,200 / 792,625 | 20,076 / 787,567 |
| `mrs_bot_reply_lane_policy.py` | absent | 323 / 11,051 |

The root loses **124 lines / 5,058 bytes**; combined runtime source grows by
**199 lines / 5,993 bytes**. The focused test file has **352 lines / 17,993 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; production configuration/state/credentials and service
controls were not accessed or changed. No live provider work, deployment,
restart or next-stage launch occurred.
Next useful domain: supervisor review of shared tweet ID parsing/ordering around
`parse_tweet_id`, `valid_tweets_sorted_by_id` and their bounded-ID dependency.
Only stage 25 is implemented; supervisor review precedes any further stage.

## Stage 26 — runtime-control reading and pause policy

Baseline: `b3736460e9b52675b370f91ed4f711a3741cfbb4` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage26` started
clean on `codex/bot-modularisation-stage26`, matching the verified pushed stage
25 parent. The supervisor's scope notes, dependency inventory and all 78 supplied
test nodes informed this extraction; normal/quote/hot-post cycles, state/receipt
recovery, guarded bootstrap and remote-boundary callers were inspected.

`mrs_bot_runtime_control.py` owns **10 functions / 301 original definition
lines**: `parse_control_time`, `validate_control_document`,
`control_failure_result`, `_runtime_control_stat_identity`,
`_read_stable_runtime_control`, `load_control`, `control_bool`,
`control_pause_active`, `lane_paused` and `global_remote_writes_paused`.
Exactly four fixed key definitions (**23 lines**) move with unchanged initializer
source, types and order. Root aliases initially share the owner's frozen objects;
ten explicit adapters pass current root dependencies, including rebound key sets
and the original mutable root cache. Root names/signatures/defaults/annotations,
including `os.stat_result`, and all implementation bodies/docstrings are exact.
Both variadic adapters forward their original `*keys` / `*lane_keys` before
dependency keywords, without regrouping arguments.

Compatibility retains strict exact Decimal/integral numeric timestamps and date
string representation; key/type/error checks; bounded nofollow/nonblock repeated
reads, syscall/finally-close order and initial absence versus disappearance during
reading; private byte-bound cached copies, failure-signature deduplication and
repair; one pause-clock sample, boolean-before-time precedence and ordered lane
aliases. The root keeps `_CONTROL_CACHE` and its lifecycle, `_RuntimeControlAbsent`,
size/path configuration, strict JSON parsing, clock/logger/event authority and
existing remote-write checks with unchanged timing. The standard-library-only
owner retains no runtime cache, callbacks, configuration or clients and performs
no import-time file/environment/provider/clock/RNG work.

Evidence: `/tmp/mrs-bot-stage26-XMd2IkU2`. Before editing, documentation passed
for **234 modules**, **160 tests collected in 4.81s**, and **160 passed in 14.13s**
(`baseline-pytest.txt`; **100 explicit nodes across 13 files**). Selection includes
all 78 supplied nodes, prior normal/quote/hot-post owner contracts, guarded
bootstrap, startup receipt preservation/recovery, real loopback lane pauses,
malformed controls failing closed globally, expired pauses and state recovery.
Existing cases retain strict duplicate/nonfinite/fractional JSON handling,
same-inode/size/mtime mutation, byte-bound private cache and repair, symlink/FIFO
and other nonregular entries, intermediate/final disappearance, ABA substitution,
short reads, premature EOF, repeated-read rewrite and rechecked provider/media/
post boundaries, including prospective pauses after durable write authority.

After extraction, documentation passed for **235 modules**, **169 tests collected
in 4.83s**, and **169 passed in 14.27s** on the first run (`current-pytest.txt`;
**108 explicit nodes across 14 files**). Eight new tests (nine cases) cover guarded
import, current dependency and variadic argument/reference/error forwarding,
shared fixed objects and rebinding, current mutable cache replacement/lifecycle,
logging-before-mutation, clock/boolean/time and lane/log/event ordering, and real
temporary-file syscall/finally-close order on success and final-path failure.
They explicitly register the existing autouse `isolate_regular_post_receipt`
fixture; existing fixtures, barriers and assertions are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and collection under `set -euo pipefail`. Both runs use installed user dependencies,
test mode, disabled plugin autoload/bytecode/cache provider and TMPDIR under the
evidence root. Temporary HOME/state, dummy credentials, dead proxies, denied
external sockets and explicit loopback fake APIs remain active. No broad suite
ran; the passing run was retained after documentation-only edits.

`verify_stage26.py` / `comparison.txt` check exact moved bodies, signatures and
dependencies against immutable `git show` using AST/symbol tables. Restoring the
original definitions and removing the one new import reconstructs the **entire
parent root byte for byte**, including **519 unaffected function definitions**
and all unrelated statements. All **25 prior owners**, existing tests/fixtures
and digest docs are unchanged; README/API only add the companion and this report
is appended. Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 20,076 / 787,567 | 19,848 / 778,533 |
| `mrs_bot_runtime_control.py` | absent | 443 / 15,210 |

The root loses **228 lines / 9,034 bytes**; combined runtime source grows by
**215 lines / 6,176 bytes**. The focused test file has **276 lines / 13,842 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; production configuration/state/credentials and service
controls were not accessed or changed. No live provider work, deployment,
restart or next-stage launch occurred.
Next useful domain: supervisor review of shared tweet ID parsing/ordering around
`parse_tweet_id`, `valid_tweets_sorted_by_id` and their bounded-ID dependency.
Only stage 26 is implemented; supervisor review precedes any further stage.
