import { describe, expect, it } from "vitest";

import {
  durationMs,
  formatDuration,
  stepDurationsById,
  stepDurationsFromTimeline,
} from "@/lib/duration";
import type { WorkflowStepResponse, WorkflowTimelineEntry } from "@/types/api";

describe("durationMs", () => {
  it("computes the elapsed milliseconds between two timestamps", () => {
    expect(durationMs("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:01.500Z")).toBe(1500);
  });

  it("returns null when start is missing", () => {
    expect(durationMs(null, "2026-01-01T00:00:01.000Z")).toBeNull();
  });

  it("returns null when end is missing", () => {
    expect(durationMs("2026-01-01T00:00:00.000Z", null)).toBeNull();
  });

  it("returns null for an unparseable timestamp", () => {
    expect(durationMs("not-a-date", "2026-01-01T00:00:01.000Z")).toBeNull();
  });

  it("never returns a negative duration, even if end precedes start", () => {
    expect(durationMs("2026-01-01T00:00:05.000Z", "2026-01-01T00:00:00.000Z")).toBe(0);
  });
});

describe("formatDuration", () => {
  it("renders an em dash for null", () => {
    expect(formatDuration(null)).toBe("—");
  });

  it("renders an em dash for undefined", () => {
    expect(formatDuration(undefined)).toBe("—");
  });

  it("renders sub-second durations in milliseconds", () => {
    expect(formatDuration(340)).toBe("340ms");
  });

  it("renders sub-minute durations in seconds, one decimal place", () => {
    expect(formatDuration(1200)).toBe("1.2s");
  });

  it("renders durations at or over a minute as minutes and seconds", () => {
    expect(formatDuration(125_000)).toBe("2m 5s");
  });
});

describe("stepDurationsById", () => {
  function makeStep(overrides: Partial<WorkflowStepResponse> = {}): WorkflowStepResponse {
    return {
      id: "step-1",
      organization_id: "org-1",
      workflow_run_id: "run-1",
      sequence_number: 1,
      step_type: "agent_decision",
      status: "completed",
      agent_name: "coordinator",
      tool_name: null,
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

  it("maps each step id to its own start/end duration", () => {
    const steps = [makeStep({ id: "a" }), makeStep({ id: "b", completed_at: null })];
    const map = stepDurationsById(steps);
    expect(map.get("a")).toBe(2000);
    expect(map.get("b")).toBeNull();
  });
});

describe("stepDurationsFromTimeline", () => {
  function makeEntry(overrides: Partial<WorkflowTimelineEntry>): WorkflowTimelineEntry {
    return {
      sequence: 1,
      event_type: "step_started",
      actor_type: "agent",
      actor_identifier: "coordinator",
      safe_metadata: null,
      created_at: "2026-01-01T00:00:00.000Z",
      workflow_step_id: "step-1",
      step_sequence_number: 1,
      step_type: "agent_decision",
      step_agent_name: "coordinator",
      ...overrides,
    };
  }

  it("derives duration from the first step_started and the terminal event", () => {
    const entries = [
      makeEntry({ event_type: "step_started", created_at: "2026-01-01T00:00:00.000Z" }),
      makeEntry({ event_type: "tool_invoked", created_at: "2026-01-01T00:00:01.000Z" }),
      makeEntry({ event_type: "step_completed", created_at: "2026-01-01T00:00:03.000Z" }),
    ];

    const map = stepDurationsFromTimeline(entries);

    expect(map.get("step-1")).toBe(3000);
  });

  it("ignores entries with no workflow_step_id", () => {
    const entries = [makeEntry({ workflow_step_id: null, event_type: "workflow_created" })];
    expect(stepDurationsFromTimeline(entries).size).toBe(0);
  });

  it("uses only the FIRST step_started when a step somehow has more than one", () => {
    const entries = [
      makeEntry({ event_type: "step_started", created_at: "2026-01-01T00:00:00.000Z" }),
      makeEntry({ event_type: "step_started", created_at: "2026-01-01T00:00:05.000Z" }),
      makeEntry({ event_type: "step_completed", created_at: "2026-01-01T00:00:10.000Z" }),
    ];

    expect(stepDurationsFromTimeline(entries).get("step-1")).toBe(10_000);
  });

  it("leaves duration unset (not zero) for a step with no terminal event yet", () => {
    const entries = [makeEntry({ event_type: "step_started" })];
    expect(stepDurationsFromTimeline(entries).get("step-1")).toBeNull();
  });
});
