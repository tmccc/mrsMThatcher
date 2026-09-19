# Generated Image Review App

Local, server-rendered review UI for the approved generated-image pool. “Remove” always means reversible quarantine; permanent deletion is not implemented.

## Safety model

- Read-only by default; `--allow-changes` is required for quarantine/restore.
- Binds to `127.0.0.1` by default. Non-loopback binding requires `MRS_REVIEW_USERNAME`, `MRS_REVIEW_PASSWORD` and TLS or an explicitly configured trusted HTTPS reverse proxy.
- State-changing forms require signed CSRF tokens and explicit count-specific confirmation phrases.
- Writes are confined to the generated pool, `generated_image_analysis.json`, `generated_image_identity_dependence_audit.json`, app data, and quarantine tree.
- Bot state, used histories, lines, config, logs, receipts, lock, X, xAI, and process control are outside the application.
- Transactions preserve exact image hashes and metadata records, create metadata backups, and roll back moves/metadata on failure.

## Dependencies

Uses the existing Python environment: FastAPI, Uvicorn, Jinja2, and Pillow. No Node, multipart parser, or CDN assets.

## Start read-only

```bash
python3 -m tools.generated_image_review_app.app --host 127.0.0.1 --port 8765
```

Browse `http://127.0.0.1:8765/` locally or use an SSH tunnel from another machine.

## Enable quarantine/restore on loopback

```bash
python3 -m tools.generated_image_review_app.app --host 127.0.0.1 --port 8765 --allow-changes
```

## Remote access

Prefer a loopback listener and SSH tunnel. For direct TLS, set credentials in an
ignored environment file and use `--tls-cert /path/cert.pem --tls-key /path/key.pem`
with `--host 0.0.0.0 --public-host review.example`.

For an explicitly trusted TLS reverse proxy, use
`--trusted-proxy-origin https://review.example --public-host review.example`.
The proxy must preserve Host, strip untrusted forwarding headers and restrict
backend access to the proxy alone (prefer loopback). Do not expose its plaintext
backend to LAN users. All requests require the configured Host; cross-origin
requests are rejected. Both workflows share signed CSRF, bounded request bodies,
constant-time credential verification and security headers.

The quarantine launcher also accepts an optional `MRS_REVIEW_SECRET_KEY` in its
process environment to keep CSRF signatures valid across restarts. Keep this key
private and stable if that continuity is wanted. Without it, each restart creates
a new signing key. After a key changes, refresh the page to receive a replacement
cookie and form token; old or tampered POST tokens remain rejected.

The legacy swipe launcher delegates to `tools.generated_image_review_app.swipe`.
Its existing SQLite decisions, staged confirmation, undo and JSON export remain
compatible. Export is a protected POST, and no database migration is required.

## Workflow

Filter/sort the active gallery, select images, and choose **Review selected**. The review page shows all selected images, affected metadata, optional reason/notes, and a phrase such as `QUARANTINE 7 IMAGES`. Quarantine creates `generated_image_quarantine/transactions/<id>/` with images, metadata backups, and `manifest.json`.

The history page supports selected restoration with `RESTORE N IMAGES`. Restoration refuses an active basename conflict and restores exact preserved metadata. Used-image history is read-only and remains historical evidence.

## Production implications

Quarantine removes a file from live filesystem discovery immediately. It also updates both active metadata files so a future startup remains valid. The running bot may retain cached audit metadata; review the transaction and perform a separately controlled Python-child restart after a real quarantine. This app never signals or restarts production.

## Notes, cache, and service

Reasons and notes live in transaction manifests, not production audit classifications. Lazy JPEG thumbnails are cached under ignored app data using source SHA-256 names; source files are never modified. The example systemd unit is not installed or enabled by this project.

## Backup and troubleshooting

Each transaction's `metadata/` directory contains pre-change metadata copies. A failed action reports rollback status in its manifest. If a stale-review error appears, refresh the gallery and review again. Hash or coverage errors must be resolved outside the app before changes are enabled.
