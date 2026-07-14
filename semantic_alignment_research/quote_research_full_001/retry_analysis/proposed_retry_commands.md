# Proposed Retry Commands

No command below is authorised by this planning task.

```bash
python3 analyse_quote_research_gemini.py retry-plan-status \
  --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_research_full_001 \
  --retry-manifest /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_research_full_001/retry_analysis/retry_manifest_20.json

python3 analyse_quote_research_gemini.py run-retry-validation \
  --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_research_full_001 \
  --retry-manifest /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/quote_research_full_001/retry_analysis/retry_manifest_20.json \
  --execute \
  --enable-vertex-fallback \
  --developer-concurrency 1 \
  --vertex-concurrency 1 \
  --max-attempts 2 \
  --pause-developer-after-consecutive-provider-wide-429 2 \
  --no-automatic-developer-reprobe \
  --confirm-combined-limit-usd 5 \
  --resume
```

The execution command must remain unavailable without `--execute` and the exact $5 confirmation. It must skip every quote already present in `research_packets.json`, including offline recoveries. Review the 20-item result before authorising any remaining candidate.
