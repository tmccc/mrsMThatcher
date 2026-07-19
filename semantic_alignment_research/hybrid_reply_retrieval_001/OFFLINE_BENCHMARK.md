# Offline Hybrid Reply-Retrieval Benchmark

Hybrid retrieval is not part of the production bot. Production conversational
replies continue to retrieve completed historical packets lexically through
`reply_strategy.retrieve_completed_evidence`.

Run this benchmark manually after changing the research corpus, embedding
model, index schema, fusion implementation or retrieval thresholds. The index
and evaluation use local files and the pinned local E5 model; they do not alter
production state or feed evidence into a reply.

```bash
python3 hybrid_reply_retrieval.py corpus-status \
  --research-run semantic_alignment_research/quote_research_full_001

python3 hybrid_reply_retrieval.py build-index \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --output semantic_alignment_research/hybrid_reply_retrieval_001

python3 hybrid_reply_retrieval.py evaluate \
  --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 \
  --research-run semantic_alignment_research/quote_research_full_001

python3 hybrid_reply_retrieval.py replay \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --since-days 30
```

`evaluate` is the deterministic lexical-versus-hybrid corpus benchmark.
`replay` reads retained local logs and creates review artefacts; it does not
submit work to the live bot. Provider-review subcommands are separate,
explicitly budgeted historical research tools and are not required for the
offline benchmark.

The retained live evidence motivating offline-only status is recorded in the
consolidation report and `shadow_feature_lifecycle.json`. It showed no
hybrid-only useful evidence rescue in the retained production comparisons.
