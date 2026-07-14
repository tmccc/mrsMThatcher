# Semantic validation rules

JSON Schema validates shape and basic types. The following additional checks
are mandatory before a record can be imported.

## File and identity

1. The source-file SHA-256 must match the frozen manifest.
2. Each `quote_id` and `quote_hash` must equal the SHA-256 of exact
   `quote_text` UTF-8 bytes without a trailing newline.
3. Every source line locator must reproduce `quote_text` exactly.
4. Every source line appears once in the occurrence index. Exact duplicate
   lines may share one quote ID.
5. Source line numbers are locators, never durable join keys.
6. Uploaded `quote_text` is immutable. Corrections belong in `verified_text`;
   changing raw text creates a new quote hash and requires an explicit migration.
7. Final record ordering is deterministic by first source line.

## Attribution and sources

8. `exact`, `normalised` and `excerpt` require direct evidence with a stable
   locator and a primary original or primary reproduction source.
9. `variant` and `paraphrase` require direct evidence for the authentic
   underlying wording plus variation notes explaining the relationship.
10. `composite` requires a separate source event and direct evidence locator for
    every component.
11. `misattributed` requires reliable alternative-attribution evidence or a
    precise, cited contradiction. Failure to find a source alone is
    `unverified`.
12. `primary_source_available = yes` requires a referenced source classified as
    `primary_original` or `primary_reproduction`.
13. A Margaret Thatcher Foundation title, summary, tag or editorial comment
    cannot be direct evidence for words in the underlying transcript.
14. For Hansard, `exact` means exact to the official substantially-verbatim
    record; suspected scanning or reporting errors require a corroborating check.
15. Search snippets and discovery-only sources cannot support factual fields.
16. Every source, source-event and evidence reference must resolve inside the
    record.
17. Structured dates must contain real calendar values. Precision, start and end
    values must agree, and event and publication dates must remain separate.

## Claim-level provenance

18. Every non-null factual field must be targeted by evidence, or its enclosing
    claim must be explicitly marked `speaker_claim` or an inference.
19. Every unknown, empty or not-applicable value whose interpretation matters
    must have a corresponding `field_status` entry.
20. A `json_pointer` evidence target must resolve within its record; a
    `claim_id` target must resolve to exactly one typed claim.
21. Direct evidence requires both a stable locator and the minimum decisive
    exact excerpt. An analyst's note is not a substitute for source wording.
22. Each historical-accuracy requirement must have basis `historical_fact` and
    cite evidence.
23. Editorial recommendations must not masquerade as sourced historical facts.
24. Thatcher's factual, causal or political assertions are `speaker_claim`
    unless independent evidence separately establishes them.
25. Locators must be durable: transcript page/section and quote anchor; Hansard
    date/debate/volume/column; book edition/page/chapter; archive folio; or AV
    timecode. Browser-result line numbers and bare home pages are invalid.

## Editorial consistency

26. `misattributed` and `unverified` require `guidance_status =
    do_not_generate` and an empty `visual_concepts` list.
27. `composite` or materially unresolved wording requires at least
    `guidance_status = provisional`.
28. `guidance_status = ready` requires two or three ranked visual concepts;
    `do_not_generate` requires none.
29. Each visual concept describes one coherent composition.
30. Mandatory visual objects must not conflict with forbidden objects.
31. Period, place, flags, organisations, uniforms and technology must agree
    with established context.
32. Thatcher's presence cannot be `required` merely because she said the quote.
33. An incidental adversary cannot dominate if doing so reverses the desired
    first impression.
34. Unresolved geography or event identity cannot produce a mandatory specific
    historical depiction.

## Confidence and publication

35. Confidence is assessed separately for wording, attribution, source event,
    date, context and editorial interpretation.
36. Confidence describes confidence in the recorded research conclusion, not
    the probability that the uploaded attribution is genuine. A proven
    paraphrase can have `very_high` confidence.
37. Low or unknown wording/attribution confidence requires `hold_for_review` or
    `exclude`.
38. `misattributed` requires `exclude`; `unverified` requires
    `hold_for_review` or `exclude` and can never be automatically eligible.
39. `composite` requires `hold_for_review` until every component and intended
    publication wording receive explicit human approval.
40. `paraphrase` or `variant` cannot be `eligible` with uploaded wording; use
    `eligible_with_verified_wording` or `hold_for_review`.

## Batch and final-database completeness

41. An input batch must match `research_batch.schema.json`, its manifest hash,
    and the exact manifest records selected for that batch.
42. A compiled output batch must contain one canonical record for every
    exact-distinct quote ID in its input batch. Difficult or unresolved records
    are still full records and are never replaced by an unresolved-list entry.
43. An output batch's `unresolved_quotes` is only an index. It must exactly
    mirror records whose `unresolved_work.is_unresolved` is true.
44. Related quote IDs outside the current batch may resolve through the frozen
    full manifest; they must resolve to final-database records before final merge.
45. Every output batch passes record, batch-coverage and reference validation
    independently before merge.
46. The final database contains exactly 632 canonical quote records, represents
    all 633 source occurrences exactly once, and has no extra quote IDs.
47. The final `unresolved_quotes` index exactly mirrors unresolved canonical
    records; it never substitutes for them.
48. All low-confidence, unverified, paraphrase, composite and misattributed
    records receive a second research pass. A random sample of high-confidence
    records receives independent source audit.
49. Production import is a separate, explicitly reviewed operation. Research
    completion alone never changes live selection behaviour.
