"""M6 tests: the HTTP surface, the SSE stream and the lead gate.

These run against the real app object with a temporary data directory, so they
exercise the same router wiring a browser hits — including the parts that are
easy to get wrong and invisible in a unit test:

* the SSE stream ends on a terminal event, and only on one;
* ``Last-Event-ID`` replays instead of restarting from zero;
* the lead gate is enforced server-side, not by the form;
* exports answer with real bytes / a real 501, never a redirect to HTML.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import routes as api_routes
from app.core.config import settings


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    settings.DATA_DIR = str(tmp_path_factory.mktemp("villaai-api"))
    # Rebuild the bootstrap singletons against the temp dir.
    import app.core.bootstrap as bootstrap

    bootstrap._store = None
    bootstrap._bus = None

    from main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def model_id(client) -> str:
    sample = client.get("/api/samples/villa_120").json()
    created = client.post("/api/models", json=sample).json()
    return created["model_id"]


# ---- basics ---------------------------------------------------------------

def test_health_and_config_are_up(client):
    health = client.get("/api/health")
    assert health.status_code == 200 and health.json()["ok"] is True

    cfg = client.get("/api/config").json()
    assert cfg["systems"] == ["drainage", "water", "air", "power", "fire"]
    # Fire is magenta; red belongs to clashes.
    assert cfg["colors"]["fire"] == "#e6379c"
    assert cfg["colors"]["clash"] == "#ff3b30"


def test_samples_are_listed_and_loadable(client):
    listing = client.get("/api/samples").json()["samples"]
    assert {s["slug"] for s in listing} >= {"villa_120", "apartment_85"}
    one = client.get("/api/samples/villa_120")
    assert one.status_code == 200
    assert one.json()["levels"], "a sample with no geometry is not a usable sample"


def test_unknown_model_is_404_not_500(client):
    assert client.get("/api/models/nope").status_code == 404


# ---- CRUD + gaps ----------------------------------------------------------

def test_model_round_trip_and_delete(client):
    sample = client.get("/api/samples/apartment_85").json()
    created = client.post("/api/models", json=sample).json()
    mid = created["model_id"]

    assert client.get(f"/api/models/{mid}").json()["id"] == mid
    patched = client.patch(f"/api/models/{mid}", json={"name": "Renamed plan"})
    assert patched.status_code == 200 and patched.json()["name"] == "Renamed plan"

    assert client.delete(f"/api/models/{mid}").status_code == 204
    assert client.get(f"/api/models/{mid}").status_code == 404


def test_gaps_endpoint_returns_card_shapes(client):
    sample = client.get("/api/samples/villa_minimal").json()
    mid = client.post("/api/models", json=sample).json()["model_id"]
    items = client.get(f"/api/models/{mid}/gaps").json()["items"]
    assert items, "the sparse sample must report gaps"
    for item in items:
        assert {"id", "kind", "label", "prompt", "severity", "options"} <= set(item)
        assert "drag" in item["options"]


def test_apply_item_updates_the_stored_model(client):
    sample = client.get("/api/samples/villa_minimal").json()
    mid = client.post("/api/models", json=sample).json()["model_id"]
    out = client.post(f"/api/models/{mid}/items",
                      json={"kind": "water_tank", "action": "skip"})
    assert out.status_code == 200
    assumptions = out.json()["assumptions"]
    assert any(a["source"] == "skip" for a in assumptions)


def test_upload_refuses_garbage_pdf_with_a_422(client):
    """A PDF with no parseable content must be refused, not guessed."""
    sample = client.get("/api/samples/villa_120").json()
    mid = client.post("/api/models", json=sample).json()["model_id"]
    res = client.post(f"/api/models/{mid}/upload",
                      files={"file": ("plan.pdf", b"%PDF-1.7 fake garbage", "application/pdf")})
    assert res.status_code == 422


def test_upload_accepts_a_real_dxf(client):
    import io

    import ezdxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    # Exterior walls + internal partitions so the quality gate (>= 8 walls) passes.
    for a, b in [((0, 0), (9, 0)), ((9, 0), (9, 7)), ((9, 7), (0, 7)), ((0, 7), (0, 0)),
                 ((4.5, 0), (4.5, 7)), ((0, 3.5), (9, 3.5)),
                 ((4.5, 1.5), (9, 1.5)), ((4.5, 5), (9, 5))]:
        msp.add_line(a, b)
    buf = io.StringIO()
    doc.write(buf)

    sample = client.get("/api/samples/villa_120").json()
    mid = client.post("/api/models", json=sample).json()["model_id"]
    res = client.post(f"/api/models/{mid}/upload",
                      files={"file": ("plan.dxf", buf.getvalue().encode(), "application/dxf")})
    assert res.status_code == 200, res.text
    assert len(res.json()["levels"][0]["walls"]) >= 8


def test_upload_create_saves_a_new_model_from_dxf(client):
    import io

    import ezdxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    # Exterior walls + internal partitions so the quality gate (>= 8 walls) passes.
    for a, b in [((0, 0), (8, 0)), ((8, 0), (8, 6)), ((8, 6), (0, 6)), ((0, 6), (0, 0)),
                 ((4, 0), (4, 6)), ((0, 3), (8, 3)),
                 ((4, 1.5), (8, 1.5)), ((4, 4.5), (8, 4.5))]:
        msp.add_line(a, b)
    buf = io.StringIO()
    doc.write(buf)

    res = client.post("/api/models/upload",
                      files={"file": ("new-plan.dxf", buf.getvalue().encode(), "application/dxf")})
    assert res.status_code == 200, res.text
    uploaded = res.json()
    assert uploaded["id"].startswith("mdl-")
    assert len(uploaded["levels"][0]["walls"]) >= 8
    assert client.get(f"/api/models/{uploaded['id']}").json()["id"] == uploaded["id"]


def test_one_step_upload_rejects_empty_or_unsupported_files(client):
    empty = client.post("/api/models/upload", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert empty.status_code == 422

    unsupported = client.post("/api/models/upload", files={"file": ("plan.dwg", b"DWG", "application/octet-stream")})
    assert unsupported.status_code == 422


def test_plan_upload_is_bounded(monkeypatch):
    class TooLargeUpload:
        async def read(self, size):
            assert size == 5
            return b"12345"

    monkeypatch.setattr(api_routes, "_MAX_UPLOAD_BYTES", 4)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_routes._read_plan_upload(TooLargeUpload()))
    assert exc.value.status_code == 413


# ---- the run, the stream, the exports ------------------------------------

def test_run_streams_and_ends_on_a_terminal_event(client, model_id):
    start = client.post("/api/runs", json={"model_id": model_id})
    assert start.status_code == 200, start.text
    run_id = start.json()["run_id"]

    names: list[str] = []
    seqs: list[int] = []
    deadline = time.time() + 90
    with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        for line in stream.iter_lines():
            if line.startswith("event: "):
                names.append(line[7:].strip())
            elif line.startswith("id: "):
                seqs.append(int(line[4:]))
            if names and names[-1] in ("run.completed", "run.failed"):
                break
            if time.time() > deadline:
                pytest.fail("the stream never produced a terminal event")

    assert names[-1] == "run.completed", f"ended on {names[-1]}, events={names[:5]}…"
    # The terminal event is LAST: nothing may follow it on the wire.
    assert names.count("run.completed") == 1
    assert seqs == sorted(seqs), "seq must be monotonic or replay dedupe breaks"
    assert len(set(seqs)) == len(seqs), "duplicate seq would be dropped by the client"


def test_last_event_id_replays_from_the_cursor(client, model_id):
    run_id = client.post("/api/runs", json={"model_id": model_id}).json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/stream") as s:
        for line in s.iter_lines():
            if line.startswith("event: ") and line[7:].strip() in ("run.completed",
                                                                   "run.failed"):
                break

    seen: list[int] = []
    with client.stream("GET", f"/api/runs/{run_id}/stream",
                       headers={"Last-Event-ID": "5"}) as s:
        for line in s.iter_lines():
            if line.startswith("id: "):
                seen.append(int(line[4:]))
            if line.startswith("event: ") and line[7:].strip() in ("run.completed",
                                                                   "run.failed"):
                break
    assert seen, "a replay must still deliver frames"
    assert min(seen) >= 6, "replay must start AFTER the cursor, not at 1"


def test_run_result_carries_real_outputs(client, model_id):
    run_id = client.post("/api/runs", json={"model_id": model_id}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/stream") as s:
        for line in s.iter_lines():
            if line.startswith("event: ") and line[7:].strip() in ("run.completed",
                                                                   "run.failed"):
                break

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "complete"
    assert run["layers"], "a completed run with no layers is not a design"
    assert run["code"] is not None
    assert run["code"]["headline"] == (
        f"passed {run['code']['passed']} of {run['code']['total']} automated checks"
    )
    # No fabricated resolution: the after-count must be reachable from before.
    assert len(run["clashes_after"]) <= len(run["clashes_before"])


def test_exports_answer_with_the_right_content(client, model_id):
    run_id = client.post("/api/runs", json={"model_id": model_id}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/stream") as s:
        for line in s.iter_lines():
            if line.startswith("event: ") and line[7:].strip() in ("run.completed",
                                                                   "run.failed"):
                break

    pdf = client.get(f"/api/runs/{run_id}/report.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")

    dxf = client.get(f"/api/runs/{run_id}/export.dxf")
    assert dxf.status_code == 200 and b"SECTION" in dxf.content

    gltf = client.get(f"/api/runs/{run_id}/export.gltf")
    assert gltf.status_code == 200
    assert json.loads(gltf.content)["asset"]["version"] == "2.0"

    ifc = client.get(f"/api/runs/{run_id}/export.ifc")
    assert ifc.status_code == 200
    assert ifc.content.startswith(b"ISO-10303-21;")
    assert b"IFC4" in ifc.content


def test_unknown_run_export_is_404(client):
    assert client.get("/api/runs/nope/report.pdf").status_code == 404


# ---- the lead gate --------------------------------------------------------

def test_lead_gate_is_server_side(client, model_id, monkeypatch):
    """A form-only gate is cosmetic; this asserts the server refuses."""
    created = client.post("/api/leads", json={
        "name": "Test Architect", "email": "arch@example.com", "role": "architect",
        "company": "Studio", "country": "ID", "consent": True,
    })
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "pending_verification"
    token = body.get("dev_token")
    assert token, "with no mail provider the token must be returned for testing"

    verified = client.post("/api/leads/verify", json={"token": token})
    assert verified.status_code == 200
    session = verified.json()["session_token"]

    monkeypatch.setattr(settings, "REQUIRE_LEAD_GATE", True)
    blocked = client.post("/api/runs", json={"model_id": model_id})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "LEAD_GATE"

    allowed = client.post("/api/runs", json={"model_id": model_id},
                          headers={"X-Session-Token": session})
    assert allowed.status_code == 200


def test_lead_requires_consent_and_a_real_email(client):
    no_consent = client.post("/api/leads", json={
        "name": "X", "email": "x@example.com", "role": "other", "consent": False,
    })
    assert no_consent.status_code == 422

    bad_email = client.post("/api/leads", json={
        "name": "X", "email": "not-an-email", "role": "other", "consent": True,
    })
    assert bad_email.status_code == 422


def test_unknown_lead_token_is_rejected(client):
    assert client.post("/api/leads/verify", json={"token": "made-up"}).status_code == 400


# ---- privacy: delete cascades --------------------------------------------

def test_deleting_a_model_takes_its_runs_with_it(client):
    sample = client.get("/api/samples/villa_120").json()
    mid = client.post("/api/models", json=sample).json()["model_id"]
    run_id = client.post("/api/runs", json={"model_id": mid}).json()["run_id"]

    client.delete(f"/api/models/{mid}")
    # The run must not outlive the plan it describes.
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_quickdraw_endpoint_creates_blank_model(client):
    res = client.post("/api/models/quickdraw", json={"width_m": 10, "depth_m": 8})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["model"]["source"] == "quickdraw"
    assert payload["model"]["levels"][0]["rooms"][0]["type"] == "living"
    assert client.get(f"/api/models/{payload['model_id']}").status_code == 200
