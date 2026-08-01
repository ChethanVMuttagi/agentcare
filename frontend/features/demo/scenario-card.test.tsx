// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ScenarioCard } from "@/features/demo/scenario-card";

// `actions.ts` is a real Server Action module ("use server") that
// imports `lib/require-session.ts` -> `"server-only"`. Vitest has no
// RSC-aware bundler split the way Next's real build does, so rendering
// `ScenarioCard` (a Client Component) without mocking this would pull
// the WHOLE server-only import chain into the "client" test bundle and
// trip `server-only`'s own safety guard — correctly, just not useful
// for a component-level render test, which only needs a valid function
// reference to hand `useActionState`, never to actually call it.
vi.mock("@/features/demo/actions", () => ({
  runDemoScenarioAction: vi.fn(),
}));

describe("ScenarioCard", () => {
  it("renders the scenario's title, description, and quoted request text", () => {
    render(
      <ScenarioCard
        organizationId="org-1"
        title="Book an appointment"
        description="Coordinator hands off to Scheduling."
        requestType="appointment_booking"
        requestText="Book a follow-up appointment."
      />,
    );

    expect(screen.getByText("Book an appointment")).toBeInTheDocument();
    expect(screen.getByText("Coordinator hands off to Scheduling.")).toBeInTheDocument();
    expect(screen.getByText(/Book a follow-up appointment\./)).toBeInTheDocument();
  });

  it("shows an enabled 'Run scenario' button before any run has started", () => {
    render(
      <ScenarioCard
        organizationId="org-1"
        title="Book an appointment"
        description="Description."
        requestType="appointment_booking"
        requestText="Book a follow-up appointment."
      />,
    );

    const button = screen.getByRole("button", { name: "Run scenario" });
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });

  it("shows no workflow graph/timeline before any run has started", () => {
    render(
      <ScenarioCard
        organizationId="org-1"
        title="Book an appointment"
        description="Description."
        requestType="appointment_booking"
        requestText="Book a follow-up appointment."
      />,
    );

    expect(screen.queryByLabelText("Zoom in")).not.toBeInTheDocument();
  });
});
