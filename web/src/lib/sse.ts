// SSE client for a live run.
//
// The contract this file exists to enforce (see docs/MODULE_CONTRACT.md §4 and
// the "stream that ends without a terminal event" failure mode):
//
//   * close the EventSource on run.completed / run.failed, and only those;
//   * a native `error` is a transport blip, NOT the end of the run — reconnect;
//   * a clean close with no terminal event is a real failure, surface it;
//   * dedupe by `seq` so a Last-Event-ID replay cannot double-apply events.

import { API_BASE, EVENT_NAMES, isTerminal, type AgentEvent } from "../contract";

export interface StreamHandlers {
  onEvent: (e: AgentEvent) => void;
  onError: (message: string) => void;
  onDone: () => void;
}

export interface StreamHandle {
  close: () => void;
}

export function streamRun(runId: string, h: StreamHandlers): StreamHandle {
  const seen = new Set<number>();
  let lastSeq = 0;
  let closed = false;
  let terminal = false;
  let retries = 0;
  let es: EventSource | null = null;

  const open = () => {
    if (closed) return;
    const url = `${API_BASE}/api/runs/${runId}/stream`;
    es = new EventSource(url);

    const handle = (ev: MessageEvent) => {
      let parsed: AgentEvent;
      try {
        parsed = JSON.parse(ev.data) as AgentEvent;
      } catch {
        return; // a frame we cannot parse is not a frame we should act on
      }
      if (typeof parsed.seq === "number") {
        if (seen.has(parsed.seq)) return; // replay after reconnect
        seen.add(parsed.seq);
        lastSeq = Math.max(lastSeq, parsed.seq);
      }
      h.onEvent(parsed);
      if (isTerminal(parsed.name)) {
        terminal = true;
        close();
        h.onDone();
      }
    };

    for (const name of EVENT_NAMES) es.addEventListener(name, handle as EventListener);

    es.onerror = () => {
      // Native error: either the run is over (we already closed) or the
      // connection dropped. EventSource retries on its own; we only give up
      // after a bounded number of failures so a dead engine does not spin.
      if (closed || terminal) return;
      retries += 1;
      if (retries > 12) {
        close();
        h.onError(
          "Lost the connection to the engine. The run may still be finishing — reload to check.",
        );
      }
    };
  };

  const close = () => {
    closed = true;
    es?.close();
    es = null;
  };

  open();

  return {
    close: () => {
      if (!terminal) h.onError("Stream closed before the run finished.");
      close();
    },
  };
}
