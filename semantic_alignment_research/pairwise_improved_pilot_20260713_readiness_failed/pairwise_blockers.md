# Pairwise Pilot Blockers

The pilot is not executable. Both mandatory regression controls have valid exact selector runner-ups, but those runner-ups lack cached first-impression fingerprints. All other gates pass.

## Everest
- Current winner: `tg_fbaf32a54650f629731270135c319348343a6c08189e71f22ccb74b1a1d89521.png`
- Exact runner-up: `tg_093fe18b5e8a685a1ec1a38813fa2dfddce5ca84853723c13d1dfc2a6ac4a429.png`
- Blocker: `missing_first_impression_fingerprint`
- Trace: `/disks/disk1/etc/mrsMThatcher/simulation_runs/audit_evidence_20x250_20260710/runs/run_0006/selections.jsonl` post 55
- Active/file/hash/identity/quality gates: `True`

## Free trade
- Current winner: `tg_8032ac6c90f358c9f5146680280edda94a7475b752b3c8e64c1670ba64986822.png`
- Exact runner-up: `tg_d4013aa361485ab2894f71e426c705de78455e93d37fe3f4d2ae27727c70c88c.png`
- Blocker: `missing_first_impression_fingerprint`
- Trace: `/disks/disk1/etc/mrsMThatcher/simulation_runs/audit_evidence_20x250_20260710/runs/run_0017/selections.jsonl` post 22
- Active/file/hash/identity/quality gates: `True`

## Minimum remaining work
Explicitly authorise independent raw-image-only first-impression analysis for exactly the two runner-up images above, persist those two fingerprints in a new versioned supplement, and rerun this readiness builder. No other image, pairwise critic, or human review is required before that gate check.
