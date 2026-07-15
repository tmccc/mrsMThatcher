#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from semantic_alignment.openai_quality_trial import generate_trial,prepare_trial,report_results,serve_review,status

def load_env(path:Path):
    if not path.exists():return
    for raw in path.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('export '):line=line[7:].lstrip()
        if '=' not in line:continue
        key,value=line.split('=',1);value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":value=value[1:-1]
        os.environ.setdefault(key.strip(),value)

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--research-run',type=Path,required=True);a.add_argument('--count',type=int,default=10);a.add_argument('--output',type=Path,required=True)
    a=sub.add_parser('status');a.add_argument('--trial-dir',type=Path,required=True);a.add_argument('--json',action='store_true')
    a=sub.add_parser('generate');a.add_argument('--trial-dir',type=Path,required=True);a.add_argument('--execute',action='store_true');a.add_argument('--confirm-max-cost-usd',type=float);a.add_argument('--resume',action='store_true');a.add_argument('--env-file',type=Path,default=Path('/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env'))
    a=sub.add_parser('serve');a.add_argument('--trial-dir',type=Path,required=True);a.add_argument('--host',default='127.0.0.1');a.add_argument('--port',type=int,default=8765)
    a=sub.add_parser('report');a.add_argument('--trial-dir',type=Path,required=True)
    args=p.parse_args()
    if args.command=='prepare':m=prepare_trial(args.research_run.resolve(),args.output.resolve(),args.count);print(json.dumps({'cases':m['case_count'],'eligible':m['eligible_corpus_size'],'trial_dir':str(args.output.resolve())},indent=2))
    elif args.command=='status':v=status(args.trial_dir.resolve());print(json.dumps(v,indent=2) if args.json else '\n'.join(f'{k}: {x}' for k,x in v.items()))
    elif args.command=='generate':
        if not args.execute:raise SystemExit('refusing paid generation without --execute')
        load_env(args.env_file);v=generate_trial(args.trial_dir.resolve(),args.confirm_max_cost_usd);print(json.dumps({'completed':sum(x.get('status')=='completed' for x in v['items'].values()),'known_cost_usd':v['known_cost_usd']},indent=2))
    elif args.command=='serve':serve_review(args.trial_dir.resolve(),args.host,args.port)
    elif args.command=='report':print(report_results(args.trial_dir.resolve()))
    return 0
if __name__=='__main__':raise SystemExit(main())
