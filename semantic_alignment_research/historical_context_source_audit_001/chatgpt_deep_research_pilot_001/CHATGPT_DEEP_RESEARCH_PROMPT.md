# ChatGPT Deep Research task: Margaret Thatcher quotation sources

Use Deep Research with the public web and the two attached files:

- `pilot_queue.json`
- `source_acceptance_policy.md`

Research every one of the 25 queue records. This is a source-discovery task, not
a request to rewrite quotations or decide production eligibility.

## Required method

1. Start from the exact quotation, but also use the recorded source event, date,
   book, chapter, document number and historically plausible wording variants.
2. Inspect every proposed page itself. Do not rely on search snippets or another
   model's assertion.
3. Prioritise primary and claim-bearing sources, especially
   `margaretthatcher.org`, Parliament/Hansard, publisher or accessible book
   pages, contemporary newspaper archives, and clearly identified memoirs.
4. Apply `source_acceptance_policy.md` strictly. Previously rejected or
   suppressed links are warnings, not evidence.
5. Do not omit difficult records. Return `no_reliable_source_located` when that
   is the honest result.
6. Do not treat an 80% lexical match as sufficient by itself. For a historical
   variant, compare actor, action, relationship, direction, polarity, date and
   quantities and explain every material difference.
7. Do not call any wording exact unless the inspected source contains it.

## Required result for every record

Return a brief human-readable findings section followed by one fenced JSON
object with this shape:

```json
{
  "schema_version": 1,
  "completed_record_count": 25,
  "results": [
    {
      "sequence": 1,
      "quote_id": "",
      "quote_text": "",
      "outcome": "reliable_source_found|defensible_variant_found|secondary_recollection_only|no_reliable_source_located",
      "wording_assessment": "exact|variant|recollection|unverified",
      "sources": [
        {
          "direct_url": "",
          "title": "",
          "publisher_or_archive": "",
          "publication_or_event_date": "",
          "source_roles": ["wording_verification"],
          "source_quality": "strong_primary|reliable_secondary|secondary_recollection|discovery_only|rejected",
          "exact_supporting_passage": "",
          "stable_locator": "page, chapter, speech date, column, document ID, or paragraph",
          "claims_supported": [],
          "claims_not_supported": [],
          "wording_differences": [],
          "rationale": ""
        }
      ],
      "rejected_candidates": [
        {"url": "", "reason": ""}
      ],
      "remaining_uncertainty": [],
      "confidence": "high|medium|low"
    }
  ]
}
```

Constraints:

- Preserve each supplied `quote_id` and `quote_text` byte-for-byte in the JSON.
- Include exactly 25 results, in queue sequence order, with no duplicate IDs.
- Include no more than three accepted sources per quotation.
- A source URL must lead directly to the inspected page.
- A short exact passage and stable locator are mandatory for any accepted role.
- Do not invent page numbers, dates, titles, passages or URLs.
- Do not recommend changing quotation eligibility.

Before finishing, count the input and output IDs and explicitly confirm that all
25 were handled.
