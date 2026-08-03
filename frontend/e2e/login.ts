import { expect, type Page } from "@playwright/test";

import { E2E_ADMIN_EMAIL, E2E_ADMIN_ROLE } from "./seed-constants";
import type { SeedData } from "./seed-data";

/**
 * Log in through the real UI as the seeded E2E admin.
 *
 * Shared by `auth.setup.ts` (which saves the resulting `storageState` for
 * every other spec) and `auth.spec.ts` (which exercises the flow itself),
 * so the hydration handling below lives in exactly one place.
 *
 * `LoginForm` is a Client Component whose Organization ID input and Role
 * select are UNCONTROLLED, carrying React `defaultValue`s (see
 * `features/auth/login-form.tsx`). React resets both to those defaults
 * when hydration runs, which silently discards anything typed before that
 * point — and whether Playwright wins that race depends on machine load,
 * so it fails intermittently rather than never. Filling inside `toPass`
 * and asserting the values actually stuck closes the race without a
 * sleep: if hydration wipes the field, the assertion fails and the whole
 * block simply runs again against the now-hydrated form.
 */
export async function loginAsSeededAdmin(page: Page, seed: SeedData): Promise<void> {
  await page.goto("/login");

  const organizationId = page.getByLabel("Organization ID");
  const role = page.getByLabel("Role");

  await expect(async () => {
    await page.getByLabel("Email").fill(E2E_ADMIN_EMAIL);
    await page.getByLabel("Password").fill(seed.admin_password);
    await organizationId.fill(seed.organization_id);
    await role.selectOption(E2E_ADMIN_ROLE);

    await expect(organizationId).toHaveValue(seed.organization_id, { timeout: 1_000 });
    await expect(role).toHaveValue(E2E_ADMIN_ROLE, { timeout: 1_000 });
  }).toPass({ timeout: 20_000 });

  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`/org/${seed.organization_id}/dashboard$`));
}
