# Historical-context semantic safety gate controlled deployment

Date: 22 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`

## Executive summary

This was the first controlled activation of the historical-context semantic
safety gate described by
`historical_context_semantic_safety_gate_report.md`.

The reviewed source was committed and pushed as:

```text
SOURCE_COMMIT: f04a3c7243d2bf7e1e5d822875bd0fdf3d70b751
SOURCE_PARENT: ff328350a21fb963bf8c9c3d40e02bdf29b842df
BRANCH: master
UPSTREAM: origin/master
```

The service was restarted once at 18:00:52 BST. It is active and running with
one wrapper and one Python child, has loaded the reviewed 24-case gate, and has
not entered a restart loop.

## Deployed behaviour

The deployed public historical-context lane now:

- blocks 24 quote IDs whose published Meaning remains open for correction or
  further evidence;
- fails closed for historical-context replies if the reviewed ledger is
  missing, stale or inconsistent;
- leaves every blocked quotation eligible for the ordinary quote/image lane;
- reconciles an existing historical-context receipt before configuration,
  packet or policy exits;
- uses a neutral public Context for the one unsafe composite event-only label;
- retains all raw packet fields, evidence records, source roles, confidence
  dimensions and diagnostics internally.

The 24 blocked records consist of 20 `future_correction_needed` cases and four
`insufficient_to_assess` cases. The other 586 attribution-eligible completed
packets remain allowed by this gate.

## History frozen during deployment review

The original review baseline contained 77 replies. Two scheduled replies were
published naturally by the still-running pre-change service while this work
was being reviewed:

- `a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7`;
- `34114f8f8fa580a2cb413c481408094ad2a8675ebb59955cf8c7d665897d8381`.

Both were reviewed explicitly against retained evidence and classified
`future_correction_needed`. The second Meaning added state ownership, stifled
economic freedom and efficiency, and a government-divestment mechanism beyond
the narrower supported claim that a genuine market requires taking the state
out of the market.

No published row was edited or deleted. The final 79-row history SHA-256 is:

```text
fc5972169cffed10680c196b41373906a9c2731263d26bb323b2eca03d4c03d6
```

## Exact source commit scope

The source commit staged these 30 files explicitly:

```text
historical_context_evidence_truth_audit.json
historical_context_evidence_truth_audit.py
historical_context_formatter.py
historical_context_mtf_priority_manual_review.json
historical_context_mtf_remaining_manual_review.json
historical_context_public_projection_review.json
historical_context_public_projection_review.py
historical_context_public_render_review.txt
historical_context_published_reply_semantic_review.json
historical_context_published_reply_semantic_review.py
historical_context_reply_semantic_gate.py
historical_context_reply_semantic_gate_audit.json
historical_context_reply_semantic_gate_audit.py
historical_context_semantic_safety_gate_report.md
mrsMThatcher2.py
mrs_log_digest.py
semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json
semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json
semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json
semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json
semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md
tests/test_digest_reply_observability.py
tests/test_historical_context_evidence_truth_audit.py
tests/test_historical_context_public_projection_review.py
tests/test_historical_context_published_reply_semantic_review.py
tests/test_historical_context_reply.py
tests/test_historical_context_reply_semantic_gate.py
tests/test_historical_context_reply_semantic_gate_audit.py
tests/test_historical_context_source_roles.py
tests/test_unit_helpers.py
```

The source-compression pilot, its test and run directory, and
`mrsMThatcher.control.json` remained untracked and unstaged.

## Validation

The final named formatter/gate set passed:

```text
706 passed, 9 warnings in 135.56s
```

The first complete-suite run correctly exposed a stale inactive shadow-manifest
fingerprint caused by the formatter change:

```text
1 failed, 2240 passed, 1 skipped, 25 warnings in 1034.27s
```

The established offline `prepare-v3-shadow` builder refreshed only the
formatter fingerprint and propagated manifest/audit checksums. Independent
comparison proved that all 610 IDs, 22,066 pair decisions, policy values and
coverage remained unchanged. Active enforcement remained false, no provider
was called, and the running pre-change process was not reloaded.

The exact failing regression then passed, followed by a complete clean rerun:

```text
2242 passed, 1 skipped, 25 warnings in 1027.35s
```

Every modified or new Python file compiled successfully. Both working-tree and
staged `git diff --check` passed. The semantic ledger `--check` passed. Two
isolated truth-audit runs, two gate-audit runs and two semantic-ledger builds
were byte-identical to one another and their tracked artifacts. The priority
and remaining MTF manual-review projections also reproduced byte-for-byte.

The post-activation isolated smoke set passed:

```text
9 passed in 3.52s
```

It covered the real 79/24/20/4 gate, blocked and unavailable gate paths,
allowed document `104653`, the safe b32 Context fallback, document `107352`
source deduplication, no-reliable-source rendering, and preservation of a
genuinely distinct primary transcript and memoir.

## Full-corpus audit headline

```text
completed packets:                         626
attribution eligible:                      610
completed attribution ineligible:          16
unresolved quotations:                       6
reviewed published replies:                 79
blocked historical-context replies:         24
allowed eligible historical-context replies:586
gate invariant failures:                     0
```

The 67 downgraded event/date projections remain 65 safe date-only cases, one
clean event-only case and one event-only case using the neutral fallback. The
1,539 unsupported packet-field findings remain a triage queue with zero
Meaning or `intended_argument` claims in that count.

Key artifact hashes:

```text
semantic ledger: b6bef0282fda2b2476bf2ab5b84b928afa09c04b7a953faae068d8c3c7db73c7
blocked projection: 5c166e3fd2c31a01ca132a8b046e8cc02e36ab787e7060dd0fa952d719afc1b2
gate audit: 2e9a90c3988cf4be6c90f3e80644d521ac4d32b474e2a514f7d1b800a9ac6add
truth audit: 762c8a17d2c6da2e79c419acd287380f6604ff4f0aa3a4f6acf48a6d614d6974
projection review: c82ddd0d98a48e647caf3e81c89138fe73689acf5baea2cd4d06370dbb0dd455
public-source audit: fcba7e6f3532a6ae2fbc4eb78f30745e073032da3c138ccac9cd8c28d13ec79f
inactive shadow manifest: b731455bbf516377ce4050a9c501ee60cd8bbccc72f68e5cd78d7d066135a560
```

## Corpus invariants

The immutable inputs retained their reviewed hashes:

```text
mrsMThatcher.txt: 10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee
quote_analysis.json: e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a
research_packets.json: 307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611
corpus_manifest.json: 81f6b2974c30d5810afc74c24704f5ee3d3868a6b2d94859cad2fa8cebce12da
source-role audit: 431793e66427d1d35da42858d9cc6b516f31667a07a21c5fc5cbe705441193a2
unresolved_quotes.json: 6acb4d2dede398f74e488902c62c672437db8721f6f75c9adebdf323889feb4f
```

Exactly 610 quotations remain attribution eligible and in the regular-post
cycle. The same 16 completed packets remain attribution ineligible, the same
six unresolved quotations remain excluded, and quote IDs/text, research
packets, source roles and confidence metadata remain unchanged.

## Rollback bundle

Protected bundle:

```text
/home/tonym/.local/state/mrsMThatcher/deployments/20260722T163413Z-historical-context-semantic-safety-gate
```

The directory is mode `0700`; its two files are mode `0600`.

```text
pre-change-reviewed-files.tar.gz
SHA-256 45e5a6c0baba9af15dd2dc3693b6ab1525eae01c15181cd4574a04f0278706c8

mrsMThatcher.service
SHA-256 6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef
```

No environment-file contents were copied or printed. Configuration was not
changed.

## Activation and service verification

Pre-activation:

```text
wrapper PID: 3745199
Python child PID: 3745200
start time: Wed 2026-07-22 14:38:32 BST
NRestarts: 0
```

Post-activation:

```text
wrapper PID: 116215
Python child PID: 116216
start time: Wed 2026-07-22 18:00:52 BST
NRestarts: 0
state: active/running
```

The `/usr/local/bin` wrapper and Python entry point resolve directly into the
committed repository. The new process started after the source commit and all
runtime files match that commit.

Startup logged:

- semantic policy `historical-context-semantic-gate-v1-open-review-whole-reply`;
- ledger SHA-256 `b6bef028...` and projection SHA-256 `5c166e3f...`;
- `blocked=24` and `regular_post_eligibility_unchanged=true`;
- 610-quotation inactive semantic-veto manifest with 22,066 pairs and
  `active_enforcement=false`;
- generated identity processing suspended because the generated pool remains
  disabled;
- the existing next quote schedule preserved at 19:23:40 BST;
- `Bot started successfully` with no traceback, import, schema, fingerprint,
  source-role, receipt or ambiguity error.

There is exactly one wrapper and one Python child. The analytics timer remains
active and unchanged, and `systemctl --user --failed` is empty.

## Durable state and remote-write safety

Immediately before and after activation, all five barriers were absent:

- regular quote/image receipt;
- meme receipt;
- historical-context receipt;
- confirmed conversational-reply receipt;
- ambiguous remote-write marker.

`pending_ai_reply_drafts` is empty. Quote/image histories and the 79-row
historical-context history retained identical hashes across the restart. The
bot-state hash changed only because startup normalised the previously absent
`pending_ai_reply_drafts` key to an explicit empty object; every other
top-level value was identical to the pre-restart backup.

The deployment made no X post or reply, uploaded no media, called no external
AI provider, deleted or bypassed no receipt, changed no posting schedule,
reset no state, changed no analytics data, and restarted no other service. The
normal 17:03 production post occurred before activation under the old process
and is separately accounted for in the 79-row semantic ledger.

## Remaining risks

- The gate prevents recurrence of 24 questionable historical-context replies;
  it does not complete their case-by-case semantic remediation.
- The 1,539 packet-field triage queue and the MTF mismatch/uncertainty queues
  remain research work rather than automatic evidence.
- The historical-context ledger remains a deliberate reviewed snapshot; later
  published replies must be reviewed explicitly before the artifact is
  regenerated.
- Test-suite performance is a worthwhile separate task, but no performance
  refactoring was mixed into this deployment.

CONTROLLED DEPLOYMENT SUCCESSFUL
