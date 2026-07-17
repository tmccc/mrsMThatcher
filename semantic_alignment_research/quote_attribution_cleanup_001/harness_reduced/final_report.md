# Offline Quote/Image Selection Harness Report

Generated: 2026-07-17T22:29:52.772813Z

## Architecture

The harness calls the real production quote and image selectors through the existing guarded production-parity simulator. It applies the original editorial scorer and corrected semantic-veto runtime observationally after each unchanged production selection.

## Isolation

- Network/provider calls: 0
- Forbidden production write attempts: 0
- Immutable source hash verification: passed

## Results

Total simulations: 246253

Seasonal signatures: 13 across years 2026, 2028, 2029
Global semantic-veto coverage gaps: 28 quotations

Corrected isolated-run audit: none required

### full_corpus_sweep

- Events: 47814
- Distinct quotations: 613
- Production/editorial disagreement rate: 0.03348391684443887
- Semantic-veto rate: 0.05086376375120258
- Known-pair coverage: 0.8262224453089053
- Vetoes with alternatives: 513
- Vetoes without alternatives: 1919

### monte_carlo

- Events: 194929
- Distinct quotations: 613
- Production/editorial disagreement rate: 0.15813963032693956
- Semantic-veto rate: 0.03262213421296985
- Known-pair coverage: 0.526191587706293
- Vetoes with alternatives: 874
- Vetoes without alternatives: 5485

### seasonal_boundary_stress

- Events: 3510
- Distinct quotations: 23
- Production/editorial disagreement rate: 0.0
- Semantic-veto rate: 0.03076923076923077
- Known-pair coverage: 0.9
- Vetoes with alternatives: 0
- Vetoes without alternatives: 108

## Seasonal Findings

- Selection failures across sweep, Monte Carlo and boundary stress: 0
- Largest configured boost share change: {"configured_multiplier": 16.0, "control_selection_share": 0.0016072357121472353, "quote_id": "7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428", "seasonal_selection_share": 0.020522388059701493, "seasonal_signature": "ccfc5a150cc9e5e93a152c0c2e2d8a8e8b9046c08b1ad6c10072f35e97350768", "selection_share_delta": 0.018915152347554257, "share_ratio": 12.768748170910156}
- Broadest selected image: {"production_image": "t13.jpg", "quotations": 570, "selections": 2850}
- These are deterministic configured-rule observations, not causal or statistical findings.

## Historical Replay

- Observed selections: 48
- Allowed / vetoed / unknown: 23 / 5 / 20
- Vetoed with / without logged alternative: 2 / 3
- Production-selection invariant failures: 0

## Parity

- All executable invariants passed: True
- Checks: completed_quote_analysis_coverage, production_observer_parity

- SQLite quick check: ok

## Resources

- Completed command wall time: 2014.68 seconds
- Completed command CPU time: 1972.82 seconds
- Peak resident memory: 368464 KiB
- SQLite database: 1593196544 bytes

## Interpretation

Seasonal and policy comparisons are deterministic simulations. They do not establish causal effects or statistical significance.

## Local Inspector

```bash
python3 quote_image_selection_harness.py serve --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_attribution_cleanup_001/harness_reduced --host 127.0.0.1 --port 8770
```
