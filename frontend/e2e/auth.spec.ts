import { test, expect } from "@playwright/test";

import { loginAsSeededAdmin } from "./login";
import { E2E_ADMIN_EMAIL, E2E_ADMIN_ROLE } from "./seed-constants";
import { readSeedData } from "./seed-data";

// This spec exercises the login form itself, so it opts out of the
// pre-authenticated `storageState` the `chromium` project otherwise
// applies (see `playwright.config.ts` / `auth.setup.ts`).
test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Authentication", () => {
  test("logs in with valid credentials and reaches the dashboard", async ({ page }) => {
    const seed = readSeedData();

    await loginAsSeededAdmin(page, seed);

    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("rejects an invalid password with an inline error", async ({ page }) => {
    const seed = readSeedData();

    await page.goto("/login");
    const organizationId = page.getByLabel("Organization ID");

    // Same hydration race as `loginAsSeededAdmin` handles — see the
    // comment there for why filling has to be confirmed rather than
    // assumed.
    await expect(async () => {
      await page.getByLabel("Email").fill(E2E_ADMIN_EMAIL);
      await page.getByLabel("Password").fill("definitely-the-wrong-password");
      await organizationId.fill(seed.organization_id);
      await page.getByLabel("Role").selectOption(E2E_ADMIN_ROLE);
      await expect(organizationId).toHaveValue(seed.organization_id, { timeout: 1_000 });
    }).toPass({ timeout: 20_000 });

    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Invalid email or password.")).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });

  test("logs out and can no longer reach an org-scoped page", async ({ page }) => {
    const seed = readSeedData();

    await loginAsSeededAdmin(page, seed);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);

    await page.goto(`/org/${seed.organization_id}/dashboard`);
    await expect(page).toHaveURL(/\/login$/);
  });
});
