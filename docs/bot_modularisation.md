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

## Stage 27 — API cooldown and error-window policy

Baseline: `989d32625a6431f33dfeca5492c97d4f9af3e22a` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage27` started
clean on `codex/bot-modularisation-stage27`, matching the pushed stage 26 parent.
Supervisor scope notes, dependency inventory and 46 curated candidates informed
the focused selection and caller inspection.

`mrs_bot_api_cooldowns.py` owns **five functions / 156 original definition
lines**: `in_api_cooldown`, `clear_expired_api_cooldowns`, `prune_error_epochs`,
`cooldown_until_for_rate_limit` and `record_api_error`. Five explicit adapters
retain public root signatures/defaults/annotations and exact bodies/docstrings,
supplying current settings, limits, classification, clock, datetime, logger and
persistence. No constants or classes move. The owner retains no dependencies or
runtime state and performs no import-time file/environment/provider/clock/RNG work.

Compatibility retains scope routing/read fallback and original scope labels;
native coercion/errors; read/write/OpenAI/quote expiry order and log-before-clear;
inclusive error cutoff, original ordering and repeated conversions; future reset
plus 60 versus current fallback; terminal target restrictions before the clock
and unsupported services after it; prune-list identity and mutation before
diagnostic reads; separate clock samples, exact logs/reasons, inclusive threshold
and ordinary saves only after 429 or threshold. Policy and durability are unchanged.

Evidence: `/tmp/mrs-bot-stage27-RcGUTotB`. Before editing, documentation passed
for **235 modules**, **37 tests collected in 4.08s**, and **37 passed in 15.83s**
(`baseline-pytest.txt`; 27 explicit nodes across eight files). The selection uses
20 supplied candidates, including direct cooldown/target-restriction cases and
all seven required loopbacks, plus nearest normal/quote/reply-generation,
runtime scheduling, historical-context and guarded-bootstrap checks.
After extraction, documentation passed for **236 modules**, **51 tests collected
in 3.95s**, and **51 passed in 16.34s** on the first run (`current-pytest.txt`;
37 explicit nodes across nine files). Ten new tests (14 cases) cover guarded
import, current dependencies/references, scope and native failure boundaries,
clock/conversion multiplicity, partial mutation and diagnostic/log/save order.
They register the existing autouse `isolate_regular_post_receipt` fixture.

The reused runner requires successful AST-validated nonempty selection and
collection under `set -euo pipefail`, with explicit pytest arguments. Both runs
retain temporary HOME/state/TMPDIR, dummy credentials, dead proxies, denied
external sockets, explicit loopback APIs, disabled plugin autoload/bytecode/cache
provider and `PYTHONUSERBASE=/home/tonym/.local`. No broad suite ran; passing logs
were retained after documentation-only edits.

`verify_stage27.py` / `comparison.txt` verify exact bodies, signatures and
dependencies against immutable `git show`. Restoring the five definitions and
removing the new import reconstructs the entire parent root byte for byte,
including **524 unaffected functions** and all unrelated statements. All **26
prior owners**, existing tests/fixtures and digest docs are unchanged; README/API
only add the companion and this report is appended. Final documentation and
`git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,848 / 778,533 | 19,742 / 773,987 |
| `mrs_bot_api_cooldowns.py` | absent | 221 / 7,940 |

The root loses **106 lines / 4,546 bytes**; combined runtime source grows by
**115 lines / 3,394 bytes**. The focused test file has **304 lines / 14,801 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production modification, deployment, restart or next-stage
launch occurred. Next useful domain: supervisor review of shared tweet ID
parsing/ordering around `parse_tweet_id`, `valid_tweets_sorted_by_id` and the
bounded-ID dependency. Only stage 27 is implemented; supervisor review precedes
any further stage.

## Stage 28 — Shared bounded X pagination and cursor-error classification

Baseline: `340731218d186013a09553eae74c4e73a5dd65dc` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage28` started
clean on `codex/bot-modularisation-stage28`, matching the pushed stage 27 parent.
Supervisor scope notes, dependency inventory and curated candidates informed
the caller inspection and focused selection.

`mrs_bot_x_pagination.py` owns **two functions / 260 original definition lines**:
`api_error_is_invalid_pagination_cursor` and `x_paginated_get`. Two explicit root
adapters preserve signatures/defaults/annotations and original docstrings, passing
current exception classes, JSON module, classifier, logger and supplied callbacks
to exact original bodies. No classes/constants move or dependencies/state remain
in the owner between calls. Import performs no file/environment/provider/clock/RNG
work. Authentication, bearer selection, discovery and persistence remain in their
existing locations; no retries or saves are added.

Compatibility retains structured-message precedence and parameter-echo exclusion;
once-only invalidation before optional head recovery; requested-token history
across retries and fresh per-traversal results; repetition before suppression;
strict response-section validation order; original data/user/media references and
duplicate-map insertion positions; logging/accumulation before `on_page`, then
repeated-token handling; callback errors/partial mutations, exact metadata and
mentions-info versus other-warning truncation logs.

Evidence: `/tmp/mrs-bot-stage28-teeufs4x`. Before editing, documentation passed
for **236 modules**, **39 cases collected in 3.74s**, and **39 passed in 12.32s**
(`baseline-pytest.txt`; 29 explicit nodes across nine files). The selection uses
25 of 29 supplied candidates: all nine direct classifier/paginator nodes, eight
cross-lane loopbacks, four mention persistence/recovery cases and four nearest
discovery contracts, plus guarded-bootstrap and isolation checks. Four supplied
early-exit/stub-failure cases that do not exercise pagination were omitted.
After extraction, documentation passed for **237 modules**, **55 cases collected
in 3.72s**, and **55 passed in 12.88s** on the first run (`current-pytest.txt`;
38 explicit nodes across ten files). Nine new tests / 16 cases cover import and
current dependencies, structured precedence, merge/reference contracts, retry
history/results, callback/error order, conflicting malformed sections and native
failures. They register the existing autouse `isolate_regular_post_receipt` fixture.

The reused runner requires successful AST-validated nonempty selection and
collection under `set -euo pipefail`, then explicit pytest arguments. Both runs
use the recorded evidence root for TMPDIR and disposable fixtures, temporary
HOME/state, dummy credentials, dead proxies, denied external sockets, explicit
loopback APIs, disabled plugin autoload/bytecode/cache provider and
`PYTHONUSERBASE=/home/tonym/.local`. No broad suite ran; passing logs were retained
after documentation-only edits.

`verify_stage28.py` / `comparison.txt` verify exact bodies/docstrings, signatures
and dependencies against immutable `git show`. Restoring the two definitions and
removing the new import reconstructs the whole parent root byte for byte,
including **527 unaffected functions** and every unrelated statement. All **27
earlier owners**, existing tests/fixtures and digest docs are unchanged. README/API
only add the companion; this report is appended. Final documentation and
`git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,742 / 773,987 | 19,533 / 765,753 |
| `mrs_bot_x_pagination.py` | absent | 291 / 11,379 |

The root loses **209 lines / 8,234 bytes**; combined runtime source grows by
**82 lines / 3,145 bytes**. The focused test file has **360 lines / 17,454 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata, without production modification, deployment or restart.
Next useful domain for supervisor assessment: shared tweet ID parsing/ordering
around `parse_tweet_id`, `valid_tweets_sorted_by_id` and their bounded-ID dependency.
Only stage 28 is implemented; supervisor review precedes any further stage.

## Stage 29 — Endpoint and X request-route values

Baseline: `8daa5ce96499d199e439c4819a6893389e850011` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage29` started
clean on `codex/bot-modularisation-stage29`, matching the pushed stage 28 parent.
The supplied scope notes, dependency inventory and all fourteen candidate bodies
informed the extraction and nearest configuration, bootstrap and transport checks.

`mrs_bot_request_route_values.py` owns **ten functions / 177 original definition
lines**: base normalization, endpoint host/loopback checks, literal X base
selection, prepared path normalization, tweet/media predicates, prepared/exact
route classification and strict JSON copying. Nine explicit adapters supply
current root modules, callbacks, configured bases and exception authority; the
dependency-free `exact_x_create_route` is an alias. Root signatures, defaults,
annotations and original docstrings remain. The early origin primitive stays
root, and the owner import precedes configuration-time base normalization.

Exact bodies preserve immediate `require_origin` delegation and dead branches,
validation/error order, exact hostname policy, literal upload-origin selection,
prepared versus exact-authority predicates, four decoding passes and separator/
path normalization order, native failures/current exception chains and isolated
strict JSON copies. No constants/classes, timeouts, authentication or transaction
orchestration move. The owner retains no dependencies or runtime state and
performs no import-time file/environment/provider/RNG work; preparation never sends.

Evidence: `/tmp/mrs-bot-stage29-4G8sqLgG`. Before editing, documentation passed
for **237 modules**, **55 tests collected in 3.48s**, and **55 passed in 12.09s**
(`baseline-pytest.txt`; 25 explicit nodes across five files). Selection includes
all fourteen supplied candidates, guarded bootstrap, test-environment/network
isolation and seven existing loopbacks: normal/quote/hot-post replies, distinct
and inherited upload origins, daily meme posting and native-photo fetching.

After extraction, documentation passed for **238 modules**, **64 tests collected
in 3.44s**, and **64 passed in 12.09s** (`current-pytest.txt`; 34 explicit nodes
across six files). Nine new tests cover guarded import, current module/dependency
authority, origin/hostname/route callback order, decoding limits, native failures,
JSON references and exception chains. They register the existing autouse
`isolate_regular_post_receipt` fixture. The initial run had 63 passes and one
new-test expectation error: the unchanged `ApiError` stores the method uppercase.
Only that assertion was corrected; existing tests and barriers are unchanged.

The reused runner requires successful AST-validated nonempty selection and
collection under `set -euo pipefail`, then explicit arguments to
`python3 -m pytest -q -p no:cacheprovider`. Runs use
`PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`, disabled plugin autoload
and bytecode, TMPDIR/disposable fixtures under the evidence root, temporary
HOME/state, dummy credentials, dead proxies, denied external sockets and explicit
loopback APIs. No broad suite ran; passing logs were retained after docs-only edits.

`verify_stage29.py` / `comparison.txt` verify exact bodies/docstrings, signatures,
defaults, annotations and dependencies against immutable `git show`. Replacing
the nine adapters and alias with the original definitions and removing the import
reconstructs the whole parent root byte for byte, including **519 unaffected
functions** and all unrelated statements. All **28 earlier owners**, existing
tests/fixtures and digest docs remain unchanged. README/API only add the companion;
this report is appended. Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,533 / 765,753 | 19,451 / 762,848 |
| `mrs_bot_request_route_values.py` | absent | 268 / 8,725 |

The root loses **82 lines / 2,905 bytes**; combined runtime source grows by
**186 lines / 5,820 bytes**. The new test file has **364 lines / 16,791 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: tweet ID parsing/ordering around
`parse_tweet_id` and `valid_tweets_sorted_by_id`, retaining their current policies.
Only stage 29 is implemented; supervisor review precedes any further stage.

## Stage 30 — Bounded tweet-create response diagnostics

Baseline: `a8fe5b7318e07b3882f8e45b5314a988f1ad8303` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage30` started
clean on `codex/bot-modularisation-stage30`, matching the pushed stage 29 parent.
The supplied scope notes, dependency inventory and eight transaction-test bodies
were checked against that immutable parent before editing.

`mrs_bot_x_response_diagnostics.py` owns **five functions / 246 original
definition lines** and **eight fixed constants / 14 lines**. Three explicit
adapters supply current root dependencies; the diagnostic type and bounded-text
helpers are aliases. Root signatures/defaults/annotations and original bodies/
docstrings remain exact. Constants initially share root/owner objects, and the
emitter receives current root constant values on every call. Postponed owner
annotations preserve syntax without importing Requests or `TransportAuthority`.

Classification/type distinctions, native conversion and elapsed failures,
inclusive body/header/key/ID limits, completeness fields, references and event
evaluation order remain exact. Canonical JSON without the diagnostic hash is
hashed before final canonical serialization; one ERROR log precedes returning
the original event. Rate-limit logging, annotation authorities, actual modules,
clock, logging, transport and error policy retain their existing locations.
No classes move, and no new retries, decodes, filtering, persistence or request
authority are introduced.
The owner performs no import-time runtime work and retains no callbacks,
configuration, clients or state.

Evidence: `/tmp/mrs-bot-stage30-zkSj100L`. Before editing, documentation passed
for **238 modules**, **26 tests collected in 3.40s**, and **26 passed in 6.41s**
(`baseline-pytest.txt`; 16 explicit nodes across four files). All eight supplied
transaction nodes retain their real isolated receipts/journals/fences and mocked
responses. Nearby checks cover guarded bootstrap and process/network isolation;
three existing loopbacks cover normal reply success, quote/media success and a
missing-created-ID failure without asset retirement.

After extraction, documentation passed for **239 modules**, **32 tests collected
in 3.40s**, and **32 passed in 6.84s** (`current-pytest.txt`; 22 explicit nodes
across five files). Six new tests cover guarded import, current dependencies/
constants and reference identity, type distinctions, native conversion/elapsed
failures, inclusive bounds, callback order, canonical hashing and JSON/logging
failure boundaries. The new test module registers the existing autouse
`isolate_regular_post_receipt` fixture. Existing assertions and barriers remain
unchanged; no broad suite ran.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and collection under `set -euo pipefail` before invoking explicit arguments to
`python3 -m pytest -q -p no:cacheprovider`. Runs use
`PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`, disabled plugin autoload
and bytecode, TMPDIR/disposable fixtures under the evidence root, temporary
HOME/state, dummy credentials, dead proxies, denied external sockets and explicit
loopback APIs. Passing logs are retained after docs-only edits.

`verify_stage30.py` / `comparison.txt` verify exact moved bodies/docstrings,
signatures/defaults/annotations, constants and dependencies without importing the
bot. Restoring the original definitions/constants and removing the owner import
reconstructs the whole parent root byte for byte, including **523 unaffected
functions** and all unrelated statements. All **29 earlier owners**, existing
tests/fixtures and digest docs remain unchanged. README/API only add the
companion; this report is appended. Final documentation and `git diff --check`
pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,451 / 762,848 | 19,261 / 756,498 |
| `mrs_bot_x_response_diagnostics.py` | absent | 314 / 11,781 |

The root loses **190 lines / 6,350 bytes**; combined runtime source grows by
**124 lines / 5,431 bytes**. The new test file has **298 lines / 14,193 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: tweet ID parsing/ordering around
`parse_tweet_id` and `valid_tweets_sorted_by_id`, retaining their current policies.
Only stage 30 is implemented; supervisor review precedes any further stage.

## Stage 31 — Reply arbitration and blocked-tick coordination

Baseline: `e16260132fc44f9828d2c3398f9c90ae5ed794f5` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage31` started
clean on `codex/bot-modularisation-stage31`, matching the pushed stage 30 parent.
The supplied scope notes, dependency inventory, all nineteen candidate test
bodies and relevant callers were read before selecting the affected baseline.

`mrs_bot_tick_coordination.py` owns **three functions / 212 original definition
lines**: `sanitize_next_reply_lane_priority`, `run_reply_lane_checks_for_tick`
and `maintain_global_remote_write_barrier_tick`. Three explicit adapters pass
**1, 19 and 4 current root dependencies**, retaining root signatures/defaults/
annotations and exact original bodies/docstrings. No constants/classes move.

Scheduler repair still precedes arbitration. Priority/due/spacing predicates,
forced-normal precedence, state identity and native failures remain exact.
Safety exceptions and post-result barriers stop siblings before scheduler
updates. Normal posting saves its interval before separately saving priority;
quote status events precede priority/save/log and interval/save. Normal spacing
keeps its interval; quote spacing saves the exact retry epoch. Every blocked
tick rechecks durability before the already-logged return, retaining the
Exception-only catch, critical `exc_info` log and retained SIGINT delivery.
Main/test loops, state loading, scheduler/persistence, lanes, durable barriers
and signal authority stay in their existing locations. The owner has no reverse
application import, retained dependencies or import-time runtime work.

Evidence: `/tmp/mrs-bot-stage31-FKcWy5LL`. Before editing, documentation passed
for **239 modules**, **41 tests collected in 3.38s**, and **41 passed in 11.30s**
(`baseline-pytest.txt`; **24 explicit nodes across five files**). Sixteen supplied
candidates include all seven loopbacks and the real blocked-daemon fsync/SIGINT
tests. Three incidental receipt/retry-policy candidates stub arbitration and
were omitted. Nearby checks exercise scheduler coercion, actual main/cycle
sibling safety, the one-shot completion barrier and guarded bootstrap/isolation.

After extraction, documentation passed for **240 modules**, **51 tests collected
in 3.43s**, and **51 passed in 11.51s** (`current-pytest.txt`; **32 explicit nodes
across six files**). Eight new tests / ten cases cover guarded import, current
dependencies and references, sanitizer warning/assignment order, real canonical
repair/posting saves, forced priority and spacing retry, real receipt barriers
after lane results, and native callback/error/signal boundaries. They register
the existing autouse `isolate_regular_post_receipt` fixture. The initial run had
50 passes and one new-test counting error: arbitration has 19 dependencies,
not 20. That assertion was corrected; existing assertions/barriers are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and collection under `set -euo pipefail` before explicit arguments to
`python3 -m pytest -q -p no:cacheprovider`. Runs use
`PYTHONUSERBASE=/home/tonym/.local`, `MRS_TEST_MODE=1`, disabled plugin autoload
and bytecode, TMPDIR/disposable fixtures under the evidence root, temporary
HOME/state, dummy credentials, dead proxies, denied external sockets and explicit
loopback APIs. No broad suite or whole enormous test file ran; passing logs are
retained without repeating unchanged tests after documentation-only edits.

`verify_stage31.py` / `comparison.txt` verify exact moved bodies/docstrings,
signatures/defaults/annotations and dependencies without importing the bot.
Restoring the original definitions and removing the owner import reconstructs
the whole parent root byte for byte, including **523 unaffected functions** and
all unrelated statements. All **30 earlier owners**, existing tests/fixtures and
digest docs remain unchanged. README/API only add the companion; this report is
appended. Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,261 / 756,498 | 19,100 / 750,287 |
| `mrs_bot_tick_coordination.py` | absent | 263 / 10,559 |

The root loses **161 lines / 6,211 bytes**; combined runtime source grows by
**102 lines / 4,348 bytes**. The new test file has **356 lines / 16,977 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: tweet ID parsing/ordering around
`parse_tweet_id` and `valid_tweets_sorted_by_id`, preserving validation, numeric
deduplication and original tweet references. Only stage 31 is implemented;
supervisor review precedes any further stage.

## Stage 32 — Durable JSON file I/O primitives

Baseline: `c0613d19a66ebcb503177e414847759258550d87` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage32` started
clean on `codex/bot-modularisation-stage32`, matching the pushed stage 31 parent.
The scope/dependency notes, all 29 candidate test bodies and relevant callers
informed the selection.

`mrs_bot_durable_json_io.py` owns **seven functions / 350 original definition
lines**: `durable_state_namespace_is_owned_single_link_file`,
`read_stable_owned_json_bytes_no_follow`, `fsync_parent_dir`, `atomic_write_json`,
`_strict_receipt_json_bytes`, `load_receipt_json_no_follow` and
`durable_create_receipt_json`. Seven explicit adapters pass **2, 5, 2, 5, 2, 6
and 6 current root dependencies**, preserving exact bodies/docstrings and root
signatures/defaults/annotations. The root predicate retains its definition-time
`maximum_bytes=DURABLE_RUNTIME_JSON_MAX_BYTES`; the owner requires that parameter.
Other modules, callbacks, limits, logger and exception classes remain current
per call. No constants/classes move or runtime dependencies/descriptors persist.

State and receipt permissions and metadata/byte checks remain distinct. Receipt
JSON still rejects duplicates/nonfinite constants and requires exact canonical
bytes; ordinary atomic JSON keeps its existing serializer behavior. Native
errors/causes, finally-close, partial writes, cleanup on BaseException, replacement
and fsync order remain exact, including strict parent fsync before exclusive
receipt acknowledgement reads. Canonical serialization and all state, receipt,
marker, business and persistence authority stay in their existing locations.
The owner has no reverse application import or import-time runtime work.

Evidence: `/tmp/mrs-bot-stage32-Q4ko5TWs`. Before editing, documentation passed for
**240 modules**, **46 tests collected in 3.55s**, and **46 passed in 7.80s**
(`baseline-pytest.txt`; **30 explicit nodes across seven files**). The 24 supplied
candidates cover direct I/O, protected persistence/replay, state/backup loading,
bootstrap rollback/sentinel, marker mutation/recovery and real blocked daemon
ticks, including all three supplied loopbacks. Five overlapping or separately
owned outbox/retirement candidates were omitted; used-history round trip and
guarded bootstrap/process/network isolation complete the selection.

After extraction, documentation passed for **241 modules**, **63 tests collected
in 3.50s**, and **63 passed in 8.23s** (`current-pytest.txt`; **41 explicit nodes
across eight files**). Eleven new tests / 17 cases cover guarded import, current
dependencies/reference/error identity, the frozen default, distinct permissions,
strict JSON causes, short reads/writes, descriptor closure and callback/fsync
ordering. They register the existing autouse `isolate_regular_post_receipt`
fixture. The first run had 60 passes and three new-test directory assumptions:
the fixture creates protocol files in `tmp_path`. Dedicated subdirectories fixed
those assertions without changing runtime code, existing tests or barriers
(`current-initial-pytest.txt`).

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and collection under `set -euo pipefail` before explicit pytest arguments. Runs
use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and loopback APIs.
Passing logs are retained; no broad suite or whole enormous test file ran, and
passing tests were not repeated after documentation-only edits.

`verify_stage32.py` / `comparison.txt` verify exact moved bodies, root signatures,
required owner maximum and dependency inventories without importing the bot.
Restoring the definitions and removing the new import reconstructs the complete
parent root byte for byte, including **519 unaffected functions** and unrelated
statements. All **31 earlier owners**, existing tests/fixtures and digest docs
remain unchanged; README/API only add the companion and this report is appended.
Final documentation and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 19,100 / 750,287 | 18,829 / 740,593 |
| `mrs_bot_durable_json_io.py` | absent | 432 / 14,671 |

The root loses **271 lines / 9,694 bytes**; combined runtime source grows by
**161 lines / 4,977 bytes**. The new test file has **377 lines / 16,603 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: tweet ID parsing/ordering around
`parse_tweet_id` and `valid_tweets_sorted_by_id`, retaining numeric deduplication,
validation/logging order and original tweet references. Only stage 32 is
implemented; supervisor review precedes any further stage.

## Stage 33 — Durable state scalar and collection normalization

Baseline: `aa2820937a4c3738c3512d0cd42b5191836ed154` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage33` started
clean on `codex/bot-modularisation-stage33`, matching the pushed stage 32 parent.
The supplied scope/dependency notes, all 18 candidate bodies and relevant
state, cache, mention, receipt and durability callers informed the selection.

`mrs_bot_state_value_normalisation.py` owns **eleven functions / 114 original
definition lines**: bounded tweet ID parsing; state integer/epoch, string/int/
epoch list, string/int/record map and optional scalar/numeric ID normalization.
Eleven explicit adapters retain exact root signatures/defaults/annotations and
docstrings, passing current regex, math, logger, epoch cap and nested normalizers
per call. Original bodies preserve exact-string versus optional-ID/scalar
coercions, equality shortcuts, truth/int conversion, inclusive caps, narrow
error boundaries, key conversion/collision/insertion order, first-error stops,
shallow record copies and original epoch-list identity. No constants/classes
move. Full state/schema/reader validation, higher-level mention/cache/receipt
policy, durable I/O and recovery authority remain in their existing locations.
The owner retains no dependencies/configuration/paths/state and performs no
import-time runtime work or reverse application import.

Evidence: `/tmp/mrs-bot-stage33-n1NrEjn9`. Documentation passed before editing
for **241 modules**. The baseline selected **28 explicit nodes across eight
files**: all 18 supplied candidates (including six real recovery/restart
loopbacks), numeric/default/encoded-state regressions, two nearest stage 32 I/O
contracts and guarded bootstrap/process/network isolation. **46 tests collected
in 3.74s; 46 passed in 9.54s** (`baseline-pytest.txt`). After extraction,
**39 explicit nodes across nine files** produced **57 tests collected in 3.95s;
57 passed in 10.06s** (`current-pytest.txt`). Eleven new contracts cover guarded
import, current dependencies/references, coercion/type/cap, shallow identity,
collisions, early stops and native failures, registering the existing autouse
`isolate_regular_post_receipt` fixture. Existing assertions/barriers are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and nonempty collection under `set -euo pipefail` before explicit pytest arguments.
Runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR under the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
APIs. Passing logs are retained; no broad suite or whole enormous test file ran,
and passing tests were not repeated after documentation-only edits.

`verify_stage33.py` / `comparison.txt` verify exact bodies/docstrings,
signatures/defaults/annotations and dependency inventories without importing the
bot. Restoring definitions and removing the new import reconstructs the whole
parent root byte for byte, including **515 unaffected functions** and unrelated
statements. All **32 earlier owners**, existing tests/fixtures and digest docs
are unchanged; README/API only add the companion and this report is appended.
Final documentation (**242 modules**) and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 18,829 / 740,593 | 18,811 / 738,764 |
| `mrs_bot_state_value_normalisation.py` | absent | 226 / 6,640 |

The root loses **18 lines / 1,829 bytes**; combined runtime source grows by
**208 lines / 4,811 bytes**. New tests: **347 lines / 14,748 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: `parse_tweet_id` and
`valid_tweets_sorted_by_id`, preserving permissive coercion, numeric deduplication,
validation/logging order and original tweet references. Only stage 33 is
implemented; supervisor review precedes any further stage.

## Stage 34 — Durable state publication and backup generations

Baseline: `0c09fb91de13c9c776abeaf45c8570263823cb3e` (2026-09-06).
Worktree `/disks/disk1/research/mrsMThatcher-bot-modularisation-stage34` started
clean on `codex/bot-modularisation-stage34` at the accepted stage 33 parent.
The supplied scope/dependency notes, all 16 candidate test bodies and relevant
state, receipt, scheduler and I/O callers informed this extraction.

`mrs_bot_state_persistence.py` owns **five functions / 124 original definition
lines**: `state_document_for_persistence`, `copy_state_backup`,
`rotate_state_backups_before_commit`, `write_latest_state_backup` and `save_state`.
Five explicit adapters preserve root signatures/defaults/annotations and
docstrings, passing current paths/counts, compatibility constants, modules,
exceptions, security/logging helpers and nested callbacks per call. Exact bodies
retain reader/experiment validation order, shallow state versus deep fence copies,
stable source bytes, optional file/parent fsync, reverse rotation and suppressed
ordinary rotation errors. Security and value-free logs precede I/O; canonical
replace precedes latest backup, with the original post-commit error/cause and
BaseException cleanup. The state writer still adds no trailing newline. No
constants/classes move; source/receipt I/O, state loading/schema/defaults and
existing persistence callers remain in their current locations. The owner retains
no runtime dependencies or descriptors and performs no import-time runtime work.

Evidence: `/tmp/mrs-bot-stage34-uAzfETvj`. Documentation passed before editing
for **242 modules**. The baseline selected **27 explicit nodes across eight files**:
all 16 supplied candidates, current reader fences, nearest stage 32/33 contracts
and guarded bootstrap/process/network isolation. **35 tests collected in 3.62s;
35 passed in 9.78s** (`baseline-pytest.txt`). After extraction, **41 explicit
nodes across nine files** yielded **54 tests collected in 3.54s; 54 passed in
10.22s** (`current-pytest.txt`). Fourteen new test bodies (19 cases) cover guarded
import, current dependency/reference identity, exact write/rotate/replace/latest
order, security/log boundaries and native/ordinary/hard-exit failures, registering
the imported autouse `isolate_regular_post_receipt` fixture. Existing backup,
restart/recovery, scheduler and protected receipt-save regressions retain their
assertions and barriers.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and nonempty collection under `set -euo pipefail` before explicit pytest arguments.
Runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
APIs. Passing logs are retained; no broad suite or whole enormous test file ran,
and passing tests were not repeated after documentation-only edits.

`verify_stage34.py` / `comparison.txt` verify exact moved bodies/docstrings,
signatures/defaults/annotations and dependencies without importing the bot.
Restoring the five definitions and removing the new import reconstructs the whole
parent root byte for byte, including **521 unaffected functions** and unrelated
statements. All **33 earlier owners**, existing tests/fixtures and digest docs are
unchanged; README/API only add the companion and this report is appended.
Final documentation (**243 modules**) and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 18,811 / 738,764 | 18,752 / 736,712 |
| `mrs_bot_state_persistence.py` | absent | 206 / 7,283 |

The root loses **59 lines / 2,052 bytes**; combined runtime source grows by
**147 lines / 5,231 bytes**. New tests: **448 lines / 21,347 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production changes, deployment or restart occurred.
Next useful domain for supervisor assessment: `parse_tweet_id` and
`valid_tweets_sorted_by_id`, preserving permissive coercion, numeric deduplication,
validation/logging order and original tweet references. Only stage 34 is
implemented; supervisor review precedes any further stage.

## Stage 35 — State candidate validation and compatibility

Baseline: `75763ed456fa4ee27e30353df4ccd413b472d9c3` (2026-09-06), verified
clean on `codex/bot-modularisation-stage35` in
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage35`; origin's accepted
stage 34 branch matched. The supplied scope/dependency notes, all 21 curated test
bodies, relevant callers and nearby stage 33/34 contracts informed the selection.

`mrs_bot_state_candidate_validation.py` owns **four functions / 367 original
definition lines**: `validate_meme_schedule_state`,
`validate_meme_schedule_version_for_candidate`, `require_compatible_state_reader`
and `normalise_state_candidate`. Four explicit root adapters preserve exact
bodies/docstrings and root signatures/defaults/annotations, passing current
settings, reader version, modules/error/hash/logger and nested callbacks per call.
`reader_version=None` still selects the current `STATE_READER_VERSION`. Original
set-based key groups, field/error/callback order, shallow and callback-returned
references, empty conversions, experiment exception boundary/reader upgrades and
partial recovery events remain exact. Overflow reset precedes legacy evaluation
pruning and pending-authority validation; generated-spacing fallback and final
quarantine pruning keep their order. No constants/classes move. Complete loading,
defaults/schema, reader error class, persistence and actual normalization/recovery
implementations remain in existing locations; the owner retains no runtime
dependencies, paths or state and performs no import-time runtime work.

Evidence: `/tmp/mrs-bot-stage35-OPwKpI4h`. Before editing, documentation passed for
**243 modules**; **30 explicit nodes across eight files** collected **44 tests in
3.66s**, and **44 passed in 9.33s** (`baseline-pytest.txt`). These include all 21
supplied candidates: state/cache/epoch/meme validation, reader fences and backup
generations, six real recovery/restart loops, continuation overflow and legacy
quarantine retirement, plus nearest stage 33/34 and bootstrap/isolation checks.
After extraction, **42 explicit nodes across nine files** collected **56 tests in
3.68s**; **56 passed in 9.74s** (`current-pytest.txt`). Twelve new contracts cover
guarded import, current dependencies/defaults, exact validation/error/callback
order, reference identity, experiment handling and partial recovery events. They
register the imported autouse `isolate_regular_post_receipt` fixture; existing
assertions and barriers are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and nonempty collection under `set -euo pipefail` before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
APIs. No broad suite or whole enormous test file ran; passing logs are retained
without repeating unchanged tests after documentation-only edits.

`verify_stage35.py` / `comparison.txt` verify exact source bodies, signatures,
defaults/annotations and dependencies without importing the bot. Restoring the
four definitions and removing the new import reconstructs the entire parent root
byte for byte, including **522 unaffected functions** and unrelated statements.
All **34 earlier owners**, existing tests/fixtures and digest docs are unchanged;
README/API only add the companion and this report is appended. Final documentation
(**244 modules**) and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 18,752 / 736,712 | 18,464 / 726,192 |
| `mrs_bot_state_candidate_validation.py` | absent | 440 / 17,437 |

The root loses **288 lines / 10,520 bytes**; combined runtime source grows by
**152 lines / 6,917 bytes**. New tests: **431 lines / 23,057 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production change, deployment or restart occurred.
Next useful domain for supervisor assessment: `parse_tweet_id` and
`valid_tweets_sorted_by_id`, preserving coercion, numeric deduplication,
validation/logging order and original tweet references. Only stage 35 is
implemented; supervisor review precedes any further stage.

## Stage 36 — Durable state loading and recovery selection

Baseline: `effc87bf809bdea90eba43dfc81fbe09edfb2564` (2026-09-06), verified
clean on `codex/bot-modularisation-stage36` in
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage36`; origin's accepted
stage 35 branch matched. The supplied scope/dependency notes, all 24 curated test
bodies, relevant callers and focused stage 32–35 contracts informed validation.

`mrs_bot_state_loading.py` owns **one function / 193 original definition lines**:
`load_state`. One explicit root adapter preserves its exact body/docstring,
nested local functions/closures and root signature/annotation, passing all **16
current dependencies** per call. `Path` is only a postponed/local annotation,
not a runtime dependency. Unsafe namespaces still refuse fallback; ordinary
decode failures remain distinct from reader/legacy rejection. Normalized
primary/latest equality ignores older generations, and strict ordered backups
remain authoritative before pending-identity recovery. Original normalized-state
and recovery-list references preserve reason-specific logs/events, durable repair
save before summary/debug, and native failures. Existing unusable state refuses
startup; defaults apply only when all candidates are absent. No constants/classes
move. Defaults/schema, reader implementation, I/O/persistence/normalizers and
post-load initialization stay in existing locations. The owner retains no runtime
dependencies, paths or state and performs no import-time runtime work.

Evidence: `/tmp/mrs-bot-stage36-7zOyCst2`. Before editing, documentation passed for
**244 modules**; **39 explicit nodes across 11 files** collected **54 tests in
3.95s**, and **54 passed in 10.18s** (`baseline-pytest.txt`). Selection includes all
24 supplied candidates, six real recovery/restart loops, namespace/reader fences,
legacy and generation selection, pending-identity and quote-cursor durable repairs,
nearest stage 32–35 contracts and guarded bootstrap/isolation callers. After
extraction, **50 explicit nodes across 12 files** collected **69 tests in 3.89s**;
**69 passed in 10.25s** (`current-pytest.txt`). Eleven new contract functions / 15
cases cover guarded import, current dependencies, reference identity, strict and
recovery selection, exact repair output order, absent defaults, legacy/namespace
refusal and native failures, including repair-save failure before post-load work.
The new tests register the imported autouse `isolate_regular_post_receipt` fixture;
existing assertions and barriers are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and nonempty collection under `set -euo pipefail` before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
APIs. No broad suite or whole enormous test file ran; actual passing logs are
retained without repeating unchanged tests after documentation-only edits.

`verify_stage36.py` / `comparison.txt` verify exact source bodies, signatures,
annotations and compiled runtime dependency loads without importing the bot.
Restoring the definition and removing the new import reconstructs the entire
parent root byte for byte, including **525 unaffected functions** and all unrelated
statements. All **35 earlier owners**, existing tests/fixtures and digest docs are
unchanged; README/API only add the companion and this report is appended. Final
documentation (**245 modules**) and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 18,464 / 726,192 | 18,292 / 719,067 |
| `mrs_bot_state_loading.py` | absent | 231 / 9,670 |

The root loses **172 lines / 7,125 bytes**; combined runtime source grows by
**59 lines / 2,545 bytes**. New tests: **358 lines / 17,541 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production change, deployment or restart occurred.
Next useful domain for supervisor assessment: `parse_tweet_id` and
`valid_tweets_sorted_by_id` (two functions / 21 definition lines), preserving
coercion, numeric deduplication, validation/logging order and original tweet
references. Only stage 36 is implemented; supervisor review precedes further work.


## Stage 37 — Main-post receipt validation and bound schedule materialization

Baseline: `8414d604f929219e269c7a2abae69b1537bbb36d` (2026-09-06), verified
clean on `codex/bot-modularisation-stage37` in
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage37`; origin's stage 36
branch matched and the stage 37 remote branch was absent. Scope/dependency notes,
the stage 36 report, relevant API rows, scoped immutable parent bodies/callers and
selected test bodies/decorators informed this combined receipt extraction.

`mrs_bot_main_post_receipts.py` owns **six functions / 726 original definition
lines**: `main_post_attempt_is_semantically_valid`,
`regular_post_receipt_is_semantically_valid`,
`confirmed_pending_schedule_receipt_is_semantically_valid`,
`materialize_bound_regular_schedule_receipt`,
`materialize_bound_meme_schedule_receipt` and
`meme_post_receipt_is_semantically_valid`. Six explicit `_main_post_receipts`
adapters pass **14/18/4/9/8/13 current root dependencies**, respectively. Exact
bodies/docstrings, root signatures/defaults/annotations, the local date closure
and `_validate_result=True` remain unchanged. Sibling validators/materializers
remain current root callbacks; `_validate_result=False` still avoids recursion.

Compatibility preserves type/schema/field/key/history/hash validation order and
native errors, including attempt set/sort failures versus confirmed-history
rejection and full validators' native `data.get` errors. Legacy and lineage
rules, pending lane/summary/schema/calendar gates and current settings/exception
authority remain exact. Schedule materialization uses the durable plan with
calendar-day/DST behavior, shallow history lists retaining children, deep source
and experiment copies, canonical hashing of the original source and final
validation gates. No constants/classes move or new validation policy, catches,
normalization, scheduling/state writes or cleanup are introduced. Builders,
stores, transport, recovery, application and primitives remain in place; the
owner has no reverse import, retained runtime dependencies/state or import-time
runtime work.

Evidence: `/tmp/mrs-bot-stage37-6rpnIKag`. Before editing, documentation passed for
**245 modules**. A proportionate selection of **25 of 42 candidate functions**
plus three bootstrap/isolation checks produced **28 explicit nodes across six
files**, **45 tests collected in 3.29s**, and **45 passed in 9.47s**
(`baseline-pytest.txt`). Coverage includes both lanes, legacy/current/lineage
validation, configuration and ambient-timezone replay through both DST changes,
experiment identity, failure-after-confirmation recovery and all four real
upload/post/missing-ID/restart-once-only loopbacks.

After extraction, **37 explicit nodes across seven files** collected **64 tests
in 3.38s**; **64 passed in 9.90s** (`current-pytest.txt`). Nine new contract
functions / 19 cases cover guarded import, all current adapters/defaults/reference
returns/errors, native validation boundaries, eager conversions and the current
date closure, source/hash/copy/pending/materialization order, current final
validation and exception authority, recursion avoidance and copy boundaries. The
new module registers the imported autouse `isolate_regular_post_receipt` fixture;
all existing assertions, fixtures and barriers are unchanged.

The reused `run_selected.sh` requires successful AST-validated nonempty selection
and nonempty collection under `set -euo pipefail` before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
providers. No broad suite, whole enormous test file or unrelated earlier-owner
contract selection ran. Complete evidence and actual logs are retained; passing
tests were not repeated after documentation-only edits.

`verify_stage37.py` / `comparison.txt` prove exact source bodies, root and owner
signatures/defaults/annotations, compiled runtime dependency loads and forwarding
without importing the bot. Restoring the six definitions and removing the new
import reconstructs the entire immutable parent root byte for byte, including
**520 unaffected functions** and all unrelated statements. All **36 previous
owners**, existing tests/fixtures and digest docs are unchanged. README/API only
add this owner; the report is append-only. Final documentation (**246 modules**)
and `git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 18,292 / 719,067 | 17,678 / 695,119 |
| `mrs_bot_main_post_receipts.py` | absent | 830 / 33,689 |

The root loses **614 lines / 23,948 bytes**; combined runtime source grows by
**216 lines / 9,741 bytes**. New tests: **364 lines / 16,678 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production change, deployment or restart occurred. Only
stage 37 is implemented; supervisor review precedes any further stage.

## Stage 38 — Main-post receipt storage and durable lifecycle transitions

Baseline: `f59ca112f233044338b13c877023679b49b55cad` (2026-09-06), verified clean
on `codex/bot-modularisation-stage38` in
`/disks/disk1/research/mrsMThatcher-bot-modularisation-stage38`. Origin's stage 37
branch matched; its stage 38 branch was absent. Applicable instructions, bounded
README/API guidance, the stage 37 report, scope/dependency notes, immutable parent
bodies/callers and selected test bodies/decorators informed the extraction.

`mrs_bot_main_post_receipt_storage.py` owns **eleven functions / 414 original
definition lines**: `main_post_attempt_path`, `write_main_post_attempt`,
`mark_main_post_attempt_attempting`, `remove_main_post_attempt`,
`finalize_confirmed_pending_schedule_receipt`, and the regular/meme
`write_*_post_receipt`, `load_*_post_receipt` and `remove_*_post_receipt` functions.
Eleven explicit `_main_post_receipt_storage` adapters supply
**2/11/10/7/10/14/6/4/14/6/4 current root dependencies**, respectively. Bodies,
docstrings, root signatures/defaults/annotations and current sibling callbacks
remain exact; no constants or classes move.

Compatibility preserves lane/path selection; ordered validation, retirement and
namespace gates; exclusive publication and original error causes; sending status
and equality checks, shallow promotion, canonical argument order and exact source
mutation authority. Pending finalization preserves loader/materializer/writer/log
order and the returned materializer object. Writers retain distinct legacy/current
branches; loaders retain the original read-only exception catch, lane-specific
type/required-field checks, statuses and data retention. Retirement keeps its
original disposition/open/JSON/error scope and canonical retirement before logs.
Source authority, atomic I/O, journals/retirement primitives, validation and
materialization, builders, scalar-global confirmation promotion, reconciliation,
application and provider transport remain in place. No new policy, catches,
cleanup, retained runtime authority or reverse import is introduced.

Evidence: `/tmp/mrs-bot-stage38-9jf7OwSz`. The pre-edit documentation check passed
for **246 modules**. **19 of 20 candidates** plus three bootstrap/isolation checks
produced **22 explicit nodes across five files**: **50 tests collected in 3.40s**,
then **50 passed in 10.73s** (`baseline-pytest.txt`). Coverage includes both lanes'
hard-death boundaries, interrupted atomic writes, namespace/inode races,
legacy/current/single-use authority, unsafe entries, fsync/retirement uncertainty,
failure-after-confirmation recovery and all four real local-server loopbacks.
The unchanged bound meme-delay replay candidate was omitted as stage 37 coverage.

After extraction, **33 explicit nodes across six files** collected **83 tests in
3.29s**; **83 passed in 11.50s** (`current-pytest.txt`). Eleven new contract
functions / 33 cases check guarded import, every current adapter/signature and
reference/error return, ordered read/publication/promotion/retirement boundaries,
shallow-copy and materializer identity, exact mutation authority and native error
scope. The new test module registers imported autouse
`isolate_regular_post_receipt`; all existing assertions, fixtures and barriers
remain unchanged.

The reused selection runner uses `set -euo pipefail`, successful AST-validated
nonempty selection and nonempty collection before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, denied external sockets and explicit loopback
providers. No broad suite or unrelated earlier-owner contracts ran; successful
tests were not repeated after documentation-only edits. Complete evidence and
actual logs are retained.

`verify_stage38.py` / `comparison.txt` prove exact bodies/signatures/defaults,
annotations, compiled dependency loads and explicit forwarding without importing
the bot. Restoring the eleven definitions and removing the new import reconstructs
the entire immutable parent root byte for byte, including **515 unaffected
functions** and all unrelated statements. All **37 prior owners**, existing
tests/fixtures and digest docs are unchanged. README/API only add this owner;
this report is append-only. Final documentation (**247 modules**) and
`git diff --check` pass.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 17,678 / 695,119 | 17,413 / 685,483 |
| `mrs_bot_main_post_receipt_storage.py` | absent | 568 / 23,377 |

The root loses **265 lines / 9,636 bytes**; combined runtime source grows by
**303 lines / 13,741 bytes**. New tests: **475 lines / 24,614 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata; no production change, deployment or restart occurred. Only
stage 38 is implemented; supervisor review precedes the next stage.

## Stage 39 — Main-post attempt, payload and bound-plan values

Baseline: `21cf5c1d20abea13a3791413ed7a598d5082982c` (2026-09-06), verified clean
on `codex/bot-modularisation-stage39` in the requested stage39 worktree. Origin's
stage38 branch matched; stage39 was absent. Applicable AGENTS instructions,
bounded README/API guidance, the stage38 report, supervisor scope/dependency
notes, immutable parent bodies/callers and selected tests/decorators were read.

`mrs_bot_main_post_attempt_values.py` owns **twelve functions / 345 original
definition lines**: payload hashing/reconstruction, bound meme-plan snapshot and
validation, experiment-envelope validation/extraction, writable/payload-binding
predicates, attempt construction, confirmed-receipt matching, pending receipt
construction and confirmation epoch clamping. Ten explicit
`_main_post_attempt_values` adapters supply current root dependencies;
`main_post_attempt_payload` and `engagement_experiment_envelope_from_attempt`
are exact zero-dependency aliases. The inventory is
**2/0/3/6/4/0/3/1/7/1/5/1**; bodies/docstrings, root signatures/defaults/annotations
and sibling callbacks remain exact. No constants or classes move.

Compatibility preserves JSON settings, UTF-8 and hash order; payload coercions,
list gates and AI identity versus builder truthiness; bound timezone/date
conversion, fallback and validator closure order; exact schema/type/version,
epoch/anchor and experiment binding/hash/weight/arm gates. Envelope extraction
returns the original reference; the experiment validator retains its narrow
exception tuple. Writable, payload and matching short circuits remain exact.
Builders keep validation before entropy, original entropy/clock/hash/coercion/
deep-copy evaluation order, current final validation, and pending source-copy
versus original-lane ordering. Confirmation keeps conversion/log/clamp behavior.
Storage, transitions, I/O, journals, transport, semantic validation/materialization,
recovery/application and scheduling/experiment primitives remain in place. No
new validation, catches, normalization or retained runtime authority is added.

Evidence: `/tmp/mrs-bot-stage39-9rbZlChu`. Pre-edit documentation passed for
**247 modules**. **21 of 26 candidates** plus three bootstrap/isolation checks
produced **24 explicit nodes across seven files**: **41 collected in 3.74s;
41 passed in 9.15s** (`baseline-pytest.txt`). Coverage includes both lanes,
current/legacy/bound plans, single-use authority, timezone/DST/cross-midnight
behavior, experiment identity, rollback/confirmation, durable source identity
including peer ABA, emergency recovery, and all four real loopbacks. Five
unchanged storage/validator/startup candidates were omitted.

After extraction, **38 explicit nodes across eight files** produced **70 collected
in 3.75s; 70 passed in 10.12s** (`current-pytest.txt`). Fourteen new contract
functions / 29 cases cover guarded import, all current adapters and aliases,
reference/error behavior and ordered payload/plan/attempt/pending construction
boundaries. The new module registers imported autouse
`isolate_regular_post_receipt`; all existing assertions, fixtures and barriers
are unchanged. The reused runner uses `set -euo pipefail`, AST-validated nonempty
selection and successful nonempty collection before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, external socket denial and explicit loopback
providers. Complete selections, source excerpts, proof and actual logs remain
in the evidence directory; no broad suite or unrelated owner contracts ran.

`verify_stage39.py` / `comparison.txt` prove exact bodies/signatures/defaults,
annotations, compiled dependency loads and forwarding without importing the bot.
Restoring the twelve definitions and removing the new import reconstructs the
whole immutable parent root byte for byte, including **514 unaffected functions**
and all unrelated statements. All **38 prior owners**, existing tests/fixtures
and digest docs are unchanged. README/API only add the owner; this report is
append-only. Final documentation (**248 modules**) and `git diff --check` pass;
passing tests were not repeated after documentation-only edits.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 17,413 / 685,483 | 17,202 / 677,306 |
| `mrs_bot_main_post_attempt_values.py` | absent | 429 / 15,693 |

The root loses **211 lines / 8,177 bytes**; combined runtime source grows by
**218 lines / 7,516 bytes**. New tests: **486 lines / 25,933 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production change, deployment or restart occurred.
Only stage39 is implemented; supervisor review precedes the next stage.

## Stage 40 — Authenticated X request execution and response handling

Baseline: `00592351126556d2b33008d3ecf4e1439b4f4a9b` (2026-09-06), verified
clean on `codex/bot-modularisation-stage40` in the requested worktree. Origin's
stage39 branch matched; stage40 was absent. Applicable instructions, bounded
README/API guidance, the stage39 report, supervisor scope/dependency/candidate
inputs and immutable parent source were used without prior-session transcripts.

`mrs_bot_x_request.py` owns the exact `x_request` and `x_bearer_request`
bodies/docstrings: **551 + 85 = 636 original definition lines**. Two `_x_request`
adapters retain exact public signatures/defaults/annotations and supply **41/11
current root dependencies**. Each root retains its original `**kwargs` collector;
the owner instead requires keyword-only, unannotated `kwargs`, using the original
AST argument. Both forward `kwargs=kwargs`, preserving the same dictionary and
nested references, including ordinary `json`, `requests`, `AUTH` and `kwargs`
keys. No unpacking into dependency parameters, copying or filtering is added.

Compatibility preserves route/type/body-channel gates; logging, freeze, pause,
redirect and durable-authority order; exact media metadata and consumption;
current binder/coordinator arguments and dictionary identity; read health,
timeout/auth and exception precedence; response/error/diagnostic handling and
causes; issued proof activation/identity and current `sys.exc_info` finally
invalidation. Bearer remains read-only with its own token/header behavior,
health finally, native duplicate-key errors and >=400 threshold. No retries,
constants/classes/global assignments move, new policy or retained authority is
introduced. Transport/media authority, error semantics, diagnostics and caller
orchestration remain in their existing owners.

Evidence: `/tmp/mrs-bot-stage40-GfgQmteU`. Pre-edit documentation passed for
**248 modules**. **24 of 33 candidates** plus three bootstrap/isolation checks
yielded **27 explicit nodes across seven files: 45 collected in 3.88s; 45 passed
in 6.98s**. Selection covers read behavior, exact write/media channels, authority,
malformed-success diagnostics, actual consumed proof ownership/publication/
interruption/cleanup, pause/durability/lock barriers and two main-post loopbacks.
Overlapping diagnostic matrices and additional scheduling loopbacks were omitted.

Current validation: **37 explicit nodes across eight files: 80 collected in
3.92s; 80 passed in 8.94s** (`current-pytest.txt`). Ten new contract functions /
35 cases cover guarded import, current dependencies, public signatures, kwargs
collisions/reference identity, native errors and ordered request/proof boundaries.
The initial run had **72 passes and two test-setup failures**: synthetic escaping
exceptions used a proof rejected by the existing constructor. Correcting that
setup and adding six native unknown-key cases produced the passing final run;
runtime extraction needed no correction. Initial logs are retained separately.

Stage39 selection/extraction/comparison helpers were reused with recorded local
adaptations. The runner retains `set -euo pipefail`, AST-validated nonempty
selection and successful nonempty collection before explicit pytest arguments.
Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, external socket denial and explicit loopback
providers. New tests import autouse `isolate_regular_post_receipt`; existing
assertions, fixtures and barriers are unchanged. No broad suite ran.

`verify_stage40.py` / `comparison.txt` verify exact bodies, signatures/defaults/
annotations, compiled dependency loads and explicit forwarding without importing
the bot. Restoring the two definitions and removing the new import reconstructs
the entire immutable parent root byte for byte, including **522 unaffected
functions** and all unrelated statements. All **39 prior owners**, existing tests,
fixtures and digest docs are unchanged. README/API only add the owner; this
report is append-only. Post-edit documentation (**249 modules**) and
`git diff --check` pass. Passing tests were not repeated after this report edit.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 17,202 / 677,306 | 16,646 / 656,558 |
| `mrs_bot_x_request.py` | absent | 706 / 26,800 |

The root loses **556 lines / 20,748 bytes**; combined runtime source grows by
**150 lines / 6,052 bytes**. New tests: **452 lines / 24,766 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production change, provider call, deployment or restart
occurred. Only stage40 is implemented; supervisor review precedes the next stage.

## Stage 41 — Receipt-bound media and public-post creation

Baseline: `a79e90b1caa967bb738db8edd39c3452e4561480` (2026-09-06), verified
clean on `codex/bot-modularisation-stage41` in the requested worktree. Origin's
stage40 branch matched and stage41 was absent. Applicable instructions, bounded
README/API guidance, the stage40 report, four supervisor inputs and immutable
parent source were used without prior-session transcripts.

`mrs_bot_post_creation.py` owns six exact bodies/docstrings: metadata validation
and construction, v2 and outer media upload, public create and confirmed-media
handoff. Six `_post_creation` adapters preserve exact root signatures/defaults/
annotations and supply **3/2/4/19/30/11 current dependencies**. The immutable
parent measures **48 + 17 + 59 + 143 + 362 + 39 = 668 definition lines**; the
supervisor scope's 670-line estimate was corrected in the evidence, without
changing any original body. The initial comparison stopped on that count
assertion; correcting the helper count completed the proof.

Compatibility preserves metadata copy/validation order and optional envelopes;
exact multipart/form/authority references and media ID/native-error behavior;
pause/binding/pretransport abort/SIGINT/marker scopes; receipt/count/content and
callback gates; frozen payload identity, caller attempt clear/update identity,
source/journal preparation and arming; the nested generator/response validator
and call-time historical import; historical phase publication immediately before
the sole request; confirmation/response identity and rejection-retirement/pause/
ambiguity exception precedence, causes and BaseException distinctions; and
handoff experiment equality before exact binding/retirement/logging. Siblings
remain current root callbacks. Receipt/transport/proof/marker/guard/clock owners
stay unchanged; no retries, new policy, cleanup, constants/classes, aliases,
global assignments or retained authority are introduced.

Evidence: `/tmp/mrs-bot-stage41-STWlAtHt`. Pre-edit documentation passed for
**249 modules**. **29 of 40 candidates** plus three bootstrap/isolation checks
yielded **32 explicit nodes across seven files: 44 collected in 3.69s; 44 passed
in 7.29s**. Coverage includes both creation boundaries, experimental binding and
abort, initial/final/consumed pauses, durable authority and confirmation, both
handoff lanes, conversational/main/historical receipts, rejection unlink/close
failures, pending barriers and real quote/meme loopbacks. Repeated broad barrier,
hard-death and scheduling matrices were omitted.

Current validation: **47 explicit nodes across eight files: 75 collected in
3.63s; 75 passed in 8.66s** (`current-pytest.txt`). Fifteen new contract functions /
31 cases cover guarded import, all current dependencies and public signatures,
reference/copy/native-error behavior and missing ordered boundaries. Existing
assertions/fixtures remain unchanged; new tests import autouse
`isolate_regular_post_receipt`. No pytest failures or broad suite runs occurred.

Stage40 selection/runner and ordinary stage39 extraction/comparison helpers were
reused with recorded stage-local adaptations. Selection is AST-validated and
nonempty; successful nonempty collection precedes explicit pytest arguments under
`set -euo pipefail`. Both runs use `PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`, TMPDIR beneath the evidence root, temporary HOME/state,
dummy credentials, dead proxies, external socket denial and explicit loopbacks.

`verify_stage41.py` / `comparison.txt` prove exact bodies, signatures/defaults/
annotations, compiled dependency loads and explicit forwarding without importing
the bot. Whole-parent-root reconstruction is byte-identical, including **518
unaffected functions** and all unrelated statements. All **40 prior owners**,
existing tests/fixtures and digest docs are unchanged. README/API only add the
owner; this report is append-only. Post-edit documentation (**250 modules**) and
`git diff --check` pass. Passing tests and the module documentation check were
not repeated for this report edit.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 16,646 / 656,558 | 16,129 / 636,893 |
| `mrs_bot_post_creation.py` | absent | 759 / 29,897 |

The root loses **517 lines / 19,665 bytes**; combined runtime source grows by
**242 lines / 10,232 bytes**. New tests: **600 lines / 33,267 bytes**.
Production remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd` per
worktree metadata. No production/private-state access, provider call, deployment
or restart occurred. Only stage41 is implemented; supervisor review precedes
the next stage.

## Stage 42 — Main-post receipt application and local recovery

Baseline: `78b650fa6e277b2fd79bc9f50da29e74e2386a83` (2026-09-06), verified
clean on `codex/bot-modularisation-stage42`; origin's stage41 matched and stage42
was absent. This fresh invocation used the four scoped supervisor inputs,
bounded project guidance and immutable parent source, without prior transcripts.

`mrs_bot_main_post_reconciliation.py` owns eight exact bodies/docstrings:
regular/meme application and reconciliation, confirmed experiment application,
both emergency completeness predicates and main-receipt reconciliation. Eight
`_main_post_reconciliation` adapters retain exact root signatures/defaults/
annotations and supply **7/12/3/10/6/6/19/7 current dependencies**. The moved
original definitions total **554 lines**.

Compatibility preserves mutable state/history identity, authoritative versus
stale/legacy histories, tied timestamp guards, generated-spacing idempotence,
exact bound and empty schedules, experiment plan fallback and callback order.
Emergency predicates retain eager work outside the narrow builder/materializer
Exception boundary. Recovery keeps lineage before finalization/application,
protected persistence and experiment events before obligation enqueue, journal
and receipt retirement, notification/emission/logging and optional auxiliary
work. The simultaneous-receipt gate precedes regular then meme callbacks, with
exact options/references and native errors. All underlying owners stay in place;
no new policy, retries, cleanup, aliases, constants/classes or retained authority.

Evidence: `/tmp/mrs-bot-stage42-eWsTnk2U`. Checked stage-local adaptations reuse
the stage41 selection, runner, extraction and comparison helpers. Pre-edit
documentation passed for **250 modules**. **24 of 31 candidates**, plus three
bootstrap/isolation checks, produced **27 explicit nodes across eight files**:
**34 collected in 3.70s; 34 passed in 8.94s**. Selection covers both lanes,
histories/spacing/stale replay, bound timezone/DST schedules, emergency/pending
recovery, lineage, protected save failure, outbox order/auxiliary faults and three
real loopbacks; overlapping protocol matrices were omitted.

Current validation: **39 explicit nodes across nine files; 59 collected in
3.73s; 59 passed in 9.74s** (`current-pytest.txt`). Twelve new contract functions /
25 cases cover guarded import, all current dependencies/public signatures,
references, native errors and missing ordered boundaries. Existing tests and
assertions are unchanged; new tests import autouse `isolate_regular_post_receipt`.
Both runs used AST-validated nonempty selections and successful nonempty
collection before explicit pytest arguments under `set -euo pipefail`, with
`PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider`. TMPDIR is
beneath the evidence root; temporary HOME/state, dummy credentials, dead proxies,
external socket denial and explicit loopback providers remain in effect.

`verify_stage42.py` / `comparison.txt` prove exact bodies, signatures/defaults/
annotations, nested expressions, compiled dependency loads and explicit
forwarding without importing the bot. Whole-parent-root reconstruction is
byte-identical, including **516 unaffected functions** and all unrelated
statements. All **41 earlier owners**, existing tests/fixtures and digest docs
are unchanged. README/API only add this owner; this report is append-only.
Post-edit documentation (**251 modules**) and `git diff --check` pass. No pytest
failures or broad suites occurred; passing tests and module documentation checks
were not repeated for this report-only edit.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 16,129 / 636,893 | 15,744 / 621,211 |
| `mrs_bot_main_post_reconciliation.py` | absent | 662 / 26,602 |

The root loses **385 lines / 15,682 bytes**; combined runtime source grows by
**277 lines / 10,920 bytes**. New tests: **544 lines / 30,566 bytes**.
Production worktree metadata remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd`. No production changes, service
operations, live provider calls or private configuration/state/credential reads
occurred. Only stage42 is implemented; supervisor review precedes the next stage.


## Stage 43 — Engagement experiment opportunity and publication validation

Baseline: `0512b0c64fa7ccbdb2db45904e5fc191a881b645` (2026-09-06), verified
clean on `codex/bot-modularisation-stage43`; origin's stage42 matched and stage43
was absent. This fresh invocation used the scoped supervisor inputs, bounded
project guidance and immutable parent source, without prior transcripts.

`mrs_bot_engagement_publication.py` owns eight exact bodies/docstrings:
invalidation, initialization, opportunity, quotation resolution, publication
authority revalidation, bounded failure diagnostic, revalidate-or-invalidate
and member deferral. Eight `_engagement_publication` adapters retain exact root
signatures/defaults/annotations and supply **3/7/5/9/4/3/6/3 current dependencies**.
The original definitions total **505 lines**; no constants/classes or aliases move.

Compatibility preserves disabled/terminal gates, narrow loader/engine catches,
pause/start/member/reservation references and durable-save-before-log order.
Exact quote scanning, newline/CR handling, hashes, attribution and call-time
historical formatting precede unchanged metadata/season/catalogue/public-text
checks. Publication authority retains current state/binding/progress/history
gates, narrow member-retrieval catch/cause, current resolution and rebuilt-envelope
short circuits. Diagnostic limits/type checks/category precedence, Exception-only
revalidation logging/diagnostic/clock/invalidation/cause order and same-state
deferral remain exact. Scalar-global loader/notifier, configuration/paths/constants/
snapshot, experiment engine, scheduling, images, receipts/recovery and transport
stay in their existing locations. No new catches, retries, policy, cleanup,
normalization, reverse import, retained authority or import-time runtime work.

Evidence: `/tmp/mrs-bot-stage43-ks64QKLO`. Checked stage-local adaptations reuse
the stage42 selection, runner, extraction and comparison helpers. Pre-edit
documentation passed for **251 modules**. The **18 curated candidates**, plus
three bootstrap/isolation checks, produced **21 explicit nodes across five files**:
**34 collected in 3.40s; 34 passed in 6.24s**. Selection covers ordinary/control/
treatment posting, valid/changed/unavailable authority, payload/binding/progress/
history gates, invalidation and durable media abort, disabled/paused/terminal
state and reservations, and two real loopbacks; no broad suites were run.

Current validation: **33 explicit nodes across six files; 82 collected in
3.35s; 82 passed in 7.64s** (`current-pytest.txt`). Twelve new contract functions /
48 cases cover guarded import, all current dependencies/public signatures,
references/native errors, ordered transitions, call-time formatter/scan authority,
envelope short circuits and bounded diagnostics. Existing tests/assertions are
unchanged; new tests import autouse `isolate_regular_post_receipt`. Both runs
used AST-validated nonempty selection and successful nonempty collection before
explicit pytest arguments under `set -euo pipefail`, with
`PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider`. TMPDIR remains
beneath the evidence root; temporary HOME/state, dummy credentials, dead proxies,
external socket denial and explicit loopback providers remain in effect.

`verify_stage43.py` / `comparison.txt` prove exact bodies, signatures/defaults/
annotations, compiled dependency loads and explicit forwarding without importing
the bot. Whole-parent-root reconstruction is byte-identical, including **516
unaffected functions** and all unrelated statements. All **42 earlier owners**,
existing tests/fixtures and digest docs are unchanged. README/API only add this
owner; this report is append-only. Post-edit documentation (**252 modules**) and
`git diff --check` pass. Neither pytest run failed; passing tests and the module
documentation check were not repeated for this report-only edit. An initial
helper-adaptation assertion stopped before selection and was corrected; details
remain in `helper-adaptation-notes.txt`.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 15,744 / 621,211 | 15,377 / 607,508 |
| `mrs_bot_engagement_publication.py` | absent | 570 / 21,078 |

The root loses **367 lines / 13,703 bytes**; combined runtime source grows by
**203 lines / 7,375 bytes**. New tests: **589 lines / 34,464 bytes**.
Production Git metadata remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd`. No production changes, service
operations, live provider calls or private configuration/state/credential reads
occurred. Only stage43 is implemented; supervisor review precedes the next stage.

## Stage 44 — Historical-context delivery and interrupted-attempt recovery

Baseline: `0b360629ead13b71f339e2be0b470fbfb3e42433` (2026-09-06), verified
clean on `codex/bot-modularisation-stage44`; origin's stage43 matched and stage44
was absent. This fresh invocation used the scoped supervisor files, bounded
project guidance and immutable parent source, without prior transcripts.

`mrs_bot_historical_context_delivery.py` owns eight exact bodies/docstrings:
`maybe_post_historical_context_reply`, both historical-context store factories,
`canonical_context_obligation_quote_id`, `_record_context_outbox_failure`,
`_record_or_verify_proved_context_failure`,
`recover_interrupted_historical_context_attempt` and
`process_due_historical_context_obligations`. Seven `_historical_context_delivery`
adapters retain exact public signatures/defaults/annotations and forward current
dependencies; the pure failure recorder is an exact alias. Dependency counts are
**17/5/2/1/0/2/8/10**, spanning **817 original definition lines**.

Compatibility preserves the prebarrier outside the outer try, reconciliation
before policy exits, call-time formatter/outbox imports, snapshot/gate/formatting,
dry-run callbacks, authoritative/legacy metadata and publication/result identity.
Guard start/store/finally and exception ordering remain exact. Factories retain
production existence flags, current paths and mutation/retirement callbacks.
Recovery preserves eager observations, exact source rebinding, independent
ordinals, outbox-before-history-before-receipt persistence, journal/disappearance
barriers and separate completed/pre-remote/current-failure/stale/missing branches.
Processing retains parent gates, narrow barrier catches, store creation outside
the worker try and current callback/worker-lock/OutboxWorkerBusy ownership.
Global-mutating workers, constants/classes/globals and all earlier owners stay
in place; no new policy, retries, normalization, cleanup or broader catches.

Evidence: `/tmp/mrs-bot-stage44-LcHGfEX6`. Checked adaptations reuse stage43's
selector, runner, extractor and source verifier, retaining the existing alias
branch and preserving unannotated factory returns. Pre-edit documentation passed
for **252 modules**. **32 of 37 curated candidates** plus three safety checks
produced **35 explicit nodes across seven files: 37 collected in 3.73s;
37 passed in 6.16s**. Selection spans delivery and interrupted recovery,
production factories, policy/dry-run/metadata, source/history/journal/ordinal
barriers, persistence faults, real worker callbacks, locking/recovery-only ticks
and one real quote-image loopback. Existing assertions/parametrization are unchanged.

Current validation: **45 explicit nodes across eight files: 80 collected in
3.76s; 80 passed in 7.75s**. Ten new contract functions / **43 cases** cover
guarded import, current dependencies/public signatures, alias/reference/native
errors, call-time factories/lookup, delivery ordering, exact failure proofs,
durable recovery ordering and narrow worker boundaries. New tests import autouse
`isolate_regular_post_receipt` and reuse semantic-gate and production-incident
fixtures/source binding with their isolation. Both runs used AST-validated
nonempty selection and successful nonempty collection before explicit pytest
arguments under `set -euo pipefail`, with `PYTHONUSERBASE=/home/tonym/.local
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
python3 -m pytest -q -p no:cacheprovider`. TMPDIR is beneath the evidence root;
temporary HOME/state, dummy credentials, dead proxies, external socket denial
and explicit loopback providers remain in effect. No pytest run failed.

`verify_stage44.py` / `comparison.txt` prove exact bodies, signatures/defaults/
annotations, compiled dependency loads and forwarding without importing the bot.
Whole-parent-root reconstruction is byte-identical, including **516 unaffected
functions** and all unrelated statements. All **43 earlier owners**, existing
tests/fixtures and digest docs are unchanged. README/API only add this owner;
the report is append-only. Post-edit documentation (**253 modules**) and
`git diff --check` pass; passing tests and the module documentation check were
not repeated for this report-only edit. Preliminary read/helper corrections are in
`helper-adaptation-notes.txt`; full selections, logs and adaptations are retained.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 15,377 / 607,508 | 14,686 / 577,001 |
| `mrs_bot_historical_context_delivery.py` | absent | 895 / 38,914 |

The root loses **691 lines / 30,507 bytes**; combined runtime source grows by
**204 lines / 8,407 bytes**. New tests: **617 lines / 33,316 bytes**.
Production Git metadata remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd`. No production changes, service
operations, live provider calls or private configuration/state/credential reads
occurred. Only stage44 is implemented; supervisor review precedes the next stage.

## Stage 45 — Receipt retirement and exact transport source verification

Baseline: `6929c30e7350f65e9983c051df6f835da19617dd`, verified clean on
`codex/bot-modularisation-stage45`; origin's stage44 matched and stage45 was
absent. This invocation used the scoped inputs and immutable parent source.

`mrs_bot_receipt_retirement.py` owns eight exact bodies/docstrings spanning
**570 original definition lines**: the confirmed-context matcher and historical
retirement authority, interrupted source and media resumers, current source
retirement, source-byte reconstruction, lineage verification and journal
retirement. Seven `_receipt_retirement` adapters and one exact pure matcher
alias retain public signatures/defaults/annotations and forward **0/12/20/3/
14/8/8/7** current dependencies. Historical formatter imports remain call-time.

Compatibility preserves current/legacy source-field and ordinal distinctions,
unique marker-bound terminal history, durable outbox authority before source
resume, active-lane and owning-journal checks, and journal-before-source
retirement. Media recovery retains the initial lock, narrow lstat errors,
canonical prepared owner and exact retired result. Exact bytes, validators,
serializers, digests, lane-specific confirmation epochs and separately issued
mutation authorities retain their references and order. Scalar state, paths,
locks/configuration, the control-snapshot resumer and uncertainty latch remain
in root; low-level engines and all 44 earlier owners remain unchanged.

Evidence: `/tmp/mrs-bot-stage45-1b6YX6GT`. Checked stage44 helper adaptations
retain the standard alias branch. Pre-edit documentation passed for **253
modules**. Selected **15 of 17** curated candidates plus three safety checks:
**18 explicit nodes across nine files; 28 collected in 3.72s; 28 passed in
10.13s**. Coverage includes wrong-source rejection before mutation, exact
failed/confirmed/legacy context authority, paused four-lane resume, multiple
lanes and prepared source/journal overlap, literal fresh-process media crash,
recovery and idempotence for both main lanes, verified mutation authority,
conversational durable state before retirement and real quote-image loopback.

Current validation: **30 explicit nodes across ten files; 81 collected in
3.75s; 81 passed in 11.82s**. Twelve new contract functions / **53 cases** cover
guarded import, current dependencies/signatures, alias and reference identity,
call-time imports, native errors and ordered authority/retirement boundaries.
They reuse the existing autouse receipt isolation and production lane builders.
Existing assertions, parametrization and fixtures are unchanged. Both runs
used AST-validated nonempty selection and successful nonempty collection before
explicit pytest arguments under `set -euo pipefail`, with
`PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider`. TMPDIR is beneath the evidence root; temporary HOME/state,
dummy credentials, dead proxies, external socket denial and explicit loopback
providers remain in effect. No pytest run failed.

`verify_stage45.py` / `comparison.txt` prove exact bodies, signatures/defaults/
annotations, nested compiled dependencies and forwarding without importing the
bot. Whole-parent-root reconstruction is byte-identical, including **515
unaffected functions** and all unrelated statements. All earlier owners,
existing tests/fixtures and digest docs are unchanged. README/API add only the
owner; this report is append-only. Post-edit documentation (**254 modules**)
and `git diff --check` pass. Passing tests and documentation coverage were not
repeated for this report-only edit; complete selections, logs and helpers remain
in the evidence directory.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 14,686 / 577,001 | 14,273 / 562,960 |
| `mrs_bot_receipt_retirement.py` | absent | 674 / 25,626 |

The root loses **413 lines / 14,041 bytes**; combined runtime source grows by
**261 lines / 11,585 bytes**. New tests: **600 lines / 36,345 bytes**.
Production metadata remains `master` at
`af5eda7c163a8174ec1365060aa923d21787e7bd`; production, services, private
configuration/state/credentials and live providers were untouched. Configured
Astra/max remains unchanged. Only stage45 is implemented; supervisor review
precedes the next stage.

## Stage 46 — Global remote-write barrier checks

Baseline: `c63ec4dbbfa45a00371f43d1660de0e7187c566e`, verified clean on
`codex/bot-modularisation-stage46`; origin's stage45 matched and stage46 was
absent. This invocation used the scoped supervisor inputs and immutable parent
source without loading prior conversations.

`mrs_bot_remote_write_barriers.py` owns nineteen exact bodies/docstrings spanning
**616 original definition lines**: receipt, incident/protocol, historical outbox
and exact sending checks, local recovery eligibility, journal/retirement/media
checks and the raising/boolean global barriers. Nineteen `_remote_write_barriers`
adapters retain public signatures/defaults/deferred annotations and forward
**1/2/2/4/3/8/1/1/4/7/2/3/2/2/13/2/2/24/11** current dependencies.

Compatibility preserves raw OR values, eager main readers, lazy path checks and
the distinct raising/boolean order. Historical journal/fence/source identity,
required prepared-attempt observation, risky phases and single-parent local
exceptions retain their original gates and catches; parent selection does not
deduplicate. Formatter imports remain call-time. Exact sending checks retain
pre-filesystem schema/canonical gates, no-follow private namespace inspection,
bounded reads, descriptor cleanup and all metadata/byte comparisons. Marker
probing precedes the current latch reread; sole-main recovery retains eager
namespace/journal observations, the legacy allowance and narrow lineage catch.
Prepared authority, durable receipt equality and per-lane exceptions remain
exact. Paths, latch setters, marker synchronization, transport/receipt engines,
protocol/lock/configuration and global prebarrier reconciliation stay unchanged.

Evidence: `/tmp/mrs-bot-stage46-clHNHGNF`. Checked stage45 helper adaptations
retain the normal explicit forwarding branch. Pre-edit documentation passed
for **254 modules**. The selection uses **20 of 25** curated candidates plus
three bootstrap/isolation checks: **23 explicit nodes across ten files;
40 collected in 4.35s; 40 passed in 10.99s**. It covers unsafe/missing ledgers,
unsafe/duplicate/pending/simultaneous receipts, marker failures and latches,
inactive protocol, exact historical source/outbox phases and local recovery,
main-loop lane stopping, durable prepared receipts, real historical-store
boundaries, fresh-process media recovery in both main lanes and quote-image
loopback.

Current validation: **41 explicit nodes across eleven files; 106 collected in
4.39s; 106 passed in 13.48s**. Eighteen new contract functions / **66 cases**
cover guarded import, current dependency/signature/reference forwarding, native
errors, lazy imports/reads, raw values and ordered boundaries. They reuse the
autouse receipt isolation and historical receipt builder. The first current
run had **105 passes and one new fixture failure**: lane receipts in one
directory share a canonical journal. The fixture now explicitly models distinct
unrelated journals; runtime code was unchanged. Both runs' full logs remain in
the evidence directory. Existing assertions, parametrization and fixtures are
unchanged.

Every pytest run followed AST-validated nonempty selection and successful
nonempty collection under `set -euo pipefail`, using
`PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
-p no:cacheprovider` with explicit selected arguments. TMPDIR is beneath the
evidence root; temporary HOME/state, dummy credentials, dead proxies, external
socket denial and explicit loopback providers remain in effect.

`verify_stage46.py` / `comparison.txt` prove exact bodies/docstrings, signatures,
defaults/annotations, nested compiled dependencies and forwarding without
importing the bot. Whole-parent-root reconstruction is byte-identical, including
**503 unaffected functions** and all unrelated statements. All **45 earlier
owners**, existing tests/fixtures and digest docs remain unchanged. README/API
add only the owner; this report is append-only. Post-edit documentation passed
for **255 modules**; the final staged whitespace check passed. Passing runtime
and documentation checks were not repeated for this report-only edit.

| Runtime file | Before lines / bytes | After lines / bytes |
|---|---:|---:|
| `mrsMThatcher2.py` | 14,273 / 562,960 | 13,873 / 550,465 |
| `mrs_bot_remote_write_barriers.py` | absent | 788 / 30,388 |

The root loses **400 lines / 12,495 bytes**; combined runtime source grows by
**388 lines / 17,893 bytes**. Final new tests: **577 lines / 35,120 bytes**;
`sizes.txt` records final sizes after the fixture correction. Production metadata
remains `master` at `af5eda7c163a8174ec1365060aa923d21787e7bd`; production,
services, private configuration/state/credentials and live providers were
untouched. Configured Astra/max remains unchanged. Only stage46 is implemented;
supervisor review precedes the next stage.
