# Generated Image Review

Private swipe-review utility for generated Margaret Thatcher quote images.

The app reviews grade `X` images by default, stores every decision in SQLite, supports undo after restart, and exports a clean JSON overrides file. It does not modify the original assessment export or the generated-image corpus.

## Install

```bash
cd /disks/disk1/etc/mrsMThatcher/tools/generated_image_review
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 app.py \
  --corpus-root /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images \
  --database /disks/disk1/etc/mrsMThatcher/review/generated_image_review.sqlite3 \
  --export-file /disks/disk1/etc/mrsMThatcher/generated_image_overrides.json \
  --host 0.0.0.0 \
  --port 8765
```

Open `http://SERVER:8765/` from the phone, tablet, or desktop browser.

## Confirmation Pass

After the first pass is complete, restart the app in confirmation mode to reassess only images you originally swiped right:

```bash
python3 app.py \
  --corpus-root /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images \
  --database /disks/disk1/etc/mrsMThatcher/review/generated_image_review.sqlite3 \
  --export-file /disks/disk1/etc/mrsMThatcher/generated_image_overrides.json \
  --mode confirm-allowed \
  --host 0.0.0.0 \
  --port 8765
```

In confirmation mode:

- swipe right keeps the image allowed;
- swipe left downgrades it to rejected;
- the original first-pass decision remains in SQLite history;
- the export uses the confirmation decision as the final `decision`.

To do one more pass over images that were allowed in confirmation mode:

```bash
python3 app.py \
  --corpus-root /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images \
  --database /disks/disk1/etc/mrsMThatcher/review/generated_image_review.sqlite3 \
  --export-file /disks/disk1/etc/mrsMThatcher/generated_image_overrides.json \
  --mode reconfirm-allowed \
  --host 0.0.0.0 \
  --port 8765
```

This queues only images whose confirmation decision is still `allow`.

The same settings may be supplied with environment variables:

```bash
export GIR_CORPUS_ROOT=/disks/disk1/etc/mrsMThatcher/openai_generated_quote_images
export GIR_DATABASE=/disks/disk1/etc/mrsMThatcher/review/generated_image_review.sqlite3
export GIR_EXPORT_FILE=/disks/disk1/etc/mrsMThatcher/generated_image_overrides.json
export GIR_GRADE=X
export GIR_MODE=review
```

`GIR_ASSESSMENT_FILE` is optional. Set it only when you want to review a pre-filtered assessment export rather than the generated corpus itself.

Production-style Uvicorn invocation using the environment variables:

```bash
uvicorn tools.generated_image_review.app:configured_app --factory --host 0.0.0.0 --port 8765
```

## Assessment Input

CSV and JSON assessment files are supported. The loader recognises common field names for:

- `quote_hash`
- `grade`
- `overall_score`
- `flags`
- `assessment` text

If no assessment file is supplied, the app uses `corpus_index.json` and reviews every generated image with `image_01.png`. This is the normal mode when the swipe decisions are the assessment output.

If an assessment file is supplied, CSV and JSON assessment files are supported. The loader recognises common field names for:

- `quote_hash`
- `grade`
- `overall_score`
- `flags`
- `assessment` text

The generated image path is always derived from the quote hash:

```text
<corpus-root>/items/<quote_hash>/image_01.png
```

Assessment-provided image paths are deliberately ignored.

## Decisions

SQLite table:

```sql
decision_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_hash TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('allow', 'reject')),
  reviewed_at TEXT NOT NULL,
  item_order INTEGER NOT NULL,
  undone_at TEXT
)
```

Only rows with `undone_at IS NULL` are effective. First-pass decisions use stage `initial`; confirmation-pass decisions use stage `confirm_allowed`. Undo marks the most recent effective decision in the current stage as undone, so refreshes and server restarts preserve the undo history.

## Export

The app writes the export atomically after each decision and undo, and also exposes `GET /api/export`.

Format:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-08T15:30:00Z",
  "items": {
    "<quote_hash>": {
      "decision": "allow",
      "reviewed_at": "2026-07-08T15:20:00Z",
      "stage": "initial",
      "initial_decision": "allow",
      "initial_reviewed_at": "2026-07-08T15:20:00Z"
    }
  }
}
```

SQLite remains the authoritative store.

## API

- `GET /api/next`
- `POST /api/decision`
- `POST /api/undo`
- `GET /api/progress`
- `GET /api/export`
- `GET /image/{quote_hash}`

The decision endpoint rejects stale decisions if the submitted quote hash is not the current first pending item.
