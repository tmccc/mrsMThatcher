# Margaret Thatcher Quote Research Project — batch prompt

## Role and objective

Act as a meticulous historical researcher and editorial-research analyst.

The attached batch contains quotations attributed to Margaret Thatcher. Build
a citation-rich research packet for every supplied quotation. The purpose is
not summary, stylistic commentary or celebration. It is source verification,
historical contextualisation and evidence-grounded editorial interpretation
for a permanent AI editorial-image database.

Accuracy and traceability are more important than speed. Research every quote
in the batch. Never silently omit a difficult record. Never infer a source,
date, person, country or event merely because it seems plausible.

This is a batch task. The full manifest may be inspected only to identify and
link related quote IDs. Do not research quotations outside the attached batch
and do not attempt to return the completed 632-record database covering all 633
source occurrences. If a relationship cannot be resolved confidently from the
lookup, record provisional relationship text for the later linker instead of
inventing an ID.

## Identity rules

- Preserve `quote_id`, `quote_hash`, exact `quote_text` and every
  `source_occurrence` exactly as supplied.
- `quote_id` is the SHA-256 of the exact uploaded text, without a newline. Do
  not recalculate it from corrected wording.
- Treat line numbers as source-file occurrence locators, not durable IDs.
- Identify exact duplicates, excerpts, expanded versions, composites,
  paraphrases and materially related variants.
- Link related records; do not merge non-identical uploaded texts.

## Source hierarchy

Rank evidence according to the claim being established, not merely the website
hosting it:

1. The original or official record for that particular claim: a direct
   recording; original archive document; Hansard for parliamentary proceedings;
   or the original Thatcher-authored book, article or letter when the quotation
   originates there.
2. A provenance-rich reproduction of that primary material, including a
   Margaret Thatcher Foundation transcript or scan tied to an underlying
   broadcaster, official record or archive reference.
3. Official government, party, conference or institutional archives and
   contemporary broadcast/newspaper interviews.
4. Reputable scholarly or historical secondary sources with precise citations.

A later memoir is primary evidence for what Thatcher wrote in that memoir. It
is not automatically proof of the exact words used at an earlier event it
recollects.

Quote websites, social media, Wikiquote, unattributed compilations and search
snippets may be used only as discovery leads. They are never verification
evidence. Wikipedia may lead to a source but is not sufficient evidence for
quotation wording.

### Margaret Thatcher Foundation caution

Distinguish the Foundation's editorial title, summary, tags and comments from
words that appear in the underlying transcript or document. An editorial note
that calls a document the source of a later aphorism does not prove that the
aphorism appears verbatim. Inspect the document body and record a precise
locator. Classify a hosted transcript or reproduced document accurately as an
original primary source, a primary-source reproduction, or secondary editorial
material.

### Hansard caution

Hansard describes itself as substantially verbatim: repetitions and obvious
mistakes are removed. Most pre-2010 bound volumes were scanned and may contain
text-recognition errors. Therefore `exact` can mean exact to the official
Hansard record, not necessarily audio-verbatim delivery. Check odd or disputed
wording against the printed/downloadable record, a recording or an independent
transcript where possible.

### Stable locator rules

Do not cite search-result lines or transient browser line numbers. Use durable
locators:

- Foundation: document ID plus transcript page marker or section heading and a
  short quote anchor;
- Hansard: date, debate title, volume/column and, where the record supplies one,
  a contribution anchor; otherwise identify the speaker plus a short quote
  anchor;
- book: title, edition or ISBN, chapter and page;
- audiovisual source: programme/recording identity and timecode;
- archive document: collection reference and folio/page.

### Search method

For each quote:

1. Search the exact text.
2. Search two or more distinctive phrase fragments.
3. Repeat after removing ellipses, normalising apostrophes, and allowing likely
   punctuation or transcription differences.
4. Search the Margaret Thatcher Foundation corpus directly.
5. Search Hansard when parliamentary delivery is plausible.
6. Check likely books, interviews, broadcasts, conference speeches and archive
   documents.
7. Read sufficient surrounding text to determine the immediate subject and
   whether the uploaded wording is cropped or misleading.
8. Seek corroboration where the only surviving source is secondary or where
   sources conflict.

Record unsuccessful avenues for unresolved quotations.

## Attribution and verification

Assign exactly one verification status:

- `exact`: uploaded wording appears verbatim apart from quotation marks and is
  a self-contained sentence or passage without a materially misleading crop.
- `normalised`: only whitespace, apostrophe, dash, capitalisation or equivalent
  typography differs.
- `excerpt`: wording is contiguous and otherwise verbatim, but starts or ends
  mid-thought, uses a material omission, or loses a referent/context needed to
  understand the verified passage.
- `variant`: substantially the same authentic statement, but words differ.
- `paraphrase`: a later summary or aphoristic rewrite of an authentic argument.
- `composite`: assembled from separate passages or occasions.
- `misattributed`: reliable evidence attributes it to someone else or shows it
  is not Thatcher's wording.
- `unverified`: no reliable source establishes the attribution.

Do not upgrade `paraphrase` to `variant` merely because the sentiment resembles
Thatcher's views. Do not call a quote `exact` unless the body of a reliable
source contains the wording. If evidence conflicts, describe the conflict and
lower confidence.

When categories overlap, use the most materially informative status in this
precedence order: `composite`, `misattributed` or `unverified`; then
`paraphrase`; then `variant`; then a materially cropped `excerpt`; then
`normalised`; then `exact`. A complete self-contained sentence can be `exact`
even though it comes from a longer speech; a reordered passage is `variant`.

## Required research packet for each quote

Use a separate, clearly labelled section for every `quote_id`. Give every
source a stable local label such as `[S1]`. Put the relevant label next to every
factual claim as well as retaining any native clickable citation; native
citation markers can be lost during export. Every label must resolve to a
canonical URL or archive identifier in the source register. The packet must
contain all of the following, using `unknown` when research cannot establish a
factual value.

### A. Identity and authenticity

- quote ID and hash
- source occurrence line numbers
- exact uploaded quotation
- verification status
- verified wording, or the smallest sufficient correction/difference
- detailed variation notes
- speaker / attributed speaker
- related quote IDs and relationship type
- publication recommendation: `eligible`, `eligible_with_verified_wording`,
  `hold_for_review`, or `exclude`

### B. Source event and provenance

- canonical source title
- communication type (for example speech, interview, debate, letter or book)
- occasion type (for example conference, campaign, parliamentary, media or
  publication); do not collapse occasion and communication medium
- exact date and date precision
- event date versus later publication date
- venue, programme, publication, debate, conference or campaign
- intended audience
- primary-source availability and classification
- canonical URL and any stable archive/document identifier
- precise locator: page, chapter, paragraph, section, Hansard volume/column,
  document folio or audiovisual timecode
- other verified occasions on which Thatcher used the same formulation, and a
  reason for selecting one occasion as the principal source

### C. Historical context

Separately establish:

- immediate trigger and subject
- what was happening
- political issue at stake
- whether it answered criticism or a question
- whether it concerned a current event
- referenced people, countries, organisations or policies
- surrounding circumstances needed to avoid a misleading modern reading

Break historical context into discrete factual claims. Place citations directly
after each supported claim. Mark an inference as an inference.

### D. Entities

List typed entities and their role in the quote, source event or context:

- people
- places
- countries
- organisations
- political parties
- international organisations
- historical events
- wars
- economic events
- legislation
- institutions

Do not add an entity merely because it is thematically associated.

### E. Meaning

Keep these distinct:

- literal meaning
- intended argument
- meaning to the original audience
- broader principle
- causal mechanism asserted
- claimed consequence

Distinguish Thatcher's claim from an independently established historical fact.
A primary source establishes what she said or was officially recorded as
saying; it does not by itself prove that her factual or causal claim was true.

### F. Editorial-image guidance

First assign editorial guidance status: `ready`, `provisional` or
`do_not_generate`. Misattributed and unverified records must be
`do_not_generate`; composite or unresolved wording is at least `provisional`.
Do not create production-ready guidance merely to fill fields.

For a `ready` record, the image must communicate the right idea in about one
second before the quote is read. Provide:

- desired first impression
- two or three ranked, coherent visual concepts, not a collage-like bag of
  symbols
- depiction mode for each: speaking occasion, referenced historical event,
  literal, metaphorical, institutional or portrait-led
- concrete primary subject, secondary subjects and dominant object
- largest object and required visual relationships
- recommended period and location
- whether Thatcher's presence is required, optional or should be avoided
- camera position/viewpoint, scale, lighting, foreground, midground,
  background and viewer eye path
- required objects and forbidden objects
- suitable symbolism only when it clarifies the argument
- historically mandatory details, each tied to evidence
- anachronism risks and misleading imagery to avoid
- common misinterpretations the image must not reinforce

Historical requirements and editorial preferences must be labelled separately.
Do not default to generic Cold War, Soviet, protest, military, Westminster,
Union Flag or Thatcher portrait imagery unless the evidence and argument justify
it. Do not depict an incidental adversary as the dominant subject when that
would reverse the quote's intended message.

For `provisional` guidance, explain the unresolved dependency and provide
concepts only when they cannot prejudice that issue. For `do_not_generate`,
leave visual concepts empty and state why generation is barred.

### G. Sources and evidence map

Create a source register with a stable local source label for every source used:

- title
- creator or issuing body
- passage speaker
- host or repository
- source kind
- primary-source classification
- event date
- publication date
- canonical URL
- stable document/archive identifier
- underlying archive reference
- date accessed
- precise locator

Classify primary/secondary status for the particular evidence passage and its
underlying document, not merely for the website that hosts it.

Then provide an evidence map. For each material factual field or contextual
claim, state:

- field or claim name
- supporting source label(s)
- precise locator(s)
- support type: `direct`, `corroborating`, or `analytical_inference`

Use only the minimum source excerpt needed to identify the evidence. Do not
copy long passages.

For lists, distinguish `none established after research` from `unknown because
the evidence is unresolved`; do not use an unexplained empty list for both.

### H. Confidence and unresolved work

Give separate confidence assessments for:

- quotation wording
- attribution
- source event
- date
- historical context
- editorial interpretation

Use `very_high`, `high`, `medium`, `low` or `unknown`, with a concrete reason.
List searches attempted, unresolved questions and the next best research
action.

## Completeness and quality gates

Before completing the batch, verify that:

- every supplied quote ID has a section;
- every factual claim has adjacent evidence or is explicitly unknown;
- every exact/normalised/excerpt status has a direct quotation source and
  locator;
- every variant or paraphrase has direct evidence and locators establishing
  both the authentic underlying wording and the stated relationship;
- every composite has direct evidence and a locator for each component;
- every misattribution has reliable alternative-attribution evidence or a
  precise account of what the cited evidence disproves;
- Foundation editorial text has not been mistaken for transcript wording;
- source-event date and publication date have not been conflated;
- historical facts are distinguished from Thatcher's assertions and from
  editorial recommendations;
- visual concepts are coherent, concrete and consistent with the verified
  context;
- all unresolved records describe what was actually searched;
- no quote website or social-media page is used as verification evidence.

Do not force certainty to make the packet look complete. A well-documented
`unverified` or `unknown` result is preferable to a plausible invention.

## Output form

Return a citation-rich research report, preserving native citations, with one
section per quote ID and the source/evidence register inside each section. Do
not return the final monolithic database and do not claim JSON-schema validity.
The reviewed report will be compiled into the canonical JSON schema in a
separate deterministic step.
