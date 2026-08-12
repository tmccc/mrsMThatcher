# Conservative-hybrid reply evaluation: pre-output adjudication protocol

## Status and boundary

This protocol governs the profile-blind context-clearance and incoming-contribution
semantic-adjudication stage that precedes any reply-profile output. Decisions made under it
are proposed Codex language-model decisions pending independent review. They are not human
decisions and are not finally accepted receipts.

The adjudicator identity is `codex_language_model_case_by_case_review`, with
`human_adjudicator=false` and global status
`proposed_pending_independent_review`.

This phase does not run either evaluated profile, generate a reply, select or freeze a
sample, build a replay pack, create an execution or response-label seed, create a blind key,
score output, contact a provider, post, merge, deploy, restart the bot, or change production
state. The repository tool is an offline validator, renderer, hasher, and packager. It never
supplies a proposed context decision or accepted semantic classification. It may derive only
the mandatory blocked or unresolved semantic accounting state from a validated context
receipt.

## Immutable source

The only real-data input is the complete checksummed directory specified by the operator.
The validator requires the accepted audit commit
`f224a5f9b7a2ebd4d27c20b5133b3536cea36b3f`, its accepted branch, and the direct ancestry

```text
0b7cd1da11d7c9ff5b3051c3d6fc4a70ae45176d
  -> 00083cc9f3501f1f4af037352d10d8e530ee18d5
  -> f224a5f9b7a2ebd4d27c20b5133b3536cea36b3f
```

It verifies the hash of `SHA256SUMS`, its exact complete member set, every member digest,
the three accepted audit-source identities, the cutoff and all 36/14/22 controls, the blank
decision templates, the exact-text-cluster controls, and all zero-execution controls. The
36-member fresh inventory is the authoritative eligibility set; context records are joined
to it by candidate ID and canonical record hash. The wider context file is not counted as
though every audited record were eligible.

Raw inventory objects never enter a review renderer. Both packet renderers build strict
allow-list projections. The accepted review Markdown files are copied only as original audit
artefacts; they are not used as packet sources because they contain non-binding lexical
navigation material that is deliberately hidden in this phase.

## Offline workflow

The first operation creates a new private output directory, verifies the immutable input,
and writes the 22-case context packet plus a blank context CSV:

```bash
python3 tools/validate_reply_hybrid_preoutput_adjudication.py prepare \
  --stage context \
  --input <CORRECTED_AUDIT_DIRECTORY> \
  --output <NEW_PRIVATE_OUTPUT_DIRECTORY> \
  --repository <PERSISTENT_WORKTREE>
```

After every context decision has been entered case by case, the second prepare stage
validates those entries, hashes 14 inherited automatic receipts and 22 proposed Codex
receipts, then writes a cleared-only semantic packet and a blank 36-row semantic accounting
CSV:

```bash
python3 tools/validate_reply_hybrid_preoutput_adjudication.py prepare \
  --stage semantic \
  --input <CORRECTED_AUDIT_DIRECTORY> \
  --output <PRIVATE_OUTPUT_DIRECTORY> \
  --repository <PERSISTENT_WORKTREE>
```

After case-by-case semantic review and both consistency passes, finalisation validates every
row, writes canonical receipts and proposed coverage, records unresolved cases explicitly,
keeps every later-stage readiness flag false, writes checksums last, and independently
verifies them:

```bash
python3 tools/validate_reply_hybrid_preoutput_adjudication.py finalise \
  --input <CORRECTED_AUDIT_DIRECTORY> \
  --output <PRIVATE_OUTPUT_DIRECTORY> \
  --repository <PERSISTENT_WORKTREE>
```

The directory is mode `0700`; each file is mode `0600`. No real contribution or completed
decision is committed to Git.

## Context pass

The context reviewer uses only the sanitised context packet. A dependency flag is a warning,
not an exclusion. `clear` means the bounded material supports safe and relevant
interpretation without guessing. `exclude` means materially necessary context remains
missing, contradictory, future-leaking, self-reply-leaking, or unresolved.
`requires_adjudication` preserves genuine uncertainty.

The only controlled reasons for `clear` are:

- `self_contained_despite_flag`
- `resolved_by_parent_thread`
- `resolved_by_quoted_post`
- `resolved_by_clarification_context`
- `resolved_by_combined_bounded_context`

The only controlled reasons for `exclude` are:

- `missing_material_parent_context`
- `missing_material_quoted_context`
- `unresolved_referent`
- `elliptical_or_symbol_only_unresolvable`
- `source_or_attribution_context_insufficient`
- `context_conflict_or_temporal_defect`
- `unsafe_or_nonoriginal_replay_context`
- `other_material_context_failure`

The only controlled reason for `requires_adjudication` is
`genuinely_ambiguous_after_review`.

A brief, difficult, impolite, disagreeable, politically opposed, or likely-`no_reply`
contribution is not excluded for that reason. Each note gives a concise, substantive,
case-specific basis without profile comparison or retrospective quality judgement. Manual
receipts use `codex-preoutput-context-review-v1` and real UTC timestamps. Automatic receipts
remain explicitly inherited automatic audit decisions, not Codex judgements.

## Semantic pass

Semantic review starts only after the context CSV validates. The packet contains only cases
whose context receipt is `automatic_clearance` or `clear`. Review proceeds one case at a time
in reverse candidate-ID order. Aggregate lexical counts, prospective coverage gaps, and
retrospective dispositions are not consulted until every first-pass semantic decision has
been drafted.

Every original eligible candidate remains in semantic accounting. An excluded context has
status `blocked_context_excluded`; a context or semantic uncertainty has status
`requires_adjudication`. Neither may carry accepted semantic fields. A cleared, resolved
case has status `completed`, at least one controlled tag, exactly one primary stratum, three
exact lower-case Boolean decisions, a controlled reason, a case-specific note, the identity
`codex-preoutput-semantic-review-v1`, and a real UTC timestamp.

The allowed tags, in canonical storage order, are:

1. `genuine_social_courtesy`
2. `substantive_agreement_with_reason_or_principle`
3. `civil_challenge_criticism_or_disagreement`
4. `analogy_distinction_or_recommendation`
5. `substantive_political_or_moral_proposition`
6. `direct_factual_or_historical_question`
7. `safe_contribution_specific_wit_opportunity`
8. `unsupported_allegation_or_sensitive_factual_correction_context`
9. `justified_safety_no_reply`
10. `other_safe_conversational_contribution`

The allowed primary strata are `genuine_social_courtesy`,
`substantive_argument_or_principle`, `civil_challenge_or_disagreement`,
`factual_or_historical_question`, `safe_wit_opportunity`,
`justified_safety_no_reply`, and `other_safe_conversational_contribution`.

The principal act determines the primary stratum. Social courtesy covers thanks, praise,
affection, sympathy, remembrance, greetings, simple support, good wishes, and celebration;
polite wording does not turn an argument, question, criticism, recommendation, or policy
position into a social act. Substantive agreement requires an actual reason, distinction,
or principle. Civil disagreement is not bad-faith bait merely because it disagrees.
Analogy/recommendation requires a meaningful comparison, distinction, recommendation, or
standard. Political/moral proposition covers a genuine institutional, governing, moral, or
political principle not captured more narrowly. A factual/historical question requires a
materially checkable answer.

A safe wit opportunity requires a natural, contribution-specific, claim-free dry or wry
line based on a particular word, contrast, irony, or implication. It is not assigned for
grief, distress, abuse, serious unsupported allegations, or sensitive factual correction.
The sensitive-allegation tag is a warning and does not itself require silence.
`justified_safety_no_reply` is reserved for a retained safeguard—spam, incoherence, abuse,
clear bad-faith bait, context-proven repetition, dangerous amplification, wholly unrelated
material, or inability to produce a safe, relevant, original response. The residual safe
category is not a substitute for uncertainty.

The social Boolean is true exactly when the social tag and social primary stratum are both
present. The wit Boolean is true exactly when the wit tag is present. The safety-no-reply
Boolean is true exactly when the corresponding tag and primary stratum are both present.

## Two-pass consistency review

After all individual drafts exist, the reviewer:

1. re-reads every context decision in reverse candidate-ID order;
2. re-reads every completed semantic decision in forward candidate-ID order;
3. checks similar cases for consistent use of the definitions;
4. makes no change merely to improve proposed coverage;
5. records each substantive first-pass/second-pass disagreement in
   `adjudication_conflicts.jsonl`;
6. resolves a disagreement only from permitted case material; and
7. retains `requires_adjudication` wherever uncertainty remains.

An empty conflict file means that the two passes produced no substantive disagreement; it
does not waive either pass.

The context prepare stage also creates a blank `review_attestation.json`. After actually
performing the semantic first pass and both consistency passes, the Codex reviewer records
the controlled pass-order, consistency, coverage-blindness, and no-coverage-driven-change
attestations there with a real UTC timestamp. Finalisation refuses blank or malformed
attestations. These are explicit reviewer attestations, not behavior inferred by the tool.
Every nonempty conflict record uses controlled phase-specific first- and second-pass objects;
its resolution and unresolved flag must match the final context or semantic receipt exactly.

## Receipts, coverage, and continuity

Each receipt hash is SHA-256 over compact, sorted-key, UTF-8 JSON with
`ensure_ascii=false`, excluding only its own `receipt_sha256` field. Context and semantic
CSVs are canonicalised with those receipt hashes only after the entered decisions validate.

Coverage is computed after individual decisions are complete. It reports context paths,
semantic statuses, multi-label tags, primary strata, the three special subsets, lane,
clearance path, and exact-text cluster. It never uses retrospective outcome as a semantic
count.

`formulaic_substantive_posted` is not an incoming-contribution semantic category and is not
assigned here. Its status is `not_adjudicated_in_semantic_phase`, its coverage value is
`null`, and a separate profile-blind historical-baseline review remains required. This
continuity property is unresolved, not absent.

Independent review is a permanent blocker at this stage. The unperformed historical
baseline review is another permanent blocker. Context or semantic uncertainty and any
unresolved conflict add blockers. Consequently final sample selection, replay-pack
construction, every seed or key creation, and readiness to freeze a sample remain false
regardless of proposed coverage.
