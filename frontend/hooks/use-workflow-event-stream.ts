"use client";

import { useEffect, useState } from "react";

import type { WorkflowStatus, WorkflowStreamEntry } from "@/types/api";

export type EventStreamConnectionState = "connecting" | "open" | "closed" | "error";

export interface UseWorkflowEventStreamResult {
  /** New timeline entries received since the connection opened, ordered
   * by `sequence`. Does NOT include history from before the connection —
   * callers with a server-rendered initial timeline should merge that in
   * themselves (see `features/workflows/live-execution-panel.tsx`). */
  entries: WorkflowStreamEntry[];
  connectionState: EventStreamConnectionState;
  /** Set once the server sends its `done` event (the run reached a
   * terminal status) — `null` while still running. */
  finalStatus: WorkflowStatus | null;
}

/**
 * Subscribes to `GET /api/backend/organizations/{organizationId}/workflows/{workflowId}/events/stream`
 * (Milestone B) via the browser's native `EventSource` — see
 * `app/api/backend/[...path]/route.ts` for why this goes through the
 * Next.js proxy rather than the FastAPI backend directly (no CORS
 * configured there, and `EventSource` can't attach an `Authorization`
 * header itself anyway).
 *
 * Deliberately relies on `EventSource`'s BUILT-IN automatic reconnect
 * (with `Last-Event-ID` resume, forwarded by the proxy) rather than
 * hand-rolling retry logic — this is exactly the browser feature SSE was
 * designed around. The connection is closed for good once the server
 * sends `done` (the workflow reached a terminal status) or `workflowId`
 * becomes `null`/`enabled` becomes `false`.
 */
export function useWorkflowEventStream(
  organizationId: string,
  workflowId: string | null,
  options?: { enabled?: boolean },
): UseWorkflowEventStreamResult {
  const enabled = options?.enabled ?? true;
  const active = enabled && workflowId !== null;

  const [entries, setEntries] = useState<WorkflowStreamEntry[]>([]);
  const [connectionState, setConnectionState] = useState<EventStreamConnectionState>("connecting");
  const [finalStatus, setFinalStatus] = useState<WorkflowStatus | null>(null);

  // Reset accumulated state when the target workflow changes, using
  // React's documented "storing information from previous renders"
  // pattern (https://react.dev/learn/you-might-not-need-an-effect) — a
  // guarded setState call during render, not inside the effect below —
  // so switching from one workflow to another can't briefly show the
  // PREVIOUS workflow's entries under the new id.
  const [trackedWorkflowId, setTrackedWorkflowId] = useState(workflowId);
  if (workflowId !== trackedWorkflowId) {
    setTrackedWorkflowId(workflowId);
    setEntries([]);
    setFinalStatus(null);
    setConnectionState("connecting");
  }

  useEffect(() => {
    if (!active || !workflowId) return;

    const seenSequences = new Set<number>();
    const url = `/api/backend/organizations/${organizationId}/workflows/${workflowId}/events/stream`;
    const source = new EventSource(url);

    source.onopen = () => setConnectionState("open");
    source.onerror = () => {
      // `EventSource` retries transient network drops on its own; this
      // only reflects the transient "reconnecting" state in the UI, it
      // never closes the connection itself.
      setConnectionState((previous) => (previous === "closed" ? previous : "error"));
    };

    source.addEventListener("workflow_event", (event) => {
      const entry = JSON.parse((event as MessageEvent<string>).data) as WorkflowStreamEntry;
      if (seenSequences.has(entry.sequence)) return;
      seenSequences.add(entry.sequence);
      setEntries((previous) => [...previous, entry].sort((a, b) => a.sequence - b.sequence));
      setConnectionState("open");
    });

    source.addEventListener("done", (event) => {
      const payload = JSON.parse((event as MessageEvent<string>).data) as { status: WorkflowStatus };
      setFinalStatus(payload.status);
      setConnectionState("closed");
      source.close();
    });

    source.addEventListener("workflow_error", () => {
      setConnectionState("error");
      source.close();
    });

    return () => {
      source.close();
    };
  }, [organizationId, workflowId, active]);

  return { entries, connectionState: active ? connectionState : "closed", finalStatus };
}
