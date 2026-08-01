"use client";

import { useMemo } from "react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Timeline } from "@/features/workflows/timeline";
import { WorkflowGraph } from "@/features/workflows/workflow-graph";
import { useWorkflowEventStream, type EventStreamConnectionState } from "@/hooks/use-workflow-event-stream";
import type { WorkflowStatus, WorkflowStepResponse, WorkflowStreamEntry } from "@/types/api";

const TERMINAL_STATUSES = new Set<WorkflowStatus>(["completed", "failed", "cancelled"]);

/**
 * The Live Agent Execution panel (Milestone B): a Workflow Graph +
 * AI Timeline pair that stay current in real time via
 * `useWorkflowEventStream`, merged with whatever `initialEntries` the
 * server already rendered (so the page has real content before any
 * client JS runs, and never re-fetches history it already has).
 *
 * Deliberately skips opening a connection at all once the workflow is
 * already terminal on the initial server render — nothing further will
 * ever arrive for it, so there is nothing to subscribe to.
 */
export function LiveExecutionPanel({
  organizationId,
  workflowId,
  initialEntries,
  initialStatus,
  initialSteps,
}: {
  organizationId: string;
  workflowId: string;
  initialEntries: WorkflowStreamEntry[];
  initialStatus: WorkflowStatus;
  /** The sibling step list, when the caller already fetched it (see
   * `services/workflows.ts#listWorkflowSteps`) — gives the Timeline exact
   * per-step duration and attempt counts instead of deriving what it can
   * from the event timestamps alone. */
  initialSteps?: WorkflowStepResponse[];
}) {
  const alreadyTerminal = TERMINAL_STATUSES.has(initialStatus);
  const {
    entries: liveEntries,
    connectionState,
    finalStatus,
  } = useWorkflowEventStream(organizationId, workflowId, { enabled: !alreadyTerminal });

  const entries = useMemo(() => {
    if (alreadyTerminal) return initialEntries;
    const bySequence = new Map<number, WorkflowStreamEntry>();
    for (const entry of initialEntries) bySequence.set(entry.sequence, entry);
    for (const entry of liveEntries) bySequence.set(entry.sequence, entry);
    return Array.from(bySequence.values()).sort((a, b) => a.sequence - b.sequence);
  }, [alreadyTerminal, initialEntries, liveEntries]);

  const currentStatus = alreadyTerminal ? initialStatus : (finalStatus ?? initialStatus);
  const isRunning = !TERMINAL_STATUSES.has(currentStatus);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={currentStatus} />
        {!alreadyTerminal ? <ConnectionBadge state={connectionState} /> : null}
      </div>
      <div className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
        <WorkflowGraph entries={entries} isRunning={isRunning} />
      </div>
      <Timeline entries={entries} steps={initialSteps} />
    </div>
  );
}

function ConnectionBadge({ state }: { state: EventStreamConnectionState }) {
  if (state === "open") return <Badge tone="success">Live</Badge>;
  if (state === "connecting") return <Badge tone="warning">Connecting…</Badge>;
  if (state === "error") return <Badge tone="danger">Reconnecting…</Badge>;
  return <Badge tone="neutral">Closed</Badge>;
}
