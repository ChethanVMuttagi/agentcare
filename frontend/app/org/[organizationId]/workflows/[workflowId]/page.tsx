import type { Metadata } from "next";

import { StatusBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { CancelWorkflowButton } from "@/features/workflows/cancel-workflow-button";
import { LiveExecutionPanel } from "@/features/workflows/live-execution-panel";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { getWorkflowTimeline, listWorkflowSteps } from "@/services/workflows";

export const metadata: Metadata = { title: "Workflow Trace — AgentCare" };

const CANCELLABLE_STATUSES = new Set(["pending", "running", "waiting"]);

export default async function WorkflowDetailPage({
  params,
}: {
  params: Promise<{ organizationId: string; workflowId: string }>;
}) {
  const { organizationId, workflowId } = await params;
  const session = await requireSession();

  const [result, stepsResult] = await Promise.all([
    settle(getWorkflowTimeline({ token: session.token, organizationId, workflowId })),
    settle(listWorkflowSteps({ token: session.token, organizationId, workflowId })),
  ]);

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load workflow" />;
  }

  const timeline = result.data;
  // Enriches the Timeline with exact per-step duration/attempt counts —
  // not fatal if it fails, since `Timeline` derives what it can from the
  // (already-loaded) event timestamps alone.
  const steps = stepsResult.success ? stepsResult.data.steps : undefined;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          AI Trace
        </h1>
        <StatusBadge status={timeline.status} />
      </div>
      <p className="font-mono text-xs text-slate-400">{workflowId}</p>

      {CANCELLABLE_STATUSES.has(timeline.status) ? (
        <CancelWorkflowButton organizationId={organizationId} workflowId={workflowId} />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Agent execution</CardTitle>
        </CardHeader>
        <CardContent>
          <LiveExecutionPanel
            organizationId={organizationId}
            workflowId={workflowId}
            initialEntries={timeline.entries}
            initialStatus={timeline.status}
            initialSteps={steps}
          />
        </CardContent>
      </Card>
    </div>
  );
}
