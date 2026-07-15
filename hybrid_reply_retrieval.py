#!/usr/bin/env python3
"""Offline CLI for multilingual hybrid conversational-reply retrieval."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from semantic_alignment.hybrid_reply_retrieval import (
    DEFAULT_MODEL_DIR,
    MODEL_ID,
    build_index,
    build_review_sample,
    audit_review_context,
    download_model,
    evaluate_retrieval,
    model_preflight,
    read_shadow_records,
    replay_historical,
    review_results,
    serve_review,
    shadow_summary,
    validate_corpus_invariants,
    write_hybrid_report,
)
from semantic_alignment.hybrid_gemini_review import (
    COMBINED_LIMIT_USD,
    DEVELOPER_LIMIT_USD,
    VERTEX_LIMIT_USD,
    GeminiReviewClient,
    GeminiReviewRunner,
    prepare_gemini_review,
    recover_gemini_reviews_offline,
    write_gemini_review_report,
)
from semantic_alignment.gemini_fallback import verify_adc_access
from semantic_alignment.hybrid_multi_provider_review import (
    COMBINED_LIMIT_USD as MULTI_PROVIDER_COMBINED_LIMIT_USD,
    PROVIDER_LIMITS_USD as MULTI_PROVIDER_LIMITS_USD,
    ReviewProviderClient,
    compare_all_ai_reviews,
    prepare_multi_provider_review,
    recover_provider_reviews_offline,
    run_multi_provider_reviews,
)

DEFAULT_RESEARCH = Path("semantic_alignment_research/quote_research_full_001")
DEFAULT_ENV = Path("/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    corpus = sub.add_parser("corpus-status")
    corpus.add_argument("--research-run", type=Path, required=True)

    model = sub.add_parser("prepare-model")
    model.add_argument("--preflight", action="store_true")
    model.add_argument("--execute-download", action="store_true")
    model.add_argument("--confirm-model")
    model.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)

    index = sub.add_parser("build-index")
    index.add_argument("--research-run", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    index.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--retrieval-dir", type=Path, required=True)
    evaluate.add_argument("--research-run", type=Path, default=DEFAULT_RESEARCH)
    evaluate.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)

    replay = sub.add_parser("replay")
    replay.add_argument("--project-dir", type=Path, required=True)
    replay.add_argument("--retrieval-dir", type=Path, required=True)
    replay.add_argument("--research-run", type=Path, default=DEFAULT_RESEARCH)
    replay.add_argument("--since-days", type=int, default=30)

    serve = sub.add_parser("serve-review")
    serve.add_argument("--retrieval-dir", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8767)

    status = sub.add_parser("shadow-status")
    status.add_argument("--project-dir", type=Path, required=True)

    reviews = sub.add_parser("review-status")
    reviews.add_argument("--retrieval-dir", type=Path, required=True)

    context = sub.add_parser("audit-review-context")
    context.add_argument("--project-dir", type=Path, required=True)
    context.add_argument("--retrieval-dir", type=Path, required=True)
    context.add_argument("--strict", action="store_true")
    context.add_argument("--dry-run", action="store_true")

    gemini_preflight = sub.add_parser("gemini-review-preflight")
    gemini_preflight.add_argument("--retrieval-dir", type=Path, required=True)

    gemini_review = sub.add_parser("gemini-review")
    gemini_review.add_argument("--retrieval-dir", type=Path, required=True)
    gemini_review.add_argument("--execute", action="store_true")
    gemini_review.add_argument("--enable-vertex-fallback", action="store_true")
    gemini_review.add_argument("--confirm-developer-limit-usd", type=float)
    gemini_review.add_argument("--confirm-vertex-limit-usd", type=float)
    gemini_review.add_argument("--confirm-combined-limit-usd", type=float)
    gemini_review.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    gemini_review.add_argument("--resume", action="store_true")

    gemini_status = sub.add_parser("gemini-review-status")
    gemini_status.add_argument("--retrieval-dir", type=Path, required=True)

    gemini_recover = sub.add_parser("gemini-review-recover-offline")
    gemini_recover.add_argument("--retrieval-dir", type=Path, required=True)

    multi_preflight = sub.add_parser("multi-provider-review-preflight")
    multi_preflight.add_argument("--retrieval-dir", type=Path, required=True)

    multi_review = sub.add_parser("multi-provider-review")
    multi_review.add_argument("--retrieval-dir", type=Path, required=True)
    multi_review.add_argument("--execute", action="store_true")
    multi_review.add_argument("--confirm-grok-limit-usd", type=float)
    multi_review.add_argument("--confirm-openai-limit-usd", type=float)
    multi_review.add_argument("--confirm-anthropic-limit-usd", type=float)
    multi_review.add_argument("--confirm-combined-limit-usd", type=float)
    multi_review.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    multi_review.add_argument("--resume", action="store_true")

    multi_recover = sub.add_parser("multi-provider-review-recover-offline")
    multi_recover.add_argument("--retrieval-dir", type=Path, required=True)

    compare_ai = sub.add_parser("compare-ai-reviews")
    compare_ai.add_argument("--retrieval-dir", type=Path, required=True)

    report = sub.add_parser("report")
    report.add_argument("--retrieval-dir", type=Path, required=True)
    report.add_argument("--project-dir", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "corpus-status":
        _, unresolved, metadata = validate_corpus_invariants(args.research_run)
        print_json({**metadata, "unresolved_quote_ids": sorted(unresolved), "status": "valid"})
        return 0
    if args.command == "prepare-model":
        if args.execute_download:
            if args.preflight:
                raise SystemExit("choose --preflight or --execute-download, not both")
            print_json(download_model(args.model_dir, str(args.confirm_model or "")))
            return 0
        if not args.preflight:
            raise SystemExit("prepare-model requires --preflight or --execute-download")
        print_json(model_preflight(args.model_dir))
        print(f"\nTo download, install the exact dependencies and run:\n  python3 hybrid_reply_retrieval.py prepare-model --execute-download --confirm-model {MODEL_ID}")
        return 0
    if args.command == "build-index":
        print_json(build_index(args.research_run, args.output, args.model_dir))
        return 0
    if args.command == "evaluate":
        result = evaluate_retrieval(args.retrieval_dir, args.research_run, args.model_dir)
        print_json({key: value for key, value in result.items() if key != "rows"})
        return 0
    if args.command == "replay":
        result = replay_historical(args.project_dir, args.retrieval_dir, args.research_run, args.since_days)
        sample = build_review_sample(args.retrieval_dir, 100)
        print_json({"replay": result["summary"], "review_sample_count": sample["case_count"]})
        return 0
    if args.command == "serve-review":
        serve_review(args.retrieval_dir, args.host, args.port)
        return 0
    if args.command == "shadow-status":
        runtime = args.project_dir / "hybrid_reply_retrieval_runtime"
        print_json(shadow_summary(read_shadow_records(runtime)))
        return 0
    if args.command == "review-status":
        print_json(review_results(args.retrieval_dir))
        return 0
    if args.command == "audit-review-context":
        print_json(audit_review_context(
            args.project_dir, args.retrieval_dir,
            strict=args.strict, apply=not args.dry_run,
        ))
        return 0
    if args.command == "gemini-review-preflight":
        print_json(prepare_gemini_review(args.retrieval_dir))
        return 0
    if args.command == "gemini-review":
        if not args.execute:
            raise SystemExit("Gemini review requires --execute")
        if not args.enable_vertex_fallback:
            raise SystemExit("Gemini review requires --enable-vertex-fallback")
        confirmed = (
            args.confirm_developer_limit_usd,
            args.confirm_vertex_limit_usd,
            args.confirm_combined_limit_usd,
        )
        expected = (DEVELOPER_LIMIT_USD, VERTEX_LIMIT_USD, COMBINED_LIMIT_USD)
        if confirmed != expected:
            raise SystemExit("exact $2 Developer, $2 Vertex and $3 combined confirmations required")
        prepare_gemini_review(args.retrieval_dir)
        load_env_file(args.env_file)
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        developer = GeminiReviewClient(transport="developer_api", api_key=key)
        vertex_env = verify_adc_access(dict(os.environ))
        vertex = GeminiReviewClient(
            transport="vertex_ai", project=vertex_env["project"], location=vertex_env["location"],
        )
        runner = GeminiReviewRunner(
            args.retrieval_dir, developer, vertex,
            developer_limit=args.confirm_developer_limit_usd,
            vertex_limit=args.confirm_vertex_limit_usd,
            combined_limit=args.confirm_combined_limit_usd,
            fallback_enabled=True,
        )
        print_json(runner.run())
        return 0
    if args.command == "gemini-review-status":
        print_json(write_gemini_review_report(args.retrieval_dir))
        return 0
    if args.command == "gemini-review-recover-offline":
        print_json(recover_gemini_reviews_offline(args.retrieval_dir))
        return 0
    if args.command == "multi-provider-review-preflight":
        print_json(prepare_multi_provider_review(args.retrieval_dir))
        return 0
    if args.command == "multi-provider-review":
        if not args.execute:
            raise SystemExit("multi-provider review requires --execute")
        confirmed = {
            "grok": args.confirm_grok_limit_usd,
            "openai": args.confirm_openai_limit_usd,
            "anthropic": args.confirm_anthropic_limit_usd,
        }
        if confirmed != MULTI_PROVIDER_LIMITS_USD:
            raise SystemExit("exact Grok $2, OpenAI $6 and Anthropic $10 confirmations required")
        if args.confirm_combined_limit_usd != MULTI_PROVIDER_COMBINED_LIMIT_USD:
            raise SystemExit("exact $18 combined confirmation required")
        prepare_multi_provider_review(args.retrieval_dir)
        load_env_file(args.env_file)
        env_names = {"grok": "XAI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        clients = {
            provider: ReviewProviderClient(provider, os.getenv(env_name, ""))
            for provider, env_name in env_names.items()
        }
        print_json(run_multi_provider_reviews(
            args.retrieval_dir,
            clients,
            provider_limits=confirmed,
            combined_limit=args.confirm_combined_limit_usd,
        ))
        return 0
    if args.command == "multi-provider-review-recover-offline":
        print_json({
            provider: recover_provider_reviews_offline(args.retrieval_dir, provider)
            for provider in ("grok", "openai", "anthropic")
        })
        return 0
    if args.command == "compare-ai-reviews":
        result = compare_all_ai_reviews(args.retrieval_dir)
        print_json({key: value for key, value in result.items() if key != "case_results"})
        return 0
    if args.command == "report":
        print_json(write_hybrid_report(args.retrieval_dir, args.project_dir))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
