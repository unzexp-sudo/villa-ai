"""SSE endpoint for a live run (M6).

Three rules, all of them load-bearing:

1. **The stream ends only on a terminal event.** ``run.completed`` /
   ``run.failed`` are the last frames, then the generator returns and the server
   closes. If a stream closed without one, the browser's ``EventSource`` would
   read the close as a transport error and reconnect forever.
2. **``id:`` is the monotonic ``seq``.** A reconnect sends ``Last-Event-ID`` and
   gets the events after it — a replay of stored events, never a re-run.
3. **A finished run replays and stops.** Opening the stream after completion
   yields the whole transcript and closes; it must not hang or duplicate.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.contract.events import TERMINAL_EVENTS

router = APIRouter()

_KEEPALIVE_S = 15.0
_MAX_IDLE_S = 300.0


def _frame(event) -> str:
    payload = event.model_dump(mode="json")
    return (f"id: {event.seq}\n"
            f"event: {event.name}\n"
            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n")


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request):
    from app.core import bootstrap
    from app.orchestrator.run import get_run

    bus = bootstrap.get_bus()
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    last_id = request.headers.get("last-event-id", "0")
    try:
        after = int(last_id)
    except ValueError:
        after = 0

    async def gen():
        cursor = after
        idle = 0.0
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            # wait() blocks on the run's Condition in a worker thread so the
            # event loop stays free for other streams.
            batch = await asyncio.to_thread(bus.wait, run_id, cursor, 0.5)
            if batch:
                idle = 0.0
                for ev in batch:
                    cursor = ev.seq
                    yield _frame(ev)
                    if ev.name in TERMINAL_EVENTS:
                        return  # the ONLY clean exit
            else:
                idle += 0.5
                if idle >= _KEEPALIVE_S:
                    idle = 0.0
                    yield ": keepalive\n\n"
                if idle >= _MAX_IDLE_S:
                    # Not a terminal event: the client must see a dropped stream,
                    # not a silent success.
                    return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


__all__ = ["router"]
