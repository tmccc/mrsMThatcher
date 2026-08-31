# Proposition ledger Phase 1.2 execution preflight

## Purpose and boundary

Phase 1.2 is a tightly bounded amendment to the standalone historical
proposition-ledger experiment. It closes four execution-preflight defects:

1. turn-zero materialisation and deterministic first-seen participant
   registration;
2. exact separation of account-root responses from genuine persistent
   multi-turn targets;
3. domain-qualified author-group exposure and an accurate cross-family
   identity limitation; and
4. an honest provider-schema compatibility gate which remains pending until a
   provider/model profile is frozen and locally validated.

This amendment does not redesign the ledger. It preserves the Phase 1.1
outcome-evidence taxonomy, the separation between semantic delta and durable
persistence, the transcript-first human-gold protocol, the compound-proposition
rules, and the existing privacy controls. It does not select a held-out set,
create a machine ledger or ordinary summary for a real conversation, perform
real annotation, generate a reply or reply/no-reply decision, make a provider
or X request, or begin Phase 2.

## Contract identities

The exact Phase 1.2 contract identities are:

- provider-facing semantic delta:
  `proposition-ledger-semantic-delta-v1.1.0`;
- deterministic materialiser:
  `proposition-ledger-semantic-delta-materialiser-v2`;
- experiment protocol: `proposition-ledger-experiment-v1.2.0`;
- private Phase 1.2 output: `proposition-ledger-phase1.2-output-v2`; and
- persisted full ledger: `proposition-ledger-v1.0.0`.

The persisted-ledger schema remains unchanged. Its authoritative `state_patch`
can already express an addition to the `participants` collection, including an
addition from empty state at genesis. No new persisted field or schema version
is required. The provider response still cannot contain participant records,
permanent participant or proposition identifiers, ledger or transition
identifiers, hashes, a state patch, source paths, or cumulative unchanged
state.

## 1. Genesis and first-seen participants

The semantic-delta schema now accepts `as_of_turn_index` zero and keeps
`prior_ledger_reference` required but nullable. The conditions are exact:

- at turn zero, `prior_ledger_reference` must be null; and
- at every later turn, it must be the strict predecessor ledger-reference
  object.

The materialiser has one semantic-application path for genesis and later
turns. At genesis it requires all of the following:

- no previous ledger;
- current turn index zero;
- a null current-turn parent;
- semantic turn index zero;
- a null semantic predecessor; and
- a bounded trusted `genesis_context` containing only the ledger-envelope and
  current-source metadata required to initialise the snapshot.

Deterministic code constructs the initial current-turn reference, initial
participant, source-completeness state, semantic state, null predecessor link,
authoritative patch from empty state, ledger ID, and ledger hash. The result
must pass the full persisted-ledger validator; genesis does not receive a
weaker validation path.

At every turn, the trusted harness supplies the exact pseudonymous current
participant descriptor. If the participant already exists, the descriptor
must agree with the stored record. If it is first seen, deterministic code
registers exactly that current speaker and records the addition in the
authoritative state patch. No future speaker is pre-seeded. A semantic
participant reference is valid only for a participant in the predecessor or
for the exact current speaker being registered. Raw contributor identities are
neither exposed nor persisted.

Later turns still require an existing immediate predecessor, the next
consecutive turn index, an exact parent among prior turns, and a semantic
predecessor reference matching the validated previous ledger. Null predecessor
semantics are forbidden after turn zero.

Materialiser readiness reports these behaviors independently:

- `genesis_materialisation_valid`;
- `first_seen_participant_registration_valid`; and
- `complete_incremental_chain_valid`.

The materialiser is not described as fully valid unless all three gates pass.

## 2. Exact target-sequence strata

Every structurally usable target receives exactly one
`target_sequence_class`, derived only from its exact parent-linked ancestor
chain:

- `initial_user_target`: the user target has no preceding ancestor turn;
- `pre_account_user_follow_up`: a preceding user turn exists but no preceding
  account turn exists;
- `account_root_response`: preceding account material exists, but there is no
  preceding user turn and no published or observed account turn whose exact
  parent is a user turn in the chain;
- `persistent_multiturn_target`: an earlier user turn is followed by a later
  published or observed account turn whose exact parent is that user turn, and
  the current user target occurs after that reply; or
- `other_sequence`: a structurally valid anomaly fitting none of the classes,
  accompanied by a bounded reason.

An account root is an account turn, but it is not an account reply.
`preceding_account_reply_count` therefore counts only published or observed
account turns which have an exact user parent in the ancestor chain.
`preceding_account_root_count`, `preceding_user_turn_count`, and
`preceding_account_turn_count` retain the distinct contextual counts.

`target_follows_prior_account_reply` is true only when the immediate
predecessor is a published or observed account turn which itself has an exact
user parent in the same chain. Drafts, unconfirmed account material, and
unparented account posts do not qualify.

`persistent_multiturn_evaluation_candidate` is true exactly for
`persistent_multiturn_target`. The retained
`multi_turn_evaluation_candidate` field is only a compatibility alias for that
same corrected boolean; it no longer includes account-root responses.
`account_root_response_control` identifies the separate contextual-control
stratum.

Only `persistent_multiturn_target` can enter the principal proposition-
persistence evaluation. Initial targets and pre-account follow-ups may support
ledger-construction or baseline analysis, and account-root responses may
support contextual first-response analysis, but none of them is persistent
multi-turn evidence.

## 3. Author-group exposure and identity limits

Within-family grouping uses the complete domain-qualified tuple
`(author_key_scheme, principal_author_key)`. It never groups on a pseudonym
alone and never infers identity from a username, display name, timing, content,
or semantic similarity. Identical pseudonym text in different key schemes
therefore remains in different identity domains.

The exact structurally assessed user target turn is now the sole authority for
target author identity. Its non-empty pseudonymous `author_key`, combined with
the source-family `author_key_scheme`, supplies the target tuple. The legacy
conversation-level `principal_author_key` remains source metadata for
compatibility, but is never a target-author fallback. A target whose exact turn
lacks a usable key is marked `target_author_identity_unavailable`; contradictory
`principal_author_key` metadata on any review candidate bound to the exact
conversation and target turn is marked `target_author_identity_conflicting`.
Both statuses prevent grouping and preliminary eligibility. Review-candidate
principal metadata can corroborate the exact turn, but cannot override it or
resolve absence by record order.

For frozen benchmark rows, the per-turn pseudonym is copied only from the
exact matching frozen benchmark canonical-post record before structural target
assessment. This author source is separate from the existing prospective
canonical-post authority used for quote/parent reconciliation. A missing or
contradictory exact-post pseudonym is never replaced by the benchmark
conversation principal.

Canonical conversations remain one row each. A separate private contributor-
exposure observation enumerates every distinct non-empty user-turn
`author_key` in each conversation, excluding account turns, and qualifies it
with the conversation's source-family scheme. Conversation-wide exposure is
carried to every represented contributor. Target-, branch-, and case-scoped
exposure is carried only to the contributor resolved from its exact retained
target identities; it is not assigned to another contributor merely because
they share a conversation. Ambiguous scoped evidence remains explicitly
unresolved and fails closed for every plausible affected contributor. Multiple
contributors in one conversation therefore form separate groups rather than a
conversation-level identity conflict.

For each comparable within-family group, Phase 1.2 records its opaque group
key, categories and bounded reasons, conversation and target counts, direct-
exposure, structurally-mined, and genuinely-unexposed membership flags, and
whether later splitting must be groupwise. The exposure statuses include:

- `clean_genuinely_unexposed_group`;
- `contains_direct_exposure`;
- `contains_structurally_mined_material`;
- `mixed_unexposed_and_structurally_mined`;
- `identity_group_unavailable`; and
- `identity_group_conflicting`.

Direct exposure in any comparable group member propagates to the entire group
for preliminary eligibility. This includes development labels, calibration,
prior model experiments, human review, report examples, the current manual
incident review, and any other direct exposure already represented by the
frozen registry. Structurally mined-only membership is not silently promoted
to direct human or model exposure. It does set
`author_group_requires_groupwise_split` and prevents a later phase from placing
members of that group in both development and held-out strata. Phase 1.2 does
not choose or assign the split.

The benchmark and prospective-v4 pseudonyms are not assumed to share an
identity domain. A common cross-family key may be created only when an
authoritative raw identity is privately available in both frozen source
families. In that case, the existing private 32-byte HMAC key is used with the
new explicit purpose string
`mrsMThatcher/proposition-ledger/phase1.2/cross-source-author-group/v1`.
Source family is deliberately absent from that HMAC input. The raw identity is
consumed only as private HMAC input and is never emitted, printed, reported, or
retained in generated output.

When authoritative identity is unavailable in both families, the
cross-family group key is null and
`cross_family_author_identity_status` is `unavailable`. Phase 1.2 does not
infer a match and does not claim cross-family author independence. The valid
claim is limited to conversation-level separation with whole-author grouping
inside each comparable within-family domain; cross-family author overlap
remains unresolvable.

Eligibility is reported separately:

- `preliminary_within_family_held_out_eligibility` requires Grade A, a frozen
  or quiescent stable conversation, a persistent multi-turn target, genuinely
  unexposed target and conversation material, no direct exposure elsewhere in
  the comparable within-family group, and no structural or outcome conflict;
  and
- `preliminary_cross_family_clean_held_out_eligibility` additionally requires
  an available non-conflicting cross-family identity and no direct exposure
  anywhere in that cross-family group.

An unavailable cross-family identity makes the stronger cross-family-clean
claim false or pending; it is never reported as passed. Any eventual Phase 2
split must remain whole-conversation and whole-author-group in every identity
domain actually available.

The corrected frozen-corpus reconciliation retained 155 canonical
conversations and produced 145 contributor observations. Conversation
contributor cardinality was zero for 37 conversations, one for 97, and multiple
for 21. All 219 usable targets had an available exact-turn identity; none was
unavailable. In total 39 target authors differ from the legacy conversation
principal. Review-candidate metadata yielded
31 agreements and zero target-author conflicts. Scoped evidence records were
exactly bound 164 times and retained unresolved 180 times; no first-contributor
resolution was used.

Across the canonical contributor-observation universe, comparable within-
family groups increased from 77 to 88; clean groups decreased from 29 to 27;
directly exposed groups increased from 33 to 39; groups containing structurally
mined material increased from 23 to 33; mixed structural/unexposed groups
increased from 12 to 18; and groups requiring a later groupwise split increased
from 35 to 65. The usable target rows represented 72 corrected groups rather
than 62 legacy groups. Preliminary
within-family eligibility changed from three prefixes across two conversations
and two contributor groups to two prefixes across one conversation and one
contributor group. Two of the former three prefixes retained their status, one
became ineligible, and none became newly eligible. This is aggregate audit
information only; no held-out list was selected or opened.

`author-binding-audit.json` records these text-free counts, exact crosstab
reconciliation, and the raw-identity-field privacy scan. The contributor and
crosstab addition is the only reason the private Phase 1.2 output contract is
now v2; `proposition-ledger-v1.0.0`, the semantic-delta contract and
materialiser, and the experiment schema remain unchanged. Cross-family
identity remains unavailable for all 155 conversations and all 219 target
rows; no cross-family match was fabricated.

All findings outside author binding remained unchanged: reconstruction grades
are 144 / 0 / 11; target outcomes are 188 published replies, 30 confirmed
pipeline-terminal no-replies, one confirmed local skip, and zero unknown,
unavailable, or conflicting outcomes; sequence counts are 62 initial targets,
one pre-account follow-up, 66 account-root responses, 90 persistent targets,
and zero other sequences. Persistent exposure remains 73 directly exposed, 11
structurally mined-only, and three genuinely unexposed stable Grade-A targets
before group checks. Genesis, participant registration, incremental
materialisation, ledger, provider-schema and privacy conclusions are unchanged.
Provider/model compatibility remains `pending_model_profile_selection`, with
zero provider or X calls.

## 4. Provider-schema compatibility gate

Draft 2020-12 validity is not evidence that an unspecified provider's
structured-output subset accepts the semantic schema. Phase 1.2 therefore
builds a deterministic provider-neutral feature inventory recording the
semantic schema version, exact hash and byte size, object and array nesting,
property and required-property counts, reference and combinator counts, enum
and const counts, nullable unions, closed-object occurrences, recursive
references, and unbounded arrays.

The compatibility record remains:

```json
{
  "status": "pending_model_profile_selection",
  "selected_provider": null,
  "selected_model": null,
  "provider_specific_validator_run": false,
  "provider_sdk_compilation_run": false,
  "network_call_made": false
}
```

It also binds the feature-inventory hash and states the next required action:
freeze the exact provider/model profile and SDK version, run that profile's
local schema compilation or dry-validation, record any transformation, and
verify that canonical and transformed semantics agree before the first paid or
unpaid provider call. Phase 1.2 selects no profile, imports no provider SDK,
adds no provider invocation code, and cannot report this gate as passed.

## Readiness semantics

Evidence-derived readiness includes the three materialiser gates, exhaustive
persistent-multi-turn classification, within-family author-group enforcement,
the cross-family identity status, provider feature-inventory validation,
provider compatibility status, separate within-family and cross-family-clean
candidate counts, and sample-size threshold status.

A failure of source identity, deterministic reconstruction, privacy, schema,
genesis, first-seen registration, incremental-chain validation, target
classification, or author-group enforcement is blocking. No remaining
within-family eligible persistent target is also blocking.

These two states are expected preflight limitations rather than implementation
failures:

- `provider_schema_compatibility_status =
  pending_model_profile_selection`; and
- `sample_size_threshold_status =
  pending_development_only_power_analysis`.

When all structural and deterministic gates pass, at least one within-family
eligible persistent target remains, and only provider compatibility and sample
size remain pending, the disposition is
`phase1_2_complete_sample_and_provider_preflight_pending`. Unavailable
cross-family identity is stated explicitly but does not by itself fail the
within-family preflight.

This disposition authorises no experiment. The precise next permitted work is
to freeze a provider/model/SDK profile, complete its local schema-compatibility
validation, and conduct development-only power analysis before any provider
call or held-out selection. Phase 1.2 makes no claim that the proposition
ledger improves reasoning.
