#!/usr/bin/env python3
"""Manage the offline quotation-image semantic-veto shadow manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from semantic_alignment.io import atomic_write_json, atomic_write_text
from semantic_alignment.quote_image_semantic_veto import (
    ShadowManifestError,
    historical_replay,
    replay_markdown,
    shadow_preflight,
    shadow_status,
    write_compiled_manifest,
)


def main() -> int:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser(description="Offline quote/image semantic-veto shadow tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    compile_parser = sub.add_parser("compile-shadow-manifest")
    compile_parser.add_argument("--run-dir", type=Path, required=True)
    compile_parser.add_argument("--strict", action="store_true")

    preflight_parser = sub.add_parser("shadow-preflight")
    preflight_parser.add_argument("--project-dir", type=Path, required=True)
    preflight_parser.add_argument("--manifest", type=Path, required=True)

    replay_parser = sub.add_parser("shadow-replay")
    replay_parser.add_argument("--project-dir", type=Path, required=True)
    replay_parser.add_argument("--manifest", type=Path, required=True)
    replay_parser.add_argument("--since-days", type=int, default=30)

    status_parser = sub.add_parser("shadow-status")
    status_parser.add_argument("--project-dir", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "compile-shadow-manifest":
            result = write_compiled_manifest(args.run_dir.resolve(), strict=args.strict)
        elif args.command == "shadow-preflight":
            result = shadow_preflight(args.project_dir.resolve(), args.manifest)
        elif args.command == "shadow-replay":
            project = args.project_dir.resolve()
            result = historical_replay(project, args.manifest, since_days=args.since_days)
            output = project / "semantic_alignment_research" / "quote_image_semantic_veto_001" / "shadow"
            atomic_write_json(output / "historical_replay_30d.json", result)
            atomic_write_text(output / "historical_replay_30d.md", replay_markdown(result))
        else:
            result = shadow_status(args.project_dir.resolve())
    except ShadowManifestError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
