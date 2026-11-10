"""Shared pytest fixtures for M1 (off limits to other modules).

At import time this redirects ``tempfile`` to a repo-local ``.pytest_tmp`` so
temporary stores never scatter across the system temp. If that mkdir is denied
we fall back gracefully to ``/tmp/villaai-pytest`` -- fixture setup must never
crash the suite.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# conftest.py lives at <workspace>/engine/tests/conftest.py
ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ENGINE_DIR / "fixtures"

# Make `app` (and `main`) importable regardless of how pytest was invoked.
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))


def _configure_tempdir() -> None:
    candidates = [
        REPO_ROOT / ".pytest_tmp",  # preferred: repo-local
        Path("/tmp/villaai-pytest"),  # graceful fallback
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        try:
            tempfile.tempdir = str(candidate)
            os.environ["TMPDIR"] = str(candidate)
        except Exception:
            pass  # never let fixture setup crash
        return
    # Both mkdirs denied: leave the system default untouched.


_configure_tempdir()

from app.contract.building import BuildingModel  # noqa: E402
from app.core.emitter import EventBus  # noqa: E402
from app.core.store import JsonFileStore  # noqa: E402


def _load(name: str):
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_model() -> BuildingModel:
    return BuildingModel.model_validate(_load("villa_120.json"))


@pytest.fixture
def apartment_model() -> BuildingModel:
    return BuildingModel.model_validate(_load("apartment_85.json"))


@pytest.fixture
def minimal_model() -> BuildingModel:
    return BuildingModel.model_validate(_load("villa_minimal.json"))


@pytest.fixture
def fresh_store():
    data_dir = tempfile.mkdtemp(prefix="villaai-store-")
    store = JsonFileStore(data_dir)
    try:
        yield store
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def fresh_bus(fresh_store):
    return EventBus(fresh_store)
