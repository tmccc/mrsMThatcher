# Historical Context Reply Implementation Report

## Formatter Design

`historical_context_formatter.py` loads the canonical 632-record manifest, requires exactly
626 schema-valid completed packets and six unresolved IDs, and verifies packet identity against
the immutable manifest wording. Runtime lookup uses the bot's existing quote hash when possible
and an exact whitespace-normalised text match for legacy records whose canonical hash retained
repeated spaces.

Replies use one stable structure: Context, Meaning, Verification, and Source. The formatter uses
only canonical packet fields. Source ranking prefers Margaret Thatcher Foundation material,
Hansard, original transcripts, authored publications, and contemporary interviews. Quotation
aggregation sources are excluded except when documenting explicitly unverified or misattributed
wording. A canonical stable locator is used when a packet has no suitable authoritative URL.

## Configuration

The optional stage is default-disabled and accepts this local configuration:

```json
{
  "historical_context_reply": {
    "enabled": true,
    "maximum_length": 4000,
    "include_meaning": true,
    "include_source": true,
    "include_verification": true
  }
}
```

The allowed maximum is 120 to 25,000 characters. The current 626 replies average 453.88
X-weighted characters and 454.34 raw characters; the maximum is 645 weighted characters.
Consequently, the 4,000-character default preserves every current section without shortening.

The posting path does not impose a 280-character application limit. It passes reply text unchanged
to the same `POST /2/tweets` implementation used for existing quotation posts. The current source
file contains eight quotations over 280 characters (maximum 449), and a regression test verifies
that a context reply over 280 characters reaches that posting function byte-for-byte unchanged.

## Receipt Behaviour

The context stage runs only after the quotation post and its used-history/state changes have been
durably committed. It also runs after successful reconciliation of a confirmed main-post receipt.
Context replies have independent receipt and history files:

* `historical_context_reply_receipt.json`
* `historical_context_reply_history.json`

A confirmed reply is written to a durable receipt before reconciliation into history. Resume
reconciles that receipt without posting again. Completed parent post IDs are idempotently skipped.
A context failure is recorded independently and never rolls back or repeats the quotation post.
The confirmed main-post receipt remains in place until context dispatch has returned. This closes
the crash window between committing the quotation locally and scheduling its reply: startup
reconciliation can safely dispatch the reply, while the context receipt/history prevents a
duplicate if it had already completed.

Before transport, the reply store durably records a numbered `sending` lifecycle state. An
interruption or ambiguous transport outcome leaves that marker in place and blocks automatic
duplication pending manual reconciliation. Confirmed ordinary failures are linked to the same
attempt number, allowing a stale sending marker to be cleared safely after restart. Confirmed
receipts and completed history are validated strictly and conflicting identities are rejected.

## Verification And Shortening

Each canonical verification status maps to one explicit editorial label. The formatter preserves
Context, Verification, and Source ahead of Meaning. If an operator configures a materially lower
limit, Meaning is shortened and then removed before provenance is compressed. Both raw and
X-weighted counts are reported in dry runs.

## Samples

Corpus-backed exact, variant, paraphrase, and historically uncertain examples are under
`sample_context_replies/`. All 626 completed packets format successfully. Unresolved quote IDs
and missing packets produce no reply.

## Safety

The source default remains disabled, while the live host explicitly enables the feature through
its ignored local configuration. Deployment did not alter posting cadence, quotation text, image
selection, or ordinary reply behavior. Context replies are attempted only after a confirmed main
quote post and use independent transactional state. The bot's own context replies are excluded
from both mention and hot-post candidate processing.

## Digest Observability

`mrs_log_digest.py` retains the structured `historical_context_reply` event. Digest JSON includes
the event list and status counts; Markdown includes the completed count in the headline and a
table containing status, parent post ID, quote ID and weighted character count. This covers
completed, already-completed, failed, skipped and dry-run outcomes without treating the context
reply as an ordinary inbound or Grok-generated reply.

## Implementation Review

Post-implementation review found and corrected two defects. Quotation-aggregation filtering now
checks both source titles and URL domains. Context dispatch now occurs before deletion of the
confirmed main-post recovery receipt in both the normal and startup-reconciliation paths. Both
corrections were preceded by failing regression tests.

A second review tightened receipt and history validation, prevents conflicting completed records
from being overwritten, and allows `KeyboardInterrupt` to propagate normally. The published JSON
schema now matches formatter output in every supported configuration. Uncertain wording uses the
neutral `Source event` label rather than claiming it was spoken verbatim, and the aggregation-site
classification covers the domains observed in the canonical corpus. Parent IDs, canonical quote
IDs, and non-empty reply text are validated before any remote posting function can be called.
Placeholder metadata such as `N/A` and `None` is never rendered as provenance. Authoritative
canonical locators outrank secondary web sources. Runtime context history and receipt files are
Git-ignored, and corpus validation rejects duplicate manifest or unresolved-status records before
dictionary or set conversion could conceal them.

After the first live reply exposed clumsy X rendering, the public layout was compacted into a
single archive-entry format with inline Meaning, Verification and Source labels. Opaque Vertex
grounding redirect URLs are never published: the formatter uses the canonical stable locator, or
the grounded source title without a public URL when no stable locator exists. This changes only
presentation and source-link hygiene; canonical context and verification data remain unchanged.
