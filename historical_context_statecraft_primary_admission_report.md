# Statecraft primary-evidence admission report

## Outcome

The seven manually reviewed strong-primary candidates from Margaret Thatcher's
*Statecraft: Strategies for a Changing World* have been admitted to the
isolated canonical candidate. Four are exact-primary matches and three are
primary variants. The public quotation corpus is unchanged.

The candidate is ready for a controlled production deployment. Production had
not been changed when this report was prepared.

## Source

- Author: Margaret Thatcher
- Work: *Statecraft: Strategies for a Changing World*
- Publisher: HarperCollins
- Edition: first edition, 2002
- PDF SHA-256:
  `6619eb3401336585ef5d27c46fdd2dbc101a66c484620b57c8cda62ab1140872`
- Corrected OCR SHA-256:
  `24582e795eec4a7e46b674563ab53c05e1dbdc2c500f688fd7393a01f6490fe1`

The PDF, OCR and seven reviewed candidate bindings were reverified from the
private research workspace. No PDF, OCR text, page image or private path is
included in the production candidate.

## Decisions

| Quote ID | Page | Decision | Canonical effect | Gate result |
|---|---:|---|---|---|
| `0a67f403…` | 427 | exact primary | New completed packet; prior unresolved failure closed | Newly ordinary/context eligible |
| `685ddfab…` | 449 | exact primary | Source/date/locator and exact status corrected | Removed from context gate |
| `928a6686…` | 432 | exact primary | Source event, locator and exact evidence added | Remains blocked |
| `a4f1d422…` | 433 | primary variant | Primary text and the inserted stored word “all” recorded | Remains blocked |
| `a97e6dd2…` | 425 | primary variant | Source sentence and stored leading ellipsis recorded | Remains blocked |
| `db46e751…` | 256 | exact primary | Source event, locator and exact evidence added | Remains blocked |
| `f4323817…` | 327 | primary variant | Complete primary passage and omissions/contractions recorded | Remains blocked |

Gate membership was recomputed rather than mechanically changed. The five
retained cases remain fail-closed because primary wording alone does not settle
every published-context semantic issue.

## Canonical partitions

- Completed packets: 626 → 627
- Unresolved: 6 → 5
- Ordinary attribution-eligible cycle: 610 → 611
- Completed but attribution-ineligible: 16 → 16
- Quotation text changes: 0
- Quotation ID changes: 0
- Unrelated packet changes: 0

The ordinary-cycle increase is deliberate: `0a67f403…` was previously
unresolved and now has manually verified exact primary evidence.

## Gate and live-history boundary

The committed base gate had 15 blocked and 595 allowed records. The candidate
has 21 blocked and 590 allowed records. That net increase is not caused by
withdrawing Statecraft evidence: seven historical-context replies were
published after the base review and are conservatively blocked pending
semantic review, while `685ddfab…` left the gate.

The final build is bound to the paused production history:

- Completed history entries: 93
- History SHA-256:
  `fdb2e55a7855757b459a7f6636206a5cb3931cc75768512102f55a922aedb607`
- Semantic ledger SHA-256:
  `80e2b8179283d722ae21b33cab7e03e8df8814fb05d3383af11f493e8bca5f19`
- Blocked projection SHA-256:
  `04988bb6a89046e19c31979d493c1beec246e486bf837e3b780b3045262dd614`

## Determinism

Two complete generation chains ran from identical inputs and the frozen
93-entry history. Each produced 1,908 compared files. After removing
filesystem `mtime_ns` from the content-derived source snapshot, there were no
missing, extra or differing files. The stable file-manifest SHA-256 is
`4ab3e4b16720691982ea808f43e07e91c9babe429a3d3b74e7f3aa8c20478c04`.

## Validation

- Final compact admission suite: 137 passed, 0 failed, 9 warnings in 25.03s.
- Compilation: passed for all 49 changed Python files.
- `git diff --check`: passed.
- Source-role, projection, closure, gate, runtime eligibility and Statecraft
  admission tests all passed.
- The last complete-suite run reached 2,317 passes and one concurrency-fixture
  failure. The failure was an ordering bug in the test double, not production
  code. The fixture now routes responses by quote identity; the test passed
  five consecutive isolated runs and all 11 tests in its affected module.
- At the operator's direction, the entire 17-minute suite was not rerun yet
  again after that focused repair.

## Isolation

- Network/model requests: 0
- Direct X actions: 0
- Credentials accessed: 0
- Production canonical mutations before deployment: 0
- Service signals before deployment: 0

The live service acknowledged the supported global maintenance pause before
the deployment boundary was frozen. Its original control file was archived
byte-for-byte for restoration after health checks.

ADMISSION CANDIDATE VALIDATED — READY FOR CONTROLLED DEPLOYMENT
