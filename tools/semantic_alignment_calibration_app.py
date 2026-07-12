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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from semantic_alignment.calibration import (BANDS, IMAGE_DECISIONS, RELATIONSHIPS,
    migrate_review_dataset, review_is_complete, review_progress,
    validate_human_label)
from semantic_alignment.io import atomic_write_json


def create_server(*, project_dir: Path, dataset_path: Path, host: str, port: int):
    csrf=secrets.token_urlsafe(24)
    def load(): return migrate_review_dataset(json.loads(dataset_path.read_text()))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args): pass
        def send(self,status,body,content_type='text/html; charset=utf-8'):
            data=body.encode() if isinstance(body,str) else body; self.send_response(status); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def find_case(self,case_id): return next((x for x in load()['items'] if x['case_id']==case_id),None)
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/':
                data=load(); progress=review_progress(data); nxt=next((x for x in data['items'] if not review_is_complete(x)),data['items'][0] if data['items'] else None)
                self.send(200,f"<h1>Semantic alignment calibration</h1><p>{progress['complete']}/{progress['total']} complete · {progress['incomplete']} incomplete</p>"+(f"<a href='/case/{nxt['case_id']}'>Continue review</a>" if nxt else 'No cases')); return
            if path.startswith('/image/'):
                row=self.find_case(path.rsplit('/',1)[1]); root=(project_dir/'generated_review_approved_images').resolve()
                if not row: self.send(404,'Not found'); return
                image=(root/row['image_basename']).resolve()
                if image.parent!=root or not image.exists(): self.send(404,'Not found'); return
                self.send(200,image.read_bytes(),'image/png'); return
            if path.startswith('/case/'):
                case_id=path.rsplit('/',1)[1]; data=load(); index=next((i for i,x in enumerate(data['items']) if x['case_id']==case_id),None)
                if index is None: self.send(404,'Not found'); return
                row=data['items'][index]; previous=data['items'][(index-1)%len(data['items'])]['case_id']; following=data['items'][(index+1)%len(data['items'])]['case_id']
                run=dataset_path.parent if (dataset_path.parent/'quote_semantic_fingerprints.json').exists() else dataset_path.parent.parent
                q=json.loads((run/'quote_semantic_fingerprints.json').read_text())['items'][row['quote_hash']]; im=json.loads((run/'image_implied_messages_generated.json').read_text())['items'][row['image_basename']]; old=json.loads((run/'semantic_alignment_critic.json').read_text())['items'][f"{row['quote_hash']}:{row['image_basename']}"]; label=row.get('human_label') or {}
                opts=lambda values,selected=None,required=False: ("<option value='' disabled selected>Choose…</option>" if required and selected is None else '')+''.join(f"<option value='{html.escape(x)}' {'selected' if x==selected else ''}>{html.escape(x)}</option>" for x in values)
                rating=lambda name,current,descriptions: ''.join(f"<label class=rating><input type=radio name={name} value={n} {'checked' if current==n else ''} required><strong>{n}</strong> {html.escape(text)}</label>" for n,text in enumerate(descriptions,1))
                decision_labels={"keep_current_image":"Keep this image","prefer_different_generated_image":"Ask the bot to choose a different generated image","unsure":"Unsure"}
                decision_controls=''.join(f"<label class=rating><input type=radio name=image_decision value='{value}' {'checked' if label.get('image_decision')==value else ''} required> {text}</label>" for value,text in decision_labels.items())
                complete=review_is_complete(row); progress=review_progress(data)
                quote_summary={'core_claim':q['core_claim'],'claims':q['claims'],'themes':q['primary_themes'],'mechanism_consequence_principle':row['quote_decomposition']}
                image_summary={'core_implied_claim':im['core_implied_claim'],'implied_claims':im['implied_claims'],'visual_evidence':im['visual_evidence'],'mechanism_consequence_principle':row['image_decomposition']}
                body=f"""<style>body{{font:16px sans-serif;max-width:1200px;margin:20px auto;color:#202124}}img{{max-width:620px;max-height:620px}}pre{{white-space:pre-wrap;background:#f3f4f6;padding:12px;border:1px solid #ddd}}label{{display:block;margin:10px 0}}select,textarea,button{{font:inherit}}.status{{padding:8px;background:{'#d9f7df' if complete else '#fff3cd'}}}.mindset{{padding:10px;border-left:4px solid #555;background:#f3f4f6}}.ratings{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.rating{{margin:5px 0}}nav a{{margin-right:18px}}</style><nav><a id=prev href='/case/{previous}'>Previous</a><a href='/'>Progress</a><a id=next href='/case/{following}'>Next</a></nav><h1>{index+1}/100 · {row['split']}</h1><p class=status>{'Complete' if complete else 'Incomplete'} · overall progress {progress['complete']}/{progress['total']}</p><p class=mindset>Judge the pairing as it would appear in the published post, with the quotation shown alongside the image. Do not infer the generation prompt or intended design brief.</p><img src='/image/{case_id}' alt='Generated editorial image under review'><h2>Quote</h2><p>{html.escape(q['quote_text'])}</p><details open><summary>Quote fingerprint summary</summary><pre>{html.escape(json.dumps(quote_summary,indent=2))}</pre></details><details open><summary>Image fingerprint summary</summary><pre>{html.escape(json.dumps(image_summary,indent=2))}</pre></details><details><summary>Old critic result</summary><pre>{html.escape(json.dumps(old,indent=2))}</pre></details><form id=review method=post><input type=hidden name=csrf value='{csrf}'><h2>Relationship</h2><label>Primary relationship <select name=primary_relationship required>{opts(RELATIONSHIPS,label.get('primary_relationship'),True)}</select></label><label>Secondary relationship <select name=secondary_relationship><option value=''>none</option>{opts(RELATIONSHIPS,label.get('secondary_relationship'))}</select></label><label>Relevance band <select name=relevance required>{opts(BANDS,label.get('relevance'),True)}</select></label><label>Directness band <select name=directness required>{opts(BANDS,label.get('directness'),True)}</select></label><div class=ratings><fieldset><legend>How appropriate is this image for this quotation?</legend>{rating('appropriateness_rating',label.get('appropriateness_rating'),['Clearly inappropriate','Weak fit','Acceptable but indirect','Strong fit','Exceptional fit'])}</fieldset><fieldset><legend>How likely would you be to publish this pairing?</legend>{rating('publish_likelihood_rating',label.get('publish_likelihood_rating'),['Definitely would not publish','Unlikely to publish','Might publish','Likely to publish','Definitely would publish'])}</fieldset></div><fieldset><legend>If this were today’s scheduled post, what would you do?</legend>{decision_controls}<p><small>Choosing a different image asks the selector to try another generated candidate; it does not guarantee the alternative will be better.</small></p></fieldset><label>Optional reviewer note<br><textarea name=notes rows=4 cols=90>{html.escape(label.get('notes',''))}</textarea></label><button type=submit>Save and next</button></form><p><small>Shortcuts: 1–5 appropriateness; Shift+1–5 publish likelihood; K keep; D different; U unsure; S save; N next; P previous.</small></p><script>document.addEventListener('keydown',e=>{{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;let match=/^Digit([1-5])$/.exec(e.code);if(match){{let n=match[1],name=e.shiftKey?'publish_likelihood_rating':'appropriateness_rating';document.querySelector(`[name="${{name}}"][value="${{n}}"]`).checked=true;e.preventDefault()}}else if(['k','d','u'].includes(e.key.toLowerCase())){{let values={{k:'keep_current_image',d:'prefer_different_generated_image',u:'unsure'}},value=values[e.key.toLowerCase()];document.querySelector(`[name="image_decision"][value="${{value}}"]`).checked=true;e.preventDefault()}}else if(e.key.toLowerCase()==='s'){{document.getElementById('review').requestSubmit();e.preventDefault()}}else if(e.key.toLowerCase()==='n'){{location=document.getElementById('next').href}}else if(e.key.toLowerCase()==='p'){{location=document.getElementById('prev').href}}}});</script>"""
                self.send(200,body); return
            self.send(404,'Not found')
        def do_POST(self):
            path=urlparse(self.path).path
            if not path.startswith('/case/'): self.send(404,'Not found'); return
            length=int(self.headers.get('Content-Length','0')); form={k:v[-1] for k,v in parse_qs(self.rfile.read(length).decode()).items()}
            if form.get('csrf')!=csrf: self.send(400,'Invalid CSRF token'); return
            data=load(); case_id=path.rsplit('/',1)[1]; index=next((i for i,x in enumerate(data['items']) if x['case_id']==case_id),None)
            if index is None: self.send(404,'Not found'); return
            def rating(name):
                raw=form.get(name,'')
                return int(raw) if len(raw)==1 and raw in '12345' else None
            prior=data['items'][index].get('human_label') or {}
            label={'primary_relationship':form.get('primary_relationship'),'secondary_relationship':form.get('secondary_relationship') or None,'relevance':form.get('relevance'),'directness':form.get('directness'),'appropriateness_rating':rating('appropriateness_rating'),'publish_likelihood_rating':rating('publish_likelihood_rating'),'image_decision':form.get('image_decision'),'notes':form.get('notes',''),'updated_at':datetime.now(timezone.utc).isoformat()}
            for key in ('acceptable_for_posting','preferred_over_current'):
                if key in prior: label[key]=prior[key]
            try: data['items'][index]['human_label']=validate_human_label(label)
            except ValueError as exc: self.send(400,html.escape(str(exc))); return
            data['items'][index]['review_complete']=True; data['schema_version']=3
            atomic_write_json(dataset_path,data); nxt=data['items'][(index+1)%len(data['items'])]['case_id']; self.send_response(303); self.send_header('Location',f'/case/{nxt}'); self.end_headers()
    return ThreadingHTTPServer((host,port),Handler)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--project-dir',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--dataset',type=Path,required=True); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=8766); args=ap.parse_args()
    if args.host not in {'127.0.0.1','localhost','::1'}: raise SystemExit('Calibration app is loopback-only')
    server=create_server(project_dir=args.project_dir.resolve(),dataset_path=args.dataset.resolve(),host=args.host,port=args.port); print(f'http://{args.host}:{server.server_port}'); server.serve_forever()

if __name__=='__main__': main()
