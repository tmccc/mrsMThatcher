#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from semantic_alignment.visualisability_audit import calibrate,run_audit,serve,status,write_human_review_outputs

def main():
 p=argparse.ArgumentParser(description='Offline quotation visualisability audit');s=p.add_subparsers(dest='command',required=True)
 a=s.add_parser('calibrate');a.add_argument('--research-run',type=Path,required=True);a.add_argument('--review-data',type=Path,action='append',required=True);a.add_argument('--output',type=Path,required=True)
 a=s.add_parser('audit');a.add_argument('--research-run',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--review-data',type=Path,action='append');a.add_argument('--resume',action='store_true')
 a=s.add_parser('status');a.add_argument('--audit-dir',type=Path,required=True);a.add_argument('--json',action='store_true')
 a=s.add_parser('serve');a.add_argument('--audit-dir',type=Path,required=True);a.add_argument('--host',default='127.0.0.1');a.add_argument('--port',type=int,default=8765)
 a=s.add_parser('report');a.add_argument('--audit-dir',type=Path,required=True)
 a=s.add_parser('review-status');a.add_argument('--audit-dir',type=Path,required=True);a.add_argument('--json',action='store_true')
 a=s.add_parser('review-report');a.add_argument('--audit-dir',type=Path,required=True)
 x=p.parse_args()
 if x.command=='calibrate':print(json.dumps(calibrate(x.research_run.resolve(),[p.resolve() for p in x.review_data],x.output.resolve()),indent=2))
 elif x.command=='audit':run_audit(x.research_run.resolve(),x.output.resolve(),[p.resolve() for p in (x.review_data or [Path('semantic_alignment_research/openai_quality_trial_001'),Path('semantic_alignment_research/openai_quality_trial_002')])]);print(json.dumps(status(x.output.resolve()),indent=2))
 elif x.command=='status':v=status(x.audit_dir.resolve());print(json.dumps(v,indent=2) if x.json else '\n'.join(f'{k}: {v}' for k,v in v.items()))
 elif x.command=='serve':serve(x.audit_dir.resolve(),x.host,x.port)
 elif x.command=='report':print((x.audit_dir/'visualisability_report.md').read_text())
 elif x.command=='review-status':
  v=write_human_review_outputs(x.audit_dir.resolve());print(json.dumps(v,indent=2) if x.json else '\n'.join(f'{k}: {value}' for k,value in v.items()))
 elif x.command=='review-report':
  write_human_review_outputs(x.audit_dir.resolve());print((x.audit_dir/'manual_review_interface'/'human_review_report.md').read_text())
 return 0
if __name__=='__main__':raise SystemExit(main())
