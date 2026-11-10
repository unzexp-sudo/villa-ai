"""EventBus implementing the Emitter Protocol.

Owns a per-run monotonic ``seq`` counter and a ``threading.Condition`` per run.
This is what M6's SSE endpoint uses. Getting the terminal-event / replay
contract right matters: an SSE stream that never terminates causes an infinite
reconnect loop in the browser (MODULE_CONTRACT.md §4 / §8 gap 1).
"""

import threading
import time

from app.contract.events import TERMINAL_EVENTS, AgentEvent


class EventBus:
    def __init__(self, store):
        self._store = store
        # A single RLock backs every per-run Condition: notify/wait is still
        # per-run, but seq assignment is globally serialised and deadlock-free.
        self._lock = threading.RLock()
        self._seq: dict[str, int] = {}
        self._conds: dict[str, threading.Condition] = {}

    def _cond_for(self, run_id: str) -> threading.Condition:
        with self._lock:
            cond = self._conds.get(run_id)
            if cond is None:
                cond = threading.Condition(self._lock)
                self._conds[run_id] = cond
            return cond

    def emit(self, event: AgentEvent) -> AgentEvent:
        """Assign monotonic seq (per run) + wall-clock ts, persist, notify."""
        cond = self._cond_for(event.run_id)
        with cond:  # == with self._lock
            if event.run_id not in self._seq:
                # Survive a fresh bus after restart: continue from the last
                # persisted seq so replay ids are never duplicated.
                existing = self._store.events_since(event.run_id, -1)
                self._seq[event.run_id] = max(
                    (e.seq for e in existing), default=0
                )
            next_seq = self._seq[event.run_id] + 1
            self._seq[event.run_id] = next_seq
            assigned = event.model_copy(update={"seq": next_seq, "ts": time.time()})
            self._store.append_event(event.run_id, assigned)
            cond.notify_all()
        return assigned

    def feed(self, run_id: str, agent: str, text: str):
        """Emit a ``feed.line`` event; the line lives in ``message``.

        Feed lines are generated from templates over real events -- never
        invent metrics here.
        """
        return self.emit(
            AgentEvent(
                run_id=run_id,
                seq=0,
                ts=0.0,
                name="feed.line",
                agent=agent,
                message=text,
            )
        )

    def since(self, run_id: str, after_seq: int) -> list[AgentEvent]:
        """Events strictly after ``after_seq`` (SSE Last-Event-ID replay)."""
        return self._store.events_since(run_id, after_seq)

    def wait(self, run_id: str, after_seq: int, timeout: float = 0.5) -> list[AgentEvent]:
        """Block on the run's Condition until new events arrive or timeout."""
        cond = self._cond_for(run_id)
        with cond:  # == with self._lock
            events = self.since(run_id, after_seq)
            if events:
                return events
            cond.wait(timeout=timeout)
            return self.since(run_id, after_seq)

    def terminal_seen(self, run_id: str) -> bool:
        """True once a ``run.completed`` or ``run.failed`` event exists."""
        return any(e.name in TERMINAL_EVENTS for e in self.since(run_id, -1))


__all__ = ["EventBus"]
