#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from semantic_alignment.image_provider_trial import (
    MAX_COST_USD,
    generate_trial,
    prepare_trial,
    report_results,
    serve_review,
    trial_status,
)


def load_env_file(path: Path) -> None:
    """Load simple shell-style assignments without overriding the environment."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-prepared, blinded Grok/OpenAI image comparison pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare"); prepare.add_argument("--research-run", type=Path, required=True); prepare.add_argument("--output", type=Path, required=True); prepare.add_argument("--count", type=int, default=10)
    status = sub.add_parser("status"); status.add_argument("--trial-dir", type=Path, required=True); status.add_argument("--json", action="store_true")
    generate = sub.add_parser("generate"); generate.add_argument("--trial-dir", type=Path, required=True); generate.add_argument("--execute", action="store_true"); generate.add_argument("--confirm-max-cost-usd", type=float); generate.add_argument("--resume", action="store_true")
    generate.add_argument("--env-file", type=Path, default=Path("/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env"))
    serve = sub.add_parser("serve"); serve.add_argument("--trial-dir", type=Path, required=True); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8765)
    report = sub.add_parser("report"); report.add_argument("--trial-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare_trial(args.research_run.resolve(), args.output.resolve(), args.count); print(json.dumps({"cases": manifest["case_count"], "trial_dir": str(args.output.resolve()), "next": f"inspect reports/preflight.md; exact generation confirmation is ${MAX_COST_USD:.2f}"}, indent=2)); return 0
    if args.command == "status":
        value = trial_status(args.trial_dir.resolve()); print(json.dumps(value, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in value.items())); return 0
    if args.command == "generate":
        if not args.execute: raise SystemExit("refusing network calls without --execute")
        load_env_file(args.env_file)
        value = generate_trial(args.trial_dir.resolve(), args.confirm_max_cost_usd); print(json.dumps({"completed": sum(x.get("status") == "completed" for x in value["items"].values()), "known_cost_usd": value["known_cost_usd"]}, indent=2)); return 0
    if args.command == "serve": serve_review(args.trial_dir.resolve(), args.host, args.port); return 0
    if args.command == "report": print(report_results(args.trial_dir.resolve())); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
