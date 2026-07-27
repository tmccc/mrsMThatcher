# Conversational reply durability repair

## Scope

This change closes the accepted-reply/restart duplicate window for the
conversational mention, hot-post and quote-tweet reply lanes. It does not alter
reply selection, reply wording, schedules, quotation data, evidence policy or
historical-context reply handling.

The work was performed on
`fix/confirmed-reply-durability-20260727T120532Z`, based on commit
`75af744227da0ef4f17f429b2dc5f07b215f8ae9`.

## Defect

The old conversational path created the X reply before writing a durable
confirmed-reply receipt. A hard interruption after X accepted the request but
before the receipt was durably written could therefore leave only the
pre-existing approved draft. A restart could treat that draft as unsent and
create a duplicate reply.

Historical-context replies already used a durable pre-send receipt and were not
part of this defect.

## Repair

Conversational replies now use a versioned two-state receipt:

| Durable state | Meaning | Automatic action after restart |
|---|---|---|
| `sending` | The exact reviewed reply and target were persisted before the X request; the remote outcome is not confirmed locally. | Block every remote-write lane pending manual reconciliation. |
| `confirmed` | X returned a valid post ID and the same receipt was atomically promoted. | Reconcile into protected state without creating another reply. |

The write order is:

1. validate the approved draft and exact reply/target binding;
2. atomically write and directory-fsync the `sending` receipt;
3. install controlled-SIGINT deferral;
4. issue the X create request;
5. atomically promote the exact receipt to `confirmed`;
6. apply and durably save protected state;
7. remove and directory-fsync the confirmed receipt.

Only a definite non-success, such as a non-ambiguous HTTP rejection, clears a
`sending` receipt as an unsuccessful request. Transport ambiguity, malformed
success responses, response-decoding errors, unclassified exceptions,
`KeyboardInterrupt` and `SystemExit` preserve it and fail closed.

If X returned a valid post ID but receipt promotion fails, the reply identity
is first written to canonical protected state. The `sending` receipt is cleared
only after that state is durably saved and independently verified; the
resulting event is recorded as a confirmed-state fallback, never as a definite
remote non-success. If neither receipt promotion nor the protected-state
fallback can be proved durable, all remote-write lanes remain blocked.

The internal prepared-transaction exception to the global write barrier now
requires the exact on-disk receipt and verifies its text and reply target.
Receipt removal also refuses to unlink a different transaction.

Schema-v2 confirmed receipts remain readable and reconcile idempotently.

## Operational handling

A surviving `sending` or malformed conversational receipt is a global
remote-write barrier. A valid `confirmed` receipt remains recoverable through
the existing idempotent reconciliation path. One-shot reply commands consult
the durable barrier before exiting.

The log digest now understands the normal
`sending` → `confirmed` → `removed` lifecycle. It reports a complete sequence as
routine, a definite pre-confirmation removal separately, a confirmed-state
fallback as recovery of a successful remote reply, and an unresolved `sending`
receipt as a current operational incident.

## Validation

- Directly affected runtime, receipt and digest tests: `486 passed`.
- Additional fail-safe, historical-context, incident and digest-observability
  modules: `290 passed`.
- The affected quote-research test module passed `11 passed`, and Python
  documentation checks passed `2 passed`.
- Coverage-equivalent three-worker suite:
  `2412 passed, 1 failed, 27 warnings` in `543.66s`.
- The sole broad-run failure was the unrelated
  `test_bounded_provider_concurrency`, whose 30 ms scheduling assumption
  observed one worker under full-suite load. The exact test then passed five
  consecutive runs unchanged.
- The fixture was replaced with a deterministic first-pair rendezvous. It then
  passed ten consecutive three-worker runs and the complete affected module
  passed `11 passed`.
- An independent final read-only review found no remaining durability, barrier,
  scheduler or digest-classification blocker.
- Python compilation passed.
- `git diff --check` passed.

The full suite was not repeated after the deterministic test-only correction;
the preceding run had completed every other test and the corrected test and its
module were exercised directly.

No X request, provider request, service action, deployment, push, production
state mutation or canonical-data mutation was performed.
