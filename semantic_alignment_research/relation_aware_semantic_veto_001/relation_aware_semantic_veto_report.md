# Relation-Aware Semantic Veto Report

## Outcome

- Model: `gemini-3.1-pro-preview`
- Canonical quotation contracts: 626 of 626
- Historical photographs: 91 (69 existing + 22 discovered)
- Selector-relevant pairs judged: 2495
- Final vetoes: 2279
- Final allows: 216
- Known spend: US$40.5739
- Ambiguous exposure: US$0.0804
- Live production activation: no

## Architecture

1. Canonical research packets are converted into immutable semantic contracts.
2. Source-grounded identities and relationships drive deterministic hard-conflict rules.
3. Gemini acts as an adversarial pair judge; `uncertain` is a veto, not an allow.
4. Broad topic scores cannot override a deterministic relationship contradiction.

## Frozen Evaluation

- Pilot passed: True
- Pilot explicit-safety veto rate: 1.0
- Pilot positive retention: 0.25
- Full passed: True
- Full explicit-safety veto rate: 1.0
- Full positive retention (diagnostic only): 0.333333
- Human approvals are not safety truth and are excluded from promotion gates; only confirmed misleading/rejected cases govern the safety evaluation.
- Eligible frozen pairs evaluated: 73/73
- Frozen labels excluded with unauthorised images: 3
- Full safety false accepts: 0
- Full nonpreferred-image allows (not a safety error): 5
- Critical established-ally cases vetoed: 3/3

## Transport and Cost

- Developer attempts: 111
- Vertex attempts: 0
- Developer run-wide pause: False
- Direct-to-Vertex requests: 0
- Preflight expected cost: US$21.4731
- Preflight conservative planned maximum: US$49.8067
- Final pair prompt/schema: `relation-aware-semantic-veto-pairs-2026-07-16-v3` / `relation-aware-pair-judgement-v4`
- SDK/API versions: `2.11.0` / Developer `v1beta` / Vertex `v1`
- Native JSON-schema Batch transport produced unusable schema skeletons in the first full pair batch; the typed SDK response schema corrected it without changing model, prompt semantics, or safety policy.
- Batch schema-transport corrections and per-request hashes are recorded in `execution_provenance.json`.

## Safety

The run used no search, URL context, tools, code execution, image generation, or facial identification.
Production image and selector hashes remained unchanged: True.
The veto was not enabled in live production.

## Limitation

The full matrix is bounded to current-selector candidates, frozen evaluated pairs, and source-grounded entity matches. An unseen arbitrary pair must be judged before it can be treated as allowed.
