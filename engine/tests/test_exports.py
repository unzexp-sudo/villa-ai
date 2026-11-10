"""M5 tests: exports.

Every exporter is tested against the run a user is MOST likely to export while
something is wrong — the empty, failed one. An exporter that only works on a
clean run is an exporter that breaks exactly when it is needed.
"""

from __future__ import annotations

import json
import time

import pytest

from app.contract.building import BuildingModel
from app.contract.enums import Country
from app.contract.results import RunResult, RunStatus
from app.exports import build_report, to_dxf, to_gltf, to_ifc


@pytest.fixture
def villa_run(sample_model: BuildingModel, fresh_store, fresh_bus) -> RunResult:
    from app.orchestrator.run import execute_run

    fresh_store.save_model(sample_model)
    run = RunResult(
        run_id="run-export", model_id=sample_model.id, status=RunStatus.RUNNING,
        country=Country.ID, enabled_systems=[], started_at=time.time(),
    )
    out = execute_run(run, emitter=fresh_bus, store=fresh_store)
    assert out.status is RunStatus.COMPLETE
    return out


@pytest.fixture
def empty_run(sample_model: BuildingModel) -> RunResult:
    """No layers, no code report, failed status: the worst case."""
    return RunResult(
        run_id="run-empty", model_id=sample_model.id, status=RunStatus.FAILED,
        country=Country.ID, enabled_systems=[], started_at=time.time(),
        finished_at=time.time() + 1.0, error="agent crashed",
    )


# ---- PDF ------------------------------------------------------------------

def test_report_is_a_real_pdf(villa_run: RunResult):
    blob = build_report(villa_run)
    assert blob.startswith(b"%PDF-")
    assert blob.rstrip().endswith(b"%%EOF")
    assert len(blob) > 3_000, "a 2 kB 'report' is a placeholder, not a document"


def test_report_survives_an_empty_failed_run(empty_run: RunResult):
    blob = build_report(empty_run)
    assert blob.startswith(b"%PDF-")
    assert len(blob) > 1_000


def test_report_contains_the_disclaimer(villa_run: RunResult):
    """The disclaimer must be in the rendered text, not just in the source.

    A PDF is compressed, so this checks the uncompressed text stream after
    decompressing — asserting on raw bytes would pass on any binary that
    happens to contain the words once.
    """
    text = _pdf_text(build_report(villa_run))
    assert "preliminary" in text.lower()
    assert "engineer" in text.lower()


def test_report_never_claims_compliance(villa_run: RunResult):
    text = " ".join(_pdf_text(build_report(villa_run)).lower().split())

    # The claim form, not the bare words: the report legitimately contains the
    # sentence "it does not certify that the design complies with any code",
    # and a naive "complies with" check fails on the very disclaimer it wants.
    for claim in (
        "the design complies with",
        "is code compliant",
        "code compliant",
        "satisfies all applicable",
        "meets all applicable code",
    ):
        assert claim not in text.replace("it does not certify that the design complies with", "")

    assert "does not certify" in text, "the no-certification sentence must be present"

    if villa_run.code:
        assert f"passed {villa_run.code.passed} of {villa_run.code.total}" in text


def test_report_lists_every_assumption(sample_model: BuildingModel):
    """Assumptions are the audit trail; dropping one hides a default."""
    from app.contract.building import Assumption

    model = sample_model.clone()
    model.assumptions.append(Assumption(
        id="as-test", field="ceiling_void", value="0.35",
        reason="not found on the plan", source="default", created_at=time.time(),
    ))
    run = RunResult(
        run_id="r", model_id=model.id, status=RunStatus.COMPLETE, country=Country.ID,
        enabled_systems=[], started_at=time.time(), assumptions=model.assumptions,
    )
    text = _pdf_text(build_report(run)).lower()
    assert "ceiling_void" in text
    assert "not found on the plan" in text


# ---- DXF ------------------------------------------------------------------

def test_dxf_is_a_r2013_ascii_file(villa_run: RunResult):
    blob = to_dxf(villa_run)
    assert isinstance(blob, bytes) and len(blob) > 500
    head = blob[:200].decode("ascii", "replace")
    assert "SECTION" in head
    assert "AC1027" in head, "DXF must declare R2013 so old CAD tools open it"


def test_dxf_has_one_layer_per_system_and_a_clash_layer(villa_run: RunResult):
    import ezdxf
    import io

    doc = ezdxf.read(io.StringIO(to_dxf(villa_run).decode("utf-8", "replace")))
    names = {layer.dxf.name.upper() for layer in doc.layers}
    for system in villa_run.layers:
        assert system.value.upper() in names, f"{system} layer missing"
    assert "CLASHES" in names


def test_dxf_geometry_matches_the_run(villa_run: RunResult):
    import ezdxf
    import io

    doc = ezdxf.read(io.StringIO(to_dxf(villa_run).decode("utf-8", "replace")))
    msp = doc.modelspace()
    drawn = sum(1 for e in msp if e.dxftype() in ("LWPOLYLINE", "LINE", "POLYLINE"))
    expected = sum(len(l.segments) for l in villa_run.layers.values())
    assert drawn >= expected, f"drew {drawn} entities for {expected} segments"


def test_dxf_survives_an_empty_run(empty_run: RunResult):
    blob = to_dxf(empty_run)
    assert b"SECTION" in blob


# ---- glTF -----------------------------------------------------------------

def test_gltf_is_valid_json_with_version_2(villa_run: RunResult):
    doc = json.loads(to_gltf(villa_run))
    assert doc["asset"]["version"] == "2.0"
    assert doc["meshes"], "a glTF with no meshes is an empty shell"
    assert doc["buffers"], "geometry needs a buffer"
    assert doc["buffers"][0]["uri"].startswith("data:application/octet-stream;base64,")


def test_gltf_one_mesh_per_drawn_system(villa_run: RunResult):
    doc = json.loads(to_gltf(villa_run))
    names = {m.get("name") for m in doc["meshes"]}
    for system in villa_run.layers:
        assert any(str(n).lower().startswith(system.value) for n in names if n), (
            f"no mesh for {system}"
        )


def test_gltf_survives_an_empty_run(empty_run: RunResult):
    doc = json.loads(to_gltf(empty_run))
    assert doc["asset"]["version"] == "2.0"


# ---- IFC: an honest refusal ----------------------------------------------

def test_ifc_refuses_instead_of_faking_a_file(villa_run: RunResult):
    with pytest.raises(NotImplementedError) as exc:
        to_ifc(villa_run)
    message = str(exc.value).lower()
    assert "ifc" in message
    # The message must tell the user what to do instead.
    assert "dxf" in message or "gltf" in message


# ---- helpers --------------------------------------------------------------

def _pdf_text(blob: bytes) -> str:
    """Extract the real text from the report.

    ReportLab writes glyph codes in a custom font encoding, so decompressing the
    content stream by hand yields mojibake and would make these assertions pass
    or fail for the wrong reason. pypdf reads the font's ToUnicode map, which is
    what a reader actually sees.
    """
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
