"""Native AWS/Qwen workspace API; no experimental agent execution routes."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .service import Service


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NativeLaunch(Body):
    model: str = Field(min_length=1, max_length=100)
    team: Literal[0, 1] = 0
    repeats: int = Field(default=1, ge=1, le=5)
    state: dict | None = None
    kind: Literal["snapshot", "match"] = "snapshot"
    duration: Literal[6, 12, 30, 60, 120] = 30
    interval: Literal[1, 2, 3] = 2
    seed: int = Field(default=42, ge=0, le=2147483647)
    opponent: Literal["RandomBaseline", "DefensiveBaseline", "AggressiveBaseline"] = "AggressiveBaseline"


class NativeControl(Body):
    action: Literal["pause", "resume", "step", "stop"]


def create_app(results_root='results', *, service=None):
    owned = service is None
    svc = service or Service(results_root)
    from .native import NativeWorkspace
    if svc.native is None:
        svc.native = NativeWorkspace(svc)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned:
            await asyncio.to_thread(svc.close)

    app = FastAPI(title='AWS Baseline Football Lab', version='1.0', lifespan=lifespan)
    app.state.service = svc
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).hostname not in {"localhost", "127.0.0.1"}:
                return JSONResponse({"detail": "This local API accepts loopback origins only"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get('/api/health')
    def health():
        return {'status': 'available', 'implementation': 'aws-balanced-qwen',
                'active_jobs': [k for k, t in svc.jobs.items() if t.is_alive()]}

    @app.get("/api/native")
    def native_workspace():
        return svc.native.catalog()

    @app.post("/api/native/runs", status_code=202)
    def native_launch(body: NativeLaunch):
        try:
            return {"id": svc.native.launch(**body.model_dump())}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/native/runs/{run_id}")
    def native_detail(run_id: str):
        try:
            return svc.native.detail(run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/native/runs/{run_id}/control")
    def native_control(run_id: str, body: NativeControl):
        try:
            return svc.native.control(run_id, body.action)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/native/runs/{run_id}/frames")
    def native_frames(run_id: str, after: int = -1, limit: int = 500):
        try:
            return svc.native.frames(run_id, after=max(-1, after), limit=max(1, min(500, limit)))
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/native/runs/{run_id}/report")
    def native_report(run_id: str):
        try:
            return svc.native.report(run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc))

    static = Path(__file__).parent / "static"
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        if path == "THIRD-PARTY-LICENSES.txt" and (static / path).is_file():
            return FileResponse(static / path, media_type="text/plain")
        index = static / "index.html"
        if not index.exists():
            return JSONResponse({"detail": "Build the dashboard first: cd dashboard; npm.cmd install; npm.cmd run build"}, status_code=503)
        return FileResponse(index)

    return app


def main(argv=None):
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description='Native AWS/Qwen football workspace')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--results', default='results')
    args = parser.parse_args(argv)
    uvicorn.run(create_app(args.results), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
