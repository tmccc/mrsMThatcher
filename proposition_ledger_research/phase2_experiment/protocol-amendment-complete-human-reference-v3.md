# Complete single-human reference amendment v3

Status: frozen supplemental amendment. This supersedes only the Arm D preparation
workflow in `experiment-freeze-v2.json`; the case, replay contexts, evaluation
points, four arms, profiles, prompts, budgets, provider policy, and 29-call plan
remain unchanged.

Arm D is one transcript-first human reference, not consensus gold. One human
annotator makes every semantic judgement in one append-only current-turn chain.
The annotator may be the investigator. Provenance records, without claiming
blindness, whether the annotator knew the working hypothesis, had seen the
historical published replies, and was the investigator. The annotator must be
independent of the experiment's machine ledger and must not see experiment
machine outputs before the reference lock.

The offline annotation command displays only the next exact turn and these
collections from the previously materialised human state: propositions, issue
states, participant commitments, conversational obligations, proposition groups,
and answer targets. It accepts the complete human-editable semantic surface from
the frozen transport contract, constructs deterministic envelope fields, validates
evidence and materialisation, displays the resulting delta, and appends it only
after literal human approval. Empty collections are valid human judgements. The
human omits `local_ref`; the runner assigns local references from collection order.
An evidence selector may omit `occurrence_index` only when its exact text occurs
once in the current turn; repeated text requires the human to choose the intended
zero-based occurrence.

One completed chain covers the frozen longest prefix. The runner derives the two
Arm D snapshot ledgers at the terminal turns of the frozen representation prefixes;
there is no second form that can retroactively revise an earlier snapshot.
Deterministic code supplies prior-ledger IDs, evidence offsets, permanent IDs,
ledger hashes, and snapshot materialisation. It must not add or repair semantic
content.

The completed chain and one human provenance receipt must pass canonical
validation and be hash-locked before any experiment-generated machine output is
opened. Machine or Codex authorship, unresolved evidence, an altered frozen case
or transcript, incomplete provenance, or a pre-lock machine reveal fails closed.
