"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { initialDemoScenarioState } from "@/features/demo/action-state";
import { runDemoScenarioAction } from "@/features/demo/actions";
import type { WorkflowRequestType } from "@/types/api";

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
          <Button type="submit" disabled={pending}>
            {pending ? "Starting…" : "Run scenario"}
          </Button>
        </form>
        {state.error ? (
          <p className="text-sm text-red-600 dark:text-red-400">{state.error}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
