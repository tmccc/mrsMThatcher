#!/usr/bin/env python3
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
from semantic_alignment.scene_grammar_pilot import blinded_candidates


def serve(run: Path, host: str = "127.0.0.1", port: int = 8773) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("reviewer must bind to loopback")
    manifest = json.loads((run / "validation_manifest.json").read_text())["items"]
    generated = json.loads((run / "generated_candidates.json").read_text())["items"]
    reviews_path = run / "human_reviews.json"
    csrf = secrets.token_urlsafe(24)
    by_case: dict[str, list[dict]] = {}
    for row in generated.values():
        by_case.setdefault(row["case_id"], []).append(row)
    order = [row["case_id"] for row in manifest]
    cases = {row["case_id"]: row for row in manifest}

    def reviews() -> dict:
        return json.loads(reviews_path.read_text())

    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: str | bytes, kind: str = "text/html; charset=utf-8") -> None:
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/image/"):
                name = path.removeprefix("/image/")
                image = run / "images" / name
                if "/" in name or ".." in name or not image.is_file():
                    return self.send(404, "not found")
                return self.send(200, image.read_bytes(), "image/png")
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
            case = cases[case_id]
            index = order.index(case_id)
            prior = saved.get(case_id, {})
            candidates = "".join(
                f'<label class="candidate"><h2>Candidate {row["label"]}</h2>'
                f'<img src="/image/{html.escape(row["image_basename"])}" alt="Candidate {row["label"]}">'
                f'<input type="radio" name="preferred_candidate" value="{row["label"]}" '
                f'{"checked" if prior.get("preferred_candidate") == row["label"] else ""} required> '
                f'Publish {row["label"]}</label>'
                for row in blinded_candidates(by_case[case_id])
            )
            matches = "".join(
                f'<label><input type="radio" name="message_match" value="{value}" '
                f'{"checked" if prior.get("message_match") == value else ""} required>{value.title()}</label>'
                for value in ("yes", "partly", "no")
            )
            body = f'''<!doctype html><meta name="viewport" content="width=device-width">
<title>Scene grammar pilot</title><style>
body{{font:16px system-ui;max-width:1080px;margin:auto;padding:16px;background:#f4f4f2;color:#171717}}
blockquote{{font-size:1.2rem;background:white;border-left:4px solid #a51d2d;padding:12px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.candidate{{background:white;padding:10px}}img{{width:100%;aspect-ratio:1;object-fit:contain}}
label{{display:inline-block;margin:8px}}textarea{{display:block;width:min(36rem,90vw);min-height:5rem}}
button,a{{padding:10px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style><nav>{index+1}/{len(order)} · reviewed {len(saved)}/{len(order)} ·
<a href="/case/{order[max(0,index-1)]}">Previous</a> ·
<a href="/case/{order[min(len(order)-1,index+1)]}">Next</a></nav>
<blockquote>{html.escape(case["quote_text"])}</blockquote><form method="post">
<input type="hidden" name="csrf" value="{csrf}"><div class="grid">{candidates}</div>
<p><label><input type="radio" name="preferred_candidate" value="none" {"checked" if prior.get("preferred_candidate") == "none" else ""} required>None</label>
<label><input type="radio" name="preferred_candidate" value="unsure" {"checked" if prior.get("preferred_candidate") == "unsure" else ""} required>Unsure</label></p>
<fieldset><legend>Does the chosen image communicate the right message immediately?</legend>{matches}</fieldset>
<label>Notes<textarea name="notes">{html.escape(prior.get("notes", ""))}</textarea></label>
<button>Save and next</button></form>'''
            return self.send(200, body)

        def do_POST(self) -> None:
            case_id = urlparse(self.path).path.removeprefix("/case/")
            if case_id not in cases:
                return self.send(404, "not found")
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            if form.get("csrf", [""])[0] != csrf:
                return self.send(403, "invalid csrf")
            choice = form.get("preferred_candidate", [""])[0]
            match = form.get("message_match", [""])[0]
            if choice not in {"A", "B", "none", "unsure"} or match not in {"yes", "partly", "no"}:
                return self.send(400, "invalid review")
            data = reviews()
            data["items"][case_id] = {
                "case_id": case_id,
                "preferred_candidate": choice,
                "message_match": match,
                "notes": form.get("notes", [""])[0].strip(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_json(reviews_path, data)
            index = order.index(case_id)
            self.send_response(303)
            self.send_header("Location", "/case/" + order[min(len(order)-1, index+1)])
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8773)
    args = parser.parse_args()
    serve(args.run_dir.resolve(), args.host, args.port)


if __name__ == "__main__":
    main()
