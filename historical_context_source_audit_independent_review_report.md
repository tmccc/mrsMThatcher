# Historical-context source audit: independent review

Date: 21 July 2026

## Executive verdict

The narrow independent review is complete. All 34 AI-located source records were
checked against the actual public pages, covering 28 unique URLs. Every page
returned HTTP 200 and every recorded supporting passage was present in the page
fetched for this review. The results are:

- 30 source records retained without role changes;
- 2 approximate matches downgraded to attribution support only;
- 1 source relabelled as a secondary recollection;
- 1 circular OUP quotation round-up rejected from evidentiary and public use.

The review found no reason to change any quotation text, quote ID, attribution
eligibility or regular-post cycle membership. The research set remains 626
completed packets, 610 of which are attribution-eligible. The six separate
unresolved canonical records remain ineligible.

The previous report's zero count for `secondary_recollection` was an accounting
defect. It counted only one class of physical source and omitted virtual locator
evidence. The corrected all-evidence count is four. Recollection evidence is
explicitly labelled and never produces an exact-wording claim on its own.

## Review method

The review did not rerun AI research. It used the frozen provider results only to
identify the 34 records requiring independent checking. For each record it:

1. fetched the final HTTPS URL directly;
2. required a successful HTTP response;
3. inspected the page title, archive metadata and text surrounding the passage;
4. required the recorded passage to occur in the fetched page;
5. compared actor, proposition, direction, polarity, dates and quantities;
6. assigned final source roles and a source-quality class;
7. stored the exact passage, stable locator, page-body hash, passage hash and
   source-record hash in a fail-closed manifest.

The page shells on several sites are dynamic, so provider-time and review-time
whole-page hashes commonly differ. Acceptance depends on the stable page
locator and exact passage, whose hash is recorded separately. A changing page
shell is not treated as evidence that a passage changed.

Authoritative review manifest:

`semantic_alignment_research/quote_research_full_001/historical_context_source_independent_review.json`

SHA-256:

`6251250f9ec102fa65ac2791b9945145df16135545034b5023f327bc7b24d6df`

The manifest contains the full quote ID, source ID, exact supporting passage,
exact passage SHA-256, page title, final URL, stable locator, source-record hash,
current and provider page hashes, final roles, claims supported and rationale for
all 34 records. The table below is a compact index; identifiers are shortened
only in this human-readable table.

## AI-located source verification

| Quote ID | Source ID | Decision | Final role | Quality | Locator | Passage SHA-256 |
|---|---|---|---|---|---|---|
| `f229667039f3` | `115dbeced19d` | keep | wording, attribution | primary | MTF 108353, Keith Joseph Memorial Lecture | `64ea9079cf41` |
| `412786187132` | `14038d823cb2` | keep | wording, attribution | secondary | Time, *A Tory Wind of Change* | `66b3c273df9e` |
| `8f7f5440bd00` | `16f281ce54b2` | keep | wording, attribution | primary | MTF 105096, Granada TV interview | `618cd39e4f3f` |
| `3fb0e6e6f45d` | `16fa874acf56` | keep | wording, attribution | primary | MTF 101374, Townswomen's Guilds | `b2f169796263` |
| `1646e92be458` | `17fab80394c1` | keep | wording, attribution | primary | MTF 108353, Keith Joseph Memorial Lecture | `fa8916fa079f` |
| `1244294a90af` | `1997f550f04a` | keep | wording, attribution | secondary | Hansard, Equality and Diversity Bill | `156e4ee1c7f1` |
| `a9fad9cc9fc5` | `227e53f65272` | keep | attribution | primary | MTF 108051, Conservative Central Council | `0ea89fbb2e6f` |
| `5e5c7e9155b8` | `3741616d1bc5` | keep | attribution | primary | MTF 102728, Free Enterprise Week | `4a087ca20c9b` |
| `741960f5326f` | `4585540959f1` | keep | wording, attribution | primary | MTF 107789, party conference | `c81f13d7494a` |
| `f4323817daee` | `4b7416e71ce9` | keep | wording, attribution | secondary | Hansard, EU Membership Bill | `90c4146b0cb3` |
| `3fb0e6e6f45d` | `5daed0133a71` | reject | discovery only | circular | OUPblog quotation round-up | `6d0eb7100a5e` |
| `8ef96d03f909` | `5e56df7af5b6` | keep | wording, attribution | primary | MTF 108353, Keith Joseph Memorial Lecture | `ed63de3ff07b` |
| `5f343702a183` | `63baba4fe352` | downgrade | attribution | primary | MTF 103684, Scottish conference | `02ef132cdb0f` |
| `7c3abbd29080` | `683845a778f6` | keep | wording, attribution | primary | MTF 108386, Language of Liberty | `05a55ae20cec` |
| `8b75093bc5c6` | `68c5abee1bf5` | keep | wording, attribution | primary | MTF 103684, Scottish conference | `c21ad585315e` |
| `313172d18e2d` | `75eea1bcef19` | keep | wording, attribution | secondary | The Herald, European funding report | `6a887d7fa6b3` |
| `c2c09852800a` | `76d2e80e2c97` | keep | wording, attribution | primary | MTF 104032, Sunday Express article | `eadaad0cc6e9` |
| `5375f62e3ac4` | `7a86f9a96233` | keep | wording, attribution | primary | MTF 108284, Jagiellonian University | `908dfae416f6` |
| `e9d8f7dff356` | `7b2c51a92899` | keep | attribution | primary | MTF 100857, Dartford adoption meeting | `aca8f5d45681` |
| `5f14e6e60077` | `7cf5cbd0212d` | keep | wording, attribution | primary | MTF 108183, European Democrat Union | `697e2286fe35` |
| `4c950f10a42b` | `7ddc1f02b2b8` | keep | attribution | primary | MTF 103926, Winter of Discontent broadcast | `8957cbbdc0d1` |
| `4c9b5b2d2dd3` | `81942922ff3f` | keep | attribution | primary | MTF 105407, International Democrat Union | `d1133fed9c91` |
| `da6727d2a7d9` | `81ef36c17145` | keep | wording, attribution | primary | MTF 108353, Keith Joseph Memorial Lecture | `d49ca4f955eb` |
| `93fa5a4803e4` | `8225f29c9299` | keep | wording, attribution | primary | MTF 106228, Parade interview | `070ddf8edf5d` |
| `53699726287c` | `825ab7405ec0` | downgrade | attribution | primary | MTF 106228, Parade interview | `ceeb0b02a7a7` |
| `7767b08facbc` | `ba78525ef5a2` | keep | wording, attribution | primary | MTF 103684, Scottish conference | `47bacb5aeef8` |
| `f21b03167596` | `c640389c343c` | keep | wording, attribution | primary | MTF 108380, Churchill Memorial Concert | `25db6f031d5c` |
| `80f94e530bec` | `d2e8deb89c03` | keep | wording, attribution | primary | MTF 108368, Nicholas Ridley lecture | `369260642f1f` |
| `d2b235b5393d` | `d646da849ddc` | keep | wording, attribution | secondary | Hansard, Lord Chancellor's Oath | `2b031f2bd61b` |
| `1dde0bb1ec4f` | `ddeba47e4ff6` | keep | wording, attribution | primary | MTF 103101, Canberra speech | `912f157ca2bc` |
| `a97e6dd2f444` | `de28b9d42802` | keep | wording, attribution | secondary | Hansard, 15 October 2014 | `ce5d8acf1963` |
| `3ba6cfba4bbf` | `f18732a95461` | keep | wording, attribution | primary | MTF 105724, Finchley remarks | `0a3f2be7a8ef` |
| `5ed720b08c8e` | `f569d184ec62` | keep | attribution | primary | MTF 105472, Lord Mayor's Banquet | `0c12be1e76e5` |
| `e7f47c3d78e9` | `f89fca2576ea` | relabel | recollection, wording, attribution | recollection | Independent report of Paul Johnson recollection | `9e30471980e5` |

The OUPblog record is not used publicly. Its quotation round-up gives no stable
source locator and is circular for verification. The direct MTF transcript for
the same quotation remains available, so rejecting the OUP page creates no
evidence loss.

## Approximate wording matches

None of the nine records below is labelled `exact wording verified`.

| Quote ID | Decision | Finding |
|---|---|---|
| `a9fad9cc9fc541d39017aabccaac1047f7079fb3ff7558c07373789e22072cb4` | defensible variant | Ampersands, punctuation and an applause marker differ; actors and proposition are unchanged. |
| `5e5c7e9155b845150fe903c5f9951af17530a54d930af8706a438ca49b2f2fc1` | defensible variant | The inserted text is an archive page-break marker. |
| `741960f5326f4c8ff102c4572f1624facdfaf123a476a66b15ebbca3bb888a06` | defensible variant | Omissions and normalisations preserve 1989, 1979, actors, direction and positive polarity. |
| `5f343702a183a99143ff0935165238b5d5ee0732521a258470450e207fb147e5` | downgrade | The canonical paraphrase borrows Ted Short's wording and combines it with Thatcher's reply. It supports attribution, not the recorded wording. |
| `e9d8f7dff356e2364bd2250fe6a4f4b5aecfde205cf7e66c316e0850a20bf316` | defensible variant | The corpus omits an article. The 1950 Dartford report is the source, rather than the later book occasion in the packet. |
| `4c950f10a42b15df1d14e37a5f431c0f20f99ef520033c4a152cd44e3e751621` | defensible variant | The source says "We have" rather than "We shall have"; actor, warning and direction are unchanged. |
| `4c9b5b2d2dd30ba5dc20d850646ca3e897d782d8d27634866f85e63cb06b8d98` | defensible variant | The difference is an archive page-break marker; the proposition is unchanged. |
| `53699726287c4fed9ef2b086e53cc2938b1fd482a7b5541f4718df3439598215` | downgrade | "What is success?" is the interviewer's question. Only the following answer is Thatcher's wording. |
| `5ed720b08c8e355b93cb25a06c458e68423c873678883cccb2aa3874d5d13017` | defensible variant | The inserted text is an archive page-break marker. |

The seven defensible records retain their existing non-exact classifications.
The two downgraded records remain in the quotation corpus and remain eligible;
only the claims made for their supporting sources are narrowed.

## Secondary recollections

Four all-evidence records are now classified as secondary recollections:

| Quote ID | Recollection | Result |
|---|---|---|
| `52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351` | Hugo Young, *One of Us* (1989), p. 193 | Rendered as a secondary recollection; no exact-wording claim. |
| `573412501ec88441938dae368acf42712f3952fd31aeab5c85dcd10ec7c968a4` | Jim Prior, *A Balance of Power* (1986), p. 106 | Rendered as a secondary recollection; no exact-wording claim. |
| `b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c` | BBC1 *Panorama*, 9 April 1984, later on-record recollection | Rendered as recollection evidence, not as the original event transcript. |
| `e7f47c3d78e910d0639668eca12491cb6406ad22191d4acc33dfb45562a5f44b` | Paul Johnson's account of a 1982 private conversation, reported by *The Independent* | Relabelled as a secondary recollection; no primary transcript claimed. |

The Woodrow Wyatt record (`8143e19d...`) cites *The Journals of Woodrow
Wyatt*, volume 3, but supplies no page or entry locator. It remains
non-renderable and marked for further research. The review did not promote it
merely because the book is plausible.

The previous zero arose because the summary counted only physical packet
sources and omitted virtual locator records, where the Hugo Young and Jim Prior
citations live. The detector also considered arbitrary model prose. It now uses
source title/type, stable locator and source event, with known recollection works
identified explicitly. This both captures real recollections and avoids prose
false positives.

## Revised source counts

Headline counts are now mutually exclusive. They describe the 3,097 observed
records:

| Provenance category | Count |
|---|---:|
| Physical packet sources | 2,017 |
| Recovered provider citation sources | 256 |
| Model-proposed source leads | 790 |
| AI-located, independently reviewed sources | 34 |
| **Total observed records** | **3,097** |

| Disposition category | Count |
|---|---:|
| Accepted evidence | 635 |
| Discovery-only or insufficient | 2,089 |
| Rejected or non-verifying | 373 |
| **Total observed records** | **3,097** |

An additional 278 virtual locator records produce 3,375 all-evidence records.
Their mutually exclusive quality classes are:

| Quality class | Count |
|---|---:|
| Strong primary evidence | 873 |
| Reliable secondary evidence | 36 |
| Secondary recollection | 4 |
| Discovery lead only | 1,036 |
| Insufficiently located evidence | 1,053 |
| Circular attribution | 246 |
| Broken or non-verifying URL | 89 |
| Irrelevant or corrupt | 38 |
| **Total** | **3,375** |

Role counts intentionally overlap because a source may support more than one
claim. Across all evidence there are 4,385 role assignments: 872 attribution,
821 wording, 226 source event, 4 recollection, 2,335 discovery-only and 127
rejected-irrelevant assignments. These role counts must not be added and
described as a source count.

The resulting packet totals remain:

- packets: 626;
- attribution-eligible: 610;
- unresolved and ineligible: 6;
- packets with no reliable public source: 108;
- packets whose role-conservative public output changes from the original
  pre-audit formatter: 484;
- packets still requiring further historical research: 220.

## Representative public outputs

The formatter was exercised against all seven requested categories:

1. **Strong MTF transcript** (`00a61fc4...`): renders `Verification - Exact
   wording verified` and the MTF National Press Club source.
2. **Thatcher-authored book** (`3cced21d...`): renders *The Downing Street
   Years*, p. 513 as the precise locator.
3. **Verified variant** (`0827a412...`): renders `Verification - Historically
   verified variant`, never exact wording.
4. **Secondary recollection** (`52f9b9f9...`): labels Hugo Young's account
   `Secondary recollection` and does not claim exact wording.
5. **Uncertain, no reliable source** (`eb1d2eba...`, Thames quotation): renders
   `Source - No reliable source located`; `nps.gov` is absent.
6. **Context without wording verification** (`01950759...`): renders the MTF
   source only for attribution, source event and date; it does not claim exact
   wording for the composite quotation.
7. **All links suppressed** (`0056972a...`): renders `Source - No reliable
   source located` and contains no URL.

The tests use the formatter's actual production function and assert both the
required and forbidden output fragments.

## Corrections and migration

The source-role audit schema is now version 4 and policy version is:

`historical-context-source-roles-v5-independent-review-and-exclusive-counts`

The independent-review manifest is required whenever AI-researched sources are
present. Missing sources, altered source hashes, missing passages, invalid role
changes and any attempt to label an approximate match exact all fail closed.

The formatter recognises the immediately preceding v4 audit policy for reading
already-issued historical-context receipt metadata. This is receipt
compatibility only; it does not restore old source-rendering rules.

The offline v3 semantic-veto deployment candidate was regenerated after the
formatter source hash changed. No pair decision changed. Its current counts are
610 quotations, 91 images, 22,066 pairs, 21,938 allow and 128 veto; active
enforcement remains false. Manifest SHA-256:

`55f571255e2570ec9584a77204ac23d6062873171da384c3079df36eea2e9b75`

The regenerated source-role audit SHA-256 is:

`ebab8591365d341b9968a6964d3b33b98d9de5a51eab1c29268d6206eb5dfa5c`

No raw provider response or historical packet was rewritten. The audit and
review manifests preserve the previous source values and source-record hashes.

## Tests and commands

Focused independent-review tests:

`13 passed, 48 deselected in 3.06s`

The first full run exposed one stale source hash in the offline v3 shadow
deployment candidate after `historical_context_formatter.py` changed:

`1 failed, 2062 passed, 1 skipped`

The candidate metadata was regenerated with the existing deterministic offline
command. The failing regression then passed individually:

`1 passed in 1.70s`

The complete suite was rerun from the corrected tree:

`2063 passed, 1 skipped, 12 warnings in 783.30s (0:13:03)`

Additional validation:

- `python3 -m py_compile historical_context_source_independent_review.py historical_context_source_roles.py historical_context_source_audit.py historical_context_formatter.py`: passed;
- `git diff --check`: passed;
- audit regeneration: passed with 626/610/6 corpus accounting;
- exact source-review coverage: 34/34 records and 28/28 pages;
- approximate semantic review: 9/9, with 0 exact promotions;
- representative public formatter cases: 7/7;
- quote text and quote-ID preservation: passed in the full suite;
- regular-post eligibility preservation: 610/610.

The 12 warnings are existing dependency deprecations from Starlette/httpx and
BeautifulSoup/lxml; no test emitted a functional warning from this change.

## Modified files

Source and tests:

- `historical_context_source_audit_independent_review_report.md` (new)
- `historical_context_formatter.py`
- `historical_context_source_audit.py`
- `historical_context_source_independent_review.py` (new)
- `historical_context_source_roles.py`
- `tests/test_historical_context_source_roles.py`

Generated, versioned research/audit outputs:

- `historical_context_source_audit_report.md`
- `semantic_alignment_research/historical_context_source_audit_001/audit_summary.json`
- `semantic_alignment_research/historical_context_source_audit_001/source_snapshot_manifest.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_independent_review.json` (new)
- `semantic_alignment_research/quote_research_full_001/historical_context_source_role_audit.json`

Regenerated offline v3 candidate metadata:

- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

## Production isolation

Before and after the review and full test run:

- wrapper PID: `3936954`;
- Python child PID: `3936955`;
- start time: `Tue 2026-07-21 14:40:36 BST`;
- restart count: `0`;
- service state: `active/running`.

No X post, service stop/restart/signal, production-state write, receipt change,
ledger change, analytics change, commit, push or deployment occurred. No paid AI
call was made during this independent review.

## Remaining risks

- The audit still identifies 108 packets with no reliable public source and 220
  packets meriting further research. They remain eligible exactly as required,
  but their context replies are conservative and may show no source.
- Current page availability is not permanent. The frozen locator, exact passage
  and hashes make future drift detectable, but do not prevent link rot.
- Woodrow Wyatt remains a plausible recollection lead rather than public
  evidence until a page or dated journal entry is supplied.
- The 484 public-output changes are broad because the original data had extensive
  role confusion. The focused and full suites support the formatter behaviour,
  but controlled deployment and ordinary log observation remain appropriate.

READY FOR COMMIT, PUSH AND CONTROLLED DEPLOYMENT
