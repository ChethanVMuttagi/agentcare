import path from "node:path";

import { test as setup } from "@playwright/test";

import { loginAsSeededAdmin } from "./login";
import { readSeedData } from "./seed-data";

const AUTH_FILE = path.join(__dirname, ".auth", "user.json");

/**
 * Playwright "setup project" pattern: logs in once via the real UI, then
 * saves `storageState` (cookies) for every other spec's project to reuse
 * (see the `chromium` project's `dependencies: ["setup"]` in
 * `playwright.config.ts`) — avoids re-logging-in at the start of each spec.
 */
setup("authenticate as the seeded e2e admin", async ({ page }) => {
  await loginAsSeededAdmin(page, readSeedData());
  await page.context().storageState({ path: AUTH_FILE });
});
