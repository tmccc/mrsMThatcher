#!/usr/bin/env python3
"""Offline historical-context formatter comparison and blind review CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from semantic_alignment.historical_context_formatter_trial import (
    generate_review_results,
    prepare_trial,
    serve_trial,
    strict_audit,
    trial_status,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--research-run", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--sample-count", type=int, default=50)
    audit = commands.add_parser("audit"); audit.add_argument("--trial-dir", type=Path, required=True); audit.add_argument("--strict", action="store_true")
    status = commands.add_parser("status"); status.add_argument("--trial-dir", type=Path, required=True)
    serve = commands.add_parser("serve"); serve.add_argument("--trial-dir", type=Path, required=True); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8766)
    report = commands.add_parser("report"); report.add_argument("--trial-dir", type=Path, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "prepare": result = prepare_trial(args.research_run, args.output, args.sample_count); summary = {"manifest": result["manifest"], "parity": {key: result["parity"][key] for key in ("passed", "records", "blocking_regression_count")}, "lengths": result["lengths"]}
    elif args.command == "audit":
        summary = strict_audit(args.trial_dir)
        if args.strict and not summary["passed"]: print(json.dumps(summary, indent=2)); return 1
    elif args.command == "status": summary = trial_status(args.trial_dir)
    elif args.command == "report": summary = generate_review_results(args.trial_dir)
    else:
        serve_trial(args.trial_dir, args.host, args.port); return 0
    print(json.dumps(summary, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
