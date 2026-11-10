"""Thread-safe JSON-file store implementing the RunStore Protocol.

Persists models, runs and events as JSON under DATA_DIR. All writes are atomic
(write a temp file in the same directory, then ``os.replace``). Deleting a
model cascades to every run belonging to it and those runs' events (the privacy
promise in MODULE_CONTRACT.md §8 is real).
"""

import json
import os
import threading
import uuid
from pathlib import Path

from app.contract.building import BuildingModel
from app.contract.events import AgentEvent
from app.contract.results import RunResult


class JsonFileStore:
    """In-process, zero-dependency RunStore. Swap for Postgres/Redis later."""

    def __init__(self, data_dir: str | os.PathLike | None = None):
        if data_dir is None:
            from app.core.config import settings  # lazy: avoid import coupling
            data_dir = settings.DATA_DIR
        self.data_dir = Path(data_dir)
        self._models = self.data_dir / "models"
        self._runs = self.data_dir / "runs"
        self._events = self.data_dir / "events"
        self._leads = self.data_dir / "leads"
        for d in (self._models, self._runs, self._events, self._leads):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ---- paths ----------------------------------------------------------
    def _model_path(self, model_id: str) -> Path:
        return self._models / f"{model_id}.json"

    def _run_path(self, run_id: str) -> Path:
        return self._runs / f"{run_id}.json"

    def _events_path(self, run_id: str) -> Path:
        return self._events / f"{run_id}.json"

    def _lead_path(self, lead_id: str) -> Path:
        return self._leads / f"{lead_id}.json"

    # ---- low-level io ---------------------------------------------------
    def _read(self, path: Path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _atomic_write(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, path)  # atomic on the same filesystem
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _remove(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _dump(obj) -> dict:
        # mode="json" turns dict[SystemKind, ...] keys into enum *values* and
        # coerces enums/datetimes to plain JSON types.
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        return dict(obj)

    # ---- models ---------------------------------------------------------
    def save_model(self, m: BuildingModel) -> None:
        with self._lock:
            self._atomic_write(self._model_path(m.id), self._dump(m))

    def get_model(self, model_id: str) -> BuildingModel | None:
        with self._lock:
            data = self._read(self._model_path(model_id))
        return BuildingModel.model_validate(data) if data is not None else None

    def delete_model(self, model_id: str) -> None:
        """Cascade: remove the model, all its runs, and those runs' events."""
        with self._lock:
            self._remove(self._model_path(model_id))
            for run_file in list(self._runs.glob("*.json")):
                data = self._read(run_file)
                if data and data.get("model_id") == model_id:
                    run_id = data.get("run_id")
                    self._remove(run_file)
                    if run_id:
                        self._remove(self._events_path(run_id))

    # ---- runs -----------------------------------------------------------
    def save_run(self, r: RunResult) -> None:
        with self._lock:
            self._atomic_write(self._run_path(r.run_id), self._dump(r))

    def get_run(self, run_id: str) -> RunResult | None:
        with self._lock:
            data = self._read(self._run_path(run_id))
        return RunResult.model_validate(data) if data is not None else None

    def _all_runs(self) -> list[RunResult]:
        runs: list[RunResult] = []
        for run_file in self._runs.glob("*.json"):
            data = self._read(run_file)
            if data is not None:
                runs.append(RunResult.model_validate(data))
        return runs

    def list_runs_for_model(self, model_id: str) -> list[RunResult]:
        """All runs for a model, oldest first (helper for M6)."""
        with self._lock:
            runs = [r for r in self._all_runs() if r.model_id == model_id]
        return sorted(runs, key=lambda r: r.started_at)

    # ---- events ---------------------------------------------------------
    def append_event(self, run_id: str, e: AgentEvent) -> None:
        with self._lock:
            path = self._events_path(run_id)
            events = self._read(path) or []
            events.append(self._dump(e))
            self._atomic_write(path, events)

    def events_since(self, run_id: str, seq: int) -> list[AgentEvent]:
        """Events with ``seq > seq``, ordered by seq (for SSE replay)."""
        with self._lock:
            raw = self._read(self._events_path(run_id)) or []
        items = [AgentEvent.model_validate(d) for d in raw if d.get("seq", -1) > seq]
        return sorted(items, key=lambda e: e.seq)

    # ---- leads (M6) -----------------------------------------------------
    def save_lead(self, lead_id: str, lead) -> None:
        with self._lock:
            self._atomic_write(self._lead_path(lead_id), self._dump(lead))

    def get_lead(self, lead_id: str):
        with self._lock:
            return self._read(self._lead_path(lead_id))


__all__ = ["JsonFileStore"]
