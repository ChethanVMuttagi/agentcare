import { describe, expect, it } from "vitest";

import {
  computeAverageDurationMs,
  computeDailyTrend,
  computeSuccessRate,
  computeToolUsage,
} from "@/features/analytics/compute";
import type {
  WorkflowRunResponse,
  WorkflowTimelineEntry,
  WorkflowTimelineResponse,
} from "@/types/api";

describe("computeSuccessRate", () => {
  it("computes completed / (completed + failed + cancelled)", () => {
    expect(computeSuccessRate({ completed: 8, failed: 1, cancelled: 1, pending: 5 })).toBe(0.8);
  });

  it("returns null when nothing has reached a decided outcome", () => {
    expect(computeSuccessRate({ pending: 3, running: 2 })).toBeNull();
  });

  it("treats missing keys as zero", () => {
    expect(computeSuccessRate({ completed: 2 })).toBe(1);
  });
});

describe("computeAverageDurationMs", () => {
  function run(overrides: Partial<WorkflowRunResponse> = {}): WorkflowRunResponse {
    return {
      id: "run-1",
      organization_id: "org-1",
      patient_id: null,
      initiated_by_user_id: "user-1",
      request_type: "administrative_routing",
      status: "completed",
      current_step: null,
      correlation_id: "corr-1",
      idempotency_key: null,
      failure_code: null,
      failure_message_safe: null,
      started_at: "2026-01-01T00:00:00.000Z",
      completed_at: "2026-01-01T00:00:10.000Z",
      created_at: "2026-01-01T00:00:00.000Z",
      updated_at: "2026-01-01T00:00:10.000Z",
      ...overrides,
    };
  }

  it("averages duration across runs that have both timestamps", () => {
    const runs = [
      run({ started_at: "2026-01-01T00:00:00.000Z", completed_at: "2026-01-01T00:00:10.000Z" }),
      run({ started_at: "2026-01-01T00:00:00.000Z", completed_at: "2026-01-01T00:00:20.000Z" }),
    ];
    expect(computeAverageDurationMs(runs)).toBe(15_000);
  });

  it("ignores runs missing either timestamp", () => {
    const runs = [
      run({ started_at: null }),
      run({ started_at: "2026-01-01T00:00:00.000Z", completed_at: "2026-01-01T00:00:10.000Z" }),
    ];
    expect(computeAverageDurationMs(runs)).toBe(10_000);
  });

  it("returns null when no run has both timestamps", () => {
    expect(computeAverageDurationMs([run({ completed_at: null })])).toBeNull();
  });

  it("returns null for an empty list", () => {
    expect(computeAverageDurationMs([])).toBeNull();
  });
});

describe("computeDailyTrend", () => {
  function run(createdAt: string): WorkflowRunResponse {
    return {
      id: `run-${createdAt}`,
      organization_id: "org-1",
      patient_id: null,
      initiated_by_user_id: "user-1",
      request_type: "administrative_routing",
      status: "completed",
      current_step: null,
      correlation_id: `corr-${createdAt}`,
      idempotency_key: null,
      failure_code: null,
      failure_message_safe: null,
      started_at: null,
      completed_at: null,
      created_at: createdAt,
      updated_at: createdAt,
    };
  }

  it("zero-fills every day in the trailing window, even with no data", () => {
    const points = computeDailyTrend([], 7);
    expect(points).toHaveLength(7);
    expect(points.every((point) => point.count === 0)).toBe(true);
  });

  it("returns points in chronological order ending today (UTC)", () => {
    const points = computeDailyTrend([], 3);
    const today = new Date().toISOString().slice(0, 10);
    expect(points[points.length - 1]!.date).toBe(today);
    expect(points[0]!.date < points[1]!.date).toBe(true);
  });

  it("counts workflows by their created_at calendar day", () => {
    const today = new Date().toISOString().slice(0, 10);
    const runs = [run(`${today}T01:00:00.000Z`), run(`${today}T23:00:00.000Z`)];
    const points = computeDailyTrend(runs, 1);
    expect(points).toEqual([{ date: today, count: 2 }]);
  });
});

describe("computeToolUsage", () => {
  function timeline(entries: WorkflowTimelineEntry[]): WorkflowTimelineResponse {
    return { workflow_id: "run-1", status: "completed", entries };
  }

  function toolInvokedEntry(toolName: string): WorkflowTimelineEntry {
    return {
      sequence: 1,
      event_type: "tool_invoked",
      actor_type: "tool",
      actor_identifier: toolName,
      safe_metadata: { tool_name: toolName },
      created_at: "2026-01-01T00:00:00.000Z",
      workflow_step_id: "step-1",
      step_sequence_number: 1,
      step_type: "tool_call",
      step_agent_name: "scheduling",
    };
  }

  it("counts tool invocations by tool name across timelines", () => {
    const timelines = [
      timeline([toolInvokedEntry("book_appointment"), toolInvokedEntry("check_availability")]),
      timeline([toolInvokedEntry("book_appointment")]),
    ];
    expect(computeToolUsage(timelines)).toEqual({
      book_appointment: 2,
      check_availability: 1,
    });
  });

  it("ignores non-tool_invoked events and entries missing tool_name", () => {
    const timelines = [
      timeline([
        { ...toolInvokedEntry("book_appointment"), event_type: "workflow_created" },
        { ...toolInvokedEntry("x"), safe_metadata: null },
      ]),
    ];
    expect(computeToolUsage(timelines)).toEqual({});
  });

  it("returns an empty object for no timelines", () => {
    expect(computeToolUsage([])).toEqual({});
  });
});
