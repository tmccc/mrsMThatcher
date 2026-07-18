# V3 Shadow, Cleaned Metadata, and Principle Reply Deployment

## Outcome

The cleaned quotation metadata, attribution-cleaned semantic-veto v3 manifest,
and previously implemented `principle_reply` code were activated on 18 July
2026. The semantic veto remains strictly observational: configuration mode is
`shadow`, runtime active enforcement is false, and no candidate filtering,
score change, RNG use, tie-breaking change, or additional provider call was
introduced.

The deployed source commit before this report was `4c4d5d1` on `master`.

## Preconditions

- Receipt barriers were clear before configuration installation and again
  immediately before the child restart: regular, meme, historical-context,
  conversational-reply, and ambiguous-outcome receipts were absent.
- Process topology was one wrapper (`3631076`) and one child (`2905742`).
- `mrsMThatcher.txt` has 619 canonical records (620 non-empty physical lines).
- The active manifest contains 613 confirmed Thatcher quote IDs.
- Six unresolved research records remain ineligible.
- All 13 attribution exclusions are absent from the active source and manifest.
- `quote_analysis.json` has 619 items and declares source SHA-256
  `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`,
  matching `mrsMThatcher.txt`.
- Production self-test passed without a source-SHA or stale-override warning.
- Focused tests: 148 passed.
- Full suite: 1,759 passed, 1 skipped, with three dependency deprecation
  warnings.
- Required `py_compile` and `git diff --check` passed.

## Installed Configuration

Only the ignored host-local configuration required an atomic replacement. The
tracked corpus, analysis, bot code, and candidate manifest were already at
their final repository paths and are loaded through the live script symlink.

`mrsMThatcher.local.json` now selects:

```json
{
  "quote_image_semantic_veto": {
    "enabled": true,
    "mode": "shadow",
    "manifest_path": "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json"
  }
}
```

The remaining fail-open, history-bound, and alternative-observation fields were
preserved. Runtime validation continues to reject every mode except `disabled`
and `shadow`.

## Hashes

| File or selection | Before | After |
|---|---|---|
| local configuration | `815c1ace73eca2309e8070f14268c09c509b02d8386cf3c78d76401aa3baf4a5` | `4cbf336f80da0977d41b195d485e91a58cefb0b9c2f489c0e5ccfc4603881a8c` |
| configured semantic-veto manifest | v2 `3e08320ffa8955e4bbad46444077ee0726e49dee90c29874a81cc872eb3751b0` | v3 `8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706` |
| `mrsMThatcher.txt` | `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee` | unchanged |
| `quote_analysis.json` | `e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a` | unchanged |
| `quote_analysis_overrides.json` | `b2a17644fbbab4f9daf7bd79b22c7494a320947831e81613a12b9a6c2d92141e` | unchanged |
| `mrsMThatcher2.py` | `0b8da34fcaecfce23316cbb52f43a3b819628018acfb549eadee83c838881a59` | unchanged |

## V3 Manifest

- Policy: `affirmative-material-contradiction-rules-v3-attribution-cleanup-candidate`
- Quotations: 613
- Images: 91
- Known pairs: 22,157
- Allow: 22,028
- Veto: 129
- Current production-winner coverage: 100%
- Seasonal-boundary coverage: 100%
- Stateful weighted coverage: 99.7666%
- Critical relationship-regression failures: 0
- Production-selection invariant failures: 0

The runtime loaded the manifest in 308.847 ms using 34,420,506 bytes and logged
`active_enforcement=false`.

## Controlled Restart

The final receipt barrier was clear. `SIGTERM` was sent only to child
`2905742`; wrapper `3631076` was not signalled. Its normal 60-second restart
delay launched replacement child `3987356`, which acquired the production lock
and logged `Bot started successfully`. Subsequent 60-second main-loop ticks
completed without traceback, error, or repeated restart.

The replacement process runs `/usr/local/bin/mrsMThatcher2.py`, whose symlink
resolves to the tested repository file containing `principle_reply`. The
principle-reply focused regressions and full suite passed before restart.

## Shadow Status

Post-restart status reports:

- configured mode: `shadow`;
- feature enabled: true;
- active enforcement: false;
- current manifest valid: true;
- current manifest SHA-256: `8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706`;
- feature network calls: 0;
- production-selection-change failures: 0.

The most recent historical selection event predates the restart and therefore
still names the v2 hash. This is retained audit history, not the currently
loaded manifest. The first v3 selection event can be checked after the next
normal quote post with:

```bash
tail -n 5 quote_image_semantic_veto_runtime/shadow_history.jsonl \
  | jq -c 'select(.manifest_sha256 == "8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706") | {timestamp,quote_id,selected_image_basename,shadow_status,production_selection_changed,manifest_sha256}'
```

## Rollback

The atomic configuration backup is:

`mrsMThatcher.local.json.before_v3_shadow_20260718T060933Z`

Its SHA-256 is
`815c1ace73eca2309e8070f14268c09c509b02d8386cf3c78d76401aa3baf4a5`.
It is ignored runtime material and was not committed.

To roll back only the v3 shadow activation, first wait for clear receipt
barriers, atomically restore the configuration, then stop only the current
child and let the existing wrapper restart it:

```bash
cd /disks/disk1/etc/mrsMThatcher
for f in regular_post_receipt.json meme_post_receipt.json \
  historical_context_reply_receipt.json confirmed_reply_receipt.json \
  ambiguous_post_outcome.json; do test ! -e "$f" || exit 1; done
cp --preserve=mode,ownership \
  mrsMThatcher.local.json.before_v3_shadow_20260718T060933Z \
  .mrsMThatcher.local.json.rollback
sync -f .mrsMThatcher.local.json.rollback
mv .mrsMThatcher.local.json.rollback mrsMThatcher.local.json
sync -f .
kill -TERM "$(sed -n 's/^pid=//p' mrsMThatcher.lock)"
```

This rollback returns semantic-veto observation to the prior v2 manifest. It
does not undo the cleaned corpus or `principle_reply` code.
