# OpenAI Medium vs High: Combined 40-Case Report

## Result

| Trial | Medium | High | Equal | Neither |
|---|---:|---:|---:|---:|
| Trial 001 | 3 | 4 | 0 | 3 |
| Trial 002 | 6 | 7 | 2 | 15 |
| **Combined** | **9** | **11** | **2** | **18** |

- High's share of the 20 decisive cases: **55%**.
- Neither acceptable: **18/40 (45%)**.
- High/Medium estimated completed-image cost ratio: **3.72x**.
- Medium estimated spend: **$1.836986**.
- High estimated spend: **$6.836987**.
- One High request timed out before succeeding on retry, creating possible additional exposure of approximately **$0.171**.

## Interpretation

The independent 30-case follow-up replicated the direction and small size of the first result: High led by one case in each trial. Combined, the advantage is only 11-9 among 20 decisive comparisons. This is not a material editorial advantage and is far too small to justify paying approximately 3.7 times as much for High across the corpus.

The 45% neither rate is more consequential than the quality-setting difference. Increasing rendering quality does not reliably repair a weak visual concept, generic composition, or poor translation of abstract political meaning. Further work should focus on candidate concept and prompt effectiveness rather than output quality.

## Recommendation

Use **Medium** as the default for further offline image-generation research. Do not regenerate the corpus at High quality on this evidence. Reserve High, if used at all, for a separately validated and narrowly scoped final-render step after a candidate concept has already been approved.

This conclusion changes no production setting or behavior.

## Integrity

- The trials used disjoint quote sets: 10 plus 30 cases.
- Every pair received byte-identical prompt text.
- `quality` was the only request-payload difference.
- Reviews were blinded and mappings were persisted before generation.
- All 40 reviews were complete before unblinding.
- No unresolved quotation was included.
