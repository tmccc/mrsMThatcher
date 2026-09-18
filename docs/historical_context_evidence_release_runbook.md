# Historical-context evidence release runbook

Use this runbook to prepare reviewed quotations and historical data in an
isolated checkout. It covers both evidence updates for existing quotations and
new quotations. Service deployment follows the canonical
[routine deployment procedure](../README.md#routine-deployment-of-an-already-tested-commit).
Do not research sources or run provider-backed metadata generation in production.

## Review the source and public wording

For each quotation, retain the proposed text, speaker or author, source title,
date, occasion, stable locator, supporting passage and enough surrounding text
to establish the historical context. Record whether the wording is exact, a
variant, an excerpt, composite or secondary. Verify each claim against its own
evidence: a wording match alone does not establish a date or occasion.

Distinguish a publication year from a speech date, and a prepared speech text
from verified spoken delivery. A correct transcription of a political argument
does not establish that the argument itself is true. Describe saved-source
inspection accurately; do not label it as human review or an audio check.

For an existing quotation, preserve its wording and identity unless a wording
change is explicitly in scope. For a new quotation, record any difference
between the mined wording and the approved version before deriving its identity.
Use only the permitted typographical normalisations for an exact match.

Keep private PDFs, HTML bodies, OCR, page images and absolute retrieval paths in
the research workspace. Public replies may expose bibliographic references and
public source URLs, but not private paths or internal evidence diagnostics.

## Prepare a new quotation batch

The September 2026 research batch is retained locally in
`quotation_additions/anti_socialism_20260918/`. Its `html_records.json` and
`book_records.json` contain the input packets and source checks; `PREVIEW.md`
shows the quotations and actual formatted context replies. These private batch
inputs are not production commit contents. Keep the preparation record separate
from the later installation result.

From the isolated checkout, with its original base files still in place:

```bash
python3 -B tools/prepare_quote_additions.py quotation_additions/anti_socialism_20260918 \
  --batch-timestamp 2026-09-18
```

Supply the authoritative batch date (`YYYY-MM-DD`) or UTC timestamp
(`YYYY-MM-DDTHH:MM:SSZ`) explicitly. No wall-clock date is substituted. Evidence
and source-role audit dates retain day precision; analysis, status and semantic-
gate audit metadata retain the supplied precision. Old records keep their dates.
Use `--base-project /path/to/isolated/base` to run current preparation code over
an earlier base corpus; the batch must be inside that base's
`quotation_additions/` directory. This option refuses the production checkout.

The command builds `staged/`, `previews.json`, `PREVIEW.md` and `validation.json`
in a new temporary directory. It publishes them only after validation succeeds;
an ordinary preparation/publication failure preserves the previous completed
outputs. Leftovers from earlier staging runs never enter the new tree or its
changed-file list. Symlink inputs and destinations are rejected. It does not install anything or call an AI provider. It
preserves old quotation bytes, extends the existing runtime formats and generates
conservative image-selection metadata. Repeating it against the same base
reproduces the same outputs; running it after the quotations have been added to
the base correctly rejects duplicates. To reproduce this particular batch, use
its original base, `9c142bc8485819061756a88ea99ef991a0683be0`, with current preparation
code (`--base-project`), the retained private input records and batch date
`2026-09-18`. That is a known batch date, not an invented time of day. For a later batch, use reviewed records
in the same input format and a checkout of the then-current production base.

Do not rerun old one-off research or attribution builders merely because their
filenames resemble this task. Some intentionally enforce historical corpus
counts. Reuse current runtime validators and the addition preparator rather
than weakening those historical contracts.

## Research coordinates and retained evidence

`corpus_manifest.json` uses the original research source's occurrence coordinates.
`thatcher_quote_research_project/build_manifest.py` froze a 633-line source before
later edits reduced the bot list. Those existing coordinates remain canonical;
they are not current physical line numbers in `mrsMThatcher.txt`. New research
occurrences append after the existing maximum. The September additions therefore
occupy 634–644, while their bot-file lines remain 621–631. The manifest validator
rejects duplicate coordinates, contradictory counts and incorrect content hashes.
Its original 632 records remain unchanged.

Evidence admission rechecks retained file hashes, supported source types/domains,
meaningful structured inspection checks, exact supporting text and surrounding
context. HTML uses structural text extraction; PDFs use existing page text with
controlled typography/layout normalization, without new OCR or provider calls.
PDF validation requires the PyMuPDF dependency in `requirements-dev.txt`.
A Foundation URL alone does not establish primary status, speaker or occasion.
Explicit `reviewed_classification` may retain excerpt, variant, composite or
secondary classifications; its wording, quality and claim scope must agree with
the packet and retained evidence. Composite passages require explicit retained
parts. Contradictory claims fail preparation; evidence is never silently upgraded.

## Install a coherent file set

Use `validation.json`'s `changed_files` as the runtime file allowlist. Before
installation, compare every current production file with its `base_sha256`,
and every prepared file with its `prepared_sha256`. Reconcile any mismatch with
the current base; never overwrite newer data.

For the eleven-quotation batch the coherent set is:

- `mrsMThatcher.txt` and `quote_analysis.json`;
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`;
- under `semantic_alignment_research/quote_research_full_001/`:
  `research_packets.json`, `corpus_manifest.json`,
  `final_unresolved/final_research_status.json`,
  `historical_context_source_recovery.json`,
  `historical_context_source_curated_evidence.json` and
  `historical_context_source_role_audit.json`;
- `historical_context_published_reply_semantic_review.json`,
  `historical_context_reply_semantic_gate.py` and
  `historical_context_reply_semantic_gate_audit.json`.

Rebuild the source-role audit against the complete corpus. The historical review
ledger retains its existing decisions but binds the new audit hash, so the gate
module's `EXPECTED_LEDGER_SHA256` changes with it. Install the ledger and matching
gate together. Test the gate's actual default hash, not a test-only override.
Preserve exact schema, policy and hash checks. Regenerate the semantic-gate
audit in the same preparation, after its ledger and gate are final. It derives
counts from the current partition and binds each input file, including the gate,
manifest, final status and unresolved cases. The digest must report an unavailable
snapshot when these bindings or counts are stale, even if the gate's stored
`available` flag is true.

**Never copy the whole `staged/` directory over production.** It contains copies
of unchanged dependencies, including `historical_context_reply_history.json`,
which may already be stale. Do not commit private research inputs or cached
evidence, or deploy an entire research branch wholesale. In particular, do not
replace live receipts, histories, schedules, queues, outboxes, control settings
or logs with copies from the isolated checkout.

Existing `image_analysis.json` and image history remain in place. The new
`quote_analysis.json` entries feed the normal image matcher. The historical
quotation/image veto matrix has no reviews for the additions and becomes stale
against the extended corpus. It is retained as offline research; production
does not load it. Do not claim new pair approvals or regenerate image metadata
without a separate need.

## Validate the changed behaviour

Derive counts and identities from the current files. Physical quote lines,
normalised unique quotations, eligible quotations and completed research packets
are different quantities. Fixed historical figures such as 620 lines or 611
eligible quotations are not limits on corpus growth. A resolved manifest can
contain historical identities while the runtime list contains normalised
identities; compare through the runtime loader rather than requiring those
lists to be identical.

For a new batch, require:

- each proposed quote has matching analysis, a valid research packet, source-role
  evidence and runtime eligibility;
- each quote fits the bot's weighted-length limit, and its context is complete,
  formats successfully and passes the actual semantic gate;
- old source bytes, research packets, context renderings, eligibility and
  historical gate decisions are preserved; only approved additions extend the
  ordinary quotation pool;
- unresolved records and unrelated evidence decisions are unchanged;
- two builds from the same inputs produce identical prepared outputs;
- focused tests for addition preparation, quotation eligibility, source roles
  and historical-context delivery pass, without provider or posting calls;
- changed Python compiles and `git diff --check` passes.

For an evidence-only update, quotation text, identity and ordinary eligibility
normally remain unchanged; validate any authorised exceptions explicitly.
Preserve established builders and serialisation where possible. Broaden testing
only when a shared runtime change or concrete failure justifies it. Do not
introduce new transition frameworks or repeat a full suite solely to deploy an
already validated content batch.

The September batch's preparation recorded 631 physical lines, 630 unique
quotations, 622 eligible quotations, 638 completed packets and five unresolved
records. These describe that batch, not requirements for future releases.

## Deploy and verify

After the operator has accepted the public previews, retain the changed-file
list and validation result and use the canonical deployment procedure. Existing
approval for this batch does not need to be requested again. Preserve unrelated
production changes; do not force-reset, clean or merge the research checkout
wholesale. Deploy a committed release by clean fast-forward to the approved
commit.

Use the supported global pause, wait for the running bot's acknowledgement,
stop only `mrsMThatcher.service`, and confirm that its wrapper and Python child
have exited. Make one private quiescent backup before changing the approved
files. After installation, verify hashes and use the deployed paths to load
the quote manifest, audited corpus and gate and render new replies offline.
These checks must not invoke posting or provider APIs.

Start the service while paused. Check one wrapper and one child, successful
startup, the paused-start safeguard, valid evidence/gate loads and no traceback
or restart loop. Then restore the original control bytes and metadata (or its
original absence) atomically and observe normal operation. Preserve quotation
and image histories; adding quotes must not reset the posting cycle. Natural
scheduler activity after unpausing is separate from deployment actions.

If the new files do not load, keep the bot paused and restore the previous
content/code before restarting. Preserve current durable state during rollback;
never replace it with a potentially stale deployment backup. Record whether
installation, restart and rollback occurred, and distinguish task-triggered
provider or posting calls from the bot's later scheduled work. The existing
batch validation plus a short installation result is sufficient; additional
release paperwork is not required for an ordinary content addition.
