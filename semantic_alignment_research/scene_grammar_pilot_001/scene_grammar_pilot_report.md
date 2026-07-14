# Scene Grammar Pilot 001

## Result

- Publishable: 10/20
- None: 10/20 (50%), previous 15/20 (75%)
- Absolute none-rate reduction: 25%
- Immediate message: {'yes': 5, 'partly': 8, 'no': 7}
- Style wins: {'scene_grammar_direct': 6, 'scene_grammar_cinematic': 4}
- Analysis known cost: $1.485196
- Estimated generation cost: $0.52
- Approximate cost per publishable case: $0.2005
- Recommendation: **repeat another bounded scene-grammar pilot**

## Method

The exact 20 difficult cases from Generation Prompt Pilot 001 were reused. Each received one deterministic physical scene specification and two prompts with identical semantic content: direct documentary rendering and cinematic photorealism. Tony reviewed stable A/B positions without seeing style or automated analysis.

Domestic scenes were placed in recognisably British settings where physically appropriate. Three first-person or personality-led quotes explicitly grounded a realistic Margaret Thatcher; those six images also received the existing identity/likeness audit.

## Failure correction

| Prior failure category | Prior cases | Corrected | Still none |
|---|---:|---:|---:|
| distracting symbolism | 5 | 2 | 3 |
| identity/likeness problem | 1 | 1 | 0 |
| incorrect physical relationship | 1 | 1 | 0 |
| missing required object | 3 | 2 | 1 |
| poor scale | 1 | 1 | 0 |
| scene too generic | 8 | 2 | 6 |
| tone mismatch | 3 | 1 | 2 |
| weak composition | 2 | 2 | 0 |
| wrong dominant subject | 7 | 3 | 4 |

Residual or newly visible problems from Tony's notes included: identity or likeness (3), garbled or distracting detail (3), historical or geographic specificity (1), concept remains hard to visualise (2). These are descriptive counts and may overlap.

## Automated score calibration

Human-selected winners averaged first-impression 69.6, tone 77.5, semantic 56.3 and editorial 50.8.
Candidates in pairs Tony rejected as none still averaged first-impression 68.6, tone 68.7, semantic 53.05 and editorial 48.9.
The small separation confirms automated-score inflation relative to publishability; the scores must remain observational.

## Regression cases

Everest: Tony selected Candidate B and marked the immediate message yes. The communist/geopolitical failure disappeared without a special post-selection rule.
Free trade: Tony selected Candidate B and marked the immediate message yes. The scene shows voluntary exchange and exported goods rather than capitalism-versus-socialism symbolism, although generated packaging details remain a general visual-quality risk.

## Style and provider scope

Direct won 6 cases and cinematic won 4; this is not a decisive rendering-style separation. A multi-provider image trial was deferred because changing provider inside this fixed 2x20 design would confound the scene-grammar test. A separate small provider-controlled trial may now be justified.

## Limitations

This deliberately difficult 20-case sample is small, non-random and reused from the prior pilot. The same image provider generated both variants. Human judgement comes from one editor, and the estimated image cost is reconstructed from observed project pricing rather than authoritative per-image billing metadata.

## Implementation and verification

Architecture: cached quote/visual-intent inputs -> deterministic Scene Grammar v1 -> two semantically identical prompt variants -> bounded gpt-image-1.5 low generation -> existing xAI first-impression, semantic, tone/editorial and scoped identity analyses -> loopback blinded review -> offline comparison.

New task files: `analyse_scene_grammar_pilot.py`, `semantic_alignment/scene_grammar_pilot.py`, `tools/scene_grammar_pilot_review.py`, `tests/test_scene_grammar_pilot.py`, and this versioned research directory. Existing Generation Prompt Pilot 001 files were read but not overwritten.

Prompt isolation: image analysis received pixels only; quote-to-image critics received cached fingerprints; generation prompts contained scene specifications but no human labels or expected winners. The direct and cinematic prompts have byte-identical `SCENE_SPECIFICATION` payloads.

Verification: `python3 -m py_compile analyse_semantic_alignment_xai.py semantic_alignment/*.py tools/*.py` passed; 180 affected semantic-alignment and production-log-isolation tests passed; `git diff --check` passed; the loopback reviewer smoke test passed. `git diff --stat` is empty because all task files are currently untracked. `git status --short` contains these new task paths plus numerous preserved unrelated untracked artefacts.

## Safety record

No production behaviour or production file changed. The production bot was not stopped, restarted or signalled. No prior fingerprint, critic output, prompt, image or human review was overwritten. The canonical simulator was not run. No search, grounding or model tools were enabled. Nothing was staged, committed, pushed or deployed.
