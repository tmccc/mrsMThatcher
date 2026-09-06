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
