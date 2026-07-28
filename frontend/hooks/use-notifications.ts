"use client";

import { useEffect, useRef, useState } from "react";

import type { ApprovalRequestListResponse, WorkflowRunListResponse } from "@/types/api";

export interface Notification {
  id: string;
  kind: "approval_pending" | "workflow_completed" | "workflow_failed";
  message: string;
  href: string;
  createdAt: string;
}

const POLL_INTERVAL_MS = 20_000;
const MAX_NOTIFICATIONS = 20;

/**
 * Notification Center (Milestone B) data source: polls the SAME
 * `approvals`/`workflows` list endpoints every other page already uses
 * (via the browser, through `/api/backend` — see that proxy route) and
 * diffs each poll against the previous one to surface just the DELTA —
 * a newly-appeared pending approval, or a workflow whose status just
 * became terminal — as a notification. No new backend endpoint exists
 * for this; it is a client-side view over data the API already exposes.
 *
 * The first poll only establishes a baseline (nothing "new" relative to
 * a baseline that didn't exist yet) — notifications start appearing from
 * the second poll onward, `POLL_INTERVAL_MS` later.
 */
export function useNotifications(
  organizationId: string,
  options?: { canSeeApprovals?: boolean },
): Notification[] {
  const canSeeApprovals = options?.canSeeApprovals ?? true;
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const seenApprovalIds = useRef<Set<string> | null>(null);
  const knownWorkflowStatus = useRef<Map<string, string> | null>(null);

  useEffect(() => {
    let cancelled = false;
    seenApprovalIds.current = null;
    knownWorkflowStatus.current = null;

    async function poll(): Promise<void> {
      const fresh: Notification[] = [];

      if (canSeeApprovals) {
        try {
          const response = await fetch(
            `/api/backend/organizations/${organizationId}/approvals?limit=10`,
            { cache: "no-store" },
          );
          if (response.ok) {
            const data = (await response.json()) as ApprovalRequestListResponse;
            if (seenApprovalIds.current === null) {
              seenApprovalIds.current = new Set(data.approvals.map((approval) => approval.id));
            } else {
              for (const approval of data.approvals) {
                if (seenApprovalIds.current.has(approval.id)) continue;
                seenApprovalIds.current.add(approval.id);
                fresh.push({
                  id: `approval-${approval.id}`,
                  kind: "approval_pending",
                  message: `New approval requested (${approval.approval_type.replace(/_/g, " ")})`,
                  href: `/org/${organizationId}/approvals/${approval.id}`,
                  createdAt: approval.created_at,
                });
              }
            }
          }
        } catch {
          // Best-effort — notifications never block the rest of the app.
        }
      }

      try {
        const response = await fetch(
          `/api/backend/organizations/${organizationId}/workflows?limit=10`,
          { cache: "no-store" },
        );
        if (response.ok) {
          const data = (await response.json()) as WorkflowRunListResponse;
          if (knownWorkflowStatus.current === null) {
            knownWorkflowStatus.current = new Map(
              data.workflows.map((workflow) => [workflow.id, workflow.status]),
            );
          } else {
            for (const workflow of data.workflows) {
              const previousStatus = knownWorkflowStatus.current.get(workflow.id);
              const justFinished =
                previousStatus &&
                previousStatus !== workflow.status &&
                (workflow.status === "completed" || workflow.status === "failed");
              if (justFinished) {
                fresh.push({
                  id: `workflow-${workflow.id}-${workflow.status}`,
                  kind: workflow.status === "completed" ? "workflow_completed" : "workflow_failed",
                  message: `Workflow ${workflow.status}: ${workflow.request_type.replace(/_/g, " ")}`,
                  href: `/org/${organizationId}/workflows/${workflow.id}`,
                  createdAt: workflow.updated_at,
                });
              }
              knownWorkflowStatus.current.set(workflow.id, workflow.status);
            }
          }
        }
      } catch {
        // Best-effort — same reasoning as above.
      }

      if (!cancelled && fresh.length > 0) {
        setNotifications((previous) => [...fresh, ...previous].slice(0, MAX_NOTIFICATIONS));
      }
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [organizationId, canSeeApprovals]);

  return notifications;
}
