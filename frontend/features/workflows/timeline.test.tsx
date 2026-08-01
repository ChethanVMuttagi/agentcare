// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Timeline } from "@/features/workflows/timeline";
import type { WorkflowStepResponse, WorkflowTimelineEntry } from "@/types/api";

function entry(overrides: Partial<WorkflowTimelineEntry>): WorkflowTimelineEntry {
  return {
    sequence: 1,
    event_type: "workflow_created",
    actor_type: "user",
    actor_identifier: "user-1",
    safe_metadata: null,
    created_at: "2026-01-01T00:00:00.000Z",
    workflow_step_id: null,
    step_sequence_number: null,
    step_type: null,
    step_agent_name: null,
    ...overrides,
  };
}

function step(overrides: Partial<WorkflowStepResponse> = {}): WorkflowStepResponse {
  return {
    id: "step-1",
    organization_id: "org-1",
    workflow_run_id: "run-1",
    sequence_number: 1,
    step_type: "agent_decision",
    status: "completed",
    agent_name: "scheduling",
    tool_name: "book_appointment",
    attempt_count: 1,
    failure_code: null,
    failure_message_safe: null,
    started_at: "2026-01-01T00:00:00.000Z",
    completed_at: "2026-01-01T00:00:02.000Z",
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:02.000Z",
    ...overrides,
  };
}

describe("Timeline", () => {
  it("shows an empty state when there are no entries", () => {
    render(<Timeline entries={[]} />);
    expect(screen.getByText("No events yet")).toBeInTheDocument();
  });

  it("renders one list item per entry, humanizing the event type", () => {
    render(
      <Timeline
        entries={[
          entry({ sequence: 1, event_type: "workflow_created" }),
          entry({ sequence: 2, event_type: "workflow_started" }),
        ]}
      />,
    );
    expect(screen.getByText("Workflow created")).toBeInTheDocument();
    expect(screen.getByText("Workflow started")).toBeInTheDocument();
  });

  it("shows a duration label for a terminal step event when steps are provided", () => {
    render(
      <Timeline
        entries={[
          entry({
            sequence: 1,
            event_type: "step_completed",
            workflow_step_id: "step-1",
            step_agent_name: "scheduling",
          }),
        ]}
        steps={[step({ id: "step-1" })]}
      />,
    );
    expect(screen.getByText("2.0s")).toBeInTheDocument();
  });

  it("shows a 'Completed' badge for a *_completed event and 'Failed' for a *_failed one", () => {
    render(
      <Timeline
        entries={[
          entry({ sequence: 1, event_type: "step_completed", workflow_step_id: "a" }),
          entry({ sequence: 2, event_type: "step_failed", workflow_step_id: "b" }),
        ]}
      />,
    );
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders a distinct tool card for tool_invoked events", () => {
    render(
      <Timeline
        entries={[
          entry({
            sequence: 1,
            event_type: "tool_invoked",
            safe_metadata: { tool_name: "check_availability" },
            workflow_step_id: "step-1",
            step_agent_name: "scheduling",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Invoked check_availability()")).toBeInTheDocument();
  });

  it("shows a retry badge when a step has been resumed", () => {
    render(
      <Timeline
        entries={[
          entry({ sequence: 1, event_type: "step_resumed", workflow_step_id: "step-1" }),
        ]}
      />,
    );
    expect(screen.getByText("Resumed 1x")).toBeInTheDocument();
  });

  it("renders an expandable payload for entries with metadata but no bespoke summary", () => {
    render(
      <Timeline
        entries={[
          entry({
            sequence: 1,
            event_type: "workflow_created",
            safe_metadata: { correlation_id: "abc123" },
          }),
        ]}
      />,
    );
    const disclosure = screen.getByText("Show payload");
    expect(disclosure.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/correlation_id/)).toBeInTheDocument();
  });
});
