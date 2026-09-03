# Original editorial production calibration

Status: **not authorised**.

The optimiser used a deterministic quotation-hash 70/30 split. The 30% group is an optimiser-frozen hold-out; prior aggregate corpus results were already known.

- Training/grouped-CV threshold status: `FAIL — no grid pair passed; thresholds below are diagnostic only`
- Minimum policy margin: `0.0`
- Maximum baseline-score loss: `2.0`
- Hold-out accepted differing-image cases: `59`
- Hold-out pooled editorial decisive share: `0.7068965517241379`
- Hold-out quotation-cluster bootstrap lower 95% bound: `0.6177777777777778`

The supplied aggregate evidence was recomputed from the frozen case-level reviewer rows, including 274 policy differences, 530 decisive mirrored orientations, 56 direct conflicts, and zero spurious decisive choices in 140 identical-image control orientations.

## Authorisation gates

- PASS — `at_least_30_accepted_differing_cases`
- PASS — `pooled_editorial_decisive_share_at_least_65_percent`
- PASS — `gpt_editorial_decisive_share_at_least_55_percent`
- PASS — `grok_editorial_decisive_share_at_least_55_percent`
- PASS — `quote_cluster_bootstrap_lower_95_above_50_percent`
- FAIL — `direct_mirrored_conflict_no_more_than_15_percent`
- PASS — `zero_historically_specific_cases`
- PASS — `zero_blocked_image_promotions`
- PASS — `zero_unanimous_strong_false_specific_failures`
- PASS — `zero_designated_historical_safety_concerns`

## Opaque-image resolution

- `IMG-3B765D6BF48F9676` → `t34.jpg` → `3f446ece7bcc1727e81b7301a8eca188f38c7a5ea0c0ddcdb5d92866a4f0beae`
- `IMG-A0A526421FD73A51` → `t14.jpg` → `eb250a4534412da58fbbf2247c7c041041972ccde4f77ce86888523d86e648c3`

No near-duplicate clusters were populated: the current original corpus has no byte-identical files and no frozen validated strict perceptual-equivalence mapping was supplied.
