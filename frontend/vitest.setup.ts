import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

// `@testing-library/react` only auto-registers cleanup when it detects a
// GLOBAL `afterEach` (e.g. `test.globals: true`) — this project imports
// test utilities explicitly per-file instead (see vitest.config.mts), so
// without this, unmounted DOM from one test leaks into the next within
// the same file (duplicate elements, stale event handlers).
afterEach(() => {
  cleanup();
});
