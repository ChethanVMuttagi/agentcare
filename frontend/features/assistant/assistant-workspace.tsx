"use client";

import { useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { AssistantChat } from "@/features/assistant/assistant-chat";
import { LiveExecutionPanel } from "@/features/workflows/live-execution-panel";

/**
 * Pairs the AI Assistant chat with a live view of whatever workflow the
 * assistant is currently executing (Milestone B's flagship "watch the
 * agents work" demo) — `AssistantChat` knows nothing about
 * `LiveExecutionPanel`; it only reports the latest `workflow_id` via
 * `onWorkflowUpdate`, kept here as the one piece of state this
 * composition needs.
 */
export function AssistantWorkspace({
  organizationId,
  defaultPatientId,
}: {
  organizationId: string;
  defaultPatientId?: string;
}) {
  const [activeWorkflowId, setActiveWorkflowId] = useState<string | null>(null);

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <AssistantChat
        organizationId={organizationId}
        defaultPatientId={defaultPatientId}
        onWorkflowUpdate={setActiveWorkflowId}
      />
      <Card>
        <CardHeader>
          <CardTitle>Live agent execution</CardTitle>
        </CardHeader>
        <CardContent>
          {activeWorkflowId ? (
            <LiveExecutionPanel
              organizationId={organizationId}
              workflowId={activeWorkflowId}
              initialEntries={[]}
              initialStatus="pending"
            />
          ) : (
            <EmptyState
              title="Nothing running yet"
              description="Send a message to watch the agent orchestration happen live — handoffs, tool calls, and decisions appear here as they happen."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
