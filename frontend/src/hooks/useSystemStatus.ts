import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Readiness } from "../api/types";

export type SystemState = "checking" | "ready" | "degraded" | "offline";

/**
 * Live backend health, polled.
 *
 * Without this, the single most confusing failure in the app is silent: when
 * Ollama is not running every triage still returns 200 with `degraded: true`
 * and a keyword-matched guess, which looks like the model quietly getting
 * dumber. Showing readiness in the top bar means the answer to "why is it
 * saying 'other' for everything?" is on screen before the question is asked.
 */
export function useSystemStatus(pollMs = 20_000) {
  const [state, setState] = useState<SystemState>("checking");
  const [info, setInfo] = useState<Readiness | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      const readiness = await api.readiness();
      if (cancelled) return;
      if (!readiness) {
        setState("offline");
        setInfo(null);
        return;
      }
      setInfo(readiness);
      setState(readiness.llm_reachable ? "ready" : "degraded");
    }

    void check();
    const timer = setInterval(() => void check(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pollMs]);

  return { state, info };
}
