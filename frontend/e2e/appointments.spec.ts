import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

test.describe("Appointments", () => {
  test("the seeded appointment appears in the list", async ({ page }) => {
    const seed = readSeedData();

    await page.goto(`/org/${seed.organization_id}/appointments`);

    await expect(page.getByRole("heading", { name: "Appointments" })).toBeVisible();
    await expect(page.getByText("No appointments found")).not.toBeVisible();
  });

  test("books a new appointment against the seeded practitioner", async ({ page }) => {
    const seed = readSeedData();

    // The booking form's `patient_id` is a hidden input populated only
    // from a `?patientId=` query param (there is no in-form patient
    // picker) — navigate the same way the patient detail page's own
    // "Book appointment" link does, rather than the org-wide appointments
    // list's unscoped link.
    await page.goto(
      `/org/${seed.organization_id}/appointments/new?patientId=${seed.patient_id}`,
    );

    await page.getByLabel("Department").selectOption({ label: "E2E General Medicine" });
    await page.getByLabel("Practitioner").selectOption({ label: "Taylor E2E-Test" });

    // The seeded practitioner is available every weekday, all day — pick
    // the next Monday so a slot is always found regardless of today.
    const nextMonday = new Date();
    const daysUntilMonday = ((1 - nextMonday.getDay() + 7) % 7) + 14; // 2 weeks out, a Monday
    nextMonday.setDate(nextMonday.getDate() + daysUntilMonday);
    const isoDate = nextMonday.toISOString().slice(0, 10);
    await page.getByLabel("Date").fill(isoDate);

    await page.getByRole("button", { name: "Find available times" }).click();
    await page.getByRole("button", { name: /^\d{1,2}:\d{2}\s?(AM|PM)$/i }).first().click();

    await page.getByRole("button", { name: "Book appointment" }).click();

    await expect(page).toHaveURL(
      new RegExp(`/org/${seed.organization_id}/appointments/[0-9a-f-]+$`),
    );
  });
});
