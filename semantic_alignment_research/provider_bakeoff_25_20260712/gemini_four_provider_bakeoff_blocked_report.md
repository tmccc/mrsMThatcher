# Gemini four-provider bake-off: blocked execution report

## Executive result

The Gemini provider implementation, four-provider comparison engine, four-way blinded review workflow, frozen-input verification, tests and cost preflight were completed. Paid execution could not proceed because the authenticated Google project has zero Gemini 2.5 Pro `generateContent` quota. No Gemini critic result was generated.

The run stopped after one lifecycle-recorded request and one diagnostic reproduction both returned confirmed HTTP 429 responses before model output. Sending the remaining 24 cases would only create guaranteed failures and was therefore unsafe and wasteful.

## Verified model and pricing

Authenticated Google model listing exposed stable `models/gemini-2.5-pro`:

- display name: Gemini 2.5 Pro;
- version: 2.5;
- stable model code: `gemini-2.5-pro`;
- output token limit: 65,536;
- `generateContent`, `countTokens`, caching and batch methods available;
- structured outputs and thinking supported by official model documentation.

Stable 2.5 Pro was selected instead of a preview 3.x model because its capability is suitable and the task preferred generally available Pro models where comparable. Official sub-200k pricing used $1.25/M input, $0.125/M cached input, and $10/M output including thinking tokens.

Sources: [Gemini 2.5 Pro](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro) and [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Credential method

The canonical environment variable is `GEMINI_API_KEY`, loaded from the ignored project `mrsMThatcher.env`. `GOOGLE_API_KEY` is supported as a compatibility alias. The key was neither printed nor persisted in source, outputs, logs, fixtures or reports.

The credential successfully authenticated model listing. It did not have inference quota for the selected model.

## Frozen-input verification

- `cases.json` remained at SHA-256 `74a796fc5d2d6d3f1a9b084b48a4b7b2820eaea7cee7f6c34ddc69da95345f7e`.
- Exactly 25 unique case IDs were validated.
- The free-trade case was present.
- Prompt version remained `provider-neutral-picture-editor-v1`.
- Common critic schema remained v1.
- All 25 normalised request inputs were hashed in `input_parity_manifest_four_provider.json`.
- Grok, OpenAI and Claude results, ledgers, comparisons, mappings and prior preference data were hashed before Gemini work.

No case was reselected and no fingerprint was regenerated.

## Prompt and schema parity

Gemini uses the same `common_prompt()` text and provider-neutral result schema as Grok, OpenAI and Claude. The Gemini envelope contains only:

- one text content part containing the shared prompt;
- native `application/json` response schema;
- 1,600 output-token maximum;
- restrained 512-token thinking budget.

No tools, search, grounding, URL context, retrieval, code execution, raw image or provider/human result data is present.

## Cost preflight

| Metric | Estimate |
|---|---:|
| calls | 25 |
| input tokens | 51,900 |
| expected output including thinking | 22,500 |
| maximum output allowance | 40,000 |
| expected cost | $0.289875 |
| conservative maximum | $0.464875 |
| provider ceiling | $1.500000 |
| combined new-work ceiling | $2.000000 |

The estimate was within both limits. Actual Gemini critic cost is $0 because no inference completed and no usage metadata was returned.

## Exact blocker

Google returned `RESOURCE_EXHAUSTED` with these explicit quota violations for `gemini-2.5-pro`:

- `GenerateContentInputTokensPerModelPerDay-FreeTier`, limit 0;
- `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 0;
- corresponding per-minute request and input-token limits, limit 0.

The runner recorded the first 429 as a confirmed-safe HTTP failure, not an ambiguous billed outcome. A diagnostic reproduction confirmed the account-level quota message. There were:

- 0 completed Gemini cases;
- 0 billed Gemini model calls;
- 0 Gemini tokens;
- $0 Gemini cost;
- 0 ambiguous outcomes;
- 1 unique frozen case attempted by the runner;
- 24 cases not attempted.

The ledger remains resumable and all existing providers remain independent and valid.

## Implemented four-provider support

Research-only implementation now supports:

- Gemini credential aliasing and request serialisation;
- native Gemini JSON schema output;
- thinking-token extraction and billing;
- durable provider attempts and isolation;
- N-provider pairwise comparison;
- four-way 4/4, 3/1, 2/2 and complete-disagreement detection;
- modal relationship/decision, median scores and provider consensus distance;
- stable sealed A/B/C/D mapping;
- a separate four-way preference file preserving older A/B and A/B/C data;
- a practical best/optional-second-best blinded interface.

No real four-provider comparison or review queue was generated because doing so without Gemini results would fabricate evidence. Synthetic tests cover those paths.

## Existing preferences

Two prior A/B preferences remain unchanged in `blinded_preferences.json`. No A/B/C preference file existed. No four-way preference was created. Older labels were not translated or overwritten.

## Tests

- Python compilation: passed.
- `git diff --check`: passed.
- semantic pipeline, calibration, provider bake-off and production-log-isolation tests: **86 passed in 2.14s**.
- Gemini APIs were mocked in tests.

Focused coverage includes four-provider semantic prompt parity, no tools, Gemini parsing/token/cost handling, private four-way mapping, six pairwise comparisons, 3/1 and 2/2 detection, complete disagreement, consensus distance, blinded storage and provider failure isolation.

## Files changed

Research-only files changed:

- `semantic_alignment/bakeoff.py`;
- `analyse_semantic_alignment_bakeoff.py`;
- `tools/semantic_alignment_bakeoff_review.py`;
- `tests/test_semantic_alignment_bakeoff.py`.

Research outputs added under the existing bake-off directory:

- authenticated Gemini model metadata and pricing verification;
- four-provider input parity manifest;
- four-provider preflight;
- resumable Gemini ledger;
- this blocked report.

No Gemini result or four-provider comparison file was created.

## Required next action

Enable paid Gemini Developer API billing/quota for the project associated with `GEMINI_API_KEY`, or provide another authorised key with non-zero `gemini-2.5-pro` quota. Then rerun only the resumable Gemini provider command. Do not rerun Grok, OpenAI or Claude.

## Safety and Git state

Cached fingerprints were reused and none regenerated. Grok, OpenAI and Claude were not rerun or altered. Only one of the 25 Gemini cases was attempted before the quota blocker; the other 24 were deliberately not sent. No search, grounding or tools were enabled. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited by the research process. The canonical simulator was not rerun. Nothing was staged, committed, pushed or deployed.

`git diff --stat` continues to show only the pre-existing generated-image curation metadata changes and four quarantined PNG deletions because the research files are untracked. `git status --short` retains those changes and the existing untracked research/runtime artifacts.
