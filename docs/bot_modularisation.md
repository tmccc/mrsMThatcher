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
