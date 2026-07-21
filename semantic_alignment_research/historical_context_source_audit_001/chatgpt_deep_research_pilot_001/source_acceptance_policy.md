# Historical quotation source acceptance policy

Research is source discovery, not adjudication. Do not change quotation text,
IDs, eligibility, wording status, or confidence merely because a model proposes
a source.

## Accepted source roles

- `wording_verification`: the accessible source contains the quotation wording.
- `attribution_support`: the accessible source attributes the words to Thatcher.
- `source_event_support`: the source establishes the speech, interview, book, or
  other publication occasion.
- `historical_context_support`: the source directly supports a specific context
  claim.
- `secondary_recollection`: a named later recollection, labelled as such.
- `discovery_only`: a lead that does not itself prove a displayed claim.
- `rejected_irrelevant`: unrelated, circular, corrupt, or non-verifying material.

## Acceptance rules

1. Prefer Thatcher Foundation transcripts, Hansard and other official records,
   Thatcher-authored books with a page/chapter locator, contemporary newspaper
   reports, and clearly labelled memoir evidence.
2. A book supports wording only when the relevant page or searchable passage is
   actually inspected. A catalogue entry or review is not wording evidence.
3. Use a direct canonical page URL. Never return a search-results URL, grounding
   redirect, tracking redirect, or generated citation.
4. Reject quotation aggregators, copied quotation lists, Goodreads, AZ Quotes,
   BrainyQuote, Wikiquote, Medium reposts, homework sites, and unattributed video
   compilations as verification evidence.
5. Exact wording requires an exact source passage. Similar wording can only be a
   `variant`, and only when actor, action, relationship, direction, polarity,
   dates and quantities remain materially unchanged.
6. A later memoir or recollection cannot establish an exact primary transcript.
7. A source supporting only a subsidiary fact must not be labelled as quotation
   verification.
8. If a page cannot be opened and inspected, treat it as a lead, not evidence.
9. Never infer that a source supports a claim from its title, snippet or domain.
10. `No reliable source located` is a valid and preferred result when evidence
    is insufficient.

All accepted results will be fetched and independently checked locally before
they can affect public historical-context output.
