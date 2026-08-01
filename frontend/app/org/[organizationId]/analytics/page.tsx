import type { Metadata } from "next";

import { BreakdownBars } from "@/components/ui/bar-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { StatCard } from "@/components/ui/stat-card";
import { TrendChart } from "@/components/ui/trend-chart";
import {
  computeAverageDurationMs,
  computeDailyTrend,
  computeSuccessRate,
  computeToolUsage,
} from "@/features/analytics/compute";
import { formatDuration } from "@/lib/duration";
import { requireSession } from "@/lib/require-session";
import { formatDate, formatDateTime, settle } from "@/lib/utils";
import { getAnalyticsSummary } from "@/services/analytics";
import { getWorkflowTimeline, listWorkflows } from "@/services/workflows";
import type { WorkflowTimelineResponse } from "@/types/api";

export const metadata: Metadata = { title: "Analytics — AgentCare" };

/** How many of the most recent workflow runs feed average duration and
 * the daily trend — both derived purely from `WorkflowRunResponse`
 * fields already on `listWorkflows`, so this is a single extra request. */
const RECENT_RUNS_SAMPLE_SIZE = 100;

/** Tool usage has no backend aggregate by tool name (only a total —
 * `AnalyticsSummaryResponse.tool_invocations_total`), so it costs one
 * `getWorkflowTimeline` request per sampled run. Kept smaller than the
 * run sample above to bound how many of those requests a single page
 * load fires. */
const TOOL_USAGE_SAMPLE_SIZE = 30;

export default async function AnalyticsPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;
  const session = await requireSession();

  const [result, recentRunsResult] = await Promise.all([
    settle(getAnalyticsSummary({ token: session.token, organizationId })),
    settle(listWorkflows({ token: session.token, organizationId, limit: RECENT_RUNS_SAMPLE_SIZE })),
  ]);

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load analytics" />;
  }

  const summary = result.data;
  const recentRuns = recentRunsResult.success ? recentRunsResult.data.workflows : [];

  const toolUsageSample = recentRuns.slice(0, TOOL_USAGE_SAMPLE_SIZE);
  const toolTimelineResults = await Promise.all(
    toolUsageSample.map((run) =>
      settle(getWorkflowTimeline({ token: session.token, organizationId, workflowId: run.id })),
    ),
  );
  const toolTimelines: WorkflowTimelineResponse[] = [];
  for (const timelineResult of toolTimelineResults) {
    if (timelineResult.success) toolTimelines.push(timelineResult.data);
  }

  const successRate = computeSuccessRate(summary.workflows_by_status);
  const averageDurationMs = recentRunsResult.success ? computeAverageDurationMs(recentRuns) : null;
  const dailyTrend = computeDailyTrend(recentRuns);
  const toolUsage = computeToolUsage(toolTimelines);

  const trendData = dailyTrend.map((point) => ({
    label: formatDate(point.date),
    value: point.count,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Analytics</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Organization-wide activity as of {formatDateTime(summary.generated_at)}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Workflows" value={summary.workflows_total} />
        <StatCard
          label="Success rate"
          value={successRate === null ? "—" : `${Math.round(successRate * 100)}%`}
        />
        <StatCard label="Avg. duration" value={formatDuration(averageDurationMs)} />
        <StatCard label="Appointments" value={summary.appointments_total} />
        <StatCard label="Approvals" value={summary.approvals_total} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Daily workflow volume</CardTitle>
          </CardHeader>
          <CardContent>
            <TrendChart data={trendData} tone="info" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tool usage</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs text-slate-400">
              Sampled from the {toolUsageSample.length} most recent runs, not all-time history.
            </p>
            <BreakdownBars data={toolUsage} tone="success" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Workflows by status</CardTitle>
          </CardHeader>
          <CardContent>
            <BreakdownBars data={summary.workflows_by_status} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Workflows by request type</CardTitle>
          </CardHeader>
          <CardContent>
            <BreakdownBars data={summary.workflows_by_request_type} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Appointments by status</CardTitle>
          </CardHeader>
          <CardContent>
            <BreakdownBars data={summary.appointments_by_status} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Approvals by status</CardTitle>
          </CardHeader>
          <CardContent>
            <BreakdownBars data={summary.approvals_by_status} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Documents by status</CardTitle>
          </CardHeader>
          <CardContent>
            <BreakdownBars data={summary.documents_by_status} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Agent activity</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-slate-500 dark:text-slate-400">Tool invocations</p>
                <p className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                  {summary.tool_invocations_total}
                </p>
              </div>
              <div>
                <p className="text-slate-500 dark:text-slate-400">Agent handoffs</p>
                <p className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                  {summary.agent_handoffs_total}
                </p>
              </div>
            </div>
            <BreakdownBars data={summary.agent_handoffs_by_target} tone="info" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
