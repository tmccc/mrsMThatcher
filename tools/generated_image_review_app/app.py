"""Serve the generated-image quarantine review application."""

from __future__ import annotations

import argparse
import os
from urllib.parse import parse_qs
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .services import Paths, ReviewError, ReviewService
from .security import ReviewSecurity, validate_binding

HERE = Path(__file__).resolve().parent


def create_app(service: ReviewService, *, username: str | None = None, password: str | None = None, secret_key: str | None = None, allowed_hosts=("localhost", "127.0.0.1", "::1"), trusted_proxy_origin=None) -> FastAPI:
    """Create app."""
    app = FastAPI(title="Generated image review", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.add_middleware(ReviewSecurity, username=username, password=password, secret_key=secret_key, allowed_hosts=allowed_hosts, trusted_proxy_origin=trusted_proxy_origin)

    def context(request: Request, **extra):
        token = request.state.review_csrf
        return {"request": request, "csrf": token, "allow_changes": service.allow_changes, **extra}

    def rendered(request: Request, template: str, **extra):
        data = context(request, **extra); response = templates.TemplateResponse(request, template, data)
        return response

    async def form_checked(request: Request):
        body = await request.body()
        if len(body) > 1024 * 1024: raise HTTPException(413, "Form is too large")
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=1000)
        return values

    @app.get("/", response_class=HTMLResponse)
    async def gallery(request: Request, q: str = "", policy: str = "", posted: str = "", sort: str = "basename", status: str = "active"):
        rows = service.filter_sort(service.index(), q, policy, posted, sort, status)
        return rendered(request, "gallery.html", images=rows, pool_token=service.pool_token(), q=q, policy=policy, posted=posted, sort=sort, status=status)

    @app.get("/image/{basename}")
    async def image(basename: str):
        try: return FileResponse(service.image_path("active", basename))
        except ReviewError as exc: raise HTTPException(404, str(exc))

    @app.get("/quarantine-image/{transaction_id}/{basename}")
    async def quarantine_image(transaction_id: str, basename: str):
        try: return FileResponse(service.image_path("quarantine", basename, transaction_id))
        except ReviewError as exc: raise HTTPException(404, str(exc))

    @app.get("/thumbnail/{basename}")
    async def thumbnail(basename: str):
        try: return FileResponse(service.thumbnail("active", basename), media_type="image/jpeg")
        except ReviewError as exc: raise HTTPException(404, str(exc))

    @app.get("/quarantine-thumbnail/{transaction_id}/{basename}")
    async def quarantine_thumbnail(transaction_id: str, basename: str):
        try: return FileResponse(service.thumbnail("quarantine", basename, transaction_id), media_type="image/jpeg")
        except ReviewError as exc: raise HTTPException(404, str(exc))

    @app.post("/review", response_class=HTMLResponse)
    async def review(request: Request):
        form = await form_checked(request); names = [str(value) for value in form.get("images", [])]
        try: preview = service.preview(names, (form.get("pool_token") or [""])[0])
        except ReviewError as exc: return rendered(request, "error.html", message=str(exc), status_code=409)
        return rendered(request, "review.html", preview=preview)

    @app.post("/quarantine", response_class=HTMLResponse)
    async def quarantine(request: Request):
        form = await form_checked(request)
        try:
            result = service.quarantine([str(value) for value in form.get("images", [])], (form.get("pool_token") or [""])[0], (form.get("confirmation") or [""])[0], (form.get("reason") or ["other"])[0], (form.get("note") or [""])[0])
        except ReviewError as exc: return rendered(request, "error.html", message=str(exc), status_code=409)
        return rendered(request, "result.html", result=result)

    @app.get("/quarantine", response_class=HTMLResponse)
    async def history(request: Request):
        return rendered(request, "history.html", transactions=service.transactions())

    @app.post("/restore", response_class=HTMLResponse)
    async def restore(request: Request):
        form = await form_checked(request)
        try: result = service.restore((form.get("transaction_id") or [""])[0], [str(value) for value in form.get("images", [])], (form.get("confirmation") or [""])[0])
        except ReviewError as exc: return rendered(request, "error.html", message=str(exc), status_code=409)
        return rendered(request, "result.html", result=result)

    return app


def parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    value = argparse.ArgumentParser(description="Local generated-image review app. Read-only unless --allow-changes is supplied.")
    value.add_argument("--host", default="127.0.0.1"); value.add_argument("--port", type=int, default=8765)
    value.add_argument("--allow-changes", action="store_true"); value.add_argument("--project-dir", type=Path, default=Path.cwd())
    value.add_argument("--generated-dir", type=Path); value.add_argument("--quarantine-dir", type=Path); value.add_argument("--data-dir", type=Path)
    value.add_argument("--debug", action="store_true")
    value.add_argument("--tls-cert", type=Path); value.add_argument("--tls-key", type=Path)
    value.add_argument("--trusted-proxy-origin"); value.add_argument("--public-host")
    return value


def main() -> int:
    """Run the command-line entry point."""
    args = parser().parse_args(); username, password = os.getenv("MRS_REVIEW_USERNAME"), os.getenv("MRS_REVIEW_PASSWORD")
    validate_binding(args.host, username, password, tls=bool(args.tls_cert and args.tls_key), trusted_proxy_origin=args.trusted_proxy_origin)
    service = ReviewService(Paths.build(args.project_dir, args.generated_dir, args.quarantine_dir, args.data_dir), args.allow_changes)
    rows = service.index()
    print(f"URL: {args.trusted_proxy_origin or (('https' if args.tls_cert else 'http') + '://' + (args.public_host or args.host) + ':' + str(args.port))}/")
    print(f"Mode: {'CHANGE-ENABLED (quarantine only)' if args.allow_changes else 'READ-ONLY'}")
    print(f"Active images: {len(rows)}; metadata valid: {sum(row['hash_status'] == 'ok' for row in rows) == len(rows)}")
    print("A real quarantine removes active metadata atomically; the running bot sees the file removal immediately and a controlled restart is recommended after review.")
    uvicorn.run(create_app(service, username=username, password=password, secret_key=os.getenv("MRS_REVIEW_SECRET_KEY"), allowed_hosts=tuple(filter(None, (args.host, args.public_host, "127.0.0.1", "localhost", "::1"))), trusted_proxy_origin=args.trusted_proxy_origin), host=args.host, port=args.port, reload=False, proxy_headers=False, ssl_certfile=str(args.tls_cert) if args.tls_cert else None, ssl_keyfile=str(args.tls_key) if args.tls_key else None, log_level="debug" if args.debug else "info")
    return 0


if __name__ == "__main__": raise SystemExit(main())
