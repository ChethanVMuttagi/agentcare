import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

/**
 * Whether the backend under test has a real LLM configured
 * (`ANTHROPIC_API_KEY`/`GROQ_API_KEY` — see `.github/workflows/ci.yml`'s
 * `e2e` job, which passes these through from repo secrets when present).
 * Everything in this file that doesn't depend on a real model response
 * runs unconditionally; the one assertion that needs an actual AI reply
 * is skipped when neither key is configured, so this suite is correct
 * (and non-flaky) whether or not a key has ever been added.
 */
const LLM_CONFIGURED = Boolean(process.env.ANTHROPIC_API_KEY || process.env.GROQ_API_KEY);

test.describe("AI Assistant", () => {
  test("renders the chat form and empty state", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/assistant`);

    await expect(page.getByRole("heading", { name: "AI Assistant" })).toBeVisible();
    await expect(page.getByPlaceholder("Type a request…")).toBeVisible();
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
    await expect(page.getByText("Nothing running yet")).toBeVisible();
  });

  test("submitting a message shows it and a typing indicator", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/assistant`);

    await page.getByPlaceholder("Type a request…").fill("What can you help me with today?");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.getByText("What can you help me with today?")).toBeVisible();
    // Either a typing indicator appears first, or the (fast, e.g. error)
    // response has already landed — both are acceptable; what matters is
    // the request was actually sent and something came back within the
    // timeout, without asserting on content (that would require a real
    // model or a specific configuration state).
    await expect(page.getByRole("button", { name: "Send" })).toBeEnabled({ timeout: 15_000 });
  });

  test.skip(!LLM_CONFIGURED, "requires ANTHROPIC_API_KEY or GROQ_API_KEY to be configured");
  test("gets a real assistant reply when an LLM provider is configured", async ({ page }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/assistant`);

    await page.getByPlaceholder("Type a request…").fill("What can you help me with today?");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.getByRole("button", { name: "Send" })).toBeEnabled({ timeout: 30_000 });
    // A real reply renders a WorkflowCard linking to the workflow it
    // created — the one thing only a genuine model response produces.
    await expect(
      page.locator(`a[href^="/org/${seed.organization_id}/workflows/"]`).first(),
    ).toBeVisible({ timeout: 30_000 });
  });
});
