// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatBubble, type ChatMessage } from "@/features/assistant/chat-bubble";
import type { AgentExecuteResponse } from "@/types/api";

const ORG_ID = "org-1";

function agentResponse(overrides: Partial<AgentExecuteResponse> = {}): AgentExecuteResponse {
  return {
    workflow_id: "wf-1",
    workflow_status: "completed",
    decision_kind: "tool_call",
    handled_by_agent: "scheduling",
    message: "Booked.",
    tool_name: "book_appointment",
    tool_result_code: "appointment_booked",
    tool_result_data: { appointment_id: "appt-1" },
    ...overrides,
  };
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("ChatBubble", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders a user message as plain text, revealed immediately", () => {
    const message: ChatMessage = { id: "1", role: "user", text: "Book me an appointment" };
    render(<ChatBubble message={message} organizationId={ORG_ID} />);

    expect(screen.getByText("Book me an appointment")).toBeInTheDocument();
  });

  it("reveals an assistant message progressively, then shows the tool card", () => {
    const message: ChatMessage = {
      id: "2",
      role: "assistant",
      text: "Booked your appointment.",
      response: agentResponse(),
    };
    render(<ChatBubble message={message} organizationId={ORG_ID} />);

    expect(screen.queryByText("book_appointment()")).not.toBeInTheDocument();

    advance(2000);

    expect(screen.getByText("Booked your appointment.")).toBeInTheDocument();
    expect(screen.getByText("book_appointment()")).toBeInTheDocument();
    expect(screen.getByText("Appointment booked")).toBeInTheDocument();
  });

  it("colors the tool card by workflow_status, not decision_kind alone", () => {
    const message: ChatMessage = {
      id: "3",
      role: "assistant",
      text: "Could not book.",
      response: agentResponse({ workflow_status: "failed", tool_result_code: "no_availability" }),
    };
    render(<ChatBubble message={message} organizationId={ORG_ID} />);

    advance(2000);

    expect(screen.getByText("No availability")).toBeInTheDocument();
  });

  it("does not render a tool card when the response has no tool_name", () => {
    const message: ChatMessage = {
      id: "4",
      role: "assistant",
      text: "Here is a summary.",
      response: agentResponse({
        tool_name: null,
        tool_result_code: null,
        tool_result_data: null,
        decision_kind: "safe_response",
      }),
    };
    render(<ChatBubble message={message} organizationId={ORG_ID} />);

    advance(2000);

    expect(screen.queryByText("book_appointment()")).not.toBeInTheDocument();
    expect(screen.getByText("Safe response")).toBeInTheDocument();
  });

  it("shows a copy button once the assistant message is fully revealed", () => {
    const message: ChatMessage = { id: "5", role: "assistant", text: "Done." };
    render(<ChatBubble message={message} organizationId={ORG_ID} />);

    advance(2000);

    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });
});
