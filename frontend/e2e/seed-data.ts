import { readFileSync } from "node:fs";
import path from "node:path";

/** Shape of the JSON `backend/scripts/seed_e2e.py` writes to
 * `E2E_SEED_OUTPUT_PATH` (see `global-setup.ts`). */
export interface SeedData {
  organization_id: string;
  admin_email: string;
  admin_password: string;
  patient_id: string;
  practitioner_id: string;
  department_id: string;
  workflow_run_id: string;
}

export const SEED_DATA_PATH = path.join(__dirname, ".auth", "seed-data.json");

/** Read the seed summary written by `global-setup.ts`. Throws with a clear
 * message if global setup hasn't run yet — every spec needs this, so
 * failing loudly here beats a confusing downstream navigation failure. */
export function readSeedData(): SeedData {
  try {
    return JSON.parse(readFileSync(SEED_DATA_PATH, "utf-8")) as SeedData;
  } catch (cause) {
    throw new Error(
      `Could not read seed data at ${SEED_DATA_PATH}. Did global setup ` +
        "(backend/scripts/seed_e2e.py via e2e/global-setup.ts) run successfully?",
      { cause },
    );
  }
}
