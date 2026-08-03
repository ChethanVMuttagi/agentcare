import { test, expect } from "@playwright/test";

import { readSeedData } from "./seed-data";

test.describe("Patients", () => {
  test("the seeded patient appears in the list", async ({ page }) => {
    const seed = readSeedData();

    await page.goto(`/org/${seed.organization_id}/patients`);

    await expect(page.getByRole("heading", { name: "Patients" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Jordan E2E-Patient" })).toBeVisible();
    await expect(page.getByText("PN-E2E-001")).toBeVisible();
  });

  test("registers a new patient and lands on their detail page", async ({ page }) => {
    const seed = readSeedData();
    const uniqueSuffix = Date.now().toString(36);
    const patientNumber = `PN-E2E-NEW-${uniqueSuffix}`;

    await page.goto(`/org/${seed.organization_id}/patients`);
    await page.getByRole("link", { name: "Register patient" }).click();
    await expect(page).toHaveURL(new RegExp(`/org/${seed.organization_id}/patients/new$`));

    await page.getByLabel("First name").fill("Playwright");
    await page.getByLabel("Last name").fill("TestPatient");
    await page.getByLabel("Patient number").fill(patientNumber);
    await page.getByLabel("Date of birth").fill("1985-06-15");
    await page.getByRole("button", { name: "Register patient" }).click();

    await expect(page).toHaveURL(
      new RegExp(`/org/${seed.organization_id}/patients/[0-9a-f-]+$`),
    );
    await expect(page.getByText("Playwright TestPatient")).toBeVisible();
  });
});
