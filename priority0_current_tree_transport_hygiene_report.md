# Priority-0 current-tree transport hygiene report

## Scope

This change removes expired provider signing material from current-tree
historical-context transport records before the Priority-0 release-assurance
candidate is replayed. It does not change source identity, evidence admission,
quotation text, eligibility, source roles, public rendering, gate membership,
unresolved status, semantic-veto decisions, or runtime state.

The implementation baseline is production commit
`be882e8121a7b4348a57b61b1cf526401a36f5c0`.

## Corrected current-tree records

The deterministic migration changed only:

- one saved resolution `final_url` and one matching redirect-chain URL,
  deleting 12 transient query values across six signing-key names;
- seven rejected-source URLs in the saved OpenAI research record, deleting 37
  transient query values across ten signing-key names;
- the resolution policy version, from the legacy saved-resolution policy to
  the deletion-only transport-redaction policy;
- the source-role audit's two input hashes and the two transport-derived fields
  for the one resolution-bound source.

The migration preserves every retained query pair byte-for-byte and in its
original order. The ordinary author-selector query parameter named `auth` is
not a credential and remains unchanged.

Newly captured resolution and research URLs pass through the same sanitizer
before they can be written. Saved manifests can be migrated offline, without a
network or provider call.

## Evidence and projection proof

The transition manifest
`historical_context_v9_transport_url_redaction_transition_manifest.json`
has SHA-256
`4a08dda2241ac9cef89659f68bf99b27307f9525cbfdf4aee5290b9788058180`.
It binds the predecessor and current resolution, research, and source-role
artefacts and accepts only the reviewed deletion-only paths.

The source-role audit changed at exactly four semantic-tree leaves:

1. the resolution input hash;
2. the saved OpenAI research input hash;
3. one resolved transport URL;
4. that source's resolution-record hash.

After transport-only fields and their input hashes are normalised, the complete
evidence-role audit and its public projection are identical before and after.

Downstream regenerated artefacts differ only in hash bindings:

- evidence-truth audit: one source-role input hash;
- published-reply semantic review: source-role and truth-audit hashes;
- semantic-gate audit: ledger and source-role hashes;
- public-source deduplication audit: source-role hash;
- public-projection review: source-role, builder, formatter, and new transition
  hashes.

All downstream records, decisions, counts, public replies, and gate semantics
remain unchanged.

## Preserved invariants

- Corpus identity and quotation text: unchanged.
- Ordinary-post cycle: 611 members, identical order.
- Historical-context gate: 13 blocked quotations, identical IDs and policy.
- Unresolved partition: 5 records, unchanged.
- Semantic-veto shadow manifest: unchanged.
- Completed packet count: 627.
- Attribution-ineligible completed count: 16.

The historical gate artefact's bytes changed only because its source bindings
changed; its blocked-ID projection is identical.

## Deterministic regeneration

Two independent offline builds from the same predecessor inputs produced
identical current artefacts:

| Artefact | SHA-256 |
|---|---|
| saved resolution | `cf81899fd29dc6e65790b182a5f73127b87ac4ccfe1087f0d0d32bf1751b0a2e` |
| saved OpenAI research | `9d5eef501e2bb605ece137e147924d9b28f2fea2f1b8b34a1816cb58b4352926` |
| source-role audit | `ea2a7ce7e9b841da20aa6db7c48cc2e14666f9791d3e7398d349d5104b892e3f` |
| evidence-truth audit | `24cdc67ac957c3a69c0b00435eb74cbe026834486ec080c28566eeeed2abdd32` |
| semantic-review ledger | `dd041bb7745fa0b018ed09c3c0c9f8db676c0755d530572bc7e4bf56cdfc793e` |
| semantic-gate audit | `b1c163e09a2c7ebf7ae33221d093ad6855e2ba7e1ddc5b4bf78706e43633ff4d` |
| public-projection review | `0ae91763a549bc0262148325b4c0855de1b842709244970b9101cb8f54d5b0fc` |
| public-source deduplication audit | `171d5a09cc64bd1d42814033cacfe901585f5680872b406a102e5fd8456f4b47` |

Every repeated build was byte-identical and matched the checked-in candidate
artefact.

## Validation

- Directly affected focused tests: 274 passed, 0 failed, 9 existing
  deprecation warnings.
- Python compilation: passed.
- `git diff --check`: passed.
- Current-tree secret scan, including new untracked candidate files: 1,951
  files inspected, 0 findings.
- No broad application suite was run at this baseline stage. The complete
  suite is reserved for the one frozen external-gate validation after the
  Priority-0 candidate is replayed.
- Network/provider calls: 0.
- Production mutations: 0.
- Service actions: 0.
- X actions: 0.

CURRENT-TREE TRANSPORT HYGIENE VALIDATED — READY FOR PRIORITY-0 REPLAY
