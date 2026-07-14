# Quote Research Retry Analysis

## Missing Grounding

The 101 original missing-grounding cases comprise 206 attempts. Of these, 189 preserved responses contained no grounding chunks. Search-entry HTML without chunks occurred in 145 attempts, and model-written URLs without chunks occurred in 182; neither is accepted as evidence.

Developer uses camelCase metadata and Vertex uses snake_case metadata, but both observed layouts are supported. The failures are therefore primarily provider non-return of linked chunks/supports, not an extractor incompatibility. Repair attempts are separately counted (88) and frequently lack grounding because schema-repair prompts synthesised structured output without a successful grounded search.

The affected attempts used Developer 42 times and Vertex 164 times. Both attempts with usable chunks were Developer responses; no affected Vertex response contained chunks. This is a strong transport association in this run, but not proof of a Vertex adapter defect: corpus-wide preserved Vertex responses also contain successfully extracted snake_case chunks/supports. Output length and search-query count do not supply a safe deterministic explanation: missing metadata occurred across all measured bands. Eighty-eight repair attempts had no chunks, so repair-without-a-new-grounded-search is a clear contributing mechanism. Quota/capacity events were temporally dense, making the five-minute proximity count descriptive rather than causal. Per-request concurrency was not persisted, so that correlation cannot be measured retrospectively.

## Offline Repair

The two remaining parser-only cases were repaired by discarding only their malformed final model-written `sources` arrays. Every other required field was complete. Sources were rebuilt solely from linked grounding chunks/supports in the same original response. No model claim or source was invented.

## Unique Paid Retry Plan

- Total unique candidates: 170
- Transport failures: 59
- Missing grounding: 99
- Identity and structured output: 12
- Identity subgroup: 7
- Schema subgroup: 3
- Malformed JSON subgroup: 2

The 20-item validation batch uses 7 transport, 10 missing-grounding, and 3 identity/structured-output cases. Its expected cost is $1.5554, with a hard combined ceiling of $5. Full-set expected cost is $13.2212; it is not authorised.

## Recommended Settings

### Transport failures

Retry is justified. Start with Developer, use concurrency 1, a 360-second grounded read timeout, and one retry. After two consecutive provider-wide 429 responses, persist the pause and route directly to Vertex without probing Developer again. Expected recovery probability: 80-90%.

### Missing grounding

Retry is justified only through the validation batch first. The prompt should require at least one grounded search before synthesis and explicitly state that a packet without provider-linked grounding will be rejected. Keep the same model, search tool, structured schema, and transport parity. Concurrency 1, 360-second timeout, one retry. Expected recovery probability: 60-80%; the provider may still omit chunks.

### Identity and structured output

Retry is justified. Remove `quote_id` and uploaded `quote_text` from the model-editable response schema. Supply them as an immutable request envelope and bind them locally after parsing. Keep `verified_text` separate. Any response researching another quotation remains invalid. Concurrency 1, 360-second timeout, one retry. Expected recovery probability: 85-95% for identity defects and 70-85% for malformed/schema defects.

No remaining case is classified as a genuine historical failure. No full retry run should be authorised before human and technical review of the 20-item batch.
