"""Serve the local generated-image swipe-review application."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.generated_image_review.assessment_loader import ReviewItem, load_corpus_review_items, load_review_items
from tools.generated_image_review.review_store import (
    DuplicateDecisionError,
    InvalidDecisionError,
    ReviewStore,
    STAGE_CONFIRM_ALLOWED,
    STAGE_INITIAL,
    STAGE_RECONFIRM_ALLOWED,
)

LOGGER = logging.getLogger("generated_image_review")
BASE_DIR = Path(__file__).resolve().parents[1] / "generated_image_review"

from .security import ReviewSecurity, validate_binding


@dataclass(frozen=True)
class AppConfig:
    """Represent app config data."""
    assessment_file: Path | None
    corpus_root: Path
    database: Path
    export_file: Path
    grade: str = "X"
    mode: str = "review"


def create_app(config: AppConfig, *, username=None, password=None, secret_key=None, allowed_hosts=("localhost", "127.0.0.1", "::1"), trusted_proxy_origin=None) -> FastAPI:
    """Create app."""
    if config.mode not in {"review", "confirm-allowed", "reconfirm-allowed"}:
        raise RuntimeError(f"Unsupported review mode: {config.mode}")
    if config.assessment_file is None:
        items, summary = load_corpus_review_items(config.corpus_root)
        source_label = "corpus"
    else:
        items, summary = load_review_items(config.assessment_file, config.corpus_root, grade=config.grade)
        source_label = f"grade {config.grade}"
    if not items:
        raise RuntimeError(
            f"No reviewable items found from {source_label}; check the corpus root"
        )
    store = ReviewStore(config.database)
    reviewed = mode_progress(store, items, config.mode).reviewed
    remaining = mode_progress(store, items, config.mode).remaining
    LOGGER.info(
        "Review source: %s; Mode: %s; Rows loaded: %s; Matching rows: %s; Reviewable items: %s; "
        "Missing images: %s; Malformed items: %s; Previously reviewed: %s; Remaining: %s",
        source_label,
        config.mode,
        summary.rows_loaded,
        summary.grade_rows,
        summary.reviewable,
        summary.missing_images,
        summary.malformed_items,
        reviewed,
        remaining,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            app.state.store.close()

    app = FastAPI(title="Generated Image Review", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(ReviewSecurity, username=username, password=password, secret_key=secret_key, allowed_hosts=allowed_hosts, trusted_proxy_origin=trusted_proxy_origin)
    app.state.config = config
    app.state.items = items
    app.state.item_by_hash = {item.quote_hash: item for item in items}
    app.state.store = store
    app.state.summary = summary
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/next")
    def api_next() -> dict[str, Any]:
        item = next_pending_item(app)
        progress = progress_payload(app)
        if item is None:
            return {"complete": True, "item": None, "progress": progress, "mode": app.state.config.mode}
        return {"complete": False, "item": item_payload(item), "progress": progress, "mode": app.state.config.mode}

    @app.get("/api/progress")
    def api_progress() -> dict[str, int]:
        return progress_payload(app)

    @app.post("/api/decision")
    async def api_decision(request: Request) -> dict[str, Any]:
        body = await request.json()
        quote_hash = str(body.get("quote_hash", "")).strip().lower()
        decision = str(body.get("decision", "")).strip().lower()
        item = app.state.item_by_hash.get(quote_hash)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown or ineligible quote_hash")
        current = next_pending_item(app)
        if current is None:
            raise HTTPException(status_code=409, detail="All eligible items have already been reviewed")
        if current.quote_hash != quote_hash:
            raise HTTPException(status_code=409, detail="Stale decision; refresh the current item")
        try:
            result = app.state.store.record_decision(quote_hash, decision, item.order, stage=current_stage(app))
        except InvalidDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DuplicateDecisionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        LOGGER.info("decision %s %s stage=%s", decision, quote_hash, current_stage(app))
        export_if_configured(app)
        return {"ok": True, "decision": result, "next": api_next()}

    @app.post("/api/undo")
    def api_undo() -> dict[str, Any]:
        result = app.state.store.undo_last(current_stage(app))
        if result is not None:
            LOGGER.info("undo %s", result["quote_hash"])
            export_if_configured(app)
        return {"ok": True, "undone": result, "next": api_next()}

    @app.post("/api/export")
    def api_export() -> dict[str, Any]:
        payload = app.state.store.export_overrides(
            app.state.config.export_file,
            [item.quote_hash for item in app.state.items],
        )
        LOGGER.info("override export written %s", app.state.config.export_file)
        return {
            "ok": True,
            "export_file": str(app.state.config.export_file),
            "reviewed": len(payload["items"]),
        }

    @app.get("/image/{quote_hash}")
    def image(quote_hash: str) -> FileResponse:
        item = app.state.item_by_hash.get(quote_hash.lower())
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown or ineligible quote_hash")
        image_path = item.image_path.resolve(strict=True)
        corpus_root = app.state.config.corpus_root.resolve()
        try:
            image_path.relative_to(corpus_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Image path escapes corpus root") from exc
        return FileResponse(image_path, media_type="image/png")

    return app


def next_pending_item(app: FastAPI) -> ReviewItem | None:
    """Return the next pending item."""
    for item in app.state.items:
        if is_pending_for_mode(app, item):
            return item
    return None


def progress_payload(app: FastAPI) -> dict[str, int]:
    """Return the progress payload."""
    progress = mode_progress(app.state.store, app.state.items, app.state.config.mode)
    return asdict(progress)


def mode_progress(store: ReviewStore, items: list[ReviewItem], mode: str):
    """Return the mode progress."""
    return store.progress(queue_hashes_for_mode(store, items, mode), stage=stage_for_mode(mode))


def queue_hashes_for_mode(store: ReviewStore, items: list[ReviewItem], mode: str) -> list[str]:
    """Return the queue hashes for mode."""
    if mode == "review":
        return [item.quote_hash for item in items]
    if mode == "confirm-allowed":
        initial = store.active_decisions(STAGE_INITIAL)
        return [
            item.quote_hash
            for item in items
            if initial.get(item.quote_hash, {}).get("decision") == "allow"
        ]
    confirmation = store.active_decisions(STAGE_CONFIRM_ALLOWED)
    return [
        item.quote_hash
        for item in items
        if confirmation.get(item.quote_hash, {}).get("decision") == "allow"
    ]


def stage_for_mode(mode: str) -> str:
    """Return the stage for mode."""
    if mode == "confirm-allowed":
        return STAGE_CONFIRM_ALLOWED
    if mode == "reconfirm-allowed":
        return STAGE_RECONFIRM_ALLOWED
    return STAGE_INITIAL


def current_stage(app: FastAPI) -> str:
    """Return the current stage."""
    return stage_for_mode(app.state.config.mode)


def is_pending_for_mode(app: FastAPI, item: ReviewItem) -> bool:
    """Return whether is pending for mode."""
    mode = app.state.config.mode
    store = app.state.store
    if mode == "review":
        return not store.has_decision(item.quote_hash, STAGE_INITIAL)
    if mode == "confirm-allowed":
        initial = store.active_decisions(STAGE_INITIAL).get(item.quote_hash)
        return (
            initial is not None
            and initial["decision"] == "allow"
            and not store.has_decision(item.quote_hash, STAGE_CONFIRM_ALLOWED)
        )
    confirmation = store.active_decisions(STAGE_CONFIRM_ALLOWED).get(item.quote_hash)
    return (
        confirmation is not None
        and confirmation["decision"] == "allow"
        and not store.has_decision(item.quote_hash, STAGE_RECONFIRM_ALLOWED)
    )


def item_payload(item: ReviewItem) -> dict[str, Any]:
    """Return the item payload."""
    return {
        "quote_hash": item.quote_hash,
        "grade": item.grade,
        "overall_score": item.overall_score,
        "flags": item.flags,
        "assessment_text": item.assessment_text,
        "quote_text": item.quote_text,
        "order": item.order,
        "image_url": f"/image/{item.quote_hash}",
    }


def export_if_configured(app: FastAPI) -> None:
    """Export if configured."""
    app.state.store.export_overrides(
        app.state.config.export_file,
        [item.quote_hash for item in app.state.items],
    )
    LOGGER.info("override export written %s", app.state.config.export_file)


def parse_args() -> argparse.Namespace:
    """Parse args."""
    parser = argparse.ArgumentParser(description="Manual review app for generated quote images")
    parser.add_argument("--assessment-file", default=os.environ.get("GIR_ASSESSMENT_FILE"), type=Path)
    parser.add_argument("--corpus-root", default=os.environ.get("GIR_CORPUS_ROOT"), type=Path)
    parser.add_argument("--database", default=os.environ.get("GIR_DATABASE"), type=Path)
    parser.add_argument("--export-file", default=os.environ.get("GIR_EXPORT_FILE"), type=Path)
    parser.add_argument("--grade", default=os.environ.get("GIR_GRADE", "X"))
    parser.add_argument(
        "--mode",
        choices=["review", "confirm-allowed", "reconfirm-allowed"],
        default=os.environ.get("GIR_MODE", "review"),
    )
    parser.add_argument("--host", default=os.environ.get("GIR_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("GIR_PORT", "8765")), type=int)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--trusted-proxy-origin")
    parser.add_argument("--public-host")
    args = parser.parse_args()
    missing = [
        name
        for name, value in [
            ("--corpus-root/GIR_CORPUS_ROOT", args.corpus_root),
            ("--database/GIR_DATABASE", args.database),
            ("--export-file/GIR_EXPORT_FILE", args.export_file),
        ]
        if value is None
    ]
    if missing:
        parser.error("Missing required configuration: " + ", ".join(missing))
    return args


def config_from_env() -> AppConfig:
    """Return the config from env."""
    required = {
        "GIR_CORPUS_ROOT": os.environ.get("GIR_CORPUS_ROOT"),
        "GIR_DATABASE": os.environ.get("GIR_DATABASE"),
        "GIR_EXPORT_FILE": os.environ.get("GIR_EXPORT_FILE"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return AppConfig(
        assessment_file=Path(os.environ["GIR_ASSESSMENT_FILE"]) if os.environ.get("GIR_ASSESSMENT_FILE") else None,
        corpus_root=Path(required["GIR_CORPUS_ROOT"]),
        database=Path(required["GIR_DATABASE"]),
        export_file=Path(required["GIR_EXPORT_FILE"]),
        grade=os.environ.get("GIR_GRADE", "X"),
        mode=os.environ.get("GIR_MODE", "review"),
    )


def configured_app() -> FastAPI:
    """Return the configured app."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.getenv("GIR_HOST", "127.0.0.1")
    username, password = os.getenv("MRS_REVIEW_USERNAME"), os.getenv("MRS_REVIEW_PASSWORD")
    proxy = os.getenv("MRS_REVIEW_TRUSTED_PROXY_ORIGIN")
    validate_binding(host, username, password, trusted_proxy_origin=proxy)
    return create_app(config_from_env(), username=username, password=password, allowed_hosts=(host,), trusted_proxy_origin=proxy)


def main() -> None:
    """Run the command-line entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    config = AppConfig(
        assessment_file=args.assessment_file,
        corpus_root=args.corpus_root,
        database=args.database,
        export_file=args.export_file,
        grade=args.grade,
        mode=args.mode,
    )
    username, password = os.getenv("MRS_REVIEW_USERNAME"), os.getenv("MRS_REVIEW_PASSWORD")
    if not args.export_only:
        validate_binding(args.host, username, password, tls=bool(args.tls_cert and args.tls_key), trusted_proxy_origin=args.trusted_proxy_origin)
    hosts = tuple(filter(None, (args.public_host, args.host, "127.0.0.1", "localhost", "::1")))
    app = create_app(config, username=username, password=password, allowed_hosts=hosts, trusted_proxy_origin=args.trusted_proxy_origin)
    if args.export_only:
        payload = app.state.store.export_overrides(config.export_file, [item.quote_hash for item in app.state.items])
        print(f"Wrote {config.export_file} with {len(payload['items'])} reviewed items")
        return
    LOGGER.info("review app started host=%s port=%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=str(args.tls_cert) if args.tls_cert else None, ssl_keyfile=str(args.tls_key) if args.tls_key else None, proxy_headers=False)


if __name__ == "__main__":
    main()
