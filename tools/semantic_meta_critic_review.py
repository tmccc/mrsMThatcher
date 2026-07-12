#!/usr/bin/env python3
from __future__ import annotations
import argparse,html,json,secrets,sys
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from semantic_alignment.io import atomic_write_json

def create_server(*,project_dir,source_run,bakeoff_dir,meta_dir,review_path,host,port):
    csrf=secrets.token_urlsafe(24); mapping=json.load(open(bakeoff_dir/'sealed_provider_mapping_four_way.json')); cases={x['case_id']:x for x in json.load(open(bakeoff_dir/'cases.json'))['items']}; queue_path=meta_dir/'review_queue.json'; queue_path=queue_path if queue_path.exists() else meta_dir/'human_review_queue.json'; queue=json.load(open(queue_path))['items']; meta={x['case_id']:x for x in json.load(open(meta_dir/'meta_results.json'))['items']}; quotes=json.load(open(source_run/'quote_semantic_fingerprints.json'))['items']; images=json.load(open(source_run/'image_implied_messages_generated.json'))['items']; results={p:json.load(open(bakeoff_dir/f'{p}_results.json'))['items'] for p in mapping.values()}
    def load(): return json.load(open(review_path)) if review_path.exists() else {'schema_version':1,'analysis_kind':'meta_critic_human_reviews','items':{}}
    class H(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def send(self,status,body,kind='text/html; charset=utf-8'):
            data=body if isinstance(body,bytes) else body.encode(); self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            parsed=urlparse(self.path); path=parsed.path
            if path=='/':
                saved=load()['items']; query=parse_qs(parsed.query); band=query.get('band',['all'])[0]; mode=query.get('mode',['all'])[0]; visible=[x for x in queue if (band=='all' or x.get('disagreement_band')==band) and (mode=='all' or (mode=='automatic')==x.get('automatic'))]; visible=visible or queue; nxt=next((x for x in visible if x['case_id'] not in saved),visible[0]); self.send(200,f"<h1>Meta-critic review</h1><p>{len(saved)}/{len(queue)} reviewed</p><form><label>Disagreement <select name=band><option>all</option><option>extreme</option><option>high</option><option>moderate</option><option>low</option></select></label><label>Mode <select name=mode><option>all</option><option>automatic</option><option>deferred</option></select></label><button>Filter</button></form><a href='/case/{nxt['case_id']}'>Continue highest disagreement</a>"); return
            if path.startswith('/image/'):
                cid=path.rsplit('/',1)[1]; root=(project_dir/'generated_review_approved_images').resolve(); image=(root/cases[cid]['image_basename']).resolve()
                if image.parent!=root or not image.exists(): self.send(404,'Not found'); return
                self.send(200,image.read_bytes(),'image/png'); return
            if not path.startswith('/case/'): self.send(404,'Not found'); return
            cid=path.rsplit('/',1)[1]; order=[x['case_id'] for x in queue]
            if cid not in cases: self.send(404,'Not found'); return
            case=cases[cid]; q=quotes[case['quote_hash']]; im=images[case['image_basename']]; m=meta.get(cid,{'recommended_action':'defer','confidence':'insufficient','requires_human_review':True}); saved=load()['items'].get(cid,{}); idx=order.index(cid); prev=order[(idx-1)%len(order)]; nxt=order[(idx+1)%len(order)]
            public=lambda x:{k:v for k,v in x.items() if k not in {'model','provider'}}; critics={label:{k:v for k,v in results[p].get(cid,{'status':'missing_or_exhausted'}).items() if k not in {'model','case_id','quote_hash','image_basename'}} for label,p in mapping.items()}; panels=''.join(f'<details><summary>{label}</summary><pre>{html.escape(json.dumps(row,indent=2))}</pre></details>' for label,row in critics.items())
            radio=lambda name,vals:''.join(f"<label><input type=radio name={name} value={v} {'checked' if saved.get(name)==v else ''} required> {label}</label>" for v,label in vals)
            checks=''.join(f"<label><input type=checkbox name=closest value={x} {'checked' if x in saved.get('closest_critics',[]) else ''}> {x}</label>" for x in 'ABCD')
            qrow=queue[idx]; compact={'recommendation':m['recommended_action'],'confidence':m['confidence'],'disagreement':f"{qrow.get('disagreement_score')} ({qrow.get('disagreement_band')})",'reasons':qrow.get('disagreement_reasons',qrow.get('review_reasons',[])),'provider_decisions':qrow['provider_decisions']}; options=''.join(f'<option value={x}>{x.replace("_"," ")}</option>' for x in ('direct_fit','consequence_only','broader_principle','ideological_substitution','too_generic','unrelated','visually_weak','other'))
            body=f"""<style>body{{font:18px sans-serif;max-width:1200px;margin:20px auto}}img{{max-width:620px;max-height:520px}}pre{{white-space:pre-wrap;background:#f3f4f6;padding:10px}}label{{display:block;margin:9px}}button,input{{font-size:18px}}</style><nav><a href='/case/{prev}'>Previous</a> · <a href='/'>Progress/filter</a> · <a href='/case/{nxt}'>Next</a></nav><h1>{idx+1}/{len(order)} · {qrow['priority']} · disagreement {qrow['disagreement_band']}</h1><img src='/image/{cid}'><h2>Quote</h2><p>{html.escape(q['quote_text'])}</p><h2>Meta-critic summary</h2><pre>{html.escape(json.dumps(compact,indent=2))}</pre><details><summary>Fingerprints</summary><pre>{html.escape(json.dumps({'quote':public(q),'image':public(im)},indent=2))}</pre></details><h2>Blinded full critiques</h2>{panels}<form method=post id=review><input type=hidden name=csrf value='{csrf}'><fieldset><legend>What should the bot do?</legend>{radio('human_action',[('keep','Keep [K]'),('replace','Replace [R]'),('unsure','Unsure [U]')])}</fieldset><fieldset><legend>Was the meta recommendation acceptable?</legend>{radio('meta_acceptable',[('yes','Yes'),('no','No'),('uncertain','Uncertain')])}</fieldset><details><summary>Optional closest critics and reason</summary><fieldset>{checks}<label><input type=checkbox name=closest value=none> None</label><label><input type=checkbox name=closest value=unsure> Unsure</label></fieldset><label>Reason <select name=reason>{options}</select></label></details><label>Notes<br><textarea name=notes>{html.escape(saved.get('notes',''))}</textarea></label><button>Save and next [S]</button></form><script>document.addEventListener('keydown',function(e){{if(e.target.matches('textarea,select,input'))return;var m={{k:'keep',r:'replace',u:'unsure'}}[e.key.toLowerCase()];if(m)document.querySelector('input[name=human_action][value='+m+']').click();if(e.key.toLowerCase()=='s')document.getElementById('review').requestSubmit();}})</script>"""; self.send(200,body)
        def do_POST(self):
            cid=urlparse(self.path).path.rsplit('/',1)[-1]; form=parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
            if form.get('csrf',[''])[0]!=csrf or form.get('human_action',[''])[0] not in {'keep','replace','unsure'} or form.get('meta_acceptable',[''])[0] not in {'yes','no','uncertain'}: self.send(400,'Invalid review'); return
            closest=form.get('closest',[])
            if any(x not in {'A','B','C','D','none','unsure'} for x in closest): self.send(400,'Invalid critics'); return
            data=load(); data['items'][cid]={'case_id':cid,'human_action':form['human_action'][0],'meta_acceptable':form['meta_acceptable'][0],'closest_critics':closest,'reason':form.get('reason',['other'])[0],'notes':form.get('notes',[''])[0],'updated_at':datetime.now(timezone.utc).isoformat()}; atomic_write_json(review_path,data); order=[x['case_id'] for x in queue]; self.send_response(303); self.send_header('Location','/case/'+order[(order.index(cid)+1)%len(order)]); self.end_headers()
    return ThreadingHTTPServer((host,port),H)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--project-dir',type=Path,required=True); p.add_argument('--source-run',type=Path,required=True); p.add_argument('--bakeoff-dir',type=Path,required=True); p.add_argument('--meta-dir',type=Path,required=True); p.add_argument('--review-file',type=Path,required=True); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8769); a=p.parse_args()
    if a.host not in {'127.0.0.1','localhost','::1'}: raise SystemExit('loopback only')
    s=create_server(project_dir=a.project_dir.resolve(),source_run=a.source_run.resolve(),bakeoff_dir=a.bakeoff_dir.resolve(),meta_dir=a.meta_dir.resolve(),review_path=a.review_file.resolve(),host=a.host,port=a.port); print(f'http://{a.host}:{s.server_port}'); s.serve_forever()
if __name__=='__main__': main()
