import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests (Sprint 3) covering: Authentication, Dashboard,
 * Patients, Appointments, Workflow execution, AI Assistant, Demo Mode.
 *
 * `globalSetup` applies migrations and seeds deterministic data via
 * `backend/scripts/seed_e2e.py` (see `e2e/global-setup.ts`) BEFORE the
 * `webServer`s below are health-checked — so by the time any spec runs,
 * both servers are up and the database already has the fixed org/admin/
 * patient/practitioner/appointment/workflow the specs assert against.
 *
 * The `setup` project (`e2e/auth.setup.ts`) logs in once via the real UI
 * and saves cookies to `e2e/.auth/user.json`; the `chromium` project
 * reuses that state so specs start already authenticated. `auth.spec.ts`
 * itself opts out of the saved state (see that file) since it needs to
 * exercise the login form directly.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  // Playwright's 5s default for `expect` assumes assertions settle at
  // client-render speed. Most here don't: submitting a form runs a Next
  // Server Action that calls the FastAPI backend, hits PostgreSQL, and
  // then redirects, and with several workers in parallel that round trip
  // was observed still mid-flight ("Booking…") when a 5s assertion
  // expired. These are ceilings, not waits — every assertion still
  // resolves as soon as it's true — so raising them removes a whole class
  // of load-dependent flake at no cost to a passing run's duration.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "setup",
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        storageState: path.join(__dirname, "e2e", ".auth", "user.json"),
      },
      dependencies: ["setup"],
    },
  ],
  webServer: [
    {
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: path.join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        ...process.env,
        // `E2E_SEED_PYTHON` may point `global-setup.ts` at a venv
        // interpreter locally; the server itself always starts via
        // whatever `python` resolves to on PATH (same as CI's backend job).
        //
        // The production default for this endpoint is 5/minute (see
        // `Settings.rate_limit_auth_token`), but this suite performs
        // several genuine logins within one minute — the `setup` project
        // plus all three `auth.spec.ts` cases, before any retry — so the
        // real limit would throttle the suite against itself and fail a
        // spec for a reason unrelated to what it asserts. Raised only for
        // this ephemeral test server; the limiter's own behavior is
        // covered properly by the backend suite
        // (`tests/api/test_rate_limiting.py` and
        // `test_rate_limiting_concurrency.py`), never here.
        RATE_LIMIT_AUTH_TOKEN: "1000/minute",
      } as Record<string, string>,
    },
    {
      command: "npm run build && npm run start -- --port 3000",
      cwd: __dirname,
      url: "http://localhost:3000/login",
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  ],
});
