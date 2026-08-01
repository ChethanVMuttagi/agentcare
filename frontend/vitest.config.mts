import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Sprint 2 added `*.test.ts` unit/integration tests for server-only
 * logic (the `/api/backend` proxy) — plain Node, no jsdom/React
 * rendering needed. Sprint 3 adds `*.test.tsx` component/hook tests
 * (`@testing-library/react`), which DO need a DOM. The default
 * environment stays `node` (so the already-passing `.test.ts` proxy
 * suite is untouched); every file that needs jsdom instead declares it
 * itself via a `// @vitest-environment jsdom` docblock as its first
 * line (Vitest's per-file override — more reliable across Vitest 4
 * versions than the `environmentMatchGlobs` option, which this project
 * found silently ignored). Mirrors `tsconfig.json`'s `@/*` -> `./*`
 * path mapping so test files can import with the same aliases the app
 * itself uses.
 *
 * `jsdom` is pinned to 25.x, not the latest major — 26+ pulls in
 * `cssstyle`/`@asamuzakjp/css-color`, whose own CJS build `require()`s
 * an ESM-only file and crashes every Vitest worker pool (`forks` and
 * `threads` alike) before a single jsdom test can run. Revisit once
 * that's fixed upstream.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["**/node_modules/**", "**/.next/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["app/**", "components/**", "features/**", "hooks/**", "lib/**", "services/**"],
      exclude: [
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.d.ts",
        // Next.js routing-convention files (Server Components fetching
        // data, layouts) — not meaningfully unit-testable without a
        // running server; covered instead by frontend/e2e/ (Playwright).
        "app/**/layout.tsx",
        "app/**/page.tsx",
        "app/**/error.tsx",
        "app/**/not-found.tsx",
        "app/**/loading.tsx",
      ],
      // Sprint 3 scoped its Vitest expansion to 6 specific areas (AI
      // Assistant, Timeline, Workflow Graph, Analytics, Demo Mode,
      // shared hooks/lib) — NOT the whole frontend (patients,
      // appointments, documents, approvals, auth, dashboard remain
      // untested here; their coverage is Playwright's job, see
      // frontend/e2e/). Measured baseline with those 6 areas covered
      // was ~33% overall; these thresholds sit a few points below that
      // real number, not an aspirational one — raise them as more areas
      // gain unit tests in future sprints, don't lower them to make a
      // regression pass.
      thresholds: {
        statements: 30,
        branches: 35,
        functions: 20,
        lines: 30,
      },
    },
  },
});
