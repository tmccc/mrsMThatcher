Findings

1. High — activation is fail-open at the deployment boundary. The replacement strategy is enabled in source defaults at [mrsMThatcher2.py:233](/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py:233), and the example deployment configuration also enables it at [mrsMThatcher.local.example.json:26](/disks/disk1/etc/mrsMThatcher/mrsMThatcher.local.example.json:26). If configuration is absent, the runtime explicitly uses those defaults ([mrsMThatcher2.py:845](/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py:845)); an eligible candidate then loads evidence and proceeds to provider generation ([mrsMThatcher2.py:9097](/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py:9097)). Thus deploying the cumulative diff can activate provider calls and, because `DRY_RUN_REPLIES` defaults false, posting without a separate feature-enable action. This is a production-activation blocker. The source and example default should remain disabled until activation is separately authorised.

2. Low — sentence counting falsely rejects ordinary numbered-address prose. The abbreviation set at [reply_strategy.py:681](/disks/disk1/etc/mrsMThatcher/reply_strategy.py:681) omits `No.`, so the boundary logic at [reply_strategy.py:724](/disks/disk1/etc/mrsMThatcher/reply_strategy.py:724) counts `No. 10 is the answer.` as two sentences. Reproduction:

   ```text
   sentence_count("No. 10 is the answer.") == 2
   ```

   This is a conservative false-negative tradeoff, not a path that accepts overlong output, but it can suppress valid two-sentence replies containing “No. 10”.

Assessment

The production/research SDK split is effective: an import probe that deliberately rejected every `google` import loaded `mrsMThatcher2` successfully, with neither the Gemini research runner nor Google SDK present. The evidence corpus also loaded offline as 626 completed, six unresolved, 610 attribution-eligible packets and 7,928 passages. Lazy loading is genuinely after candidate/context eligibility and before media/provider work.

The V2 pipeline, evidence validation, persisted-draft binding, receipt reconciliation and ambiguous-write barrier appeared fail-closed in inspected paths. Hot-post ineligible results are removed before candidate limiting at [mrsMThatcher2.py:3725](/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py:3725). I found no confirmed duplicate-write, schema-bypass, or false-acceptance defect in those paths.

The diff is suitable for a separately authorised, explicitly non-posting provider pilot provided the pilot supplies controlled configuration and dry-run isolation. It is not ready for production activation because merely deploying it can enable the feature.

Commands/tests actually run

- `git status --short`, cumulative `git diff`, targeted source inspection, and `git diff --check` — clean apart from the intentional worktree.
- Offline corpus construction — succeeded with the counts above.
- Production import with Google imports blocked — succeeded.
- Direct sentence-count probes — reproduced the `No.` false rejection.
- Focused pytest selection was attempted but could not start because the read-only environment had no writable temporary directory.
- `py_compile` was also blocked because it attempted to create `__pycache__`.
- No full suite, network/provider calls, service operations, state writes, or project-file edits were performed.

READY FOR SEPARATELY AUTHORISED PROVIDER PILOT