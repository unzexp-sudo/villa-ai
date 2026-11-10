"""HTTP API (M6): models, intake, runs, exports, samples.

Every route is thin. The rules live in the modules that own them, so this file
is a contract surface rather than a second implementation. Two behaviours are
deliberate and easy to get wrong:

* ``POST /runs`` returns immediately with a ``run_id``; progress arrives on
  ``GET /runs/{id}/stream``. Blocking here would make the browser's UI thread
  wait for a multi-second pipeline.
* ``DELETE /models/{id}`` really deletes the model *and* its runs and events.
  A delete button that only hides a row is a privacy claim we cannot keep.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

router = APIRouter()

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class RunIn(BaseModel):
    model_id: str
    enabled_systems: list[str] | None = None
    country: str | None = None


class ItemIn(BaseModel):
    kind: str
    at: dict | None = None
    action: str = "place"


class ScaleIn(BaseModel):
    p1: dict
    p2: dict
    distance_m: float


def _store():
    from app.core import bootstrap
    return bootstrap.get_store()


def _get_model(model_id: str):
    m = _store().get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return m


# ---- config / samples ---------------------------------------------------
@router.get("/sample-model")
def sample_model():
    with (_FIXTURES / "villa_120.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/samples")
def list_samples():
    out = []
    for p in sorted(_FIXTURES.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rooms = sum(len(lvl.get("rooms", [])) for lvl in data.get("levels", []))
            out.append({"id": data["id"], "name": data["name"],
                        "building_type": data.get("building_type"),
                        "rooms": rooms, "slug": p.stem})
        except Exception:
            continue
    return {"samples": out}


@router.get("/samples/{slug}")
def sample_by_slug(slug: str):
    p = _FIXTURES / f"{slug}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Sample not found")
    return json.loads(p.read_text(encoding="utf-8"))


# ---- models -------------------------------------------------------------
@router.post("/models")
def create_model(payload: dict):
    from app.contract.building import BuildingModel

    try:
        model = BuildingModel.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store().save_model(model)
    return {"model_id": model.id, "model": model.model_dump(mode="json")}


@router.get("/models/{model_id}")
def get_model(model_id: str):
    return _get_model(model_id)


@router.patch("/models/{model_id}")
def patch_model(model_id: str, patch: dict):
    from app.contract.building import BuildingModel

    current = _get_model(model_id).model_dump(mode="json")
    current.update(patch)
    current["id"] = model_id
    try:
        updated = BuildingModel.model_validate(current)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store().save_model(updated)
    return updated


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str):
    _store().delete_model(model_id)
    return Response(status_code=204)


@router.post("/models/{model_id}/scale")
def calibrate(model_id: str, body: ScaleIn):
    from app.contract.building import Point2D
    from app.intake.parse import ParseError, calibrate_scale

    model = _get_model(model_id)
    try:
        out = calibrate_scale(model, Point2D(**body.p1), Point2D(**body.p2),
                              body.distance_m)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store().save_model(out)
    return out


@router.post("/models/{model_id}/upload")
async def upload(model_id: str, file: UploadFile = File(...)):
    """Parse an upload into the existing model's id, keeping the same handle."""
    from app.intake.parse import ParseError, parse_upload

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=422, detail="the file is empty")
    try:
        parsed = parse_upload(file.filename or "", blob)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    parsed.id = model_id
    _store().save_model(parsed)
    return parsed


@router.get("/models/{model_id}/gaps")
def gaps(model_id: str):
    from app.intake import audit

    return {"items": [i.model_dump(mode="json") for i in audit(_get_model(model_id))]}


@router.post("/models/{model_id}/items")
def add_item(model_id: str, body: ItemIn):
    from app.contract.building import Point2D
    from app.intake import apply_item

    model = _get_model(model_id)
    at = Point2D(**body.at) if body.at else None
    try:
        out = apply_item(model, body.kind, at, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store().save_model(out)
    return out


@router.get("/models/{model_id}/scene")
def scene(model_id: str):
    model = _get_model(model_id)
    try:
        from app.interior.scene import build_scene
        return build_scene(model)
    except Exception as exc:
        raise HTTPException(status_code=501,
                            detail=f"3D scene unavailable: {exc}") from exc


# ---- runs ---------------------------------------------------------------
@router.post("/runs")
def create_run(body: RunIn, request: Request):
    from app.api.leads import require_lead
    from app.contract.enums import Country as CountryEnum, SystemKind
    from app.orchestrator.run import start_run

    require_lead(request)
    model = _get_model(body.model_id)
    systems = None
    if body.enabled_systems is not None:
        try:
            systems = [SystemKind(s) for s in body.enabled_systems]
        except ValueError as exc:
            raise HTTPException(status_code=422,
                                detail=f"unknown system: {exc}") from exc
    try:
        run = start_run(body.model_id, systems,
                        CountryEnum(body.country) if body.country else model.country)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run.run_id, "status": run.status.value,
            "enabled_systems": [s.value for s in run.enabled_systems]}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    from app.orchestrator.run import get_run as _get

    run = _get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0):
    from app.core import bootstrap

    return {"events": [e.model_dump(mode="json")
                       for e in bootstrap.get_bus().since(run_id, after)]}


# ---- exports ------------------------------------------------------------
def _run_or_404(run_id: str):
    from app.orchestrator.run import get_run as _get

    run = _get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/report.pdf")
def report_pdf(run_id: str):
    from app.exports.report import build_report

    data = build_report(_run_or_404(run_id))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="villa-ai-{run_id}.pdf"'})


@router.get("/runs/{run_id}/export.dxf")
def export_dxf(run_id: str):
    from app.exports.dxf import to_dxf

    return Response(content=to_dxf(_run_or_404(run_id)), media_type="application/dxf",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.dxf"'})


@router.get("/runs/{run_id}/export.gltf")
def export_gltf(run_id: str):
    from app.exports.gltf import to_gltf

    return Response(content=to_gltf(_run_or_404(run_id)), media_type="model/gltf+json",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.gltf"'})


@router.get("/runs/{run_id}/export.ifc")
def export_ifc(run_id: str):
    from app.exports.ifc import to_ifc

    try:
        return Response(content=to_ifc(_run_or_404(run_id)), media_type="application/ifc")
    except NotImplementedError as exc:
        return JSONResponse(status_code=501,
                            content={"code": "NOT_IMPLEMENTED", "detail": str(exc)})


__all__ = ["router"]
