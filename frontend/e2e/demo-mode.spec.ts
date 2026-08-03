import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

/** See `ai-assistant.spec.ts` for why this conditional exists. */
const LLM_CONFIGURED = Boolean(process.env.ANTHROPIC_API_KEY || process.env.GROQ_API_KEY);

test.describe("Demo Mode", () => {
  test("renders all six scenario cards, none run yet", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/demo`);

    await expect(page.getByRole("heading", { name: "Demo Mode" })).toBeVisible();
    for (const title of [
      "Book an appointment",
      "Reschedule an appointment",
      "Cancel an appointment",
      "Document collection",
      "Administrative routing",
      "Ambiguous request",
    ]) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }

    await expect(page.getByLabel("Zoom in")).not.toBeVisible();
  });

  test("clicking 'Run scenario' shows a pending state", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/demo`);

    const firstCardButton = page.getByRole("button", { name: "Run scenario" }).first();
    await firstCardButton.click();

    // Disabled + relabeled while starting/running — asserted without
    // depending on which exact intermediate label lands first (both are
    // valid and can race depending on backend response speed).
    await expect(firstCardButton).toBeDisabled();
  });

  test.skip(!LLM_CONFIGURED, "requires ANTHROPIC_API_KEY or GROQ_API_KEY to be configured");
  test("running a scenario reveals the real workflow graph and a link to it", async ({
    page,
  }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/demo`);

    await page.getByRole("button", { name: "Run scenario" }).first().click();

    await expect(page.getByRole("link", { name: "View full workflow" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByLabel("Zoom in")).toBeVisible();
  });
});
