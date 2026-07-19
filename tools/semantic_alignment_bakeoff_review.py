#!/usr/bin/env python3
"""Serve the local semantic alignment bakeoff review interface."""

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

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from semantic_alignment.io import atomic_write_json

PREFERENCES={"A","B","C","D","none","unsure"}
SECOND={"","A","B","C","D"}
DECISIONS={"A","B","C","D","all","none","unsure"}


def validate_preference(value):
    """Validate preference."""
    if not isinstance(value,dict) or value.get("critique_preference") not in PREFERENCES: raise ValueError("invalid critique preference")
    if value.get("second_best","") not in SECOND: raise ValueError("invalid second-best preference")
    if value.get("image_decision_agreement") not in DECISIONS: raise ValueError("invalid image decision agreement")
    if type(value.get("notes","")) is not str: raise ValueError("invalid notes")
    return dict(value)


def create_server(*,project_dir:Path,source_run:Path,bakeoff_dir:Path,host:str,port:int):
    """Create server."""
    csrf=secrets.token_urlsafe(24); mapping=json.loads((bakeoff_dir/'sealed_provider_mapping_four_way.json').read_text()); all_cases=json.loads((bakeoff_dir/'cases.json').read_text())['items']; queue=bakeoff_dir/'prioritised_four_provider_review_queue.json'; prioritised=json.loads(queue.read_text())['items'] if queue.exists() else []; queued={x['case_id'] for x in prioritised}; cases=prioritised+[x for x in all_cases if x['case_id'] not in queued]; quotes=json.loads((source_run/'quote_semantic_fingerprints.json').read_text())['items']; images=json.loads((source_run/'image_implied_messages_generated.json').read_text())['items']; results={p:json.loads((bakeoff_dir/f'{p}_results.json').read_text())['items'] for p in ('grok','openai','anthropic','gemini')}; preference_path=bakeoff_dir/'blinded_preferences_four_way.json'
    def load_preferences(): return json.loads(preference_path.read_text()) if preference_path.exists() else {"schema_version":3,"analysis_kind":"blinded_four_provider_preferences","items":{}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def send(self,status,body,kind='text/html; charset=utf-8'):
            data=body.encode() if isinstance(body,str) else body; self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/':
                prefs=load_preferences(); nxt=next((x for x in cases if x['case_id'] not in prefs['items']),cases[0]); self.send(200,f"<h1>Blinded critic comparison</h1><p>{len(prefs['items'])}/{len(cases)} reviewed</p><a href='/case/{nxt['case_id']}'>Continue</a>"); return
            if path.startswith('/image/'):
                case=next((x for x in cases if x['case_id']==path.rsplit('/',1)[1]),None); root=(project_dir/'generated_review_approved_images').resolve(); image=(root/case['image_basename']).resolve() if case else None
                if not image or image.parent!=root or not image.exists(): self.send(404,'Not found'); return
                self.send(200,image.read_bytes(),'image/png'); return
            if not path.startswith('/case/'): self.send(404,'Not found'); return
            case_id=path.rsplit('/',1)[1]; index=next((i for i,x in enumerate(cases) if x['case_id']==case_id),None)
            if index is None: self.send(404,'Not found'); return
            case=cases[index]; q=quotes[case['quote_hash']]; im=images[case['image_basename']]; prefs=load_preferences(); saved=prefs['items'].get(case_id,{})
            public_q={k:v for k,v in q.items() if k not in {'model','provider'}}; public_im={k:v for k,v in im.items() if k not in {'model','provider'}}
            blinded={label:{k:v for k,v in results[provider][case_id].items() if k not in {'model','case_id','quote_hash','image_basename'}} for label,provider in mapping.items()}
            radios=lambda name,values,current:''.join(f"<label><input type=radio name={name} value='{value}' {'checked' if current==value else ''} required> {label}</label>" for value,label in values)
            previous=cases[(index-1)%len(cases)]['case_id']; following=cases[(index+1)%len(cases)]['case_id']
            panels=''.join(f"<section><h2>{label}</h2><pre>{html.escape(json.dumps(blinded[label],indent=2))}</pre></section>" for label in ('Critic A','Critic B','Critic C','Critic D'))
            body=f"""<style>body{{font:16px sans-serif;max-width:1700px;margin:20px auto}}img{{max-width:620px;max-height:550px}}.critics{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}pre{{white-space:pre-wrap;background:#f3f4f6;padding:12px}}label{{display:block;margin:8px}}</style><nav><a href='/case/{previous}'>Previous</a> · <a href='/'>Progress</a> · <a href='/case/{following}'>Next</a></nav><h1>{index+1}/25</h1><img src='/image/{case_id}' alt='Pairing image'><h2>Quote</h2><p>{html.escape(q['quote_text'])}</p><details><summary>Quote fingerprint</summary><pre>{html.escape(json.dumps(public_q,indent=2))}</pre></details><details><summary>Image fingerprint</summary><pre>{html.escape(json.dumps(public_im,indent=2))}</pre></details><div class=critics>{panels}</div><form method=post><input type=hidden name=csrf value='{csrf}'><fieldset><legend>Best critique</legend>{radios('critique_preference',[('A','A'),('B','B'),('C','C'),('D','D'),('none','None acceptable'),('unsure','Unsure')],saved.get('critique_preference'))}</fieldset><label>Optional second best <select name=second_best><option value=''>None</option>{''.join(f'<option value={x} {"selected" if saved.get("second_best")==x else ""}>{x}</option>' for x in "ABCD")}</select></label><fieldset><legend>Which keep/replace decision do you agree with?</legend>{radios('image_decision_agreement',[('A','Critic A'),('B','Critic B'),('C','Critic C'),('D','Critic D'),('all','All'),('none','None'),('unsure','Unsure')],saved.get('image_decision_agreement'))}</fieldset><label>Optional notes<br><textarea name=notes rows=3 cols=90>{html.escape(saved.get('notes',''))}</textarea></label><button>Save and next</button></form>"""; self.send(200,body)
        def do_POST(self):
            path=urlparse(self.path).path
            if not path.startswith('/case/'): self.send(404,'Not found'); return
            form={k:v[-1] for k,v in parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()).items()}
            if form.get('csrf')!=csrf: self.send(400,'Invalid CSRF'); return
            case_id=path.rsplit('/',1)[1]; index=next((i for i,x in enumerate(cases) if x['case_id']==case_id),None)
            if index is None: self.send(404,'Not found'); return
            try: value=validate_preference({"critique_preference":form.get('critique_preference'),"second_best":form.get('second_best',''),"image_decision_agreement":form.get('image_decision_agreement'),"notes":form.get('notes',''),"updated_at":datetime.now(timezone.utc).isoformat()})
            except ValueError as exc: self.send(400,html.escape(str(exc))); return
            data=load_preferences(); data['items'][case_id]={"case_id":case_id,**value}; atomic_write_json(preference_path,data); nxt=cases[(index+1)%len(cases)]['case_id']; self.send_response(303); self.send_header('Location',f'/case/{nxt}'); self.end_headers()
    return ThreadingHTTPServer((host,port),Handler)


def main():
    """Run the command-line entry point."""
    ap=argparse.ArgumentParser(); ap.add_argument('--project-dir',type=Path,required=True); ap.add_argument('--source-run',type=Path,required=True); ap.add_argument('--bakeoff-dir',type=Path,required=True); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=8768); args=ap.parse_args()
    if args.host not in {'127.0.0.1','localhost','::1'}: raise SystemExit('Review app is loopback-only')
    server=create_server(project_dir=args.project_dir.resolve(),source_run=args.source_run.resolve(),bakeoff_dir=args.bakeoff_dir.resolve(),host=args.host,port=args.port); print(f'http://{args.host}:{server.server_port}'); server.serve_forever()

if __name__=='__main__': main()
