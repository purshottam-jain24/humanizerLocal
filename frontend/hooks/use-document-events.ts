"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";
import type { ProcessingEvent } from "@/lib/types";

/**
 * Subscribe to a document's processing event stream (SSE).
 * EventSource reconnects automatically and sends Last-Event-ID, so the
 * backend only replays events newer than the last one received.
 */
export function useDocumentEvents(docId: string, onEvent: (e: ProcessingEvent) => void) {
  const cb = useRef(onEvent);
  useLayoutEffect(() => {
    cb.current = onEvent;
  });
  const [connected, setConnected] = useState(false);
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/documents/${docId}/events`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as ProcessingEvent;
        if (ev.type?.startsWith("document.")) cb.current(ev);
      } catch {
        /* ignore malformed */
      }
    };
    es.addEventListener("idle", () => setIdle(true));
    return () => es.close();
  }, [docId]);

  return { connected, idle };
}
