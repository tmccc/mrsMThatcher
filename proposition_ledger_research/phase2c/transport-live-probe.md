# Proposition-ledger Phase 2C transport live probe

## Purpose and boundary

This is a six-call, wholly synthetic smoke test of the Phase 2B exact-evidence transport. It tests whether both retained xAI profiles can return `exact_text` and `occurrence_index` while deterministic local code supplies `turn_id`, `start_char`, and `end_char`. It covers exact Unicode formatting, CRLF, a tab, consecutive spaces, a repeated phrase at occurrence index 1, overlapping matches at occurrence index 1, and a two-turn incremental ledger chain.

The probe is not a model-quality comparison, a development-pilot rerun, or a production experiment. It uses no real conversation, private conversation run, held-out data, or production input, and it does not select a winner.

## Tracked inputs and prompt

The probe uses the unchanged Phase 2B semantic schema, transport schema, system prompt, manifest, resolver, materialiser, and xAI provider preflight. Their established contract hashes are checked against `phase2b/transport-contract-manifest.json`; this probe does not re-audit or redesign them.

The exact Phase 2B system prompt is followed by `synthetic-probe-addendum.txt`. The conversational user payload is derived from `synthetic-cases.json`, but every `hidden_local_expectation` object is removed before message construction. Hidden exact-text and occurrence-index expectations are available only to deterministic post-response validation. The complete schema is absent from both conversational messages and is supplied through `response_format` only.

## Provider contract and call order

Both profiles use `reasoning_effort=low`, at most 4096 visible output tokens, `store_messages=false`, `tools=[]`, omitted `tool_choice`, non-streaming sampling, and no search or code execution. Application and SDK retries are disabled. There are no retry, repair, or fallback calls.

The fixed provider-call order is:

1. `format-chain` turn 0 — `grok-4.3`
2. `format-chain` turn 0 — `grok-4.6`
3. `format-chain` turn 1 — `grok-4.6`
4. `format-chain` turn 1 — `grok-4.3`
5. `overlap` turn 0 — `grok-4.3`
6. `overlap` turn 0 — `grok-4.6`

Each turn-1 request is constructed from only that model's validated and materialised turn-0 ledger. A failed turn-0 call blocks only the dependent turn-1 entry for the same model; the other model and both independent overlap entries continue.

## Prepare, run, and verify

`--prepare` is local-only. It validates the cases and established hashes, constructs the six bounded request representations, proves that the full schema is absent from conversational messages and present in `response_format`, creates the mode-0700 private output directory, and writes a six-entry `call-log.json`. It makes no provider call.

`--run` requires the exact acknowledgement `--confirm-calls 6`; every other value is refused. Immediately before a provider invocation, the corresponding log entry is atomically changed from `planned` to `attempted` and persisted. A returned observation finishes as `completed` or `failed`; an unsent dependent entry may finish as `blocked`. Only `planned` entries may be invoked. An `attempted`, `completed`, `failed`, or `blocked` entry is never repeated, including after interruption.

`--verify` requires no API key and creates no provider client. It strictly reparses each saved raw response, repeats the complete deterministic validation and materialisation chain, and requires the regenerated canonical deltas, ledgers, validation results, and aggregate summary to match the saved artefacts. It does not alter provider-call counts.

## Validation chain

Each received response crosses these stages in order:

1. strict UTF-8 JSON parsing with no extraction or repair;
2. validation against the provider transport schema;
3. deterministic exact-evidence resolution into canonical turn IDs and Unicode code-point offsets;
4. validation against the canonical semantic-delta schema;
5. semantic-reference validation;
6. deterministic materialisation;
7. persisted-ledger validation, including predecessor binding and self-hash; and
8. the hidden local case expectation.

The hidden check requires the expected `exact_text` and occurrence index, an exact resolved substring slice, at least one evidence-bearing semantic record, and `extraction_status=complete` without abstention. It also confirms three overlapping `aa` matches in `aaaa`, with index 1 resolving the middle match. Server acceptance, structural validity, and hidden-expectation validity are recorded separately. No malformed response is repaired and no second response is requested.

## Private artefacts

The private directory and all subdirectories use mode 0700; generated files use mode 0600. Each attempted call may contain only:

- `raw-response.txt`;
- `parsed-transport.json`, when parseable;
- `resolved-canonical-delta.json`, when valid;
- `materialised-ledger.json`, when valid;
- `validation.json`; and
- `usage.json`.

The run root contains only `call-log.json`, `result-summary.json`, `SHA256SUMS`, and the six call directories. `SHA256SUMS` inventories every generated file other than itself using sorted relative paths. Credentials, request headers, client objects, and hidden reasoning are never stored.

## Freeze and scope

All harness, case, prompt-addendum, test, and protocol changes are committed and pushed before the first provider call. The live command is executed once for the prepared private directory. Verification and checksum checking follow that single run; aggregate results alone are then added to the repository and the external report.

No existing Phase 2B file or production file is modified. The probe does not access the main production worktree, the Phase 2A private conversation run, or any held-out or unexposed data. It creates no extra audit, inventory, environment, review pack, or planning document. Nothing is merged or deployed, and work ends after this six-call synthetic probe and its result report.
