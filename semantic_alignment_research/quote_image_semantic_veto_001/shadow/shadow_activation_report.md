# Material-veto v2 shadow activation report

Generated: 16 July 2026

## Result

The corrected relation-aware quotation/image material-veto v2 policy is integrated and enabled only as a production shadow observer. It cannot filter candidates, replace an image, change a score, alter tie-breaking, or select an active/enforcement mode.

- Shadow enabled: **yes**
- Active enforcement: **no**
- Additional AI/provider calls: **0**
- Additional AI/provider spend: **US$0.00**
- Production wrapper processes: **1**
- Production child processes: **1**
- Restarted child PID: **2848963**
- Pending posting/reply receipts at restart: **none**

## Authoritative source

Only corrected post-run v2 artefacts were accepted. The compiler rejects the older uncorrected result.

| Artefact | SHA-256 |
|---|---|
| Corrected final status | `e57d7890b4369c62f3fdea90bde2aee7ee7578d63edd88f81d7a838ff6451e23` |
| Corrected evaluation | `2e929c043bbff79c5590d5d1227da4211a84750bdbf3def61f41a7e6ee17e7c7` |
| Correction audit | `42c6d850f79bb410e6bcd7fc878e3bdf14567702ccc524fb282c21f642a81aed` |
| Corrected pair decisions | `44e5c22053b332c92f3a863f0fee19967cb8a8a6fc2f13d77a4bb53aea47d064` |
| Corrected candidate manifest | `b8f58f1f5b8e149760cf119f6e55d91db98480febb8b5f6584957ca2aafbbc4c` |
| Quote contracts | `4ac5e3093224519864759554fdb8339bdecc7806fa3eae858535db577763dc96` |
| Image contracts | `d3e0f92f665200f5d3e7c76cc90b5dbc6d069cd45148e6b00ea3ea0db22b3115` |
| Quotation corpus | `3af44e85fa2259e50d049210ca7152817eb9f2bd1e267837afbfb5eb98eb14c1` |
| Image corpus | `251cc8e1f3054c7042ae1d1969f58ca057470ce72e62f74be0f4e70e2dc23c17` |
| Run manifest | `44076a13aef1ce92a520d8727325e363e48e6a173660b08e8212d08c1b65ce70` |

The compiled manifest SHA-256 is:

`3e08320ffa8955e4bbad46444077ee0726e49dee90c29874a81cc872eb3751b0`

Repeated compilation produced the same hash.

## Manifest audit

- Quotations: **626**
- Historical photographs: **91**
- Pair decisions: **5,862**
- Allow: **5,453**
- Veto: **409**
- Current production winners judged: **626**
- Current production winners allowed/vetoed: **583 / 43**
- Quotations with/without an allowed candidate: **598 / 28**
- Critical ally/enemy cases vetoed: **3 / 3**
- Eligible positive calibration retained: **5 / 5**
- Unknown decisions in the corrected pair set: **0**
- Corrected deterministic veto changes retained: **8**
- Relationship metadata corrections retained: **5**

The production lookup key is canonical quote ID plus image SHA-256. Basenames are retained only for readable telemetry.

## Historical replay

The network-free replay found **46** successful regular quote/image selections in retained logs for the requested trailing 30-day window:

- In-scope original selections: **43**
- Generated images, out of scope: **3**
- Allowed: **20**
- Vetoed: **1**
- Unknown/unjudged: **22**
- Vetoed winners with a logged allowed alternative: **1**
- Vetoed winners without an observed allowed alternative: **0**
- Veto reason: `wrong_action` (1)
- Median alternative score delta: **-23.90**
- Production selection invariant failures: **0**

The vetoed historical winner was `t36.jpg`; the best allowed alternative in the logged top-candidate set was `t54.jpg`. Replay results are observational and do not alter history or imply causation.

## Runtime performance

- Startup manifest load: **153 ms** in the activated child (197 ms in the separate benchmark)
- In-memory lookup structure: **9,932,909 bytes**
- Benchmark samples: **10,000**
- Lookup p50: **0.0025 ms**
- Lookup p95: **0.0027 ms**
- Lookup maximum: **0.0207 ms**

These measurements exclude durable telemetry fsync time; selection itself remains unchanged before telemetry is written.

## Production invariants

The observer runs only after the existing selector has calculated scores and chosen its winner. It receives detached scalar snapshots of the winner and candidate set. The hook records and verifies the production RNG state, candidate identity/order/scores, and chosen winner. Any observer exception is caught and production continues.

Generated images are recorded as `out_of_scope_generated`. Memes and conversational-reply media do not pass through this hook. New historical images absent from the manifest are `unknown_unjudged` and never blocked.

The runtime event always records `production_selection_changed=false`; active/enforcement configuration values are rejected.

## Validation

- Focused material-veto shadow tests: **23 passed**
- Wider selector/digest/analytics tests: **200 passed**
- Unit, integration and logging-isolation tests: **561 passed, 1 skipped** before the confirmed bootstrap fix
- Final full repository suite after all fixes: **1,637 passed, 1 skipped**
- `py_compile`: **passed**
- `git diff --check`: **passed**
- New-file Ruff checks: **passed**

The final suite reported three dependency deprecation warnings and no test failures.

## Activation

The existing wrapper `/usr/local/bin/runMrsMThatcher2` was left running. Its child received a controlled `SIGINT`; the wrapper started one replacement child after its normal 60-second delay.

Startup confirmed:

```text
Quote/image semantic-veto shadow manifest loaded.
policy=material-veto-v2-postrun-corrected-shadow-v1
sha256=3e08320ffa8955e4bbad46444077ee0726e49dee90c29874a81cc872eb3751b0
pairs=5862
active_enforcement=false
```

No normal quote/image selection occurred before this report was closed, so the live observation count is **0**. Check after the next quote post with:

```bash
python3 semantic_quote_image_veto.py shadow-status \
  --project-dir /disks/disk1/etc/mrsMThatcher
```

The source default remains disabled. Only the ignored local configuration enables shadow observation.
