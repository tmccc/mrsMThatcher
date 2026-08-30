# Substantive-question engagement experiment

This directory contains the frozen, provider-independent machinery for
`substantive-question-v1`. The experiment asks whether adding one approved,
quotation-specific substantive question to an otherwise unchanged regular
quotation root improves reach and useful engagement.

The control root is the exact canonical quotation. The treatment root is exactly:

```text
<exact canonical quotation>

Question — <exact approved question body>
```

The tracked approved catalogue is schema version 1, contains 93 entries, and has
SHA-256
`cdce2b7f7a7ceb140e907716f1ef7392d77aab99061997bb492cddf98952f6ba`.
Questions are never generated, rewritten, shortened, or normalised at runtime.
The live quotation text is always resolved again from the current canonical
production source and all stored hashes and weighted lengths are rechecked.

## Plan and scheduling

The offline preparation tool excludes used or currently ineligible quotations,
then builds 30 deterministic matched pairs. Both members have an approved
question, the same existing broad topic, and the same fixed quotation-length
band. Matching minimises verification-label mismatch, source-class mismatch and
control weighted-length difference in that order. Domain-separated hashes assign
arms and balance publication order to exactly 15 treatment-first and 15
control-first pairs. No engagement result, image score, or question wording is
used in assignment or ordering.

Plan preparation must be performed later while the bot is paused, after the
pushed implementation and proposed plan have been reviewed. It refuses an active
regular-post receipt and a changing used-history snapshot, never calls a network
or model provider, never marks a quotation used, and creates a mode-0600 live
plan. A future deployment task can run:

```bash
python3 /disks/disk1/etc/mrsMThatcher/tools/prepare_engagement_question_experiment.py \
  --prepare-plan \
  --repository-root /disks/disk1/etc/mrsMThatcher \
  --runtime-root /disks/disk1/etc/mrsMThatcher \
  --catalogue-path /disks/disk1/etc/mrsMThatcher/engagement_question_experiment/approved_question_catalogue.json \
  --plan-path /disks/disk1/etc/mrsMThatcher/engagement_question_experiment/active_plan.json \
  --report-path /disks/disk1/research/engagement-question-live-plan/proposed_plan.md \
  --manifest-path /disks/disk1/research/engagement-question-live-plan/proposed_plan_manifest.json \
  --plan-kind live
```

The live JSON uses the exact canonical byte representation required by the bot's
secure no-follow loader. It also carries the bounded, canonical matching inputs
for every creation-time eligible candidate and the exact canonical used-history
identity snapshot from which that roster was derived. `--validate-plan` therefore
reconstructs matching, selection, arm, and order assignments even after the
running bot has advanced used history, while still revalidating current source
and eligibility metadata.

Only normal rolling regular-root opportunities are used. At most one pair starts
on a Europe/London date; its other member is preferred at the next opportunity.
A carry-over member blocks a new pair on its later completion date, and at most
one treatment can be confirmed per date. Ordinary unreserved quotation posts
continue on other opportunities. The experiment stops automatically after 30
complete pairs.

Once the first member starts, every unposted plan quotation is reserved from
ordinary selection without being written prematurely to `lines_used.json`.
Reservations survive restart, used-history cycle handling, and a temporary
configuration pause. With the source-default feature disabled before the first
member, no plan or catalogue is loaded, no experiment state exists, no quote is
reserved, and the ordinary selection/RNG and root-payload paths are unchanged.
Disabling an already-started experiment pauses experimental publication while
retaining its reservations.

After a treatment post is confirmed and experiment progress is durable, the bot
atomically writes the configured compact Home Assistant source document. A failed
notification write is observational: it cannot retry or invalidate the X post,
and every treatment notification remains in a bounded durable queue until it has
been published idempotently. Older failures cannot be overwritten by a later
treatment; retries always publish the oldest pending identity first and preserve
each different document for at least two 30-second Home Assistant polling
intervals before replacement.
The later deployment configuration is:

```json
{
  "engagement_question_experiment_enabled": true,
  "engagement_question_experiment_plan_path": "engagement_question_experiment/active_plan.json",
  "engagement_question_notification_output_path": "/HOST/HOME-ASSISTANT/CONFIG/.runtime/mrs_m_thatcher_engagement_question.json"
}
```

That later task would install
`deploy/home-assistant/mrs_m_thatcher_engagement_question.yaml`; this repository
task does not edit the live Home Assistant configuration.

The analytics report uses existing collected observations only:

```bash
python3 mrs_engagement_analytics.py report \
  --project-dir "$PWD" --experiment substantive-question-v1
```

It is descriptive. There is no live model generation, automatic stopping from
interim results, or automatic production promotion.
