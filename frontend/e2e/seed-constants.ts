/**
 * Mirrors the constants in `backend/scripts/seed_e2e.py` — kept in sync by
 * hand since one is Python and the other TypeScript. The organization id
 * (and every other database-generated UUID) is NOT here: it's read from
 * the JSON file `global-setup.ts` asks the seed script to write, since a
 * UUID can't be hardcoded.
 */
export const E2E_ADMIN_EMAIL = "e2e.admin@agentcare-e2e-tests.com";
export const E2E_ADMIN_PASSWORD_DEFAULT = "E2E-test-password-1!";
export const E2E_ADMIN_ROLE = "admin";
