# Digest023 independent diff review

Date: 18 July 2026 (Europe/London)
Host: `big-nas-2`
User: `tonym`
Project: `/disks/disk1/etc/mrsMThatcher`

## 1. Executive verdict

The pre-review main-bot changes were not yet ready to activate. The review found
two substantive defects:

1. topical acceptance could still be established by a broad one-concept match,
   while handles and URL components remained in the lexical retrieval query;
2. generated-identity winner changes were inferred from correlation rather than
   a true same-RNG counterfactual selection.

Both defects are now corrected. Topical principle replies require an
incoming-contribution provenance check and a concrete local issue bridge;
topically rejected mention decisions become durable `no_reply` outcomes. The
identity-policy telemetry now replays policy-disabled and policy-enabled
selection from the same saved RNG state without consuming the production RNG.

The final offline suite passed with **1,786 passed and 1 expected skip**. The
running service was not restarted or signalled and retained the same wrapper and
child PIDs throughout.

## 2. Exact pre-review state

The pre-review `git status --short` was:

```text
 M .gitignore
 M engagement_analytics/README.md
 M mrsMThatcher2.py
 M mrs_engagement_analytics.py
 M mrs_log_digest.py
 M reply_strategy.py
 M tests/test_engagement_analytics.py
 M tests/test_generated_identity_policy_production_scoring.py
 M tests/test_generated_image_utilisation_digest.py
 M tests/test_reply_strategy.py
?? engagement_analytics/quote_identity_corrections.json
?? engagement_identity_conflict_report.md
?? systemd_user_service_report.md
```

There were no index-staged changes. The pre-review tracked diff comprised 10
files, 836 insertions and 29 deletions. `git diff --check` passed.

Pre-review SHA-256 values for every modified tracked file:

| File | SHA-256 |
|---|---|
| `.gitignore` | `cfc4282116b11ecaf9945cdbfe7259436436d49df8d4a08f06fa43a9c7a00843` |
| `engagement_analytics/README.md` | `af61a304ff09e6bfb0db60ebf57acf831deb5ba40589f5fbc4a37db70c31ff53` |
| `mrsMThatcher2.py` | `ed5861a3613f560a26ec48ceb146c70abfe990e2601bf4bd02c2b76f8d412234` |
| `mrs_engagement_analytics.py` | `8516c4f7efca1a6f4bc1d1495db3325e5d701edc32a933925a681f8004192d93` |
| `mrs_log_digest.py` | `0b50732e169d29fbdc2e6c88be1baf496d55de67f5bce2de430695974d263f6b` |
| `reply_strategy.py` | `17989db30a67a43fbdd851f6107693e406f3314b14f2aeabf96accdc79740fff` |
| `tests/test_engagement_analytics.py` | `d9014eb849e59b06ac44f8586792f2d8915c320def345f1fe90df9e73a057986` |
| `tests/test_generated_identity_policy_production_scoring.py` | `059fd1a4ee0ff259afdcd1f92b2d643d4252e62956c2ca41024c946257a07215` |
| `tests/test_generated_image_utilisation_digest.py` | `43328d444a08767bd7d406e10a9d300bacc99b4f19ccce4d1692676ffdd050f6` |
| `tests/test_reply_strategy.py` | `0d54bdc417c31433eaad85da2394e1d17d184b2d343ff15adee3a35feba8df80` |

The previous report was inspected independently; its SHA-256 was
`9c46d6793d1075b4545d2c918b3e49b30c227aa09f4ee7d13dbf0d45bb0d507d`.
Its prose was not treated as proof.

## 3. Files and functions inspected

The review covered:

- `ask_grok_for_reply()`, its production mention and quote-tweet callers,
  `validate_reply_decision()`, `retrieve_research_packets()`, pending drafts,
  durable evaluation records, clarification handling and reply receipts;
- generated-image candidate scoring, `random.choice()` tie-breaking,
  `generated_identity_policy_applied_result()`, the dormant shadow observer,
  editorial shadow ordering and semantic-veto shadow invocation;
- generated-identity and utilisation analysis/rendering in
  `mrs_log_digest.py`;
- the offline future-simulator capture adapters;
- the analytics identity repair and its tests, only to establish current diff
  scope; no analytics history was changed in this review;
- all directly affected test modules and the broader offline suite.

A search found no `importlib.reload`, file watcher, inotify reload, `execv`, or
equivalent source hot-reload path in the reviewed production modules or wrapper.

## 4. Final reply-relevance guard

### Actual implementation

The final guard is **hybrid deterministic/model-assisted**:

1. The ordinary structured xAI response must include `topical_basis` for
   `principle_reply` and `researched_principle` only.
2. The value must be a short exact span from the incoming user contribution.
3. Local validation normalises Unicode and HTML entities, removes handles and
   URLs, proves the token sequence occurs in the incoming contribution, and
   rejects a basis derived only from a short incidental list fragment when the
   post contains longer substantive segments.
4. Generic political vocabulary such as `government`, `freedom`, `leadership`,
   `socialism`, `nation` and `economy` cannot establish relevance by itself.
5. A principle reply must bridge the sourced issue to the final reply through a
   specific lexical anchor or a narrow concept relation.
6. A researched principle must independently bridge issue-to-reply,
   issue-to-evidence and reply-to-evidence.
7. The provider output remains untrusted; the local validator is authoritative.

The compact concept map is not sufficient by itself: it is considered only
after exact incoming-span provenance and substantive-segment checks, and generic
members are removed before alignment. This remains intentionally conservative.

### Contamination exclusion

Production callers pass `shadow_incoming_text` separately from rendered parent
and thread context. Lexical retrieval now uses only that incoming contribution,
after stripping handles and complete URLs. It does not fall back to assembled
thread context. Parent and older-thread text remain available to the model for
conversation comprehension but cannot supply a valid `topical_basis`.

Quoted-post text, profile text, prior bot replies, URL components and page-title
metadata cannot independently establish the local relevance invariant. A test
proves that adding `@freedom` and a `/berlin/wall` URL does not alter retrieval.

### Exact invariant

A `principle_reply` or `researched_principle` is accepted only when:

- its issue basis is an exact, substantive span of the actual incoming
  contribution;
- the reply addresses that issue after incidental metadata and inherited context
  are excluded;
- a researched reply's selected evidence addresses the same issue;
- ordinary grounding, confidence, attribution, factuality, quotation, safety,
  repetition and mode rules also pass;
- unsupported actor-specific allegations are neither repeated nor implied.

Grounding confidence alone cannot satisfy topical relevance. An authentic but
irrelevant Thatcher principle fails closed. For mentions, that failure is stored
as a durable `no_reply` evaluation so backlog replay does not buy another xAI
call for the same target. Other malformed provider output remains retryable.

### Behaviour preserved

- The exact Burnham-style wall reply is rejected through the real reply route.
- Wall evidence remains usable for an actual Berlin Wall/coercive-border issue.
- Conviction versus popularity remains valid for a contribution about
  popularity.
- The misspelt Berlin Wall factual question retains answer-first validation and
  requires an East-to-West first sentence.
- Author caps, global budgets, clarification limits, terminal threads, durable
  no-reply handling, pending drafts and receipts retain their existing paths.
- Rejection telemetry records a static reason and detail code, not the copied
  user-text span. `topical_basis` is stripped before durable draft or receipt
  metadata is created.

### False-positive and false-negative assessment

The previous broad concept acceptance produced false positives for generic
government/freedom, economy/leadership and nation/leadership examples. Those now
fail. Remaining acceptance requires source provenance plus a specific bridge.

The conservative guard can reject a valid paraphrase whose semantic relation is
not represented by a specific anchor or narrow concept. Non-English exact anchors
are Unicode-safe, but the small concept relations are presently English. This is
a deliberate false-negative bias: silence is preferable to an unrelated
authentic quotation. No extra model or network call was introduced.

## 5. Generated-identity causation

### Pre-review defect

The pre-review implementation treated a winner difference as policy-caused when
some generated candidate had been penalised or excluded and the final filename
differed. Its diagnostic baseline used a deterministic basename choice while
production used `random.choice()`. That established correlation, not causation,
and could mislabel an unrelated original-image tie.

### Counterfactual definition now used

Immediately before the existing single production `random.choice()`, the code
saves the global RNG state. It then:

- performs the actual production choice exactly once with the global RNG;
- reconstructs the policy-disabled top tie set from unadjusted scores;
- reconstructs the policy-enabled top tie set from adjusted scores and
  exclusions;
- uses separate local `random.Random` instances initialised from the same saved
  state to select one winner from each tie set in original candidate order;
- verifies that the replayed policy-enabled winner and tie count equal the
  actual production result;
- marks a policy-caused change only when the validated disabled and enabled
  winners differ because policy changed score or eligibility.

The local replay does not mutate the global RNG. Candidate set, non-policy
scores, ordering, tie-breaking, quote/season context, pool state, editorial
shadow and semantic-veto state remain fixed. A mismatch is telemetry, not a
selection fallback.

The same saved-RNG rule now applies to the disabled identity-policy shadow
observer. Old shadow and production events without this telemetry remain
compatible but are classified as `legacy_policy_causation_unverified` and are
excluded from causal winner-change counts.

### Final digest categories

| Category | Meaning |
|---|---|
| `identity_policy_winner_change` | Valid same-RNG counterfactual selected different winners. |
| `identity_policy_scores_or_eligibility_only` | Policy changed a candidate score/eligibility but not the winner. |
| `no_policy_effect` | No production-affecting policy change. |
| `policy_neutral_equal_score_tie_resolution` | Legacy filename difference with equal scores and no policy relevance. |
| `policy_neutral_downstream_winner_difference` | Legacy non-policy difference attributable to another stage. |
| `legacy_policy_causation_unverified` | Old event lacks same-RNG proof. |
| `counterfactual_invariant_failure` | New telemetry does not reproduce actual selection. |

The regenerated local digest reported:

```text
regular selections             17
policy-relevant selections      0
policy-caused winner changes    0
counterfactual failures         0
policy-neutral differences      1
equal-score neutral ties        1
```

The `t34.jpg`/`t45.jpg` row appears only under policy-neutral equal-score tie
resolution. It does not appear in a policy-changed section. Generated images
remain disabled in current local configuration. Identity-policy reporting and
the simulator were proved not to change the selected image or RNG sequence.

## 6. Generated-image usage semantics

The current machine-readable schema is `usage_metric_schema_version=2`:

- `active_images_used_in_observed_logs`: active generated images seen in the
  bounded structured-log scan;
- `active_images_not_seen_in_observed_logs`: its bounded-log complement;
- `active_pool_observed_usage_percentage`: bounded-log percentage;
- `active_images_used_in_current_cycle`: active images retained in persistent
  current-cycle history;
- genuinely all-time usage is not claimed because no authoritative all-time
  source is available.

The old fields remain deterministic deprecated aliases:

```text
active_images_used_ever -> active_images_used_in_observed_logs
active_images_never_used -> active_images_not_seen_in_observed_logs
active_pool_ever_used_percentage -> active_pool_observed_usage_percentage
```

They retain their old bounded-log values and have a documented removal plan for
a future major digest schema. Markdown uses only accurate observed-log names and
renders the coverage interval. Pool health, runway and image counts were not
changed.

Repository-wide consumer search found the deprecated names only in
`mrs_log_digest.py`, its utilisation tests and the historical
`generated_image_utilisation_digest_report.md`. No other code consumer was found.

## 7. Tests added or strengthened

Coverage now includes:

- exact Burnham regression through the production reply route;
- related and unrelated wall evidence;
- inherited-context-only basis rejection;
- handle/URL retrieval contamination;
- generic political vocabulary rejection;
- deterministic relevance results;
- grounding-insufficient relevance;
- valid conviction/popularity and market/accountability bridges;
- actor-specific non-endorsement;
- durable topical `no_reply` outcome;
- Berlin Wall answer-first behaviour and existing clarification/cap tests;
- policy-disabled/enabled same-RNG counterfactual replay;
- non-winning penalties, winner exclusion and winner-displacing penalties;
- equal-score ties and counterfactual mismatch handling;
- legacy event compatibility without false causal attribution;
- deterministic digest rendering;
- observed-log/current-cycle terminology and aliases;
- simulator capture, branch, resume and RNG parity.

## 8. Activation-risk table

| File | Runtime relationship | Current process | Next restart/invocation | Accidental activation risk |
|---|---|---|---|---|
| `mrsMThatcher2.py` | Main child entry point | Old code loaded at 12:16 | Main changes activate on child restart | Medium: stricter fail-closed replies and new telemetry; no posting-selection change. |
| `reply_strategy.py` | Lazily imported by reply calls | Imported by 12:17, before review edits; cached | New schema/validator activates on child restart | Medium: valid but novel paraphrases may be skipped; unsafe/irrelevant output is not posted. |
| `mrs_log_digest.py` | Standalone reporting tool | Not imported by main bot | Used on next digest invocation; exercised offline here | Low; reporting only. |
| `mrs_engagement_analytics.py` | Separate oneshot analytics unit | Not part of main process; prior repair already used by timer | Loaded afresh by analytics runs | Out of main activation scope; not changed by this review. |
| `tools/simulate_regular_post_futures.py` | Offline simulator | Not imported by main bot | Only explicit simulator invocation | None to production. |
| `.gitignore`, README and reports | Repository/docs | Not loaded | No runtime activation | None. |
| Test modules | Test-only | Not loaded | Only pytest | None. |
| `engagement_analytics/quote_identity_corrections.json` | Analytics correction manifest | Not read by main bot | Read by analytics oneshot | Existing narrow analytics repair; not changed here. |

No dynamic source reload was found. The running process therefore still uses
the pre-review main and reply module objects. A crash, reboot or deliberate child
restart would load the current working-tree source automatically.

## 9. Service proof

Before and after review/testing:

```text
ActiveState=active
SubState=running
MainPID=1805
child PID=1808
ExecMainStartTimestamp=Sat 2026-07-18 12:16:59 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

Process layout remained exactly one wrapper and one child:

```text
1805 /bin/bash /usr/local/bin/runMrsMThatcher2
1808 python3 /usr/local/bin/mrsMThatcher2.py
```

Production logs show the current child entered `ask_grok_for_reply()` at 12:17,
before the reviewed module mtimes, establishing that its cached
`reply_strategy` module is the previous version. No watcher or reload path was
found.

## 10. Commands and results

Principal commands included:

```text
git status --short
git diff --stat / --name-only / full diff / --check
sha256sum ...
systemctl --user status/show mrsMThatcher.service
pgrep -af 'runMrsMThatcher2|mrsMThatcher2.py'
repository-wide rg inspections
focused pytest groups
python3 -m pytest -q
python3 mrs_log_digest.py ... --no-state
python3 -m compileall -q .
python3 -m compileall -q -x '(^|/)\._|semantic_alignment_research/.*/source_snapshot/' .
python3 -m py_compile ...
```

Validation results:

- focused reply/identity/utilisation/hybrid groups: **203 passed**;
- expanded reply/digest/selector/helper groups: **662 passed**;
- representative simulator parity regressions: **2 passed**;
- final full suite: **1,786 passed, 1 skipped**, three dependency deprecation
  warnings, in 697.65 seconds;
- `git diff --check`: passed;
- changed production modules and simulator `py_compile`: passed;
- compileall with historical non-source artefacts excluded: passed.

The literal `python3 -m compileall -q .` returned non-zero only because the tree
contains ten pre-existing AppleDouble `._*.py` resource-fork files with null
bytes and six read-only historical `source_snapshot` cache directories. No
tracked source compilation error was reported. Those unrelated archival hygiene
items were not deleted or permission-modified.

Two intermediate full runs found review-test integration issues and were not
treated as success: one old nine-field mock omitted `topical_basis`, and offline
simulator hooks did not accept the new saved-RNG keyword. Both fixtures/adapters
were corrected before the clean final run; production validation was not
weakened.

## 11. Final hashes and diff

Final SHA-256 values for production/runtime files changed in the working tree:

| File | SHA-256 |
|---|---|
| `mrsMThatcher2.py` | `ced125be1fc70d5c5ec3d7c42b248f4d599c45296266b78c0a7d9be04b35c25b` |
| `reply_strategy.py` | `cf2479e1de80b47d1514a9142f502d7ed9e882378438950346b00c29222fb985` |
| `mrs_log_digest.py` | `2a5e6832323d0bed875775da8c2748a3967be2b19502b7d1216bd3279933142e` |
| `mrs_engagement_analytics.py` | `8516c4f7efca1a6f4bc1d1495db3325e5d701edc32a933925a681f8004192d93` |
| `tools/simulate_regular_post_futures.py` | `aa286270fab118d0aeee07bed8ec6cbba21270b74e94df260f477fd07d19f10e` |

Final tracked diff:

```text
13 files changed, 1565 insertions(+), 105 deletions(-)
```

Final `git status --short`:

```text
 M .gitignore
 M engagement_analytics/README.md
 M mrsMThatcher2.py
 M mrs_engagement_analytics.py
 M mrs_log_digest.py
 M reply_strategy.py
 M tests/test_engagement_analytics.py
 M tests/test_generated_identity_policy_production_scoring.py
 M tests/test_generated_identity_policy_shadow_scoring.py
 M tests/test_generated_image_utilisation_digest.py
 M tests/test_hybrid_reply_retrieval.py
 M tests/test_reply_strategy.py
 M tools/simulate_regular_post_futures.py
?? engagement_analytics/quote_identity_corrections.json
?? engagement_identity_conflict_report.md
?? systemd_user_service_report.md
```

This report is intentionally ignored by the repository's existing `digest*.md`
rule and therefore does not appear in that status.

## 12. Remaining risks

- The relevance guard favours false negatives and may skip valid uncommon or
  non-English paraphrases lacking a recognised bridge. This is consistent with
  the accuracy-first policy but should be observed after separately authorised
  activation.
- `topical_basis` is a new strict structured-response field. Provider failure to
  populate it causes a local fail-closed rejection, not an unsafe post.
- No live test post was manufactured, so provider conformance is covered by
  schema/prompt fixtures and fail-closed tests rather than a production call.
- Whole-tree compileall remains noisy until unrelated AppleDouble files and
  read-only historical snapshots are handled by repository hygiene policy.

None of these risks can change an image choice, weaken factual grounding, bypass
reply caps, or produce a post from a rejected decision.

## 13. Prohibited-action confirmation

This review performed no X post or X API call, no service restart/reload/signal,
no bot activation, no production-state/receipt/ledger/history mutation, no
generated-pool activation, no semantic-veto activation, no hybrid-retrieval
activation, no analytics-history mutation, no commit, no push and no deployment.

Files that would become active in the main child after separately authorised
activation are `mrsMThatcher2.py` and `reply_strategy.py`. Their support reporting
change in `mrs_log_digest.py` is standalone. Readiness is supported by the exact
production-route reply tests, same-RNG selector and simulator parity tests,
affected 662-test group, final 1,786-test suite, targeted compilation and clean
diff check.

READY FOR SEPARATELY AUTHORISED ACTIVATION
