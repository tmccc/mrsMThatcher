# Pilot method findings

These findings test the research protocol against three materially different
records. They are not substitutes for full schema-v2 records.

## Line 1 — exact, checked-delivery speech

Uploaded text:

> A man may climb Everest for himself, but at the summit he plants his country's flag.

- Verification: `exact`
- Confidence: `very_high`
- Source: [Speech to Conservative Party Conference, 14 October 1988](https://www.margaretthatcher.org/document/107352)
- Provenance: Margaret Thatcher Foundation record based on Thatcher MSS; the
  page says the text was checked against delivery.
- Locator: transcript p.8, section headed “INDIVIDUAL IN THE COMMUNITY”,
  immediately after the `[end p7]` marker, using the Everest sentence as the
  quote anchor.
- Context: Thatcher was rebutting the claim that prosperity and personal
  advancement necessarily produce materialism, greed or a selfish society. The
  wider passage connects individual effort with service to family, community
  and country.
- Editorial consequence: a climber, summit and national flag are justified as
  Thatcher's own metaphor. Neither the speech nor its metadata identifies a
  particular expedition, and no evidence in this pilot links the metaphor to
  one. As an editorial inference, the image must therefore avoid implying
  Hillary, Tenzing or 1953. The immediate meaning is individual achievement
  culminating in shared national service, not conquest or solitary ego.

## Line 65 — popular paraphrase, not verbatim wording

Uploaded text:

> The problem with socialism is that you eventually run out of other people's money.

- Verification: `paraphrase`
- Confidence: `very_high` that the popular uploaded formulation is not the
  transcript's wording.
- Source: [Live Thames TV *This Week* interview, 5 February 1976](https://www.margaretthatcher.org/document/102953)
- Decisive transcript wording: “Socialist governments traditionally do make a
  financial mess. They always run out of other people's money. It's quite a
  characteristic of them.”
- Locator: transcript p.14, closing exchange, using “Socialist governments” as
  the quote anchor.
- Critical distinction: the Foundation's editorial comment identifies the
  interview as the apparent source of the later aphorism, but the transcript
  uses a multi-sentence formulation about Socialist governments exhausting
  other people's money. The editorial comment must not be copied into
  `verified_text` as though Thatcher said it.
- Context: Thatcher was attacking the Labour Government's financial record,
  spending and nationalisation while arguing for greater individual choice.
  The source itself dates this exchange to 5 February 1976; an image must not
  import a later event that this evidence does not identify.
- Editorial consequence: 1970s British public finance, taxpayer funds or an
  exhausted Exchequer can fit. A dominant Soviet tableau or imagery claiming
  that she was responding to the IMF crisis would be misleading.

## Line 120 — reordered and abridged Commons retort

Uploaded text begins:

> The right hon. Gentleman is afraid of an election is he? ...

- Verification: `variant` (reordered and abridged)
- Confidence: `very_high`
- Primary source: [Hansard, Commons, 19 April 1983, vol. 41, col. 159](https://hansard.parliament.uk/commons/1983-04-19/debates/2422d96c-7078-4211-9eb7-e899513a14ac/Engagements#159)
- Foundation mirror: [Commons statement/PMQs transcript](https://www.margaretthatcher.org/document/105294)
- Decisive official sequence: “Afraid? Frightened? Frit? Could not take it?
  Cannot stand it?” precedes “If I were going to cut and run”.
- Locator: Hansard, 19 April 1983, *Engagements*, vol. 41, col. 159, column
  anchor `#159`; Thatcher's contribution immediately follows Denis Healey's
  “Cut and run”.
- Differences: the uploaded line inserts a word, moves the Falklands sentence,
  uses contractions, omits part of the retort and changes the final clause.
- Context: Michael Foot challenged Thatcher's inflation claim; Denis Healey
  interjected about cutting and running; Thatcher answered an election challenge.
  The official exchange itself makes election and inflation the immediate
  subject, with Falklands appearing inside the retort.
- Editorial consequence: the Commons dispatch-box confrontation and election
  challenge should dominate. A Falklands naval battle would make the incidental
  reference look like the quotation's principal subject.

## General source-handling rules confirmed by the pilot

1. A Margaret Thatcher Foundation page has distinct evidential layers:
   metadata, Foundation editorial material, and the underlying transcript or
   document body. Only the last establishes Thatcher's exact words.
2. Prepared speech text does not establish delivery unless the record says it
   was delivered or checked against delivery.
3. Hansard is a strong official record but should retain its debate title,
   date, volume, column and stable anchor.
4. A proven paraphrase can have very high research confidence. Confidence and
   authenticity status measure different things.
5. Thatcher reused formulations. Record all materially relevant occurrences
   and explain why one is canonical rather than assuming the earliest hit is
   the intended source.
6. Event date, recording date and publication date must not be collapsed into
   one field.

Useful starting points:

- [Margaret Thatcher Foundation search](https://www.margaretthatcher.org/search)
- [Margaret Thatcher Foundation PREM 19 archive list](https://www.margaretthatcher.org/archive/PREM19_list)
- [Hansard search](https://hansard.parliament.uk/search)
- [About Hansard and its transcription caveats](https://hansard.parliament.uk/about)
- [The National Archives guide to Prime Minister's Office records](https://www.nationalarchives.gov.uk/help-with-your-research/research-guides/prime-ministers-office-records/)
- [Churchill Archives Centre: Thatcher Papers](https://archivesearch.lib.cam.ac.uk/repositories/9/resources/1868)
