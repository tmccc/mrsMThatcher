#!/usr/bin/env python3
"""Serve the local generation prompt pilot review interface."""

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


def serve(run: Path, host: str = "127.0.0.1", port: int = 8772) -> None:
    """Serve the configured local interface."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("reviewer must bind to loopback")
    manifest = json.loads((run / "validation_manifest.json").read_text())["items"]
    generated = json.loads((run / "generated_candidates.json").read_text())["items"]
    reviews_path = run / "human_reviews.json"; csrf = secrets.token_urlsafe(24)
    by_case: dict[str, list[dict]] = {}
    for row in generated.values(): by_case.setdefault(row["case_id"], []).append(row)
    order = [row["case_id"] for row in manifest]; cases = {row["case_id"]: row for row in manifest}
    def reviews(): return json.loads(reviews_path.read_text())
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, kind="text/html; charset=utf-8"):
            data=body if isinstance(body,bytes) else body.encode(); self.send_response(status); self.send_header("Content-Type",kind); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path
            if path.startswith("/image/"):
                name=path.removeprefix("/image/")
                if "/" in name or ".." in name or not (run/"images"/name).is_file(): return self.send(404,"not found")
                return self.send(200,(run/"images"/name).read_bytes(),"image/png")
            saved=reviews()["items"]
            if path=="/":
                target=next((cid for cid in order if cid not in saved),order[0]); self.send_response(303); self.send_header("Location","/case/"+target); self.end_headers(); return
            cid=path.removeprefix("/case/")
            if cid not in cases:return self.send(404,"not found")
            case=cases[cid]; idx=order.index(cid); rows=sorted(by_case[cid],key=lambda x:x["candidate_id"]); labels="ABC"; prior=saved.get(cid,{})
            candidates="".join(f'<label class="candidate"><h2>Candidate {labels[i]}</h2><img src="/image/{row["image_basename"]}"><input type="radio" name="preferred_candidate" value="{labels[i]}" {"checked" if prior.get("preferred_candidate")==labels[i] else ""} required> Publish {labels[i]}</label>' for i,row in enumerate(rows))
            radios="".join(f'<label><input type="radio" name="message_match" value="{v}" {"checked" if prior.get("message_match")==v else ""} required>{v.title()}</label>' for v in ("yes","partly","no"))
            body=f'''<!doctype html><meta name="viewport" content="width=device-width"><title>Generation prompt pilot</title><style>body{{font:16px system-ui;max-width:1250px;margin:auto;padding:16px;background:#f4f4f2}}blockquote{{font-size:1.2rem;background:white;border-left:4px solid #a51d2d;padding:12px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.candidate{{background:white;padding:8px}}img{{width:100%;aspect-ratio:1;object-fit:contain}}label{{display:inline-block;margin:8px}}button,a{{padding:10px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}</style><nav>{idx+1}/{len(order)} · reviewed {len(saved)}/{len(order)} · <a href="/case/{order[max(0,idx-1)]}">Previous</a> · <a href="/case/{order[min(len(order)-1,idx+1)]}">Next</a></nav><blockquote>{html.escape(case["quote_text"])}</blockquote><form method="post"><input type="hidden" name="csrf" value="{csrf}"><div class="grid">{candidates}</div><p><label><input type="radio" name="preferred_candidate" value="none" {"checked" if prior.get("preferred_candidate")=="none" else ""} required>None</label><label><input type="radio" name="preferred_candidate" value="unsure" {"checked" if prior.get("preferred_candidate")=="unsure" else ""} required>Unsure</label></p><fieldset><legend>Does the winning image communicate the right message immediately?</legend>{radios}</fieldset><label>Notes <textarea name="notes">{html.escape(prior.get("notes",""))}</textarea></label><button>Save and next</button></form>'''; return self.send(200,body)
        def do_POST(self):
            cid=urlparse(self.path).path.removeprefix("/case/")
            if cid not in cases:return self.send(404,"not found")
            form=parse_qs(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode())
            if form.get("csrf",[""])[0]!=csrf:return self.send(403,"invalid csrf")
            choice=form.get("preferred_candidate",[""])[0]
            match=form.get("message_match",[""])[0]
            if choice not in {"A","B","C","none","unsure"} or match not in {"yes","partly","no"}:return self.send(400,"invalid review")
            data=reviews(); data["items"][cid]={"case_id":cid,"preferred_candidate":choice,"message_match":match,"notes":form.get("notes",[""])[0].strip(),"updated_at":datetime.now(timezone.utc).isoformat()}; atomic_write_json(reviews_path,data)
            idx=order.index(cid); self.send_response(303); self.send_header("Location","/case/"+order[min(len(order)-1,idx+1)]); self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer((host,port),Handler); print(f"http://{host}:{port}",flush=True); server.serve_forever()


def main():
    """Run the command-line entry point."""
    p=argparse.ArgumentParser(); p.add_argument("--run-dir",type=Path,required=True); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8772); a=p.parse_args(); serve(a.run_dir.resolve(),a.host,a.port)


if __name__=="__main__":main()
