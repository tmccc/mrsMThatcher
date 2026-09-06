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
