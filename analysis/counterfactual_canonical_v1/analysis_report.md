# Counterfactual canonical dataset analysis

## Executive verdict

The canonical session validates as complete and internally coherent: 20 runs, 5,000 shared-quote indices, and 15,000 branch selections. No trajectory-invalidating defect was found, so the expensive simulation was not rerun.

## Dataset

- Session: `counterfactual_evidence_20x250_20260710`
- Aggregate SHA-256: `b304566af8fe0ad558f7ed6d54f87613a47cf89bb86aecad489574232a7b7ae8`
- Simulator snapshot commit: `e711036983e334dbbdf8de35982885c3a26a5100`
- Runtime recorded by session: 939.071 seconds

## Recalculated headline metrics

| metric | value |
|---|---:|
| Production/editorial agreement | 60.68% |
| Production/identity agreement | 62.70% |
| Editorial/identity agreement | 49.82% |
| All-three agreement | 44.36% |
| Editorial divergence | 39.32% (1,966) |
| Identity divergence | 37.30% (1,865) |

## Diversity

| branch | original | generated | entropy | top-10 share | unique | resets |
|---|---:|---:|---:|---:|---:|---:|
| production | 3,563 | 1,437 | 6.9643 | 12.06% | 143 | 60 |
| editorial | 3,568 | 1,432 | 6.9600 | 12.06% | 141 | 60 |
| identity | 3,641 | 1,359 | 6.9214 | 12.20% | 142 | 60 |

## Divergence and reconvergence

Production/editorial produced 941 divergence episodes, of which 940 later reconverged. Production/identity produced 828 episodes, of which 822 reconverged. There were 2,154 same-winner cases where compact-record state signatures still differed.

## Evidence limits

The canonical run used `candidate_detail=none`. Exact winner-versus-runner-up margins, full candidate rankings, full per-index history equality, and reasons for candidate absence cannot be reconstructed. Case-study `selected_score_gap` values compare selected winners from independently evolved branch candidate sets; they are not runner-up margins.

## Diagnostic images

`t10.jpg` selections: production 60, editorial 60, identity 60.

`t18.jpg` selections: production 60, editorial 60, identity 60.

`tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` appeared in final candidate sets 7/1/24 times for production/editorial/identity; identity excluded it 24 times and no branch selected it.

## Interpretation

This is a counterfactual selector simulation over the current corpus, analyses, shared quote sequence, deterministic random conditions, and branch-local image state. It is not a real-world A/B test and does not predict engagement, audience behaviour, future assets, or future code.
