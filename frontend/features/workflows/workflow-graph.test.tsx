// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkflowGraph } from "@/features/workflows/workflow-graph";
import type { WorkflowStreamEntry } from "@/types/api";

function entry(overrides: Partial<WorkflowStreamEntry>): WorkflowStreamEntry {
  return {
    sequence: 1,
    event_type: "workflow_created",
    actor_type: "system",
    actor_identifier: "system",
    safe_metadata: null,
    created_at: "2026-01-01T00:00:00.000Z",
    workflow_step_id: null,
    step_sequence_number: null,
    step_type: null,
    step_agent_name: null,
    ...overrides,
  };
}

describe("WorkflowGraph", () => {
  it("with no entries, only the Coordinator is styled as visited", () => {
    render(<WorkflowGraph entries={[]} isRunning={false} />);

    const coordinatorNode = screen.getByText("Coordinator").closest("div");
    const schedulingNode = screen.getByText("Scheduling").closest("div");
    expect(coordinatorNode).toHaveClass("bg-slate-900");
    expect(schedulingNode).toHaveClass("border-dashed");
  });

  it("shows a specialist node and its tool badge once handed off to", () => {
    const entries: WorkflowStreamEntry[] = [
      entry({
        sequence: 1,
        event_type: "agent_handoff",
        safe_metadata: { from_agent: "coordinator", to_agent: "scheduling" },
      }),
      entry({
        sequence: 2,
        event_type: "tool_invoked",
        step_agent_name: "scheduling",
        safe_metadata: { tool_name: "check_availability" },
      }),
    ];
    render(<WorkflowGraph entries={entries} isRunning={false} />);

    expect(screen.getByText("Scheduling")).toBeInTheDocument();
    expect(screen.getByText("check_availability()")).toBeInTheDocument();
  });

  it("marks a specialist's node as failed when its step failed", () => {
    const entries: WorkflowStreamEntry[] = [
      entry({
        sequence: 1,
        event_type: "agent_handoff",
        safe_metadata: { from_agent: "coordinator", to_agent: "scheduling" },
      }),
      entry({
        sequence: 2,
        event_type: "step_failed",
        step_agent_name: "scheduling",
        workflow_step_id: "step-1",
      }),
    ];
    render(<WorkflowGraph entries={entries} isRunning={false} />);

    const node = screen.getByText("Scheduling").closest("div");
    expect(node).toHaveClass("border-red-500");
  });

  it("renders pan/zoom controls (zoom in, zoom out, reset)", () => {
    render(<WorkflowGraph entries={[]} isRunning={false} />);
    expect(screen.getByLabelText("Zoom in")).toBeInTheDocument();
    expect(screen.getByLabelText("Zoom out")).toBeInTheDocument();
    expect(screen.getByLabelText("Reset view")).toBeInTheDocument();
  });

  it("shows a duration label once a visited agent's step has completed", () => {
    const entries: WorkflowStreamEntry[] = [
      entry({
        sequence: 1,
        event_type: "step_started",
        step_agent_name: "coordinator",
        workflow_step_id: "step-1",
        created_at: "2026-01-01T00:00:00.000Z",
      }),
      entry({
        sequence: 2,
        event_type: "step_completed",
        step_agent_name: "coordinator",
        workflow_step_id: "step-1",
        created_at: "2026-01-01T00:00:01.500Z",
      }),
    ];
    render(<WorkflowGraph entries={entries} isRunning={false} />);

    expect(screen.getByText("1.5s")).toBeInTheDocument();
  });
});
