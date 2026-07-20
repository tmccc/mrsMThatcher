# Gemini Reply-Strategy Deep-Dive Follow-up

Date: 20 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`

## Executive verdict

The external review contained one confirmed parser defect, one useful signal
that exposed a narrower hot-post harvesting defect, and two recommendations
that would weaken deliberate fail-closed integrity checks.

The confirmed defects were reproduced with failing tests and fixed. The
production service was not restarted and has not loaded these changes.

## 1. Hot-post reply lane

The suggested bypass of `reply_target_is_directly_eligible()` is unsafe and was
not implemented. Production has already observed X reject replies with HTTP 403
and the explicit response that an account may reply only when it is mentioned
or authored the target post. Parent-thread membership is insufficient. Removing
the preflight specifically for hot-post candidates would restore that known
failure, spend model calls on impossible writes and risk write-cooldown noise.

The lane is not completely paralysed: it can recover directly eligible mentions
inside watched conversations, including candidates not reached in a bounded
mentions scan. Its broad conversation search did, however, contain a real
starvation bug. Ineligible organic replies were appended before applying
`MAX_HOT_POST_REPLIES_PER_CHECK`; enough early replies could consume the cap and
delay a later directly eligible candidate.

The new regression supplied an ineligible reply followed by an eligible direct
mention with a candidate cap of one. Before correction the lane returned the
ineligible ID. It now:

- checks direct target eligibility while harvesting search results;
- records an ineligible target durably as `reply_not_permitted`;
- clears any stale pending V2 draft for that target;
- records the hot-post skip and terminal telemetry;
- excludes it before candidate limiting and media/model work;
- still repeats the same direct-eligibility check in the consolidated reply
  path as defence in depth.

The test now returns the eligible candidate and proves the ineligible target is
terminally recorded.

## 2. Exact claim coordination

The exact comparisons in `validate_evidence_response()` and the reviewer claim
inventory are intentional integrity boundaries, not confirmed bugs.

The evidence model receives stable `claim_id` values and is explicitly told to
copy the supplied claim and semantic dimensions unchanged. JSON escaping does
not alter a decoded string. If the model changes claim text, actor, action,
relationship, polarity, date or quantity, the evidence verdict is no longer
provably bound to the proposer's claim and must fail closed.

The reviewer is independently instructed to inventory every factual claim in
the actual reply. Exact disagreement catches both omitted proposer claims and
reviewer-discovered claims. The existing regression for a factual claim omitted
by the proposer depends on this behaviour.

The proposed `[A-Za-z0-9]+` token normalisation was rejected because it discards
punctuation, apostrophes and every non-ASCII name or quantity marker. It can
erase distinctions relevant to negation, quotation, quantities and identity.
There is currently no provider-pilot evidence of harmless echo drift. If such
drift is later observed, the safe redesign is to remove unnecessary model echo
fields and bind results locally by opaque IDs, not to use fuzzy equality.

## 3. Authorised-wording detector

The two phrases cited by the external review were tested against the actual
repository and matched no authorised quotation. A current-corpus analysis found:

- authorised wording records: 554;
- unique contiguous eight-word sequences: 13,476;
- sequences repeated across records: 100;
- maximum cross-record frequency: 4.

The detector deliberately catches undeclared exact corpus wording without
depending on ASCII quotation delimiters. That protects against fabricated or
mislabelled historical wording using Unicode punctuation or no delimiters. A
coincidental eight-word match can conservatively suppress a usable reply, but a
missed reply is safer than publishing corpus wording as apparently original
prose. No observed false rejection currently justifies weakening the guard.

The detector remains unchanged. Its rejection rate should be measured in the
separately required non-posting provider pilot before any threshold change.

## 4. Sentence counting

This finding was confirmed. The original counter parsed:

`The Rt. Hon. member must look at the data.`

as three sentences. `Govt.` and `MP.` also created false boundaries.

The counter now recognises `Rt.`, `Hon.`, `Govt.` and `MP.` alongside the
existing abbreviations. Context-sensitive abbreviations such as `etc.`, `Govt.`
and `MP.` are treated as sentence endings when followed by a new sentence, so
the correction does not simply suppress every full stop after those tokens.
Regression cases cover one, two and three genuine sentences.

## Validation

Initial regressions, after correcting a missing test import:

- five UK-abbreviation cases failed with over-counts;
- the hot-post cap case returned the ineligible candidate instead of the later
  eligible one.

Passing verification:

- exact new regressions: 8 passed;
- complete `tests/test_reply_strategy.py`: 98 passed;
- direct-eligibility and terminal-target focus: 6 passed, 404 deselected;
- hot-post pagination, watermark, rescan and deduplication integration focus:
  8 passed;
- complete hot-post integration selection: 10 passed, 163 deselected;
- complete hot-post unit selection: 4 passed, 406 deselected;
- bootstrap, runtime-isolation and normal reply integration focus: 36 passed;
- targeted `py_compile`: passed;
- Ruff fatal/static checks (`E9`, `F63`, `F7`, `F82`): passed;
- `git diff --check`: passed.

The twelve-minute full suite was not repeated for these two narrow corrections.
The immediately preceding broad run and its isolated provenance repair are
documented in `ai_first_reply_strategy_replacement_report.md`.

## Files changed in this follow-up

- `mrsMThatcher2.py`
- `reply_strategy.py`
- `tests/test_reply_strategy.py`
- `tests/test_unit_helpers.py`
- `gemini_reply_strategy_deep_dive_followup_report.md`

## Production isolation

Before and after the review, `mrsMThatcher.service` retained wrapper PID
`1595442`, Python child PID `1595443`, start time
`Sun 2026-07-19 23:07:45 BST`, and `NRestarts=0`.

No X post, media upload, provider call, production-state or receipt mutation,
service restart, deployment, commit or push occurred.

READY FOR INDEPENDENT REVIEW
