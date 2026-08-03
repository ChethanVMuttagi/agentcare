import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

test.describe("Workflow execution", () => {
  test("shows the seeded workflow's real agent handoff, tool call, and completion", async ({
    page,
  }) => {
    const seed = readSeedData();

    await page.goto(`/org/${seed.organization_id}/workflows/${seed.workflow_run_id}`);

    await expect(page.getByRole("heading", { name: "AI Trace" })).toBeVisible();
    await expect(page.getByText(seed.workflow_run_id)).toBeVisible();

    // The seed script drives a real Coordinator -> Scheduling handoff and
    // a real `book_appointment` tool call through the actual orchestrator
    // (via a FakeLLMProvider — no LLM key involved), so the graph and
    // timeline reflect genuine, deterministic data.
    // Exact + `.first()`: "Coordinator"/"Scheduling" also appear lowercased
    // inside timeline actor badges and timestamps (e.g. "coordinator ·
    // Aug 1, 2026…"), so a loose text match is ambiguous — the graph
    // node's own label is what this assertion cares about.
    await expect(page.getByText("Coordinator", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Scheduling", { exact: true }).first()).toBeVisible();

    await expect(page.getByText("No events yet")).not.toBeVisible();
    await expect(page.getByText("Workflow created")).toBeVisible();
    await expect(page.getByText("Workflow completed")).toBeVisible();
  });

  test("the pan/zoom controls on the workflow graph work", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/workflows/${seed.workflow_run_id}`);

    await expect(page.getByLabel("Zoom in")).toBeVisible();
    await page.getByLabel("Zoom in").click();
    await page.getByLabel("Reset view").click();
  });

  test("the workflows list includes the seeded run", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/workflows`);

    await expect(page.getByRole("heading", { name: "Workflows" })).toBeVisible();
    // The table shows request type/status/date, not the raw id — so
    // assert via the row's link href rather than visible text.
    await expect(
      page.locator(`a[href="/org/${seed.organization_id}/workflows/${seed.workflow_run_id}"]`),
    ).toBeVisible();
  });
});
