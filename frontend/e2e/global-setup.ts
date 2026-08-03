import { execFileSync } from "node:child_process";
import path from "node:path";

import { SEED_DATA_PATH } from "./seed-data";

const BACKEND_DIR = path.join(__dirname, "..", "..", "backend");

/**
 * Runs once before the whole Playwright run (see `globalSetup` in
 * `playwright.config.ts`) — applies migrations and seeds deterministic
 * data via `backend/scripts/seed_e2e.py`, which writes the resulting
 * UUIDs (organization id above all) to `e2e/.auth/seed-data.json` for
 * every spec to read (see `seed-data.ts`).
 *
 * Requires `DATABASE_URL`/`JWT_SECRET_KEY` already set in the environment
 * (backend/.env locally, or CI secrets/env) — this script does not invent
 * database configuration, only orchestrates it. The Python executable is
 * `python` by default (matches how `.github/workflows/ci.yml`'s backend
 * job installs dependencies directly onto `PATH`, no venv); override via
 * `E2E_SEED_PYTHON` for a local venv, e.g. `.venv/Scripts/python.exe`.
 */
export default function globalSetup(): void {
  const python = process.env.E2E_SEED_PYTHON ?? "python";
  const env = { ...process.env, E2E_SEED_OUTPUT_PATH: SEED_DATA_PATH };

  execFileSync(python, ["-m", "alembic", "upgrade", "head"], {
    cwd: BACKEND_DIR,
    env,
    stdio: "inherit",
  });
  execFileSync(python, ["scripts/seed_e2e.py"], {
    cwd: BACKEND_DIR,
    env,
    stdio: "inherit",
  });
}
