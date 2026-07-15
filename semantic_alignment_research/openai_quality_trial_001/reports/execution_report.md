# OpenAI Quality Trial 001 Execution Report

## Scope

- Eligible corpus: 626 completed, schema-valid research packets.
- Permanently excluded unresolved records: 6.
- Selected cases: 10 deterministic editorial categories.
- Images: 10 Medium and 10 High, one accepted first generation per prompt.
- Model: `gpt-image-1`.
- Size: 1024x1024 PNG.

## Execution

| Quality | Completed | Attempts | Failures | Estimated cost |
|---|---:|---:|---:|---:|
| Medium | 10 | 10 | 0 | $0.458353 |
| High | 10 | 10 | 0 | $1.708355 |
| Total | 20 | 20 | 0 | $2.166708 |

No transport retry or aesthetic retry occurred. All originals are provider-supplied 1024x1024 PNG files. Review thumbnails were generated separately.

## Integrity

- All ten canonical prompt hashes matched the manifest.
- Both quality requests received byte-identical substantive prompts.
- The OpenAI `quality` parameter was the only payload difference.
- Every selected ID exists in the completed packet collection.
- No selected ID exists in the six-record unresolved set.
- Blind A/B mappings are deterministic and persisted separately.
- The normal review page does not expose quality assignments.

## Review

URL: `http://127.0.0.1:8765/`

Launch command:

```bash
python3 compare_openai_quality.py serve \
  --trial-dir semantic_alignment_research/openai_quality_trial_001 \
  --host 127.0.0.1 \
  --port 8765
```

After all reviews are complete:

```bash
python3 compare_openai_quality.py report \
  --trial-dir semantic_alignment_research/openai_quality_trial_001
```

## Verification

- Python compilation passed.
- Relevant quality-trial, provider-trial, semantic-alignment, and production-log-isolation tests: 60 passed.
- `git diff --check` passed.
- No production behavior or state was changed.
- Nothing was staged, committed, pushed, or deployed.
