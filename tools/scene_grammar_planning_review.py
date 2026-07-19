#!/usr/bin/env python3
"""Serve the local scene grammar planning review interface."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semantic_alignment.io import atomic_write_json

DECISIONS = {"approve", "revise", "reject"}


def validate_review(decision: object, notes: object) -> dict[str, str]:
    """Validate review."""
    if decision not in DECISIONS:
        raise ValueError("decision must be approve, revise or reject")
    if not isinstance(notes, str):
        raise ValueError("notes must be text")
    return {"decision": str(decision), "notes": notes.strip()}


def save_review(path: Path, case_id: str, decision: object, notes: object) -> None:
    """Save review."""
    review = validate_review(decision, notes)
    payload = json.loads(path.read_text())
    payload["items"][case_id] = {
        "case_id": case_id,
        **review,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(path, payload)


def serve(run: Path, host: str = "127.0.0.1", port: int = 8774) -> None:
    """Serve the configured local interface."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("scene reviewer must bind to loopback")
    records = json.loads((run / "scene_specs.json").read_text())["items"]
    review_path = run / "scene_review.json"
    cases = {row["case_id"]: row for row in records}
    order = [row["case_id"] for row in records]
    csrf = secrets.token_urlsafe(24)

    def reviews() -> dict:
        return json.loads(review_path.read_text())

    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            saved = reviews()["items"]
            if path == "/":
                target = next((case_id for case_id in order if case_id not in saved), order[0])
                self.send_response(303)
                self.send_header("Location", "/case/" + target)
                self.end_headers()
                return
            case_id = path.removeprefix("/case/")
            if case_id not in cases:
                return self.send(404, "not found")
            record = cases[case_id]
            spec = record["scene_spec"]
            prior = saved.get(case_id, {})
            index = order.index(case_id)
            rows = []
            for key, value in spec.items():
                rendered = "<ol>" + "".join(f"<li>{html.escape(item)}</li>" for item in value) + "</ol>" if isinstance(value, list) else html.escape(value)
                rows.append(f"<tr><th>{html.escape(key.replace('_', ' ').title())}</th><td>{rendered}</td></tr>")
            radios = "".join(
                f'<label class="decision"><input type="radio" name="decision" value="{decision}" '
                f'{"checked" if prior.get("decision") == decision else ""} required>{decision.title()}</label>'
                for decision in ("approve", "revise", "reject")
            )
            body = f'''<!doctype html><meta name="viewport" content="width=device-width">
<title>Scene Grammar Planning Review</title><style>
body{{font:16px system-ui;max-width:1100px;margin:auto;padding:16px;background:#f3f3f1;color:#171717}}
nav{{position:sticky;top:0;background:#f3f3f1;padding:10px 0;border-bottom:1px solid #aaa}}
blockquote{{font-size:1.25rem;background:white;border-left:4px solid #a51d2d;padding:14px}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:9px;border-bottom:1px solid #ddd;vertical-align:top;text-align:left}}th{{width:25%}}
ol{{margin:0;padding-left:20px}}.decision{{display:inline-block;margin:14px 18px 14px 0;font-weight:650}}
textarea{{width:min(50rem,95%);min-height:6rem;display:block}}button,a{{padding:10px}}@media(max-width:650px){{th,td{{display:block;width:auto}}}}
</style><nav>{index+1}/{len(order)} · reviewed {len(saved)}/{len(order)} ·
<a href="/case/{order[max(0,index-1)]}">Previous</a> · <a href="/case/{order[min(len(order)-1,index+1)]}">Next</a></nav>
<blockquote>{html.escape(record['quote_text'])}</blockquote><table>{''.join(rows)}</table>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><div>{radios}</div>
<label>Optional notes<textarea name="notes">{html.escape(prior.get('notes', ''))}</textarea></label>
<button>Save and next</button></form>'''
            self.send(200, body)

        def do_POST(self) -> None:
            case_id = urlparse(self.path).path.removeprefix("/case/")
            if case_id not in cases:
                return self.send(404, "not found")
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            if form.get("csrf", [""])[0] != csrf:
                return self.send(403, "invalid csrf")
            try:
                save_review(review_path, case_id, form.get("decision", [""])[0], form.get("notes", [""])[0])
            except ValueError as error:
                return self.send(400, html.escape(str(error)))
            index = order.index(case_id)
            self.send_response(303)
            self.send_header("Location", "/case/" + order[min(len(order)-1,index+1)])
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    serve(args.run_dir.resolve(), args.host, args.port)


if __name__ == "__main__":
    main()
