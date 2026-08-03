import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

test.describe("Dashboard", () => {
  test("renders the seeded organization's summary cards", async ({ page }) => {
    const seed = readSeedData();

    await page.goto(`/org/${seed.organization_id}/dashboard`);

    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText(`Organization ${seed.organization_id}`)).toBeVisible();

    // The seed script books a real appointment and completes a real
    // workflow, so both summary cards should show real (non-empty) rows,
    // not their empty states.
    await expect(page.getByRole("heading", { name: "Recent Appointments" })).toBeVisible();
    await expect(page.getByText("No appointments yet")).not.toBeVisible();
  });

  test("the 'View all' appointments link navigates to the appointments list", async ({
    page,
  }) => {
    const seed = readSeedData();
    await page.goto(`/org/${seed.organization_id}/dashboard`);

    // `AppointmentsSummary` renders first in the dashboard grid (see
    // `app/org/[organizationId]/dashboard/page.tsx`), so its "View all"
    // link is the first one on the page.
    await page.getByRole("link", { name: "View all" }).first().click();

    await expect(page).toHaveURL(new RegExp(`/org/${seed.organization_id}/appointments$`));
  });
});
