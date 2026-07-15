# Image Provider Trial 001 Execution Report

## Scope

- Cases: 10 deterministic, representative completed quote-research packets.
- Providers: xAI Grok Imagine and OpenAI image generation.
- Images: 20 total, exactly one accepted first generation per provider and quotation.
- Production effect: none.

## Models And Settings

| Provider | Model | Settings | Completed | Attempts | Known cost |
|---|---|---|---:|---:|---:|
| Grok | `grok-imagine-image-quality` | 1:1, 1k, n=1 | 10 | 10 | $0.50 |
| OpenAI | `gpt-image-1.5` | low, 1024x1024 PNG, n=1 | 10 | 10 | $0.13 |
| Total | | | 20 | 20 | $0.63 |

There were no failures or retries. Every decoded original is a 1024x1024 PNG. Thumbnails are separate files; originals were not re-encoded.

## Prompt Integrity

Each quotation has one canonical prompt stored in `prompts/<quote_id>.txt`. The substantive prompt bytes and SHA-256 match both saved provider request envelopes for all ten cases. Provider-specific settings differ only in API formatting and supported image controls.

## Review

The blind map is deterministic and persisted under `review/blind_map.json`. The normal review page contains no Grok/OpenAI names in HTML, image URLs, alt text, or visible metadata.

Launch command:

```bash
python3 compare_image_providers.py serve \
  --trial-dir semantic_alignment_research/image_provider_trial_001 \
  --host 127.0.0.1 \
  --port 8765
```

Review URL: `http://127.0.0.1:8765/`

After all ten decisions:

```bash
python3 compare_image_providers.py report \
  --trial-dir semantic_alignment_research/image_provider_trial_001
```

## Verification

- Prompt/image integrity: 10 prompt hashes, 20 parity checks, and 20 image dimension checks passed.
- Python compilation passed.
- Focused semantic-alignment and production-log-isolation tests: 54 passed.
- `git diff --check` passed.
- No files were staged, committed, pushed, or deployed.
