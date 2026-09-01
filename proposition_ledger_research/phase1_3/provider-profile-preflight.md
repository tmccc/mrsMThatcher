# Phase 1.3 xAI provider-profile and structured-output preflight

## Disposition

The corrected Phase 1.3 disposition is
`locally_compatible_with_mandatory_canonical_postvalidation`.

The official `xai-sdk==1.19.0` can construct and deterministically serialise
both intended requests locally, without transport, and preserves the raw
provider-facing schema. Five required `additionalProperties: true` insertions
and 14 restricted outer-anchor removals are exact.

Commit `777b40f67793d6140fa3f923186cf737c89042ad` incorrectly treated ordinary
Python `re` behavior as the canonical regex oracle. Python permits `$` to
match immediately before a final LF. Draft 2020-12 instead specifies the
ECMA-262 dialect; under non-multiline ECMA-262 semantics, `$` succeeds at the
actual end of input. Both the intended canonical `^INNER$` constraint and
xAI's implicitly whole-string `INNER` constraint therefore reject the
terminal-LF witness. The 14 Python observations remain recorded as
`python_jsonschema_regex_engine_divergence`, not provider incompatibilities.

This is not evidence of live-server rejection or acceptance. The frozen
statuses remain:

- `server_acceptance_status: not_tested`
- `provider_call_authorised: false`
- `development_pilot_authorised: false`
- `provider_calls: 0`

## Frozen execution profiles

The two profiles are execution profiles for one machine-ledger role, not four
new experimental arms.

| Field | Profile 1 | Profile 2 |
|---|---|---|
| profile ID | `xai-grok-4.3-low-ledger-v1` | `xai-grok-4.6-low-ledger-v1` |
| provider | xAI | xAI |
| model | `grok-4.3` | `grok-4.6` |
| reasoning effort | `low` | `low` |
| SDK | `xai-sdk==1.19.0` | `xai-sdk==1.19.0` |
| interface | official Python SDK structured-output chat | same |
| tools/search/code execution | none/disabled | none/disabled |
| streaming | disabled | disabled |
| response | strict structured JSON | strict structured JSON |
| canonical post-validation | mandatory | mandatory |

Reasoning effort is held at `low` to isolate the model-ID difference. Prompt,
schema, tool availability, SDK, streaming, post-validation, and the
still-pending retry/failure policy are also held fixed. Later profile
comparison remains `pending_not_run`; no winner was selected.

The separate downstream reviewer/writer constraint remains OpenAI
`gpt-5.6-sol` with medium reasoning. Phase 1.3 did not install an OpenAI SDK,
validate that role, or change its prompt, behavior, or A/B/C/D design.

## Documentation provenance and resolved profile-page omission

Provider rules use official xAI documentation and official PyPI metadata. The
regex correction additionally uses only the named official JSON Schema,
ECMA-262, and Python sources. Hashes cover each decoded HTTPS response body
after content-encoding decompression; complete snapshots are not committed.

| Source | Retrieved UTC | SHA-256 |
|---|---|---|
| [Structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs) | 2026-08-31 21:54:03 | `6ef0faa73d5cb166a717cf93736e675f079f1786d9df71bc5c52ed7a139a0c03` |
| [Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning) | 2026-08-31 21:54:04 | `da1c527a86a05c868e08d221e90d7282408c173ed5a4ef07799e302cd609d402` |
| [Grok 4.3](https://docs.x.ai/developers/models/grok-4.3) | 2026-08-31 21:54:04 | `0108d9512f173667871a41a21401585b5b786303eb3f43a6ef8758847f3e40b6` |
| [Grok 4.6](https://docs.x.ai/developers/models/grok-4.6) | 2026-08-31 21:54:05 | `7c8444c9ed3c83be879c54eb73b6f9561b0b1f83fa066940985b9b77f1761934` |
| [May 15 model retirement](https://docs.x.ai/developers/migration/may-15-retirement) | 2026-09-01 01:36:13 | `8a90f421cc5fb70664c0f8dac54910d28f2253a7ae16fc3edf069f31596c4dd2` |
| [PyPI: xai-sdk 1.19.0](https://pypi.org/project/xai-sdk/1.19.0/) | 2026-08-31 21:54:07 | `b193dd78741e0eab9f67913d41bd2c3bcd91d7348edab8f3a7185b7f26eda44e` |

The regex correction adds the following authoritative provenance:

| Source | Retrieved UTC | SHA-256 |
|---|---|---|
| [JSON Schema Draft 2020-12 validation](https://json-schema.org/draft/2020-12/json-schema-validation) | 2026-09-01 02:52:02 | `2b4849011dab7fef819a93ff3ff77b04a09a687640a6525b5e395f4153144e44` |
| [ECMA-262 text processing](https://tc39.es/ecma262/multipage/text-processing.html) | 2026-09-01 02:52:02 | `33d947d1bfd75b568504f2b10ce4f3ca20d1d8f14e54cfe3eb3aa4a0b4ceda7b` |
| [Python 3.10 `re`](https://docs.python.org/3.10/library/re.html) | 2026-09-01 02:52:02 | `efc63d833aa1ccc6db45e1e1a0c2d90851a914a22e05ccc99517051fca1dfc0e` |
| [xAI structured outputs, correction capture](https://docs.x.ai/developers/model-capabilities/text/structured-outputs) | 2026-09-01 02:52:03 | `2cf69f355796c25a491f45e293ce82986d775289ea1852e724e547af109e859f` |

Pinned `jsonschema==4.26.0` delegates `pattern` to `re.search` in
`jsonschema/_keywords.py`; that installed source hashes to
`afcfc3aea01f9fa40bc109e65c4820bde89253e51a20bda9da7b8f20d7a57c57`.
This records local validator behavior, not normative canonical semantics.

The first attempt halted because the generic reasoning page's omission of
Grok 4.3 was interpreted too cautiously. The dedicated Grok 4.3 page lists
structured outputs and `none`, `low`, `medium`, and `high` reasoning. The more
specific May 15 migration guide also identifies `grok-4.3` with low reasoning
as the target for retired reasoning-model requests and lists all four effort
levels. The generic page is therefore an incomplete summary, not a
prohibition. The recorded resolution is:

```text
documentation_status: resolved_by_more_specific_official_sources
generic_reasoning_page_status: incomplete_for_grok_4_3
profile_documentation_status: sufficiently_supported_for_local_preflight
```

That profile-documentation resolution is independent of this regex
correction. The halted and completed prior private runs remain unchanged as
audit evidence.

## Pinned environment

Both isolated environments use CPython 3.10.12
(`cpython-310-x86_64-linux-gnu`, `linux-x86_64`, glibc 2.35), pip 22.0.2, and a
37-distribution exact hash lock. Principal versions are:

- `xai-sdk==1.19.0`
- `pydantic==2.13.5`
- `protobuf==6.33.6`
- `grpcio==1.83.1`
- `jsonschema==4.26.0`
- `pytest==9.1.1`

The official wheel is `xai_sdk-1.19.0-py3-none-any.whl`, SHA-256
`4af1a629ad9304d0b05fa052d84ce77b7b61242a6d59d5936b692fec95fb52c1`.
The private pip requirements lock SHA-256 is
`2b85726da91529a78869a8042d80c562e042e541127fbba85704c63d1455701e`.
The tracked environment lock records every direct and transitive package,
wheel filename, wheel hash, dependency edge, and environment marker.

Environment B was recreated offline solely from that exact hash lock and the
verified wheelhouse. Its substantive outputs are byte-identical to
Environment A's outputs.

## Canonical schema identity and audit

The canonical authority remains
`proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json`:

- version: `proposition-ledger-semantic-delta-v1.1.0`
- source SHA-256: `eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a`
- source size: 47,726 bytes
- object schemas: 43 when typed objects and object-applicator branches are
  counted; all 38 typed objects are explicitly closed
- `$ref` occurrences: 186; graph vertices: 62; graph edges: 156
- `oneOf`: 6; `anyOf`: 15; `allOf`: 1
- maximum object depth: 4; maximum array nesting: 3
- maximum object property count: 22; unbounded arrays: 31
- rejected provider constructs: 0
- formats present: 0; patterns present: 14

The independent audit exactly reconciles the prior provider-neutral inventory.
The reference graph is local, fully resolved, and acyclic; its longest logical
definition chain is five edges. The single `allOf` has exactly one subschema.

The 14 patterns avoid every explicitly rejected regex feature and fit a
deliberately narrow ASCII grammar: literals, positive ASCII classes/ranges,
ordinary capturing groups, `*`, and bounded repetition. Every pattern has one
unescaped outer `^...$` pair, no interior anchor, no top-level alternation, no
inline modifier, no dot wildcard, and no unproved escape syntax. This
structural proof is the basis for exact outer-anchor removal.

## Provider-facing transformation

The canonical schema was never modified. Its provider-facing copy has
canonical semantic SHA-256
`37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21`.

xAI defaults omitted `additionalProperties` to false, while canonical JSON
Schema defaults it to true. Exactly five object-applicator nodes omit the
keyword, so the pure transformation inserts `additionalProperties: true` at:

- `/$defs/newAnswerTarget/anyOf/0`
- `/$defs/newAnswerTarget/anyOf/1`
- `/allOf/0/if`
- `/allOf/0/then`
- `/allOf/0/else`

Each object ledger entry proves the same rule: canonical omission equals true, and
making the provider default explicit preserves the accepted instance set.
Counterfactual negative witnesses show that false would reject
canonical-valid objects at every location.

At each of the 14 pattern locations, the provider copy changes `^INNER$` to
`INNER`. A fail-closed structural helper proves the exact outer pair and the
restricted interior grammar before making the change. It rejects escaped or
interior anchors, top-level alternation, lookaround, backreferences, inline
modifiers, dot wildcards, unproved shorthand/Unicode syntax, and malformed
groups/classes/quantifiers. Thus the ledger contains 19 transformations: five
object-default expansions and 14 exact regex normalisations. No property,
type, required list, enum, const, data constraint, annotation, alternative,
or reference changed.

## `oneOf`, references, and semantic-equivalence evidence

All six alternative pairs are structurally disjoint:

- `/properties/prior_ledger_reference/oneOf`: object versus null type domains.
- `/properties/commitment_changes/items/oneOf`: required `operation` const
  `add` versus `update`.
- `/properties/obligation_changes/items/oneOf`: the same const proof.
- `/properties/answer_target_changes/items/oneOf`: the same const proof.
- `/properties/rejected_answer_target_changes/items/oneOf`: the same const
  proof.
- `/properties/repair_records/items/oneOf`: the same const proof.

Thus xAI's documented anyOf-like handling of `oneOf` cannot weaken exclusivity
for this schema. No `oneOf` was rewritten.

Structural proofs cover all 19 transformations. Independent intended-canonical
and xAI-full-string validation paths agree on all 380 wholly synthetic
positive, negative, boundary, combinator, existing-fixture, enum, const,
nullable, optional-field, pattern, array, and counterfactual cases. Six of
those are existing wholly synthetic semantic-delta fixtures. This bounded
agreement is reported separately from structural proof.

The audit separately retains ordinary `python-jsonschema` observations. Its
Python regex engine accepts each valid sample plus final LF under canonical
`^...$`; the restricted intended-canonical and xAI full-string paths both
reject all 14 witnesses. They also agree on terminal CR, CRLF, U+2028, U+2029,
embedded newline, and invalid leading/trailing characters. Accordingly:

- structurally proved transformation equivalence: yes
- bounded intended canonical/xAI agreement: 380/380
- Python-validator implementation divergences: 14
- intended canonical/xAI regex mismatches: 0
- unresolved semantic uncertainty: 0
- overall documented provider semantics equivalent: yes

## Guarantees and mandatory canonical post-validation

The documented thresholds used by this schema are within their guaranteed
ranges: maximum string length 2,000 is below 2,048; the largest item
constraint is 2, below 256; and the largest object has 22 properties, below
64. Numeric bounds have no documented threshold.

One conditional construct (`if` / `then` / `else`, three keyword occurrences)
is only best-effort. In addition, xAI's documented subset does not state a
guarantee for 17 `uniqueItems` occurrences. These guarantee gaps prevent
`locally_compatible_exact` and make unchanged canonical post-validation
mandatory.

If provider use is ever separately authorised, every response must still:

1. parse as strict JSON without coercion or repair;
2. validate against the unchanged canonical semantic-delta schema;
3. pass deterministic semantic-reference validation;
4. pass deterministic materialiser validation;
5. be rejected before persistence if any canonical check fails.

The provider-facing schema is never a persistence authority. Retry/failure
policy remains pending and must be frozen before any first provider call; this
phase implements no live retry loop.

## Local SDK compilation and network denial

The public `xai_sdk.sync.chat.Client.create` raw-schema path accepted
`reasoning_effort="low"` for both model IDs and preserved the exact provider
schema. It applies no local model-specific rejection to Grok 4.3. The private
`xai_sdk.chat.BaseChat._make_request` helper was used only to obtain the exact
unary protobuf locally without sampling; installed `xai_sdk/chat.py` hashes to
`2802197f8243970935f237178c04ae57f62e3d012ef71e5bdb8789239039dc1b`.
That private-helper dependency is recorded as pin-fragile and must be
revalidated for any future SDK pin.

| Profile | Schema SHA-256 | Request SHA-256 | Bytes | Result |
|---|---|---|---:|---|
| `grok-4.3` low | `37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21` | `449d28a88dadb3acea0c22c37a829877bf7cdb7746ec973192fb5d41062719fb` | 28,310 | succeeded without transport |
| `grok-4.6` low | `37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21` | `b88a5eecbd195a3e25c8f83b2c1ded6ddfc255aca993c3012b3cdd7579096c82` | 28,310 | succeeded without transport |

After normalising only the model field, the deterministic protobuf bytes are
identical. Both contain low effort, zero tools, no search parameters, no code
execution, no streaming, and only synthetic placeholder messages. Local SDK
serialisation does not establish xAI server acceptance.

Before importing the SDK, credential variable names were removed without
reading their values and xAI tracing controls were disabled. The active guard
patched socket, DNS, HTTPS, and synchronous/asynchronous gRPC channel creation;
deliberate attempts in all four categories were observed and blocked.
Generated RPC callables were fail-closed. A non-privileged `unshare -n`
namespace was tested first and was unavailable in this execution environment,
so the patched subprocess guard was the operative denial mechanism. Provider,
inference, model-list, OpenAI, and X call counts are all zero.

## Scope confirmations

No real conversation record, target identity, held-out record, private author
key, or production state was read. The two eligible clean prefixes remained
sealed. No power analysis, pilot, experiment arm, ledger generation, model
comparison, downstream reply evaluation, merge, deployment, or service action
occurred. Production and every prior private run were untouched.
