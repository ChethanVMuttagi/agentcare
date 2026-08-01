// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { Bot, CalendarClock } from "lucide-react";
import { describe, expect, it } from "vitest";

import { AGENT_ICONS, AgentIcon, EVENT_CATEGORY_ICONS, EventCategoryIcon } from "@/lib/agent-icons";

describe("AGENT_ICONS / EVENT_CATEGORY_ICONS", () => {
  it("maps each known agent name to a distinct icon", () => {
    expect(AGENT_ICONS.coordinator).toBe(Bot);
    expect(AGENT_ICONS.scheduling).toBe(CalendarClock);
    expect(new Set(Object.values(AGENT_ICONS)).size).toBe(Object.keys(AGENT_ICONS).length);
  });

  it("maps every EventCategory to an icon", () => {
    for (const category of ["agent", "tool", "approval", "failure", "lifecycle"] as const) {
      expect(EVENT_CATEGORY_ICONS[category]).toBeDefined();
    }
  });
});

describe("AgentIcon", () => {
  it("renders an svg for a known agent", () => {
    const { container } = render(<AgentIcon name="coordinator" className="h-4 w-4" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveClass("h-4", "w-4");
  });

  it("falls back to the Bot icon for an unknown agent name", () => {
    const known = render(<AgentIcon name="coordinator" />);
    const unknown = render(<AgentIcon name="totally-unknown-agent" />);
    expect(unknown.container.innerHTML).toBe(known.container.innerHTML);
  });
});

describe("EventCategoryIcon", () => {
  it("renders an svg for each category", () => {
    const { container } = render(<EventCategoryIcon category="tool" />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});
