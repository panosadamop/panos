"""Serve the built web dashboard (``apps/dashboard``, M9-08) from the hub.

The build is looked up in ``PANOS_DASHBOARD_DIR``, then in the package's ``static`` folder
(release builds copy it there), then in the repository's ``apps/dashboard/dist``. Without
a build, ``/`` explains how to make one. Security headers: strict CSP (no inline script,
same-origin connections only), no framing, no referrer.
"""

import os
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
PLACEHOLDER: Final = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Panos hub</title><body><h1>Panos hub</h1>
<p>The dashboard is not built. Run <code>pnpm --dir apps/dashboard build</code>.</p>
</body></html>"""


def dist_dir() -> Path | None:
    candidates = [
        os.environ.get("PANOS_DASHBOARD_DIR"),
        str(Path(__file__).parent / "static"),
        str(Path(__file__).resolve().parents[4] / "apps" / "dashboard" / "dist"),
    ]
    for c in candidates:
        if c and (Path(c) / "index.html").exists():
            return Path(c)
    return None


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


def mount(app: FastAPI) -> None:
    app.add_middleware(SecurityHeaders)
    root = dist_dir()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith("api/"):
            return Response(status_code=404)
        if root is None:
            return HTMLResponse(PLACEHOLDER)
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root.resolve()):
            return FileResponse(target)
        return FileResponse(root / "index.html")
