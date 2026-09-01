# Proposition ledger Phase 1.4 xAI live-schema probe

## Purpose and boundary

This corrected Phase 1.4 run is a two-call, wholly synthetic live-server schema-acceptance probe. It asks whether the live xAI service accepts the exact provider-facing proposition-ledger schema proved locally compatible in Phase 1.3 and whether each returned observation crosses every mandatory local validation and materialisation boundary. The correction-run report separately discloses the two calls from the malformed-envelope run and the five bounded diagnostic calls that established the correction.

This phase is not a development pilot, corpus experiment, effectiveness test, power analysis, or substantive model comparison. It does not select a winner. A successful result does not establish proposition-ledger effectiveness, and semantic-smoke success is not a comparative model score. A failure of either single synthetic response does not show that the ledger concept is unsound.

The historical corpus, all real conversations and identities, every held-out candidate, and the two sealed clean prefixes remain outside scope and sealed. A later development pilot still requires a separately frozen protocol.

## Schema identities

The unchanged canonical authority is `proposition-ledger-semantic-delta-v1.1.0`, SHA-256 `eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a`. The corrected provider-facing schema SHA-256 is `37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21`. It is the Phase 1.3 pure copy with five explicit `additionalProperties: true` insertions and 14 proved outer-anchor removals, for 19 transformations in total.

Provider enforcement of the conditional construct remains documented best effort, and no provider guarantee was captured for the 17 `uniqueItems` occurrences. Mandatory canonical postvalidation is therefore unchanged even if the live server accepts the provider schema.

## Frozen synthetic input

The sole target is the tracked case in `synthetic-live-probe-case.json`. It contains only stable synthetic identifiers, one contributor turn at index zero, and the exact visible text `The lamp is on.`. Its evidence boundary is calculated from the exact Unicode string: start 0, end 15, exact substring `The lamp is on.`.

The trusted materialisation inputs are also synthetic. There is no previous ledger, the parent and post identifiers are null, the current participant is `participant-contributor-1` with role `contributor`, and the genesis context contains only the exact participant descriptor and bounded synthetic source-completeness metadata.

The semantic instruction requires exactly one directly evidenced positive descriptive proposition, with the current speaker committed and no third-party attribution. Every other semantic-delta collection is empty. It forbids issues, obligations, relations, answer targets, repairs, warnings, unsupported inferences, motives, causes, dates, background facts, and all information beyond the supplied turn. It does not contain a complete expected JSON response.

## Deterministic prompt construction

The system message is the exact UTF-8 content of `synthetic-live-probe-system-prompt.txt`, including its final line feed. The user payload is the canonical compact JSON encoding of the parsed tracked case: keys sorted recursively, UTF-8 without ASCII escaping, no insignificant whitespace, and no non-finite numbers. The message array contains exactly that system message followed by that user payload.

The system-prompt hash covers the exact tracked prompt bytes. The user-payload hash covers the canonical compact payload bytes. The complete-message-array hash covers the canonical compact JSON encoding of the two role/content objects. Both profiles use byte-identical message arrays. Hashes are recorded by the prepared private run rather than copied manually into this document.

## Frozen provider profiles and call limit

The ordered profiles are:

1. `xai-grok-4.3-low-ledger-v1`: xAI `grok-4.3`, reasoning effort `low`.
2. `xai-grok-4.6-low-ledger-v1`: xAI `grok-4.6`, reasoning effort `low`.

Both use `xai-sdk==1.19.0`, the same corrected provider schema, messages, maximum visible-output token budget of 4096, and `store_messages=false`. Tools are empty and the `tool_choice` parameter is omitted, which is the provider-compatible representation of no possible tool choice when no tools exist. Parallel tool calls are disabled, search parameters are absent, code execution and streaming are disabled, and no sampling parameter is added. There is no fallback and no retry. The correction-run provider-call budget is exactly two in total and one attempt per model.

The omission is an explicit correction to the first live attempt. Sending `tool_choice="none"` together with an empty tools list caused xAI to reject the request envelope before evaluating the schema. Local construction now fails closed if a zero-tool request contains a `tool_choice` protobuf field.

The calls run in the listed order. A definite first result permits the second call, including a definite schema or model-specific rejection. An uncertain send or a global authentication, authorisation, billing, quota, rate-limit, DNS, or transport failure stops execution. Server schema acceptance and local response validity remain separate findings.

## Validation and persistence boundary

Every received response is preserved as exact private bytes and processed in this order:

1. strict UTF-8 JSON parsing with duplicate-member, non-finite-number, code-fence, trailing-content, and multiple-value rejection;
2. diagnostic validation against the exact provider-facing schema using xAI full-string pattern semantics;
3. authoritative validation against the unchanged canonical semantic-delta schema using the Phase 1.3 intended ECMA-consistent pattern semantics, with ordinary Python-jsonschema behaviour retained only as a diagnostic;
4. exact conversation, turn, predecessor, participant, and genesis binding validation;
5. exact current-turn evidence-span validation;
6. deterministic turn-zero materialisation from no previous ledger and trusted synthetic metadata; and
7. full persisted-ledger validation followed by the separate synthetic semantic-smoke invariants.

The provider never controls persistent identifiers, participant registration, ledger or predecessor hashes, state patches, or source paths. The canonical schema and persisted-ledger validator remain authoritative.

Determinism is tested by processing the same saved response bytes twice, never by requesting another response. Verify-only mode cannot create a provider transport, does not require a credential, preserves the call ledger and counts, repeats the full local processing chain, and verifies the private checksums.

The corrected verifier is intentionally bound to correction-run request-contract revision `phase1.4-no-tools-omit-tool-choice-v2`. The immutable malformed-envelope run remains auditable with the original tool committed at `39bb70b6f7974623ce13d2fcbf53331e5acbdffc`; the correction does not rewrite that earlier evidence.

## Authorisation limits

Regardless of outcome, `development_pilot_authorised`, `real_corpus_use_authorised`, `held_out_use_authorised`, and `production_integration_authorised` remain false. Phase 1.4 authorises no OpenAI or X call, model-list request, search, tool execution, service action, merge, deployment, real ledger, real summary, or reply decision. This correction run ends after its two planned synthetic xAI inference attempts and their deterministic verification.
