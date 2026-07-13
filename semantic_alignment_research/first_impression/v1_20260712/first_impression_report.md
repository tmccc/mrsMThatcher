# First-Impression Alignment Validation Report

- Validation cases: 50
- Independent image analyses: 38
- Provider results: {'grok': 50, 'openai': 50, 'anthropic': 50, 'gemini': 38}
- Pairwise rankings: 5
- Total known spend: $2.581871

## Architecture and isolation

Image vision analysis was pixels-only. Quote intent was deterministically projected from existing quote analyses. Text critics received only the two new fingerprints. Pairwise judges received anonymous A/B fingerprints and cached semantic summaries only. No tools, search, winner labels or human labels were supplied. Scores are model judgements, not eye tracking.

## Provider models, results and costs

| Provider/transport | Model | Completed/calls | Known cost | Alignment mean | Tone mean | High risk |
|---|---|---:|---:|---:|---:|---:|
| grok | grok-4.5 | 50 | $0.471496 | 35.46 | 58.1 | 43 |
| openai | gpt-5.6-sol | 50 | $0.900785 | 47.12 | 65.06 | 36 |
| anthropic | claude-sonnet-4-6 | 50 | $0.583752 | 42.06 | 54.3 | 41 |
| gemini Developer | gemini-3.1-pro-preview | 2 calls | $0.017898 |  |  |  |
| gemini Vertex | gemini-3.1-pro-preview | 36 calls | $0.271892 |  |  |  |
| gemini logical | gemini-3.1-pro-preview | 38/50 | $0.289790 | 44.87 | 57.89 | 27 |
| vision | grok-4.5 | 38 | $0.291034 |  |  |  |
| pairwise | grok-4.5 | 5 | $0.045014 |  |  |  |

Gemini stopped with one explicitly ambiguous Vertex request after 38 valid results; 12 cases were not attempted and denominators remain explicit. Raw records retain their original lifecycle; `gemini_provenance_correction.json` documents the generic-version metadata defect without rewriting them.

## Everest case study

- Quote desired first impression: Individual achievement reaches its highest point when it culminates in service to or representation of one's country.
- Desired tone: ['patriotic', 'inspirational', 'reflective']
- Image actual first impression: A stern leader firmly rejects a glowing red communist threat over Britain.
- First object: Raised open palm of Margaret Thatcher
- Dominant symbol: Hammer and sickle
- Tone: resolute with warning=80 and inspirational=45
- Visual competition: 70
- Provider alignment scores: {'grok': 12, 'openai': 24, 'anthropic': 8, 'gemini': 10}
- Provider first-second fit: {'grok': 'poor', 'openai': 'poor', 'anthropic': 'poor', 'gemini': 'poor'}
- Editorial conclusion: technically strong and semantically Thatcher-related, but the communist-warning message dominates and is wrong for achievement culminating in patriotic service.
- Preferred direction: lone climber, summit, national flag, sunrise, individual achievement becoming national representation.

## Free-trade case study

- Case: 55f5cd6d633c143f5170
- First impression: A stern leader at a podium stands between a grim industrial past and a bright modern future.
- Provider alignment scores: {'grok': 18, 'openai': 32, 'anthropic': 18, 'gemini': 35}

## Pairwise ranking

- Decisions: {'A': 3, 'B': 2}
- Known cost: $0.045014
- Five comparisons are too few for a ranking policy. The current pair manifests use anonymous deterministic A/B ordering; `would_replace_current_winner` is not treated as evidence because no winner identity was shown.

## Strategies A-E

```json
{
  "schema_version": 1,
  "observational_only": true,
  "strategies": {
    "A": {
      "winner_changes": 0,
      "resolved": 50,
      "human_agreement": 23,
      "false_keeps": 26,
      "false_replaces": 0
    },
    "B": {
      "winner_changes": 31,
      "resolved": 50,
      "human_agreement": 31,
      "false_keeps": 7,
      "false_replaces": 11
    },
    "C": {
      "winner_changes": 32,
      "resolved": 50,
      "human_agreement": 30,
      "false_keeps": 7,
      "false_replaces": 12
    },
    "D": {
      "winner_changes": 10,
      "resolved": 50,
      "human_agreement": 24,
      "false_keeps": 21,
      "false_replaces": 4
    },
    "E": {
      "winner_changes": 0,
      "resolved": 0,
      "human_agreement": 0,
      "false_keeps": 0,
      "false_replaces": 0
    }
  },
  "limitations": [
    "Validation-set counterfactual only; generated share, exhaustion and diversity require candidate traces not available here."
  ]
}
```

These are observational validation-set calculations only. Generated share, candidate exhaustion and diversity cannot be reconstructed from these cases.

## Human review

Run: `python3 tools/first_impression_review.py --project-dir /disks/disk1/etc/mrsMThatcher --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/first_impression/v1_20260712 --host 127.0.0.1 --port 8771`

## Limitations and recommendation

The validation set is small and partly selected from previously reviewed difficult cases. Claude produced at least one internally inverted salience-interference score on Everest, so that field needs calibration before scaling. Gemini is incomplete. Human first-impression review is not yet complete. Full-corpus analysis is not justified until human review confirms that the new layer improves bad-case detection without rejecting strong indirect images.

## Schemas and methodology

The versioned schemas are `image_first_impression` v1, `quote_visual_intent` v1,
`first_impression_alignment` v1 and `first_impression_pairwise_ranking` v1. Salience scores
describe model judgement, not measured gaze. Tone uses a bounded vocabulary and separate 0–100
strengths. First-impression alignment remains independent of semantic alignment, identity,
editorial power and production scoring.

## Validation-set construction

The deterministic 50 cases include the forced Everest and free-trade cases, 15 previously kept
cases, 25 previously replaced cases, unsure/disagreement cases and deterministic high-disagreement
fill. The set uses 38 unique active images. No original or quarantined image was analysed.

## Files changed

Implementation:

* `analyse_first_impression.py`
* `semantic_alignment/first_impression.py`
* schema-injection extensions in `semantic_alignment/bakeoff.py` and `vertex_recovery.py`
* Gemini fallback provenance configurability in `semantic_alignment/gemini_fallback.py`
* `tools/first_impression_review.py`
* `tests/test_first_impression.py`

Existing uncommitted Gemini fallback/status files from the preceding tasks remain present and
were preserved. All generated outputs are confined to this versioned directory.

## Tests and smoke tests

Python compilation and `git diff --check` passed. The affected semantic-alignment and
production-log-isolation suite passed: **173 passed**. The loopback reviewer loaded the Everest
case, rendered its image and displayed the first-impression, match and keep/replace questions;
the server was then stopped without saving a review.

## Git diff stat

Tracked diff at completion:

```text
 analyse_semantic_alignment_large.py   | 56 +++++++++++++++++++++++++++++++----
 analyse_semantic_alignment_xai.py     | 25 ++++++++++++++++
 semantic_alignment/bakeoff.py         | 18 +++++------
 semantic_alignment/large_bakeoff.py   |  4 +--
 semantic_alignment/vertex_recovery.py |  8 +++--
 5 files changed, 92 insertions(+), 19 deletions(-)
```

New task files include `analyse_first_impression.py`, `semantic_alignment/first_impression.py`,
`tools/first_impression_review.py`, `tests/test_first_impression.py` and this 791 KiB versioned
research directory. Git's ordinary unstaged diff stat does not include untracked files.

## Git status short

Task-related entries:

```text
 M analyse_semantic_alignment_large.py
 M analyse_semantic_alignment_xai.py
 M semantic_alignment/bakeoff.py
 M semantic_alignment/large_bakeoff.py
 M semantic_alignment/vertex_recovery.py
?? analyse_first_impression.py
?? semantic_alignment/first_impression.py
?? tools/first_impression_review.py
?? tests/test_first_impression.py
?? semantic_alignment_research/first_impression/
```

Other pre-existing untracked artefacts remain untouched and excluded.

## Safety confirmation

No production behavior changed and no production file was edited by the research process. No
prior fingerprint or critic output was overwritten. The canonical simulator was not rerun. Only
the 50-case validation subset and five pairwise comparisons were analysed. No search, grounding,
tools, code execution or computer use was enabled in model requests. Nothing was staged,
committed, pushed or deployed.
