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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    return app


app = create_app()

__all__ = ["create_app", "app"]
