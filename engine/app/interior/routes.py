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

# routes.py lives at <workspace>/engine/app/interior/routes.py; parents[2] is
# <workspace>/engine, so this resolves to engine/fixtures/villa_120.json.
_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "villa_120.json"


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
