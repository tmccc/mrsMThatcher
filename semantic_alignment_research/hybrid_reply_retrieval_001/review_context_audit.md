# Hybrid Retrieval Review Context Audit

## Root cause

- Original capture: The reported first incoming text is already stored in the retained tweet cache as the literal abbreviated text; no fuller local copy exists.
- Replay extraction: Replay serialisation contained a 500-character slice. No current replay item reached that bound, but the unsafe slice has been removed. Replay also retained only one merged parent string and no structured thread fields.
- Sample construction: The v1 sample copied the replay incoming string and collapsed the single parent string into thread_context; it omitted references, authors, timestamps, quoted-post separation and older thread context.
- Server and browser: The v1 browser used textContent and no CSS line clamp, so it did not truncate incoming text. It merely rendered the incomplete sample fields it received.

## Migration

- Original cases: 100
- Final context-complete cases: 100
- Original suspected truncated cases: 1
- Retrievals based on those incomplete source texts: 1
- Cases with direct-parent context: 59
- Cases with quoted-post context: 37
- Cases with older-thread context: 11
- Retrieval/display query mismatches: 0
- Repaired in place: 41
- Replaced: 7
- Reviews preserved: 0
- Reviews invalidated: 0

The audit and migration are entirely offline. Retrieval scores, A/B evidence sets, fusion thresholds, production lexical retrieval and live shadow behaviour were not recalculated or changed.
