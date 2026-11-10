"""HTTP routes for the additive interior scene feature.

Registered by ``engine/main.py`` with prefix ``/api``. These endpoints are
free, deterministic and read-only: no POST, no upload, no auth, no rate limit,
no credentials and no new dependencies.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.contract.building import BuildingModel
from app.interior.contract import InteriorScene
from app.interior.scene import build_scene

router = APIRouter()

# routes.py lives at <root>/app/interior/routes.py, so <root> (the directory
# holding the `app` package) is parents[2] — NOT parents[3]. Locally that is
# `<repo>/engine`; in the Docker image `app` is copied to /app, so it is /app
# and the fixtures sit at /app/fixtures. Walking up by a hardcoded number of
# levels silently points at the wrong directory when the install layout
# changes, so derive it from the package location instead.
_APP_DIR = Path(__file__).resolve().parents[1]  # .../app
_ROOT = _APP_DIR.parent                            # dir containing `app`
_FIXTURE = _ROOT / "fixtures" / "villa_120.json"


@router.get("/interior/scene", response_model=InteriorScene)
def get_sample_scene() -> InteriorScene:
    """InteriorScene for the bundled sample model."""
    with _FIXTURE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return build_scene(BuildingModel.model_validate(data))


@router.get("/interior/scene/{model_id}", response_model=InteriorScene)
def get_scene(model_id: str) -> InteriorScene:
    """InteriorScene for a model in JsonFileStore; 404 when missing."""
    from app.core.bootstrap import get_store  # lazy: sibling module may still be landing

    m = get_store().get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return build_scene(m)


__all__ = ["router"]
