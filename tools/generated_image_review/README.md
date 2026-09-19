# Generated Image Review

Compatibility launcher for the swipe workflow in `tools.generated_image_review_app.swipe`.
Both review workflows use the same authenticated request boundary. The SQLite database
and all initial, confirmation, reconfirmation and undo decisions are retained in place;
no database conversion is needed. Keep using the same `--database` and `--export-file`.

Non-loopback service requires `MRS_REVIEW_USERNAME` and `MRS_REVIEW_PASSWORD` and
TLS (`--tls-cert` / `--tls-key`) or `--trusted-proxy-origin https://review.example`.
Use `--public-host review.example` to allow the actual HTTP Host. A trusted proxy
must terminate TLS, preserve that Host, restrict access to its backend and strip
untrusted forwarding headers. Never expose the plaintext backend to LAN clients.
Loopback development permits no credentials; requests still require signed CSRF,
valid Host/Origin and bounded bodies. Export is a CSRF-protected POST.


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
  --host 127.0.0.1 \
  --port 8765
```

Open `http://127.0.0.1:8765/` locally, or use an SSH tunnel from another device.

## Confirmation Pass

After the first pass is complete, restart the app in confirmation mode to reassess only images you originally swiped right:

```bash
python3 app.py \
  --corpus-root /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images \
  --database /disks/disk1/etc/mrsMThatcher/review/generated_image_review.sqlite3 \
  --export-file /disks/disk1/etc/mrsMThatcher/generated_image_overrides.json \
  --mode confirm-allowed \
  --host 127.0.0.1 \
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
  --host 127.0.0.1 \
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

Uvicorn factory invocation using the environment variables (local by default):

```bash
uvicorn tools.generated_image_review.app:configured_app --factory --host 127.0.0.1 --port 8765 --no-proxy-headers
```

For access through an HTTPS reverse proxy, set `MRS_REVIEW_USERNAME` and
`MRS_REVIEW_PASSWORD` in the process environment, then configure the public
hostname and exact browser origin:

```bash
export GIR_HOST=127.0.0.1
export MRS_REVIEW_PUBLIC_HOST=review.example
export MRS_REVIEW_TRUSTED_PROXY_ORIGIN=https://review.example
uvicorn tools.generated_image_review.app:configured_app --factory --host "$GIR_HOST" --port 8765 --no-proxy-headers
```

Keep the backend reachable only by the proxy and preserve the public Host. The
same public-host and proxy-origin variables supply defaults for the CLI's
`--public-host` and `--trusted-proxy-origin` options. The factory requires the
same authentication and CSRF protection as the CLI, even if Uvicorn's `--host`
differs from `GIR_HOST`. Use the CLI's `--tls-cert` and `--tls-key` options for
direct TLS service without a reverse proxy.

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

Only rows with `undone_at IS NULL` are effective. First-pass decisions use stage
`initial`; later passes use `confirm_allowed` and `reconfirm_allowed`. Undo marks
the most recent effective decision in the current stage and its dependent later
reviews as undone, in one database transaction. Reviewing that image again
requires fresh later approvals. Queue eligibility and exports follow this same
sequence, ordered by history ID. Opening an existing database also invalidates
inconsistent later reviews. All history rows are retained across refreshes and
server restarts.

## Export

The app writes the export atomically after each decision and undo, and also
exposes `POST /api/export` with the same authentication and signed CSRF
requirements. `GET /api/export` never writes an export.
After opening an older database with inconsistent review history, use **Export**
to refresh any previously written JSON from the repaired decisions.

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
- `POST /api/export`
- `GET /image/{quote_hash}`

The decision endpoint rejects stale decisions if the submitted quote hash is not the current first pending item.
All POST endpoints, including undo and export, require the signed cookie and
matching `X-CSRF-Token` header issued by a preceding GET. Cross-origin requests
are rejected; a caller-supplied loopback Host cannot bypass network authentication.
