#!/usr/bin/env python3
"""Serve the local first impression review interface."""

from __future__ import annotations
import argparse,html,json,secrets,sys
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from semantic_alignment.io import atomic_write_json,read_json

def parser():
    """Build the command-line argument parser."""
    p=argparse.ArgumentParser();p.add_argument('--project-dir',type=Path,required=True);p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8771);return p

def serve(project:Path,run:Path,host='127.0.0.1',port=8771):
    """Serve the configured local interface."""
    if host not in {'127.0.0.1','localhost'}:raise ValueError('reviewer must bind to loopback')
    cases=read_json(run/'validation_cases.json')['items'];quotes=read_json(project/'semantic_alignment_research/runs/v2_20260711T111526Z/quote_semantic_fingerprints.json')['items'];images=read_json(run/'image_first_impressions.json',{'items':{}})['items'];reviews_path=run/'human_reviews.json';pair_reviews_path=run/'pairwise_human_reviews.json';pairs=read_json(run/'pairwise_rankings.json',{'items':{}}).get('items',{});csrf=secrets.token_urlsafe(24)
    providers={p:(read_json(run/f'{p}_first_impression_results.json') or read_json(run/f'{p}_results.json') or {'items':{}})['items'] for p in ('grok','openai','anthropic','gemini')}
    def reviews():return read_json(reviews_path,{'schema_version':1,'analysis_kind':'first_impression_human_reviews','items':{}})
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,body,kind='text/html; charset=utf-8'):
            data=body if isinstance(body,bytes) else body.encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path
            if path.startswith('/image/'):
                key=path.split('/')[-1]
                if not key:return self.send(404,'missing image')
                basename=next((x['image_basename'] for x in cases if x['case_id']==key),key);data=(project/'generated_review_approved_images'/basename).read_bytes();return self.send(200,data,'image/png')
            if path.startswith('/pairwise/'):
                pid=path.split('/')[-1];pair=pairs[pid];saved=read_json(pair_reviews_path,{'items':{}})['items'].get(pid,{});q=quotes[pair['quote_hash']]
                radio=''.join(f"<label><input type=radio name=preferred value='{v}' {'checked' if saved.get('preferred')==v else ''} required>{v}</label>" for v in ('A','B','neither','unsure'))
                body=f"""<style>body{{font:18px sans-serif;max-width:1200px;margin:auto}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}img{{max-width:100%}}label{{display:block}}</style><h1>Pairwise review</h1><p>{html.escape(q['quote_text'])}</p><div class=grid><div><h2>A</h2><img src='/image/{pair['candidate_a']}'></div><div><h2>B</h2><img src='/image/{pair['candidate_b']}'></div></div><form method=post><input type=hidden name=csrf value='{csrf}'>{radio}<label>Notes<textarea name=notes>{html.escape(saved.get('notes',''))}</textarea></label><button>Save</button></form>""";return self.send(200,body)
            done=reviews()['items'];order=[x['case_id'] for x in cases]
            if path=='/':
                nxt=next((cid for cid in order if cid not in done),order[0]);self.send_response(303);self.send_header('Location','/case/'+nxt);self.end_headers();return
            if not path.startswith('/case/'):return self.send(404,'not found')
            cid=path.split('/')[-1];case=next(x for x in cases if x['case_id']==cid);idx=order.index(cid);saved=done.get(cid,{});q=quotes[case['quote_hash']];im=images.get(case['image_basename'],{'status':'pending'});critics={p:rows.get(cid,{'status':'pending'}) for p,rows in providers.items()}
            radio=lambda name,values:''.join(f"<label><input type=radio name='{name}' value='{v}' {'checked' if saved.get(name)==v else ''} required>{label}</label>" for v,label in values)
            body=f"""<style>body{{font:18px sans-serif;max-width:1100px;margin:20px auto}}img{{max-width:650px;max-height:520px}}pre{{white-space:pre-wrap;background:#f3f4f6;padding:10px}}label{{display:block;margin:8px}}textarea{{width:95%;min-height:70px}}button{{font-size:18px}}</style><nav><a href='/case/{order[(idx-1)%len(order)]}'>Previous</a> · {idx+1}/{len(order)} · reviewed {len(done)}/{len(order)} · <a href='/case/{order[(idx+1)%len(order)]}'>Next</a></nav><h1>First-impression review</h1><img src='/image/{cid}'><h2>Quote</h2><p>{html.escape(q['quote_text'])}</p><details open><summary>Independent first impression</summary><pre>{html.escape(json.dumps(im,indent=2))}</pre></details><details><summary>Critic results</summary><pre>{html.escape(json.dumps(critics,indent=2))}</pre></details><form method=post><input type=hidden name=csrf value='{csrf}'><label>What is the first thing this image appears to be about?<textarea name=first_thing required>{html.escape(saved.get('first_thing',''))}</textarea></label><fieldset><legend>Does that match the quotation?</legend>{radio('matches_quote',[('yes','Yes'),('partly','Partly'),('no','No'),('unsure','Unsure')])}</fieldset><fieldset><legend>Would you keep or replace it?</legend>{radio('image_decision',[('keep','Keep'),('replace','Replace'),('unsure','Unsure')])}</fieldset><label>Notes<textarea name=notes>{html.escape(saved.get('notes',''))}</textarea></label><button>Save and next</button></form>""";self.send(200,body)
        def do_POST(self):
            cid=urlparse(self.path).path.split('/')[-1];length=int(self.headers.get('Content-Length','0'));form=parse_qs(self.rfile.read(length).decode())
            if form.get('csrf',[''])[0]!=csrf:return self.send(403,'invalid csrf')
            if urlparse(self.path).path.startswith('/pairwise/'):
                choice=form.get('preferred',[''])[0]
                if choice not in {'A','B','neither','unsure'}:return self.send(400,'invalid pairwise review')
                data=read_json(pair_reviews_path,{'schema_version':1,'analysis_kind':'first_impression_pairwise_human_reviews','items':{}});data['items'][cid]={'pair_id':cid,'preferred':choice,'notes':form.get('notes',[''])[0].strip(),'updated_at':datetime.now(timezone.utc).isoformat()};atomic_write_json(pair_reviews_path,data);return self.send(200,'saved')
            if form.get('matches_quote',[''])[0] not in {'yes','partly','no','unsure'} or form.get('image_decision',[''])[0] not in {'keep','replace','unsure'}:return self.send(400,'invalid review')
            data=reviews();data['items'][cid]={'case_id':cid,'first_thing':form.get('first_thing',[''])[0].strip(),'matches_quote':form['matches_quote'][0],'image_decision':form['image_decision'][0],'notes':form.get('notes',[''])[0].strip(),'updated_at':datetime.now(timezone.utc).isoformat()};atomic_write_json(reviews_path,data);order=[x['case_id'] for x in cases];self.send_response(303);self.send_header('Location','/case/'+order[(order.index(cid)+1)%len(order)]);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer((host,port),Handler);print(f'http://{host}:{port}');server.serve_forever()

def main(argv=None):
    """Run the command-line entry point."""
    a=parser().parse_args(argv);serve(a.project_dir.resolve(),a.run_dir.resolve(),a.host,a.port)
if __name__=='__main__':main()
