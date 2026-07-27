# MTF live-context canonical admission report

## Outcome

The isolated admission candidate incorporates all 422 human-reviewed Margaret
Thatcher Foundation records. It creates 407 curated source records and upgrades
15 existing records in place. No unreviewed source, private retrieval path, or
search snippet is admitted.

All 422 affected packets gain reviewed historical context. Exactly the eight
Priority A records leave the historical-context semantic gate. The blocked
count changes from 21 to 13; no quotation enters the gate. The unresolved set
remains five records and the ordinary posting cycle remains 611 quotations in
identical order.

## Evidence and transition controls

- Production base: `99c141d7fc9cd85478c1f70510db8311cdebd4ef`
- Reviewed records: 422
- New curated sources: 407
- Existing source upgrades: 15
- Packet semantic changes outside the reviewed scope: 0
- Quotation-text changes: 0
- Meaning changes: 0
- Private paths persisted: 0
- Network or provider calls during admission: 0

The public-projection transition is explicitly bound to the reviewed transition
manifest hash. Existing historical transitions remain independently checked.
Strong official primary transcripts may consolidate compatible legacy
representations only when the reviewed source supplies the required URL,
passage, date, locator, and claim superset.

## Determinism

The complete affected builder sequence was run independently in two clean
copies using the same frozen 115-row published-history snapshot. Transition,
projection, source-role, source-deduplication, evidence-truth, semantic-review,
gate, runtime-eligibility, manual-review, and render-review outputs were
byte-identical. Earlier MTF transport hashes are retained explicitly when a
reviewed source is upgraded from newly fetched bytes. The semantic-veto shadow
manifest was rebound only to the changed packet and runtime-manifest hashes;
all matrix entries are unchanged and enforcement remains disabled.

## Gate transition

Eight reviewed Priority A quotations move from blocked to allowed:

- `00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae`
- `01d50c556a2d6283599e8c1eaa04925d42a5b499cc1c5a22925c7cb44097e1ea`
- `21db3d129f143edca731ac38704b1add8ef666662555fbbd3a91bd217d09f6a7`
- `283636fc2526ccd22c302f80f6c494c36ec1e612d16f9b5a145d4eca70b14e9b`
- `9ec3e9ca9ac00dac4619d19fe5312503eeef8fdca7b1b22f2b9fe2866b79bcdc`
- `a9426dce186893768be1d61ea3ca82d90d05667d085c5a3d217e3a08059eba5b`
- `b2af0519a698004f70a0bb37e506a55513af31f07b745bc953728b54078f6a10`
- `cb0389dcbc1d4532742f73271d0e6b26f62d6b3ada40db984d3a0fa7d25a7a1f`

## Validation status

The initial complete suite produced 2,341 passes and 16 failures. Fifteen were
stale transition expectations or dependent fingerprints; one was the known
timing-sensitive worker test. All 16 failed nodes passed after repair. The
complete directly affected set then passed 387 tests. Per operator direction,
the 21-minute complete suite was not run a second time. Compilation and
whitespace validation pass. Production has not yet been modified.
