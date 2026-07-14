# Accuracy-First Reply Strategy Implementation Report

## Architecture

The existing mention and quote-tweet lanes retain their scheduling, candidate,
posting, receipt, and replay behavior. An optional strategy layer retrieves up
to five lexically relevant records from the validated completed corpus and asks
the existing xAI reply model for a structured decision. The feature is disabled
by default.

Supported modes are `historical_correction`, `historical_context`,
`researched_principle`, `wry_reply`, `playful_reply`, `deadpan_reply`,
`warm_reply`, and `no_reply`.

## Retrieval And Confidence

Retrieval is deterministic and offline. It searches only the 626 schema-valid
completed packets and fails closed unless the corpus also contains exactly six
unresolved records. Prompt records contain bounded historical fields rather
than complete packet prose.

Historical correction requires high confidence. Historical context and
researched principle require the configured minimum of medium or high.
Historical modes require selected retrieved evidence and grounded status;
factual claims are rejected when ungrounded. The local validator also prevents
paraphrases from being presented as exact quotations.

## Humour And Repetition

Dry, wry, playful, deadpan, and warm replies remain available when no factual
correction is needed. Recent reply text is supplied to the model and checked
locally. Exact, highly similar, and configured canned formulations are rejected.
Replies remain one or two sentences, contain no hashtags, and use the existing
270-character conversational reply ceiling.

## Transactional Metadata

The confirmed reply receipt optionally carries strategy mode, humour tone,
confidence, retrieved quote IDs, evidence summary, factual/grounded flags, and
final text. Receipt validation rejects malformed strategy metadata. On receipt
reconciliation, the metadata is retained idempotently in a capped
`reply_strategy_history`; duplicate replay does not create a second record or
post.

For strategy-enabled replies, the validated draft is also saved atomically
before the X write. A definite posting failure leaves that draft available for
resume, preventing a second model call from changing the reply. Confirmation,
or a definitive `reply_not_allowed` response, clears it. Pending drafts are
validated before reuse and capped at 100 records.

## Audit Finding

The requested source is `/home/tonym/Dropbox/digest014.md`. It contains no
mention-reply, hot-post-reply, or quote-tweet-reply table rows. The offline audit
therefore reports zero auditable replies. It cannot responsibly classify humour
or historical accuracy where no reply records exist.

Command:

```bash
python3 mrsMThatcher2.py audit-replies \
  --digest /home/tonym/Dropbox/digest014.md \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --json
```

The audit validates 626 completed and six unresolved packets and makes no
network or posting calls.

## Illustrative Behavior

Before, a false claim such as `Government creates wealth` could receive a
generic joke. Under the enabled strategy, a grounded result can instead say:

> Government may set the conditions for prosperity; it does not manufacture prosperity itself.

For a harmless joke with no factual claim, a concise wry or playful reply
remains valid. These are implementation examples, not findings from
`digest014.md`.

## Configuration

`mrsMThatcher.local.example.json` documents the complete `reply_strategy`
object. Enabling requires accuracy-first behavior and completed-packets-only
retrieval. Individual historical and humour capabilities can be disabled, and
the configured choices are enforced locally after model output.

## Limitations

Retrieval is lexical rather than embedding-based. It can miss conceptually
related records with little shared vocabulary; this is safer than pretending a
weak match is evidence. The audit command can inventory digest reply rows but
cannot reconstruct replies absent from the digest.

## Review Corrections

Repeated regression-led review additionally fixed:

- configured mode, tone, confidence and hashtag-policy enforcement;
- low-confidence factual claims outside explicitly historical modes;
- malformed strategy metadata in confirmed receipts and pending drafts;
- escaped Markdown table pipes in digest audit parsing;
- resume stability after definite X posting failures;
- contradictory historical and `no_reply` metadata;
- accidental treatment of normalised wording as exact quoted wording;
- omission of canonical packet confidence from historical evidence checks;
- incomplete model-facing definitions of the reply-mode hierarchy; and
- unbounded pending-draft state growth;
- contradictory strategy metadata accepted by receipt validation; and
- audit JSON polluted by import-time diagnostic logging.

Validation completed with 433 focused reply, context, persistence and isolation
tests plus the full 170-test integration harness (one unrelated skip).

## Safety Confirmation

- No production configuration was changed.
- No post was made.
- No external API call was made for implementation or audit.
- No production state, receipt, history, or metadata was edited.
- Nothing was staged, committed, pushed, or deployed.
