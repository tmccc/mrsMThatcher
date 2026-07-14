# Margaret Thatcher Quote Research Project

This pack defines a durable, resumable research process for the 633-line
`mrsMThatcher(1).txt` source file. It is designed to produce a historically
grounded database that can be joined to the existing quote-analysis data and
used by the editorial image-generation and image-selection systems.

## Current status

This is a research-specification and pilot scaffold, not a completed research
database. It freezes the input identity, schemas, research protocol and
validation contract. The report compiler, merger and executable semantic
validator are intentionally not claimed as complete: their final shape should
be implemented only after the ten-record pilot proves that the evidence packet
contains everything required by schema version 2. The included JSON Schemas and
semantic rules are the contracts those later tools must enforce.

## Source audit

- Source filename: `mrsMThatcher(1).txt`
- SHA-256: `4a3eec7b9233f74c58ddfe8a3484bd32d72d6fe658ba1882f0dfd6b3b8479496`
- Encoding: US-ASCII (valid UTF-8)
- Line endings: LF
- Physical lines / source occurrences: 633
- Non-empty quotation lines: 633
- Exact-distinct quotation texts: 632
- Exact duplicate: lines 232 and 234

Each quotation's durable join key is the SHA-256 of the exact quotation text
without its terminating newline. This is the same convention already used by
`quote_analysis.json`; for example, line 1 is:

`230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a`

Line numbers remain occurrence locators, not identities. Identical lines share
one research record and retain all source line occurrences.

## Why this is a pipeline rather than one Deep Research run

There are too many records and too much required evidence for one reliable
report. A monolithic run would encourage abbreviated fields, inconsistent
classifications and untraceable claims. It would also make one corrected quote
require regeneration of the whole database.

The work is divided into four stages:

1. **Manifest and pilot** — freeze IDs, file fingerprint and a deliberately
   varied pilot set.
2. **Deep Research evidence packets** — research modest batches, retaining
   native citations and precise source locators.
3. **Compilation** — convert reviewed packets to independently valid JSON
   records and merge them deterministically.
4. **Validation** — run schema checks, semantic checks and an independent
   evidence audit before the records can influence production.

Deep Research is the evidence-gathering engine. It is not treated as the final
database writer because its native citations and a strict JSON document have
different reliability requirements.

Research packets use the visible word `unknown` so uncertainty cannot be
missed during review. During compilation, unknown scalar values become JSON
`null`; an empty array means research positively established that the relevant
list is empty, not that the field was skipped.

## Files

- `deep_research_master_prompt.md` — reusable instructions for each research
  batch.
- `quote_research.schema.json` — JSON Schema draft 2020-12 for a canonical
  research record.
- `quote_record.schema.json` — standalone canonical-record schema entry point.
- `quote_manifest.schema.json` — machine-enforced contract for the frozen input
  manifest.
- `research_batch.schema.json` — machine-enforced contract for pilot and later
  research input batches.
- `research_output_batch.schema.json` — contract for independently compiled
  research output batches before final merge.
- `validation_rules.md` — semantic checks that JSON Schema cannot express.
- `quote_manifest.json` — generated inventory of all source occurrences and
  exact-distinct quote IDs.
- `pilot_batch_001.json` — ten deliberately varied records for the schema and
  research-method pilot.
- `pilot_method_findings.md` — three source-tested examples demonstrating an
  exact quotation, a popular paraphrase and a reordered Commons variant.
- `example_record_line_001.json` — a schema-valid completed record showing how
  the evidence map and editorial layer compile for the first quotation.
- `build_manifest.py` — reproducibly rebuilds the manifest and pilot from the
  uploaded source file.

## Recommended execution order

1. Run the ten-record pilot with `deep_research_master_prompt.md`,
   `pilot_batch_001.json` and `quote_manifest.json` attached. The full manifest
   is a lookup index for related quote IDs only; the research scope remains the
   ten pilot records.
2. Review attribution statuses, source locators, evidence mappings and visual
   concepts manually.
3. Amend the schema once if the pilot exposes a genuine omission, then freeze
   schema version 2.
4. Form production batches of about ten exact-distinct quotes. Keep suspected
   variants or excerpts of the same passage in the same batch.
5. Compile and validate each batch before starting a large merge.
6. Independently re-research every `misattributed`, `composite`, `paraphrase`,
   `unverified` or low-confidence record, plus a sample of high-confidence
   records.

## Compatibility with the existing bot research

This database supplements rather than replaces `quote_analysis.json`.
`quote_id` is the existing full quote hash, so a record can be joined without
fuzzy matching. The uploaded `quote_text` is immutable raw input: punctuation
or wording corrections belong only in `verified_text` and never rewrite the
identity-bearing text. Historical facts and provenance remain separate from mutable
selection weights, manual overrides, generated-image metadata and production
state.

No research result should change the live bot automatically. Production use
should require a separate reviewed import and explicit enablement.
