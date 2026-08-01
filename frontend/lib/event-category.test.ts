import { describe, expect, it } from "vitest";

import { categorizeEvent, eventOutcome } from "@/lib/event-category";
import type { WorkflowEventType } from "@/types/api";

describe("categorizeEvent", () => {
  it("categorizes agent_handoff as agent", () => {
    expect(categorizeEvent("agent_handoff")).toBe("agent");
  });

  it("categorizes tool_invoked as tool", () => {
    expect(categorizeEvent("tool_invoked")).toBe("tool");
  });

  it.each<WorkflowEventType>(["approval_requested", "approval_granted", "approval_rejected"])(
    "categorizes %s as approval",
    (eventType) => {
      expect(categorizeEvent(eventType)).toBe("approval");
    },
  );

  it.each<WorkflowEventType>(["step_failed", "workflow_failed", "reminder_failed"])(
    "categorizes %s as failure",
    (eventType) => {
      expect(categorizeEvent(eventType)).toBe("failure");
    },
  );

  it.each<WorkflowEventType>(["workflow_created", "step_started", "step_resumed"])(
    "falls back to lifecycle for %s",
    (eventType) => {
      expect(categorizeEvent(eventType)).toBe("lifecycle");
    },
  );
});

describe("eventOutcome", () => {
  it.each<WorkflowEventType>(["step_completed", "workflow_completed"])(
    "maps %s to success",
    (eventType) => {
      expect(eventOutcome(eventType)).toBe("success");
    },
  );

  it.each<WorkflowEventType>(["step_failed", "workflow_failed"])(
    "maps %s to failure",
    (eventType) => {
      expect(eventOutcome(eventType)).toBe("failure");
    },
  );

  it("maps step_waiting to waiting", () => {
    expect(eventOutcome("step_waiting")).toBe("waiting");
  });

  it("maps step_resumed to resumed", () => {
    expect(eventOutcome("step_resumed")).toBe("resumed");
  });

  it("returns null for an event with no lifecycle outcome", () => {
    expect(eventOutcome("tool_invoked")).toBeNull();
    expect(eventOutcome("agent_handoff")).toBeNull();
  });
});
