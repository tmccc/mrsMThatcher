# Historical-context evidence release runbook

This runbook describes the safe path from reviewed historical evidence to a
production historical-context release. It records durable operating rules; a
specific release must still create its own transition manifest, audit,
validation record and rollback evidence.

Use this runbook for content validation when changing canonical historical
evidence or the generated corpus. For an already reviewed, tested, committed
and pushed code commit, use the canonical
[routine deployment procedure](../README.md#routine-deployment-of-an-already-tested-commit).
A new or untested change must still complete its
appropriate validation before becoming an approved deployment candidate.

## Scope and safety model

Historical-context evidence work has three distinct stages:

1. **Research** discovers and reviews possible sources in an isolated research
   workspace.
2. **Canonical admission** maps only approved evidence into authoritative
   inputs in a clean worktree based on the current production branch.
3. **Production deployment** installs a committed, fully regenerated candidate
   during a controlled maintenance window.

Do not perform research in the production worktree. Do not merge a research
branch wholesale into production. Private books, mirrors, fetched bodies, OCR
text, page images, caches and absolute private paths must never enter the
production commit.

Historical-context blocking is context-only unless a separately documented
ordinary-eligibility policy says otherwise. Evidence admission must not alter
quotation text, quotation IDs or the ordinary posting cycle.

## Evidence decisions

Review the underlying source material before trusting an automatic match or
classification. Record:

- canonical source identity and publisher;
- author or speaker;
- title, date and source event;
- stable locator;
- exact supporting passage and concise surrounding context;
- exact, variant, excerpt, composite or secondary status;
- source and extracted-text hashes where the schema permits them;
- every evidence role the source may support.

Exact-primary status permits only the project's authorised typographical
normalisations. A substantive insertion, omission, changed actor, changed
object, changed polarity or non-contiguous assembly is not exact.

For a primary variant, preserve the public quotation unchanged and record the
complete primary wording plus every material textual difference.

Primary status does not automatically grant every public evidence role. In
particular, wording evidence does not by itself establish the original source
event, date or historical context. Secondary reproductions must not be promoted
to primary or used to invent an original occasion.

## Authoritative and generated files

Before editing, trace the current builders and identify:

- authoritative evidence inputs;
- authoritative packet corrections;
- deterministic generated packets and audits;
- public projections and rendering reviews;
- semantic-review ledgers and gate projections;
- runtime manifests and exact fail-closed pins.

Edit only authoritative inputs. Regenerate dependent artefacts with established
builders. Do not hand-edit packets, audits, projections, checksums or
fingerprints.

Every admission needs a new, explicit transition manifest. Do not rewrite a
historical transition as though later evidence had been part of it. A
post-transition validator may need to reconstruct an earlier projection with
later sources removed so that the historical transition remains independently
verifiable.

Transition manifests should bind:

- the production base commit;
- the approved review record and its hash;
- exact quote, candidate and source IDs;
- source hashes and evidence decisions;
- before/after packet hashes;
- before/after public supported fields and source roles;
- before/after gate, unresolved and ordinary-cycle membership;
- every generated output and invariant.

Runtime pins are updated last, after regeneration and independent validation.
Keep them exact and fail closed. Never weaken a policy-version, schema or hash
check merely to make a new candidate load.

## Public source identity

Internal source-role audits must remain lossless. Public rendering may group a
weak legacy locator with a newly reviewed canonical source only under a narrow,
deterministic rule:

- exactly one independently inspected canonical source is present;
- dates and supported claims are compatible;
- no competing identifier or provenance exists;
- the weak record is genuinely redundant;
- ambiguous or unreviewed locators remain separate.

Do not expose private retrieval paths, transport URLs, page hashes, OCR paths,
confidence diagnostics or source-role internals in public output.

## Canonical admission workflow

1. Verify production `master`, its upstream and all pre-existing dirty files.
2. Create a clean admission branch and worktree from the current production
   commit.
3. Hash the authoritative baseline inputs and record baseline counts and
   memberships.
4. Reverify every approved source against the reviewed body, passage, metadata
   and hashes.
5. Apply only the approved source records, Meaning corrections and
   exact/variant decisions to authoritative inputs.
6. Regenerate all dependent artefacts with established builders.
7. Generate and validate a machine-readable semantic diff. Derive release
   counts and exact changed quote-ID lists from that diff, not from a prose
   report, abbreviated terminal output or conversational summary.
8. Run the semantic-diff checks.
9. Build the complete generated output twice from identical inputs.
10. Inspect representative public rendering, including unrelated allowed and
   blocked controls.
11. Run focused tests, compilation and `git diff --check`.
12. Run the complete offline suite once against the final candidate.
13. Inspect and stage every changed file explicitly.
14. Commit on the isolated admission branch; do not push yet.

Preserve the established serialisation of large generated artefacts unless the
authoritative builder deliberately changes it. A semantically harmless
pretty-print or key-order rewrite can obscure the reviewed transition, produce
an enormous diff and make hash review unnecessarily difficult.

## Required invariants

Derive expected counts from current authoritative files rather than from an old
report or conversational prompt. Require:

- quotation IDs unchanged;
- quotation text unchanged;
- ordinary-cycle membership and order unchanged;
- no unrelated packet semantic changes;
- no unrelated evidence-role or confidence changes;
- unresolved membership changed only when explicitly authorised and proved;
- gate changes produced only by the fail-closed policy;
- no stale fingerprints or pins;
- no network, provider or X calls during tests;
- no private evidence material in the staged diff.

Report counts using the authoritative schema fields. For example, read the
unresolved ID list or documented unresolved count directly; do not infer it
from a similarly named nested field.

Do not assume that two 611-item identity lists in one manifest must be
byte-for-byte or set-identical. A resolved-manifest list may retain historical
or alias identities while the runtime-eligible list contains current canonical
IDs. Prove ordinary-cycle invariance by comparing the authoritative before and
after runtime-cycle membership and order through the established builder.

## Deterministic-build proof

Generate twice in separate clean temporary directories from identical inputs.
Require byte-identical stable outputs, source IDs, gate membership, supported
fields and checksums.

If a builder includes an unavoidable generated timestamp, compare canonicalised
outputs excluding only that documented timestamp field. Any other difference is
unexplained nondeterminism and blocks deployment.

## Testing strategy

Run cheap and focused checks while developing. Reserve the complete suite for
the final integrated candidate unless a shared runtime change creates a reason
to run it sooner.

Before deployment require:

- all focused tests pass;
- compilation passes for every changed Python file;
- `git diff --check` passes;
- the complete offline suite has zero failures and errors;
- no test performs a live network, provider or X request.

When a broad-suite failure appears, diagnose and fix the cause rather than
repeatedly rerunning the whole suite. Rerun the affected node first, then run
the complete suite once more only when the integrated candidate is ready.

Test expectations which bind exact hashes or generated counts must be updated
only after verifying that the underlying semantic change is authorised.

An evidence change must have a final green complete-suite run before deployment. If the
operator explicitly directs that a second long run not be performed after a
broad run exposes repair-related failures, do not describe the broad suite as
passing. Preserve the complete failed-node list and signatures, require every
failed node to pass after repair, run the complete directly affected test set,
and record the omitted second run as an explicit release deviation in both
admission and deployment validation.

## Production deployment

Before touching production:

1. Confirm production still equals the candidate base and upstream.
2. Preserve every unrelated dirty or untracked file byte-for-byte.
3. Create a private deployment directory.
4. Back up changed tracked files, canonical artefacts, runtime state,
   receipts, histories, schedules, the control file and relevant logs.
5. Record service PIDs, command line, imported paths, start time and restart
   count.
6. Confirm the due state of every remote-write lane: ordinary posts, memes,
   mention replies, quote-tweet replies and historical-context outbox work.
7. Activate the existing supported global remote-write pause atomically.
8. Wait for the current runtime's actual pause-acknowledgement message.

The first live backup proves the pre-pause state but can race a running
scheduler. After the pause is acknowledged and the service has stopped, take a
second quiescent snapshot before changing Git or canonical artefacts.

Pause acknowledgement is runtime-path dependent. At startup the process may
acknowledge the pause by saying that receipt reconciliation was left untouched,
while an already-running main loop may emit the general “all remote-write lanes
remain idle” message. Require a message produced after the pause boundary; do
not require one hard-coded wording when both paths are established and tested.

Then:

1. Stop only `mrsMThatcher.service`.
2. Verify both wrapper and Python child have exited.
3. Fast-forward production to the approved commit.
4. Confirm every installed tracked file matches the committed hash.
5. Run deployed-path imports, schema loads, gate loads and offline renders.
6. Start `mrsMThatcher.service` once.
7. Keep the global pause active for at least five minutes.
8. Verify one wrapper and one child, stable PIDs, no crash loop, correct
   source-role and gate hashes, and unchanged runtime policies.
9. Check for stale pins, compatibility errors, receipt replay, duplicate main
   posts, delayed context replies, outbox failures and unintended X actions.
10. Restore the original control file byte-for-byte only after health checks
    pass and the schedule is safe.
11. Observe at least one idle scheduler cycle.
12. Push the approved commit only after live validation passes.

The deployment process itself must make no X post, reply, quote-tweet, deletion
or media upload. Distinguish any natural bot read-only activity after unpausing
from deployment-process activity.

Record the exact log offset or timestamp at restart and at unpause. An overdue
reply check may make natural read-only X requests or a provider assessment as
soon as the pause clears, even when quote and meme posts are not due. Report
read requests, provider calls and write requests separately, and require zero
deployment X writes. If the release requires zero provider activity as well,
do not unpause while any reply lane is due.

Do not require `NRestarts` to increment after a deliberate
`systemctl stop`/`start`; systemd commonly reports zero automatic restarts for
the new invocation. Prove the controlled restart using the deployment event
trace, changed start timestamp, new wrapper/child PIDs and stable post-start
process count.

## Runtime and digest compatibility

Confirm the deployed import paths rather than assuming repository files are the
files loaded by the service. The entry point may be a symlink while imported
modules and canonical data are loaded from the service working directory.

Always inspect the current `mrs_log_digest.py`. Do not copy a digest module or
test expectation from an older worktree. Evidence releases normally should not
change the digest; update digest expectations only when the current digest's
intentional wording or newly generated counts require it.

Verify that:

- historical-context gate loading is reported accurately;
- source-role compatibility failures remain visible;
- semantic-veto shadow mode is not presented as enforcement;
- generated-pool `allowed` and `enabled` states remain distinct;
- resolved legacy incidents are not presented as current failures.

For semantic-veto validation, use the manifest's actual field names and keep
three quantities distinct:

- the authorised universe (`quote_count × image_count`, currently
  `611 × 91 = 55,601`);
- resolved or adjudicated matrix entries;
- runtime lookup entries loaded for shadow observation.

`live_production_enabled: false` is the fail-closed enforcement fact in the
current manifest. A smaller resolved/runtime pair count is not a malformed
55,601-pair universe.

After an unpaused idle cycle, scheduler state and analytics may legitimately
change because of natural read-only checks. Compare immutable posting evidence
separately: quotation and image histories, confirmed receipts, context history
and outbox. Explain expected state/analytics changes rather than falsely
requiring every runtime-state byte to remain identical.

## Rollback

If installation, loading, startup or health validation fails:

1. keep the global pause active;
2. stop only `mrsMThatcher.service`;
3. restore the previous committed artefacts and runtime files;
4. preserve all failure and migration evidence;
5. restore receipts or state only when necessary to return to the exact prior
   runtime model;
6. start the prior version and verify its health;
7. restore the original control bytes only after rollback health passes;
8. do not push the failed admission commit.

Never delete archived receipts or rewrite historical posting evidence during a
rollback.

## Release record

Each release should retain:

- admission audit and report;
- semantic diff;
- deterministic-build comparison;
- transition manifest;
- final admission validation;
- production preflight and file manifest;
- before/after service and hash snapshots;
- startup log review and event trace;
- rollback manifest;
- production deployment report and final validation.

The release record should state explicitly whether production changed, whether
the service restarted, whether rollback occurred, and the number of
task-triggered X actions.

The tracked admission validation is necessarily pre-deployment and may record
`deployment_started: false`, `push_succeeded: false` and no commit hash. Do not
rewrite that committed evidence after deployment. Record the commit, push,
service health, natural post-unpause activity and rollback outcome in the
private production deployment validation instead.
