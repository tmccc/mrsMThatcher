# Blind review interface

Run from the repository root:

```bash
python3 compare_historical_context_formatters.py serve \
  --trial-dir semantic_alignment_research/historical_context_formatter_trial_001 \
  --host 127.0.0.1 \
  --port 8766
```

Open <http://127.0.0.1:8766>. The normal review page exposes only A/B labels.
