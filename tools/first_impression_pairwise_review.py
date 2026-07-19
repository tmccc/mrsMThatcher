#!/usr/bin/env python3
"""Serve the local first impression pairwise review interface."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from semantic_alignment.pairwise_validation import REASONS, review_progress, save_review


def serve(run_dir: Path, project_dir: Path, host: str = "127.0.0.1", port: int = 8771) -> None:
    """Serve the configured local interface."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("reviewer must bind to loopback")
    manifest = json.loads((run_dir / "pairwise_manifest.json").read_text())["items"]
    intents = json.loads((project_dir / "semantic_alignment_research/first_impression/v1_20260712/quote_visual_intents.json").read_text())["items"]
    review_path = run_dir / "human_pairwise_reviews.json"
    by_id = {row["case_id"]: row for row in manifest}
    order = list(by_id)
    csrf = secrets.token_urlsafe(24)

    def reviews():
        return json.loads(review_path.read_text())["items"] if review_path.exists() else {}

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(status); self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/image/"):
                name = path.removeprefix("/image/")
                if "/" in name or ".." in name: return self.send(404, "not found")
                image = project_dir / "generated_review_approved_images" / name
                if not image.is_file(): return self.send(404, "not found")
                return self.send(200, image.read_bytes(), "image/png")
            if path == "/":
                saved = reviews(); target = next((cid for cid in order if cid not in saved), order[0])
                self.send_response(303); self.send_header("Location", "/case/" + target); self.end_headers(); return
            if not path.startswith("/case/") or path.removeprefix("/case/") not in by_id:
                return self.send(404, "not found")
            cid = path.removeprefix("/case/"); row = by_id[cid]; index = order.index(cid)
            saved_all = reviews(); saved = saved_all.get(cid, {}); progress = review_progress(order, saved_all)
            quote = intents[row["quote_hash"]]["quote_text"]
            def radios(name, values, key):
                return "".join(f'<label><input type="radio" name="{name}" value="{v}" {"checked" if saved.get(key)==v else ""} required>{v.title()}</label>' for v in values)
            reasons = "".join(f'<label><input type="checkbox" name="reason" value="{v}" {"checked" if v in saved.get("reasons",[]) else ""}>{v.replace("_"," ")}</label>' for v in sorted(REASONS))
            previous = order[max(0, index-1)]; following = order[min(len(order)-1, index+1)]
            body = f'''<!doctype html><meta name="viewport" content="width=device-width"><title>Pairwise review</title>
<style>body{{font:16px system-ui;max-width:1200px;margin:auto;padding:16px;background:#f4f4f2;color:#171717}}.top{{display:flex;justify-content:space-between}}blockquote{{font-size:1.25rem;border-left:4px solid #b32025;padding:10px;background:white}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.candidate{{background:white;padding:10px}}img{{width:100%;aspect-ratio:16/9;object-fit:contain;background:#111}}fieldset{{margin:14px 0;padding:12px}}label{{display:inline-block;margin:6px 14px 6px 0}}button,a{{padding:10px 14px}}@media(max-width:700px){{.pair{{grid-template-columns:1fr}}}}</style>
<div class="top"><b>Case {index+1}/{len(manifest)}</b><span>{progress['complete']} complete, {progress['incomplete']} remaining</span></div>
<blockquote>{html.escape(quote)}</blockquote><div class="pair"><section class="candidate"><h2>Candidate A</h2><img src="/image/{row['candidate_a']}"></section><section class="candidate"><h2>Candidate B</h2><img src="/image/{row['candidate_b']}"></section></div>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><fieldset><legend>Which image would you publish?</legend>{radios('choice',['A','B','neither','unsure'],'preferred_candidate')}</fieldset><fieldset><legend>How strong is your preference?</legend>{radios('strength',['slight','moderate','strong'],'preference_strength')}</fieldset><fieldset><legend>Optional reasons</legend>{reasons}</fieldset><label>Notes <input name="notes" value="{html.escape(saved.get('notes',''))}" size="60"></label><p><button type="submit">Save and next</button> <a href="/case/{previous}">Previous</a> <a href="/case/{following}">Next</a></p></form>
<script>document.addEventListener('keydown',e=>{{if(['INPUT','TEXTAREA'].includes(e.target.tagName))return;let m={{a:'A',b:'B',n:'neither',u:'unsure'}}[e.key.toLowerCase()];if(m)document.querySelector(`[name=choice][value=${{m}}]`).click();if(e.key==='1'||e.key==='2'||e.key==='3')document.querySelectorAll('[name=strength]')[+e.key-1].click();if(e.key.toLowerCase()==='s')document.querySelector('form').requestSubmit();}})</script>'''
            return self.send(200, body)

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/case/") or path.removeprefix("/case/") not in by_id:
                return self.send(404, "not found")
            cid = path.removeprefix("/case/"); length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode())
            if form.get("csrf", [""])[0] != csrf: return self.send(403, "invalid csrf")
            try:
                save_review(review_path, set(by_id), {"case_id": cid,
                    "preferred_candidate": form.get("choice", [None])[0],
                    "preference_strength": form.get("strength", [None])[0],
                    "reasons": form.get("reason", []), "notes": form.get("notes", [""])[0]})
            except ValueError as exc:
                return self.send(400, html.escape(str(exc)))
            index = order.index(cid); target = order[min(index + 1, len(order)-1)]
            self.send_response(303); self.send_header("Location", "/case/" + target); self.end_headers()

        def log_message(self, *_args): pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}", flush=True)
    server.serve_forever()


def main() -> None:
    """Run the command-line entry point."""
    parser=argparse.ArgumentParser(); parser.add_argument("--run-dir",type=Path,required=True); parser.add_argument("--project-dir",type=Path,default=ROOT); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8771)
    args=parser.parse_args(); serve(args.run_dir.resolve(), args.project_dir.resolve(), args.host, args.port)


if __name__ == "__main__": main()
