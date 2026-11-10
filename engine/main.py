"""Villa AI engine entry point (M1 FOUNDATION).

Startup note
------------
The application is designed to boot while sibling modules are still being
written in parallel. CORS, ``/api/health`` and ``/api/config`` are defined
directly here (M6 will NOT redefine them). Routers from
``app.api.routes`` / ``app.api.sse`` / ``app.api.leads`` are registered
lazily via ``importlib``; any that are missing or half-written are skipped
with an info log so the health check stays green with zero external services
and zero sibling code present.

Run from ``engine/``::

    python -m uvicorn main:app --app-dir engine --port 8000

Note: when running from ``engine/`` the ``app`` package resolves as TOP LEVEL,
so imports are ``from app...`` (never ``from engine.app...``).
"""

import importlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.contract.enums import (
    BuildingType,
    Country,
    DEFAULT_BANDS,
    OVERLAY_COLORS,
    SYSTEM_COLORS,
    SYSTEM_ORDER,
)
from app.core.config import settings

log = logging.getLogger("villaai.main")

# Routers owned by M6. They land at different times during the parallel build.
_OPTIONAL_ROUTERS = ("app.api.routes", "app.api.sse", "app.api.leads", "app.interior.routes")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.PRODUCT_NAME, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "engine": "villa-ai", "version": "0.1.0"}

    @app.get("/api/config")
    def config():
        # One source of truth for the frontend: the five systems, the shared
        # colour language (5 systems + 2 overlays), height bands, countries
        # and building types.
        colors = {k.value: v for k, v in SYSTEM_COLORS.items()}
        colors.update(OVERLAY_COLORS)
        return {
            "product_name": settings.PRODUCT_NAME,
            "systems": [s.value for s in SYSTEM_ORDER],
            "colors": colors,
            "bands": {k.value: list(v) for k, v in DEFAULT_BANDS.items()},
            "countries": [c.value for c in Country],
            "building_types": [b.value for b in BuildingType],
        }

    for mod_name in _OPTIONAL_ROUTERS:
        try:
            mod = importlib.import_module(mod_name)
            router = getattr(mod, "router", None)
            if router is None:
                log.info("Optional router %s has no 'router' (skipping)", mod_name)
                continue
            app.include_router(router, prefix="/api")
            log.info("Registered router from %s", mod_name)
        except (ImportError, AttributeError) as exc:
            log.info("Optional router %s not available yet: %s", mod_name, exc)
        except Exception as exc:  # half-written sibling must not block boot
            log.info("Optional router %s failed to load (%s); continuing", mod_name, exc)

    _mount_spa(app)
    return app


# The built frontend lives next to the engine package. Locally it is absent
# (Vite dev server serves it on :5173); in the Docker image the Dockerfile
# copies the bundle to <root>/web/dist.
_WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"


def _mount_spa(app: FastAPI) -> None:
    """Serve the built SPA from the same origin as the API.

    Mounted AFTER the /api routers so that API routes always win. The catch-all
    route returns index.html for unknown paths so client-side routes such as
    /interior and /app survive a hard refresh.
    """
    if not (_WEB_DIST / "index.html").is_file():
        log.info("No SPA build at %s; serving API only", _WEB_DIST)
        return

    assets = _WEB_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # Unknown API paths must 404, not fall through to the SPA shell —
        # otherwise a frontend fetch to a missing endpoint receives HTML and
        # the real error is lost.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (_WEB_DIST / full_path).resolve()
        # Guard against path traversal out of the dist directory.
        if (
            full_path
            and candidate.is_file()
            and str(candidate).startswith(str(_WEB_DIST.resolve()))
        ):
            return FileResponse(candidate)
        return FileResponse(_WEB_DIST / "index.html")

    log.info("Serving SPA from %s", _WEB_DIST)


app = create_app()

__all__ = ["create_app", "app"]
