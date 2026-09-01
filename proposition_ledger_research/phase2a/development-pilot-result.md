# Proposition-ledger Phase 2A development-pilot result

## Disposition

`phase2a_development_pilot_completed_with_profile_attrition_review_pending`

This bounded development pilot tested whether incremental ledgers could be
constructed on already exposed historical prefixes. It did not establish
ledger effectiveness, select a model profile, authorise a held-out evaluation,
or authorise the downstream four-arm experiment or production integration.

## Frozen inputs and selection

- Source commit: `79429508e31beedec9a3d6470c67f854b60dbf3a`
- Protocol-freeze commit: `27c8f7edc73c769efe2f1379e7e9915cace0b195`
- Execution freeze-core SHA-256: `301606ed2cf09061916be569ae8e90044ccff73991dcd74c9cd598705bd72a6c`
- Private case-manifest SHA-256: `1a89b29e7c0cea9c3f7a44ce5dedae82ed5353f833a0d0779d0905e848a692f6`
- Exposed-development sidecar SHA-256: `47d18afbed683651d007d8c31652acf4c5e616d994fe66b3c325615161d62548`
- Content-seal audit SHA-256: `b532b68a9c814e6600194cc9e5deff581b3dee551504ad1c58b05afff5e225c4`
- Selected conversations: 8
- Selected unique turns: 31
- Comparable contributor groups: 7
- Frozen-open-prefix development exceptions: 1
- Frozen stressor families covered: 8 of 8

The sole exception used a frozen, directly exposed historical prefix from a
conversation whose source stability remained `open_at_frozen_cutoff`. No
post-target or post-cutoff extension was used.

The administrative exclusion pass scanned 219 metadata rows, including 118
protected metadata rows. It excluded 99 genuinely unexposed rows, 49
structurally mined-only rows, and 2 preliminarily held-out rows. Protected rows
written to the development sidecar and protected transcript, source-mapping,
provider-payload, model-output, and human-review accesses were all zero. The
substantive seal was not breached.

## Provider execution

- Planned calls: 62
- Attempted calls and definite responses: 21
- Validated and materialised: 7
- Strict response-pipeline failures: 14
- Blocked dependent calls: 41
- Provider errors: 0
- Uncertain calls: 0
- Automatic retries: 0
- Repair calls: 0
- Fallback calls: 0

| Profile | Planned | Attempted | Strict JSON | Provider schema | Intended canonical | Evidence and references | Materialised and persisted | Complete chains |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Grok 4.3 low | 31 | 13 | 13 | 13 | 13 | 7 | 7 | 2 |
| Grok 4.6 low | 31 | 8 | 7 | 7 | 7 | 0 | 0 | 0 |

Paired completion counts were: both profiles 0, only Grok 4.3 2, only Grok
4.6 0, and neither 6. Two conversations reached the deepest target under one
profile; none did so under both profiles.

These are operational outcomes, not a semantic profile ranking.

## Mechanical diagnostics and usage

Seven Grok 4.3 materialised turns were structurally evaluable. Each used
`no_stable_issue`; all eight other frozen structural signals had zero observed
instances. No Grok 4.6 turn materialised, so its structural signals were not
assessable. The paired structural-disagreement count was zero because there
were no paired complete ledgers, not because semantic equivalence was shown.

| Profile | Maximum ledger bytes | Median ledger bytes | Mean growth bytes/turn | Prompt tokens | Cached prompt | Reasoning | Completion | Total | Latency median / p95 / max (s) | Raw `cost_in_usd_ticks` total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Grok 4.3 low | 3,451 | 3,451 | 1,920.571429 | 184,691 | 2,496 | 12,205 | 10,011 | 206,907 | 15.930402 / 22.752497 / 22.752497 | 2,837,829,500 |
| Grok 4.6 low | n/a | n/a | n/a | 140,584 | 3,072 | 15,015 | 21,469 | 177,068 | 55.527659 / 108.918906 / 108.918906 | 4,954,640,000 |

No currency conversion was performed.

The Phase 1.4 Grok 4.3 carry-forward invariant was
`no_unsupported_modality_quantification_or_date`. Recurrence was not assessable
in this pilot and was not used as a profile-selection rule.

## Offline review and planning

- Blinded, unscored review-pack cases: 2
- Blinded review-pack SHA-256: `864bb18e6e79ec2b868a2611c3e7a79d5f1567d4a7ee8ad014ff350d61e09a12`
- Sample-planning JSON SHA-256: `ffbc88fdcb7386938830052ef56a5b9a00528ffa4dd7b0fec754af05841de557`

Sample planning treats conversation as the primary independent unit and
contributor group as a clustering sensitivity unit. Across assumed discordant
pair fractions of 0.2 to 0.4, the scenarios require approximately 157–314,
70–140, and 40–79 complete paired conversations for absolute differences of
10, 15, and 20 percentage points, respectively. Applying the illustrative
1.25 clustering sensitivity factor gives 197–393, 88–175, and 50–99.
Operational inflation was not calculated because paired completion was zero.

Eight development conversations cannot establish effectiveness. The two
sealed clean prefixes are not a usable held-out evaluation, no held-out split
was selected, prospective collection must continue, and human review must
precede profile selection or prompt revision.

## Verification and limitations

Offline verification reprocessed all 21 saved raw responses byte-identically,
verified call-ledger transitions and the content seal, and passed 152 file
checksums. The blinded pack remains unscored.

The principal limitation is severe operational attrition: only 7 of 21
attempted calls materialised, no Grok 4.6 call materialised, and no conversation
completed under both profiles. Structural observations therefore cannot be
interpreted as semantic correctness or comparative effectiveness.

The pilot made no prompt revision, model selection, held-out evaluation,
downstream experiment, production change, merge, or deployment.
