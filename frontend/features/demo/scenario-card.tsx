"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useActionState } from "react";

import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { initialDemoScenarioState } from "@/features/demo/action-state";
import { runDemoScenarioAction } from "@/features/demo/actions";
import { Timeline } from "@/features/workflows/timeline";
import { WorkflowGraph } from "@/features/workflows/workflow-graph";
import { useStagedReveal } from "@/hooks/use-staged-reveal";
import type { WorkflowRequestType } from "@/types/api";

const REVEAL_DELAY_MS = 350;

export function ScenarioCard({
  organizationId,
  title,
  description,
  requestType,
  requestText,
}: {
  organizationId: string;
  title: string;
  description: string;
  requestType: WorkflowRequestType;
  requestText: string;
}) {
  const [state, formAction, pending] = useActionState(
    runDemoScenarioAction.bind(null, organizationId, requestType, requestText),
    initialDemoScenarioState,
  );

  const entries = state.result?.entries ?? [];
  const { revealed, done: revealDone } = useStagedReveal(entries, {
    enabled: entries.length > 0,
    delayMs: REVEAL_DELAY_MS,
  });
  const isPlaying = entries.length > 0 && !revealDone;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">{description}</p>
        <p className="rounded-md bg-slate-50 p-2 text-xs text-slate-500 italic dark:bg-slate-800 dark:text-slate-400">
          &ldquo;{requestText}&rdquo;
        </p>
        <form action={formAction}>
          <Button type="submit" disabled={pending || isPlaying}>
            {pending ? "Starting…" : isPlaying ? "Running…" : "Run scenario"}
          </Button>
        </form>
        {state.error ? <p className="text-sm text-red-600 dark:text-red-400">{state.error}</p> : null}

        {state.result ? (
          <div className="animate-fade-in-up space-y-3 border-t border-slate-200 pt-3 dark:border-slate-800">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={isPlaying ? "running" : state.result.status} />
              {revealDone ? (
                <Link
                  href={`/org/${organizationId}/workflows/${state.result.response.workflow_id}`}
                  className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
                >
                  View full workflow
                  <ArrowRight className="h-3 w-3" />
                </Link>
              ) : null}
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-950">
              <WorkflowGraph entries={revealed} isRunning={isPlaying} />
            </div>
            <Timeline entries={revealed} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
