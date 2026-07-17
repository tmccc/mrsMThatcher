# Offline Quote/Image Selection Harness Report

Generated: 2026-07-17T14:17:16.378883Z

## Architecture

The harness calls the real production quote and image selectors through the existing guarded production-parity simulator. It applies the original editorial scorer and corrected semantic-veto runtime observationally after each unchanged production selection.

## Isolation

- Network/provider calls: 0
- Forbidden production write attempts: 0
- Immutable source hash verification: passed

## Results

Total simulations: 247331

Seasonal signatures: 13 across years 2026, 2028, 2029
Global semantic-veto coverage gaps: 28 quotations

Corrected isolated-run audit: Current-production quote history filtered canonical research IDs instead of the five proven production whitespace aliases; three used runtime IDs were dropped.

### full_corpus_sweep

- Events: 48828
- Distinct quotations: 626
- Production/editorial disagreement rate: 0.03518473007290899
- Semantic-veto rate: 0.05380109773081019
- Known-pair coverage: 0.852727942983534
- Vetoes with alternatives: 552
- Vetoes without alternatives: 2075

### monte_carlo

- Events: 194933
- Distinct quotations: 626
- Production/editorial disagreement rate: 0.15855704267620158
- Semantic-veto rate: 0.03543268712839796
- Known-pair coverage: 0.5295665690262809
- Vetoes with alternatives: 930
- Vetoes without alternatives: 5977

### pilot_monte_carlo

- Events: 20
- Distinct quotations: 19
- Production/editorial disagreement rate: 0.15
- Semantic-veto rate: 0.05
- Known-pair coverage: 0.7
- Vetoes with alternatives: 0
- Vetoes without alternatives: 1

### pilot_sweep

- Events: 40
- Distinct quotations: 10
- Production/editorial disagreement rate: 0.025
- Semantic-veto rate: 0.05
- Known-pair coverage: 0.85
- Vetoes with alternatives: 0
- Vetoes without alternatives: 2

### seasonal_boundary_stress

- Events: 3510
- Distinct quotations: 23
- Production/editorial disagreement rate: 0.0
- Semantic-veto rate: 0.0
- Known-pair coverage: 0.9
- Vetoes with alternatives: 0
- Vetoes without alternatives: 0

## Seasonal Findings

- Selection failures across sweep, Monte Carlo and boundary stress: 0
- Largest configured boost share change: {"configured_multiplier": 4.0, "control_selection_share": 0.0015128116234359734, "quote_id": "741960f5326f4c8ff102c4572f1624facdfaf123a476a66b15ebbca3bb888a06", "seasonal_selection_share": 0.013207547169811321, "seasonal_signature": "ccfc5a150cc9e5e93a152c0c2e2d8a8e8b9046c08b1ad6c10072f35e97350768", "selection_share_delta": 0.011694735546375347, "share_ratio": 8.730463836477988}
- Broadest selected image: {"production_image": "t13.jpg", "quotations": 582, "selections": 2850}
- These are deterministic configured-rule observations, not causal or statistical findings.

## Historical Replay

- Observed selections: 44
- Allowed / vetoed / unknown: 21 / 4 / 19
- Vetoed with / without logged alternative: 2 / 2
- Production-selection invariant failures: 0

## Parity

- All executable invariants passed: True
- Checks: completed_quote_analysis_coverage, production_observer_parity

- SQLite quick check: ok

## Resources

- Completed command wall time: 2689.55 seconds
- Completed command CPU time: 2583.25 seconds
- Peak resident memory: 336096 KiB
- SQLite database: 1606705152 bytes

## Interpretation

Seasonal and policy comparisons are deterministic simulations. They do not establish causal effects or statistical significance.

## Local Inspector

```bash
python3 quote_image_selection_harness.py serve --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_image_selection_harness_001 --host 127.0.0.1 --port 8770
```
