"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SystemInfo } from "@/lib/types";

type Listener = (s: SystemInfo | null, e: string | null) => void;
let cache: SystemInfo | null = null;
let cacheErr: string | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<Listener>();

async function refreshSystem() {
  if (!inflight) {
    inflight = api
      .system()
      .then((s) => {
        cache = s;
        cacheErr = null;
      })
      .catch((e: Error) => {
        cacheErr = e.message;
      })
      .finally(() => {
        inflight = null;
        listeners.forEach((l) => l(cache, cacheErr));
      });
  }
  return inflight;
}

/** Shared, lightly-polled system status (Ollama, storage, settings). */
export function useSystem(pollMs = 15000) {
  const [state, setState] = useState<{ system: SystemInfo | null; error: string | null }>({ system: cache, error: cacheErr });
  useEffect(() => {
    const l: Listener = (s, e) => setState({ system: s, error: e });
    listeners.add(l);
    refreshSystem();
    const t = setInterval(refreshSystem, pollMs);
    return () => {
      listeners.delete(l);
      clearInterval(t);
    };
  }, [pollMs]);
  const refresh = useCallback(() => refreshSystem(), []);
  return { ...state, refresh };
}
