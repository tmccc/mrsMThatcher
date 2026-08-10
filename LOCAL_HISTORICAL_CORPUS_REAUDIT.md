# Local historical-context corpus re-audit

`historical_context_local_corpus_reaudit.py` performs a bounded advisory search
of the exact current 611-member eligible quotation corpus against an
operator-owned Thatcher-archive-2 mirror. It reuses the maintained eligible
corpus validator, query-fragment policy, local mirror/index, MTF document
validator, passage matcher, source classifier, semantic gate, and wording
comparison helpers.

Prepare a new empty directory outside every checkout, set it to mode `0700`,
and export these private environment variables without adding them to tracked
configuration:

```bash
export MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT=/operator/private/archive-root
export MRS_HISTORICAL_REAUDIT_RUN_DIR=/operator/private/new-empty-run
python3 historical_context_local_corpus_reaudit.py --execute
```

`MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT` names the parent directory containing
`www.margaretthatcher.org/document`. Passing the
`www.margaretthatcher.org` directory itself is invalid. This shape check tests
only that relative directory and performs no archive-wide inventory or
recursive scan, so a mirror may still be downloading.

The process denies socket/DNS entry points, does not construct a cloud search
backend, and writes only the seven private advisory outputs named by the
runner. It records numeric-document inventories before and after the search;
an inventory change makes the package non-reproducible and incomplete. The
outputs never contain the private archive or run-directory path and must not be
added to Git.

## Reclassify an existing package without discovery

To reuse an existing candidate ledger after an unstable source-run inventory,
prepare another empty mode-`0700` private directory and run:

```bash
export MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT=/operator/private/archive-root
python3 historical_context_local_corpus_reaudit.py \
  --reclassify-existing /operator/private/existing-run \
  --output-dir /operator/private/new-empty-run
```

This mode reads current authoritative historical inputs, but does not prepare
search queries, build an index, run archive discovery across the 611
quotations, or inventory the whole archive. It selects candidates through the
existing positive/admission/advisory predicate and also selects an old
exact/recorded-variant primary-wording candidate when it retains a numeric MTF
document ID, an agreeing canonical MTF URL, a valid recorded local-file SHA-256,
and a non-empty supporting passage. For each selected candidate it reopens only
the recorded local MTF document, verifies the document identity and exact
local-file SHA-256, extracts the current text, and freshly reruns
contribution-aware matching and source classification. The previous match type,
passage, speaker, classification, acceptance, and confidence are retained only
as recorded discovery history and cannot decide the fresh result. In
particular, an old negative speaker verdict does not veto fresh rematching. An
unchanged file remains identity-valid even when the fresh rematch rejects its
wording or speaker; changed or missing files alone are marked stale. Unselected
candidates do not retain positive proposals from their old semantic fields.
Reclassification aborts without publishing any advisory output when at least
one candidate was selected for identity revalidation and every selected result
has the exact `candidate_file_is_missing_or_unreadable` failure reason. Partial
availability remains advisory and non-fatal: missing candidates remain stale
when at least one selected candidate file can be opened, and other stale
reasons do not activate this guard. Negative and no-hit conclusions retain
their provisional semantics.

Programme v14 changes only these operational archive-root preflight and
all-selected-files-missing failure semantics. It does not alter the approved
v13 candidate-level evidence classifications; the approved private v13 package
remains the evidence receipt and need not be regenerated solely for this patch.

Optional historical metadata uses conservative missing-value semantics.
Case-, spacing-, and punctuation-normalised forms such as `unknown`, `not
known`, `unavailable`, `not available in primary sources`, `none`, `null`,
`n/a`, `unspecified`, and `undated` are excluded from authorised variants and
treated as missing dates, events, and locators. The stored quotation itself is
never discarded by this filter. After word-token and punctuation/typography
normalisation, identical wording is accepted. Otherwise an ordinary variant
must share at least two distinct content tokens, cover at least two thirds of
the larger content-token set for wordings of at most four content tokens (40%
for longer wording), and have SequenceMatcher word-token similarity of at least
0.65 for short wording (0.50 for longer wording). A provenance-bound,
explicitly non-substantive typography/punctuation or editorial-bracket
transformation may pass only when its maintained transformation reproduces the
variant deterministically and similarity is at least 0.50. Each decision
records overlap, similarity, thresholds, shared tokens, and a reason. The same
rule filters variants, admits fresh variant matches, and determines strong
variant evidence. Original packet values remain available in provenance.

Transcript segmentation retains each block's source tag. Explicit role labels
such as `Question`, `Interviewer`, `Prime Minister`, and `Mrs Thatcher` remain
speaker boundaries. A titled personal name or name-plus-outlet remains
recognisable in paragraph/colon transcript structure. An untitled two-to-four
token personal name, optionally followed by a parenthesised outlet, becomes an
interviewer boundary only at a paragraph/list/blockquote contribution boundary
when recognised transcript roles and label/text alternation establish the
context; repeated contextual labels are supporting evidence. Short uppercase
interviewer initials require alternation with an explicit Thatcher label.
Arbitrary title-case phrases, policy headings, acronyms, and short all-capital
text are not person labels. Ordinary `h1`/`h2`/`h3` section headings never
change the active speaker, and a personal name in a structural heading does not
establish authorship.
This prevents interviewer repetitions from inheriting a Thatcher speaker label
while preserving explicit document authorship across article and speech
section headings. Fresh matching first selects exact or authorised-variant
support inside a verified Thatcher contribution; only when none exists does a
stronger interviewer or unverified occurrence become the diagnostic result.
Cross-speaker clause assembly remains rejected.

The reclassification runner resolves one contribution root before segmentation.
Legacy documents retain `#documentbody`, and the maintained `.document-body`
fixture/current fallback remains supported. For a modern
`article.node-archive-document`, exactly one non-empty contained `.field-body`
is preferred; no `.field-body` retains the enclosing-article fallback, while
multiple usable `.field-body` regions fail closed. The selected-body diagnostic
is recorded without a local path. Consequently an article title, metadata, and
the “Other documents from this day” sidebar outside the selected field body do
not enter whole-document fallback text, contribution segments, matching, or
surrounding context.

The runner also retains each contribution block's exact CSS class tokens and
any recognised attribution tokens on ancestors through the selected body root.
Matching is case-insensitive but exact-token only:
`mt` marks Margaret Thatcher contribution text, `intmt` marks a Thatcher
speaker/contribution label, `nonmt` marks non-Thatcher contribution text,
narration, or reporting, and `intnonmt` marks a non-Thatcher
speaker/contribution label. Similar strings such as `notmt`, `mt-note`, and
`nonmtish` have no attribution meaning. Simultaneous MT and non-MT polarity in
the applicable ancestry is a conflict and fails closed. `h1`, `h2`, and `h3`
remain structural rather than speaker or quotation elements.

The ordered body model also retains non-searchable editorial events. A numbered
`ed-comment` is a source boundary only when its complete normalised text begins
with `(1)`, `(2)`, or another one-to-three-digit section number. Its number is
bound only to the same numbered entry parsed from the document's structured
`Source` or `Editorial comments` table row. A direct MT baseline requires both a
direct form such as `speaking text` in the label and corresponding entry and an
explicitly verified Margaret Thatcher document author. Numbered `ed-comment`
labels additionally admit only the bounded source qualifier `Thatcher Archive`
followed by a colon, en dash, em dash, or spaced hyphen and one complete
maintained direct terminal phrase; an arbitrary prefix ending in `speaking
text` is not direct. This does not widen ordinary italic-marker recognition. A
non-MT baseline requires explicit partial-paraphrase, newspaper-report,
reportorial-account, event-report, press-report, or other non-direct semantics.
Direct structured metadata uses a positive complete-form grammar: `speaking
text`, `speech text`, `modified speaking text`, `modified speaking text
begins`, `full speaking text`, `full speaking text begins`, `direct Thatcher
text`, and `direct speech text`, plus the bounded `Thatcher Archive`-qualified
speaking/modified/full forms. Direct metadata semantics and source-identity
extraction share one complete-form `Thatcher Archive` qualifier parser. A
colon, en dash, em dash, or spaced ASCII hyphen therefore extracts the same
exact `Thatcher Archive` source identity without retaining the direct terminal.
Embedded wording such as `commentary on speaking text`, `notes concerning the
speaking text`, or `summary of speaking text` is not itself direct evidence.
Non-direct structured metadata is evaluated before any embedded direct wording,
so `partial paraphrase of speaking text` and `press report of direct speech
text` remain non-MT. Opposite label and effective metadata polarities, including
incompatible duplicate same-number entries, fail closed as `unverified`.
Same-source reportorial Editorial comments contribute non-MT semantics to that
effective polarity before conflicts or a baseline are selected.

A named newspaper by itself is insufficient. A named source establishes
reportorial semantics only when the structured Editorial comments use the same
complete normalised source identity of at least two meaningful words, treating
an optional leading `The` as equivalent, with either the bounded `reported`
verb form or an attached `report of`, `report on`, or `report from` noun
construction. Report verbs admit exactly no modifier, `later`, or
`subsequently`; report nouns admit exactly no modifier, `its`, `detailed`,
`morning`, or `morning edition`. These positive modifier tuples supply
reportorial secondary evidence only. Arbitrary publication suffixes are never
treated as report modifiers, and longer, extended, or joined publication names
do not match a shorter numbered source identity. An unrelated report or a
different source name also does not qualify. A missing, inconsistent, or
ambiguous numbered entry still creates a hard boundary but gives the section an
`unverified` baseline.

A numbered `ed-comment` nested in a contribution parent is a source boundary
only when it is the first substantive content in that parent. Whitespace,
formatting wrappers, and `span.pagenum` do not count as substantive. A
marker-first parent remains supported: the non-searchable marker is emitted
first and the remaining parent text receives the new baseline. If substantive
text precedes the marker, its placement is ambiguous and fails closed with the
bounded reason `inline_source_marker_after_substantive_text`; the marker stays
out of matching and context, the entire parent is unverified, matching cannot
join text across the marker, and following material remains unverified until a
later clean source boundary or explicit attribution establishes new state.

An italic source marker is recognised only as a direct child of the selected
body, or as the only substantive content of a paragraph apart from formatting
and a `span.pagenum`. After an optional number, the complete maintained direct
forms are `speaking text`, `modified speaking text begins`, and `full speaking
text begins`; the maintained reportorial forms are `partial paraphrase`,
`partial paraphrase of speaking text`, and `opening of press release (partial
paraphrase of speaking text)`. Complete `end of partial paraphrase` forms and
`beginning/end of section checked against ...` forms are retained as
non-searchable editorial-only diagnostics without changing section polarity.
Ordinary headings, generic uses of speech/text/report/press/introduction,
unmatched root-level italic sentences, `Manuscript addition by MT`, and
`Typescript resumes` are not source boundaries. Editorial source labels and
check/end markers never enter contribution text, matching, supporting passage,
or surrounding context.

Source-section baseline state is separate from archive contribution-run state.
Every source boundary increments a deterministic document-local section ID,
ends the current contribution run, and is a hard matching boundary. An
unclassed contribution inherits an `mt`, `nonmt`, or `unverified` section
baseline. MT-baseline acceptance records
`editorial_source_section_mt_baseline`; reportorial evidence records
`editorial_source_section_nonmt_baseline`. Consecutive contributions may be
coalesced only within both one run ID and one source-section ID, so clauses are
never assembled across source sections and a later report occurrence cannot
suppress a valid direct occurrence in another section.

When the selected body contains a recognised source boundary, verified
document authorship is not seeded as attribution for unmarked material before
the first boundary. Such introductions, summaries, and reports remain
unverified; explicit pre-boundary archive or maintained speaker attribution is
still evaluated normally. Documents without a source boundary, including
documents containing only editorial check/end markers, retain the document-
author fallback.

Inside a recognised source section, an `mt` or `nonmt` content token without a
compatible active `intmt`/`intnonmt` label overrides only that block. The next
unclassed contribution returns to the section baseline and a new run. Before
that return, a standalone or incompatible explicit content block, including a
conflicting block, terminates any superseded archive-label or maintained
transcript turn state. A
compatible content token following `intmt` or `intnonmt` remains part of that
explicit labelled contribution, whose turn semantics persist to the next label
or source boundary. Outside recognised source sections, the v8 persistent-run
rules remain unchanged. In particular, a 105381-style `p.nonmt` speaker
introduction followed only by unclassed purported speech has neither a positive
source boundary nor an `mt`/`intmt` marker and remains fail-closed as non-MT or
unverified/manual evidence.

Archive attribution otherwise has first precedence and is a persistent ordered run state.
Each archive run has a deterministic document-local integer ID and an `mt`,
`nonmt`, or `conflicting` polarity. `intmt` and `intnonmt` are label-only state
changes and always start a new contribution run, including repeated equal
labels. Outside an editorial source section, `mt` and `nonmt` classify their
current content and continue an active
compatible run or start a run when the active polarity differs. Following
unclassed paragraph, list, and blockquote contributions inherit the active run
ID and polarity until a later unambiguous marker replaces it or the selected
body ends. Structural headings neither supply quotation content nor reset the
run. Conflicting explicit polarity starts a conflicting run whose following
unclassed contributions remain conflicting/unverified until an unambiguous
marker starts another run. Maintained transcript labels and contextual speaker
heuristics are used only with no active archive state. Verified whole-document
authorship is unavailable whenever recognised archive attribution exists
elsewhere in the selected body. Documents without any of the four exact tokens
retain the previous transcript-label and document-author fallbacks unchanged.

Each contribution and generated candidate records whether its applicable
polarity is explicit MT content, inherited MT content, explicit non-MT content,
inherited non-MT content, an explicit MT/non-MT label state, conflicting, or
absent. Explicit MT content uses its direct content basis; an accepted unclassed
MT continuation uses `inherited_archive_mt_run`. No synthetic speaker name is
invented when the markup supplies polarity without a visible label.

Matching coalesces consecutive searchable contribution blocks with the same
non-empty archive run ID, joining their text with ordinary spacing while
retaining the ordered unique explicit/inherited constituent provenance. Thus a
quotation may match directly across an explicit paragraph and its inherited
continuation within one run; an MT run is direct Thatcher evidence and a non-MT
run remains reported/secondary evidence. Separate run IDs, archive polarities,
conflicting attribution, repeated contribution labels, and maintained
transcript-speaker turns remain hard matching boundaries: wording found only by
joining them is rejected as assembled clauses. Supporting passage and context
for a within-run match come from that run-level unit.

Text under explicit or inherited non-MT state is not removed. An exact or
authorised-variant occurrence is retained with its supporting passage and
context as reported/secondary
diagnostic evidence, but it cannot set `accepted_as_primary_evidence` or
verified speaker attribution. A stronger `nonmt` exact occurrence also cannot
displace a valid explicit or inherited Thatcher variant, and clauses are never
assembled across archive polarities. A candidate whose best direct match is
only under explicit or inherited non-MT state receives
`reverified_rejected_archive_nonmt`, records whether that state was inherited,
and retains valid document identity rather than becoming stale.

The source inventory may remain unstable: only freshly accepted candidates
retain positive evidential value, while negative/no-hit conclusions remain
provisional. The existing package is read only and is resnapshotted before any
output is published; a source mutation aborts publication, and a failed output
write removes the incomplete package. The new ledger records the source
ledger's SHA-256 without recording either private path.

The semantic advisory categories include `additional_primary_occurrence` for
another valid date/event that does not disprove the current occurrence,
`exact_excerpt_confirmation` when the stored quotation is an exact excerpt of
a longer authoritative passage, and
`attribution_or_noncontiguous_match_review` for attribution-sensitive or
assembled wording. Only `contradictory_evidence` represents a genuine conflict
with an existing canonical claim. Date, event, or verified-text corrections
require evidence about the current occurrence or transcription; they are not
inferred merely from another occurrence or a longer source passage.
Direct MTF identity is recorded in two sets. The narrow
`current_occurrence_direct_mtf_document_ids` set comes only from the packet
stable locator and trustworthy packet/public source-role rows explicitly bound
by locator, exact day, or matching date and event. The broad
`known_evidence_direct_mtf_document_ids` set retains direct identities from all
evidence lanes for provenance, including research and model-proposed leads, but
never authorises current-occurrence replacement. Same-occurrence source
refinements may use only the narrow set; exact-day event descriptions may also
establish the same occurrence when their substantive subjects agree after
generic House of Commons, HC, speech, debate, statement, report, and motion
wrappers are removed. Clearly different dates/events remain additional
occurrences; ambiguous same-day identities are neutral manual-review cases.

Reclassification summaries preserve the identity counters and distinguish
three positive totals. `recorded_positive_candidates_remaining_valid` counts
only previously positive candidates that are identity-valid and freshly
accepted. `reverified_positive_candidate_count` counts every selected candidate
freshly accepted. `positive_candidates_remaining_valid` counts every
identity-valid, freshly reverified and accepted candidate regardless of its old
ledger status. Summaries also retain placeholder-variant rejections and the
quote IDs whose evidence is complete but whose maintained gate explicitly
requires a separate semantic/Meaning review. That advisory flag never changes
or bypasses the gate. Five candidate-level markup counters report only
documents opened by the bounded reclassification: each candidate whose selected
body contains any recognised markup, counted once; acceptances using explicit
or inherited MT state; rejections with a direct match under explicit or
inherited non-MT state; candidates retaining any explicit or inherited
reported/secondary non-MT match (including one alongside an accepted MT match);
and candidates with a match under explicit or inherited conflicting state.
Four additional candidate-level counters are equally bounded:
`candidates_with_editorial_source_sections` counts a freshly reverified
candidate once when its selected body contains at least one constrained source
boundary; `accepted_candidates_using_editorial_mt_section_baseline` counts an
accepted candidate once when its selected occurrence relies on that MT
baseline; `candidates_with_reported_nonmt_editorial_sections` counts a candidate
once when any recognised section has a non-MT reportorial baseline; and
`rejected_cross_editorial_section_boundary_matches` counts a candidate once
when its otherwise stronger wording would require crossing a source boundary.
These counters never inventory the archive or count archive elements.
