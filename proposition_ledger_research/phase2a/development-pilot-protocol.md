# Phase 2A proposition-ledger development-pilot protocol

## Purpose and boundary

Phase 2A is a bounded, development-only, paired incremental-ledger
construction pilot over already exposed historical conversations. It asks
whether two frozen xAI profiles can construct canonically valid, evidence-bound,
incremental proposition ledgers reliably enough to support later blinded human
review. This is the first real-conversation generation stage; it is not another
schema-preflight stage.

The two profiles are one paired comparison:

| Profile ID | Model | Reasoning effort |
|---|---|---|
| `xai-grok-4.3-low-ledger-v1` | `grok-4.3` | `low` |
| `xai-grok-4.6-low-ledger-v1` | `grok-4.6` | `low` |

This pilot does not evaluate held-out cases, estimate ledger effectiveness,
select a model winner, revise the prompt, run the downstream
transcript/summary/ledger/human-ledger four-arm experiment, or authorise
production use. It ends with an unscored, blinded development review pack.

The immutable result flags remain false regardless of operational success:

- `ledger_effectiveness_established`
- `model_profile_selected`
- `downstream_four_arm_pilot_authorised`
- `held_out_evaluation_authorised`
- `production_integration_authorised`

The governing development-boundary policies are:

- `development_stability_policy_version=frozen-open-prefix-development-exception-v1`;
- `held_out_seal_policy_version=substantive-content-and-experimental-exposure-seal-v2`;
- `selection_policy_version=phase2a-development-selection-v2`;
- `protected_metadata_administrative_scan_permitted=true`;
- `protected_content_access_forbidden=true`;
- `exposed_only_sidecar_required=true`; and
- `maximum_open_prefix_exceptions=1`.

## Frozen contracts and environment

The canonical provider-response authority is
`proposition-ledger-semantic-delta-v1.1.0`, SHA-256
`eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a`.
The xAI-facing transformed schema has SHA-256
`37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21`.
Its 19 proved transformations are five explicit
`additionalProperties: true` insertions and 14 redundant outer-regex-anchor
removals. Provider-schema acceptance never replaces intended canonical
post-validation.

Deterministic materialisation uses
`tools/proposition_ledger_semantic_delta.py`; the durable output remains
`proposition-ledger-v1.0.0`. The pinned environment is CPython 3.10.12,
`xai-sdk==1.19.0`, `pydantic==2.13.5`, `protobuf==6.33.6`,
`grpcio==1.83.1`, and `jsonschema==4.26.0`. The Phase 1.3 dependency lock and
installed-source checks are authoritative. No package may be installed,
upgraded, or downgraded for this pilot.

Both profiles use the exact same system prompt, provider-facing schema,
current-turn payload format, output-token limit, timeout, tools and search
settings, validation sequence, materialisation implementation, and failure
policy. The only profile difference is model identity. Frozen request settings
are:

- maximum visible output tokens: 4096;
- client timeout: 300 seconds;
- application retries: zero;
- SDK/gRPC retries: disabled with `grpc.enable_retries=0` and empty service
  configuration;
- `store_messages=false`;
- tools empty and parallel tool calls disabled;
- `tool_choice` omitted, never set to `"none"`, under request-contract revision
  `phase1.4-no-tools-omit-tool-choice-v2`;
- web search, X search, code execution, and streaming disabled;
- no fallback model; and
- temperature, top-p, seed, and all other unfrozen sampling controls unset.

## Credential boundary

`XAI_API_KEY` is the only authorised credential. Before opening or preparing
real development cases, the harness checks only that it is present and
non-empty. It never prints, logs, hashes, measures, serialises, discloses, or
writes the value, and never places it in a file or subprocess argument. It does
not read `.env` files, source shell profiles, search the home directory, or
read production configuration. Unrelated provider credentials are removed from
the live-pilot subprocess environment.

If the key is absent, preparation stops before real-case access with
`phase2a_not_run_missing_xai_api_key`. Import, `--help`,
`--build-review-pack`, `--verify-only`, and every test require no credential and
cannot construct a provider transport. All non-live modes make zero provider
calls.

## Corpus and substantive-content seal

Only the named frozen Phase 1/1.2 research corpora, calibration pack,
multi-turn audit, benchmark, and prospective-v4 batch at cutoff
`2026-08-31T14:03:05Z` are in scope. The frozen source-manifest SHA-256 is
`b42ce8f0ead2961f04e81e041c51455a41c64a7830854a7ec080e593ae7289ec`;
the prospective batch-manifest SHA-256 is
`a28add4ce6b019e56c5bd9ec63bed217c4d5405c0bd3bd48c5ee814e506a339f`.
The moving prospective link, later batches, production logs, ZFS snapshots,
newly mined conversations, and X queries are forbidden.

The held-out seal policy is
`substantive-content-and-experimental-exposure-seal-v2`. It permits a
deterministic administrative scan of metadata-only eligibility rows for row
hash verification, exposure classification, exclusion enforcement, aggregate
counting, and construction of an exposed-only development sidecar. Such a scan
is not substantive experimental exposure. It may inspect only reconstruction
grade, exposure status, preliminary held-out eligibility, stability status,
target sequence class, author-identity status, source family, and opaque
conversation and target keys. It may validate a row's canonical hash.

Before any development transcript is loaded, `--prepare` writes the private
`exposed-development-candidate-index.jsonl`. For every source metadata row it
classifies protection before any transcript or private source mapping can be
dereferenced. A row is protected if any of the following is true:

- `preliminary_within_family_held_out_eligibility == true`;
- `effective_exposure_status == "genuinely_unexposed"`;
- `conversation_exposure_status == "genuinely_unexposed"`;
- `target_exposure_status == "genuinely_unexposed"`; or
- its effective exposure status is `structurally_mined_only`; or
- its only exposure category is `structurally_mined_only`.

For a protected row, the builder increments aggregate counters but does not
render its opaque identifiers, emit it to the sidecar, open transcript
content, or open a source-ID mapping. Selection and transcript loading consume
only the completed exposed-only sidecar and never rescan the complete target
index. A sidecar row contains only the minimum private metadata required for
deterministic development selection.

An eligible case must have consistent, affirmative evidence of direct exposure
through at least one of `development_labelled`, `prior_human_review`,
`calibration`, `prior_model_experiment`, `report_excerpt`, or
`current_manual_incident_review`. Absent, inconsistent, or ambiguous exposure
evidence fails closed.

Protected transcript text, quoted conversation text, historical replies,
case-specific semantic annotations, protected-case stressor interpretation,
raw contributor identity, protected source-ID mappings, provider payloads and
outputs, generated ledgers, human-review material, and prompt-tuning examples
remain forbidden. Metadata administration does not reclassify or retire a
protected case.

The private `held-out-content-seal-audit.json` records policy version; source
and protected metadata rows scanned; protected identifiers rendered;
protected rows emitted; protected transcript records and bytes read;
protected source mappings opened; protected provider payload, model output,
and human-review counts; exposed rows emitted; each exclusion count; and
whether the substantive seal was breached. It must establish:

- protected metadata identifiers rendered to the operator: zero;
- protected rows written to the development sidecar: zero;
- protected transcript records opened and text bytes read: zero;
- protected source-mapping records opened: zero;
- protected provider payloads, model outputs, and human-review records: zero;
  and
- `substantive_seal_breached=false`.

The protected-metadata scan count is expected to be non-zero and is reported
honestly. The audit separately acknowledges the pre-resumption administrative
scan: it records the named early-stop report, a known minimum of two protected
metadata rows, unknown prior genuinely-unexposed metadata-row count, zero
known protected transcript bytes, provider payloads, and model outputs, and
`prior_substantive_seal_breached=false`. It does not invent an exact historical
count that was not retained.

Any protected substantive access sets `substantive_seal_breached=true` and
blocks protocol freeze. Protected rows cannot enter the call plan or review
pack, and metadata processing never changes their exposure classification.

The two clean persistent prefixes remain substantively sealed. Their aggregate
count may be stated only in sample-planning limitations.

## Deterministic development selection

Selection policy and algorithm `phase2a-development-selection-v2` choose exactly
eight independent conversations and between 24 and 32 distinct transcript
turns across their complete selected prefixes. Each conversation contributes
one deepest development target. Its complete, exact, untruncated ancestor
prefix is replayed; overlapping shorter prefixes from that conversation are
not rerun. If eight suitable conversations cannot fit within 32 turns, the
pilot stops before provider calls and reports the exact structural limitation.

Every conversation and included turn must be Grade A. Target text and ancestor
links must be exact, publication identity must be confirmed or observed where
required, and target-author identity must be available and non-conflicting.
By default, a conversation must be frozen historical or quiescent at the
cutoff.

Stability policy `frozen-open-prefix-development-exception-v1` permits at most
one `frozen_open_prefix_development_exception`. It is restricted to the sole
pre-labelled, directly exposed calibration case supplying the otherwise
unavailable compound-allegation and account-clarification stressors, including
the already exposed Greater London Council development case when eligible. It
may be selected only if all of the following hold:

1. conversation and target are directly exposed;
2. the target and every included turn are Grade A;
3. exact text exists for every prefix turn;
4. every ancestor relationship is exact and turn order is unambiguous;
5. the entire selected prefix existed by the frozen cutoff;
6. target author identity is exact, available, and non-conflicting;
7. the target is neither genuinely unexposed, preliminarily held out, nor
   structurally mined-only;
8. the required stressor labels pre-date Phase 2A;
9. no stable directly exposed substitute carries the same required compound
   allegation label;
10. no later or post-cutoff turn is included or used for ledger construction,
    diagnosis, or scoring; and
11. no other selected conversation uses the exception.

The source stability remains `open_at_frozen_cutoff`; the protocol never calls
the conversation quiescent. The manifest records
`selected_prefix_immutability_status=frozen_at_cutoff`,
`development_stability_exception=true`,
`development_stability_exception_policy=frozen-open-prefix-development-exception-v1`,
`development_stability_exception_reason=sole_prelabelled_stressor_case_without_stable_substitute`,
`post_target_extension_withheld=true`, and
`post_cutoff_extension_withheld=true`. The eligible record is located from the
exposed-only sidecar and frozen calibration tags, without hard-coding a real
identifier or semantically mining unlabelled transcripts.

No future turn is included for any case. A historical reply after the target,
if one exists, is withheld. Production decisions, labels, outcomes, and
reply/no-reply state are withheld.

The eight stressor slots are filled in this fixed order:

1. existence or availability versus security, durability, or independence;
2. a valid distinction previously ignored or answered through a weaker nearby
   proposition;
3. a still-live counterfactual or conditional issue;
4. a compound allegation whose conduct, causation, outcome, or motive claims
   need separate representation where present;
5. an explicit correction, qualification, or “not X but Y” relation;
6. an account clarification followed by an apparent contributor answer,
   including the exposed Greater London Council calibration case when its
   record is available and Grade A;
7. a rhetorical or expressive contribution for which `no_stable_issue` is a
   legitimate possibility; and
8. a healthy sustained debate, partial concession, multilingual exchange, or
   other control without a known proposition-substitution defect.

A conversation may carry several tags, and the single permitted open-prefix
case may satisfy both stressors 4 and 6, but the pilot still selects eight
independent conversations. The fixed-order coverage pass reuses an already
selected case when it carries another frozen stressor tag; after all eight
families are covered, the same ranking fills any remaining conversation slots
to eight. Ties are resolved, in order, by complete Grade-A status, direct
exposure strength, greater relevant depth, contributor-group diversity,
source-family diversity, then stable calibration-case identifier in lexical
order. Direct exposure strength is the count of independently evidenced
allowed exposure categories, followed by a fixed boolean vector in the listed
category order. The selection is order-independent and completes before any
Phase 2A model output exists.

No comparable contributor group supplies more than two conversations. The
selection uses at least six comparable groups where the eligible pool permits
it and includes both benchmark and prospective-v4 families where feasible.

## Private case material and identifiers

The private case manifest records its version; frozen source hashes; selection
policy and algorithm,
stability, and seal policy versions; eight pilot cases; private source
provenance; exposure evidence; reconstruction, conversation-stability, and
selected-prefix-immutability status; stressor tags; source family;
contributor-group pseudonym; selected target; ordered ancestors; exact prefix;
exception use and reason; withheld-later-turn, post-target-extension, and
post-cutoff-extension status; withheld-production-outcome status; unique-turn
count; and row and manifest SHA-256 values.

Raw contributor IDs are forbidden. A separate mode-0600 private mapping holds
the source-to-pilot mapping. Provider inputs use only identifiers shaped as
`dev-conversation-...`, `dev-turn-...`, `participant-account`, and
`participant-contributor-...`. The provider never receives real X post IDs,
numeric contributor IDs, local paths, production state, exposure evidence, or
audit labels. Exact visible public conversation text is preserved byte for
byte so Unicode evidence offsets remain meaningful.

The private run and every subdirectory use mode 0700; generated private files
use mode 0600. Symlinks are forbidden inside the private run. Each artefact is
labelled as a frozen input, provider observation, deterministic derivation, or
human-review template as applicable.

## Protocol freeze and immutability

`--prepare` first builds the exposed-only sidecar and substantive-seal audit,
then selects cases exclusively from that sidecar, assigns pilot-local
identifiers, writes the private manifest, and creates an immutable call plan
without contacting xAI. The exact planned call count is twice the selected
unique-turn count, hence 48–64 and never above the maximum budget of 64.

Before the first call, the tracked protocol, prompt, rubric, schemas, harness,
and tests are committed and pushed. `protocol-freeze.json` binds the source
commit; canonical, provider-schema, prompt, rubric, exposed-sidecar,
content-seal-audit, private-manifest, and call-plan hashes; both profiles;
pinned environment; request revision; selection, stability, and seal policy
versions; administrative-scan and substantive-access rules; frozen counts,
open-prefix exception count, and call budget; model-order seed/hash; output
limit; timeout; retry policy; validation sequence; diagnostics; and declared
non-goals. The private copy is checksum-bound to the same freeze.

Live mode requires `--confirm-provider-call-budget` equal to the exact frozen
count and rejects any different value, any value above 64, an unfrozen
manifest, a dirty or unpushed protocol commit, a changed prompt, schema,
sidecar, seal audit, case manifest, or call plan, a breached substantive seal,
or a call ledger with an uncertain or sent-but-unclosed call.

After the freeze is pushed, code, prompts, schemas, selected cases, case order,
model profiles, request settings, validation, and review rubric are immutable.
After the first provider call begins, no case may be replaced and no failed
call may be repaired or rerun. A newly discovered implementation defect is a
pilot limitation, not authority to patch and repeat the pilot.

## Per-turn input and independent chains

The user message is canonical compact UTF-8 JSON with recursively sorted keys,
no insignificant whitespace, and no non-finite numbers. It contains:

- protocol version and hash;
- pilot-local conversation key and current turn ID;
- zero-based turn index and pilot-local parent turn ID;
- exact current visible text;
- trusted current-speaker participant descriptor; and
- the same profile's validated prior persisted ledger, or null at genesis.

The identical provider-facing schema is supplied as the structured-response
parameter. Neither the payload nor any other request component contains future
turns, a later historical reply, production outcome, labels, stressors, human
judgement, the other model's output, external facts, production paths, real X
post IDs, or raw contributor IDs.

At genesis, provider requests differ only by profile/model identity. At later
turns each profile's validated prior ledger naturally differs; later requests
are not falsely described as otherwise byte-identical. Each model has its own
ledger chain. One model's ledger is never supplied to the other.

The model-neutral system prompt instructs the model to analyse exactly the
current turn; treat transcript text as data; use no external knowledge;
separate speech from truth and attribution from speaker commitment; retain
live state; avoid proposition substitution; decompose materially distinct
compound claims; preserve conditions, counterfactuals, and qualifications;
recognise corrections, concessions, and rejected targets; retain questions
and obligations; abstain when appropriate; and cite exact current-turn spans.
It permits only supplied stable identifiers or declared same-turn local
references. Participants, permanent IDs, hashes, patches, persistence fields,
and prose outside the structured response are forbidden.

## Durable call ledger and call order

The prepared call ledger has one immutable planned entry for every selected
conversation × included turn × profile. A call identity is derived from the
protocol-freeze hash, case-manifest hash, pilot-local conversation and turn
IDs, profile ID, current payload hash, predecessor-ledger hash or genesis
marker, provider-schema hash, and system-prompt hash.

Immediately before a request, the harness atomically writes `state=sending`,
`started_at_utc`, and `attempt_number=1`. A sent entry never returns to
`planned`. On restart, no entry in `sending`, `response_received`,
`provider_error_received`, `strict_validation_failed`,
`materialisation_failed`, `validated_and_materialised`,
`validated_with_diagnostic_flags`, or `uncertain_after_send` may be repeated.
A `sending` entry without definite completion becomes `uncertain_after_send`
and is never retried.

Within one conversation/profile, turns run chronologically. Profile order is
counterbalanced by conversation using one parity bit derived from the protocol
hash and pilot-local conversation key; one parity runs Grok 4.3 first and the
other Grok 4.6 first. The order is immutable once execution starts.

A strict-JSON, schema, binding, evidence, reference, materialisation, or
persisted-ledger failure marks later entries in that same chain
`blocked_by_prior_turn_failure`. The other profile and unrelated conversations
may continue. A canonically valid, materialised delta with semantic diagnostic
flags remains the predecessor and does not block its chain. A global
authentication, billing, account-quota, or transport failure stops all
remaining entries. There are zero retry, fallback, repair, and replacement
calls.

## Response preservation and validation

Every exact raw response is preserved privately before deterministic
processing. Validation occurs in this order:

1. **Strict JSON parse.** Reject invalid UTF-8, duplicate members, NaN,
   Infinity, multiple top-level values, code fences, leading/trailing prose,
   and extracted or repaired substrings.
2. **Provider-facing schema.** Validate against the exact frozen provider
   schema.
3. **Intended canonical schema.** Validate against the unchanged canonical
   schema and corrected ECMA-consistent intended-pattern layer. Ordinary
   Python-jsonschema regex behaviour is diagnostic only.
4. **Binding.** Require exact conversation key, target turn ID, turn index, and
   predecessor reference.
5. **Evidence.** Every new span names the exact current pilot-local turn, uses
   valid Unicode character offsets, stays within the turn, and reproduces the
   exact substring.
6. **Semantic references.** Reject unknown existing IDs, wrong namespaces,
   duplicate local references, future references, provider-created
   participants or permanent IDs, and illegal lifecycle transitions.
7. **Deterministic materialisation.** The harness alone registers a first-seen
   speaker and assigns permanent IDs, transition fields, state patch,
   predecessor hash, ledger hash, and persisted ledger.
8. **Persisted-ledger validation.** Validate the snapshot through the existing
   full or incremental `proposition-ledger-v1.0.0` path.

A failed result is never repaired, extracted, replaced, or resampled.
`--verify-only` starts no transport, requires no key, reprocesses every raw
response from saved bytes, regenerates parsed, validation, diagnostic, and
materialised artefacts, compares them byte for byte, reconciles states and
counts, verifies checksums and exclusions, and verifies every frozen hash.

## Development diagnostics

After canonical validation and successful materialisation, deterministic
diagnostics record, per turn:

- proposition and proposition-group additions and updates;
- live and closed issue counts;
- commitment and obligation changes;
- relation additions by type;
- answer-target, rejected-target, and repair changes;
- abstentions, warnings, and unsupported-inference rejections;
- canonical ledger bytes, payload bytes, and state-growth delta;
- evidence-span count and coverage of current text;
- unresolved/resolved counts and lifecycle transitions;
- duplicate semantic objects; and
- current-turn semantic density.

The stressor-facing structural signals are: live proposition retained; nearby
proposition represented separately; compound components separately represented;
counterfactual remains live; correction relation represented; rejected answer
target represented; open question or obligation retained; `no_stable_issue`
used; and attribution separated from speaker commitment. These are structural
observations, not human gold, and the presence of a required object type does
not establish semantic correctness.

Only after the pushed protocol freeze is immutable, the saved Phase 1.4 Grok
4.3 validation and parsed-response artefacts may be inspected sufficiently to
record the exact pre-existing failed semantic-smoke invariant under
`phase1_4_grok_4_3_carry_forward_diagnostic`. The raw response is not used to
tune this prompt or schema. Recurrence is reported as
`phase1_4_failure_pattern_observed_again`,
`phase1_4_failure_pattern_not_observed`, or
`phase1_4_failure_pattern_not_assessable`; recurrence alone never selects a
profile.

## Mechanical reporting

Per profile, report planned and attempted calls, definite responses, provider
errors, uncertain calls, success counts/rates at every validation stage,
materialisation and persisted-ledger success, complete conversation chains,
blocked downstream turns, diagnostic flags, maximum and median ledger size,
average state growth, prompt/cached/reasoning/completion/total tokens, raw
provider cost, and median/p95/maximum latency. Preserve
`cost_in_usd_ticks` as a raw field unless retained official metadata supplies
an authoritative conversion.

Paired reporting counts conversations where both profiles completed, only
Grok 4.3 completed, only Grok 4.6 completed, or neither completed; reports
whether both or one reached the deepest target; and compares structural
signals, ledger size, tokens, and latency. Mechanical reliability is not a
semantic winner.

Evidence derives one top-level disposition:

- `phase2a_development_pilot_completed_review_pending`;
- `phase2a_development_pilot_completed_with_profile_attrition_review_pending`;
- `phase2a_development_pilot_blocked_before_calls`;
- `phase2a_development_pilot_inconclusive_operational_failure`; or
- `phase2a_development_pilot_uncertain_after_send`.

## Blinded review pack

Offline review-pack construction includes every conversation whose deepest
target was reached by at least one profile. Each case contains the exact
prefix, reconstruction grade, turn boundaries, a concise deterministic
projection of every available final ledger, turn-by-turn change summaries,
validation status, and an empty scoring form copied from the frozen rubric.

The two projections are called only `Ledger A` and `Ledger B`. Their order is
derived deterministically per case from the protocol-freeze hash. Model names,
profile IDs, provider usage, cost, latency, audit and stressor labels,
historical outcomes and later replies, call order, and model-specific filenames
are absent. The mapping is stored only in the separate private
`human-review-pack/unblinding.json`; it is absent from the review index and
aggregate report.

The rubric covers proposition completeness, unsupported inventions, speaker
and attribution accuracy, polarity and modality, commitments, compound
decomposition, issue state, open questions, obligations, corrections and
concessions, answer and rejected targets, live-proposition retention,
proposition substitution, ignored distinctions, incremental consistency,
overall preference, fatal defects, reviewer confidence, and notes. No score is
filled in during Phase 2A execution. This is exploratory blinded development
review, not the independent transcript-first Arm-D gold-annotation process.

## Development-only sample planning

Conversation is the primary independent unit and contributor group is a
clustering-sensitivity unit. Pilot operational attrition informs feasible
completion scenarios only. Scenario tables cover paired human-adjudicated
binary and ordinal outcomes at plausible absolute differences of 10, 15, and
20 percentage points; assumptions and sensitivity ranges are shown instead of
a single precise effect estimate.

Eight development conversations cannot establish effectiveness. The two
sealed clean prefixes are not a usable held-out evaluation and enter no
calculation beyond their aggregate count. No held-out split is selected.
Prospective collection must continue before a credible held-out result, and
human review of this pilot must precede profile selection or prompt revision.

## End condition

After the one authorised execution, the pack is built offline, saved responses
are reprocessed byte-identically, checksums and privacy exclusions are verified,
and only aggregate non-private results may be recorded in Git. Production,
services, timers, and the sealed corpus remain untouched. Nothing is merged or
deployed. Work stops with the unscored blinded review pack; human adjudication,
prompt revision, model selection, held-out evaluation, and the downstream
four-arm experiment do not begin.
