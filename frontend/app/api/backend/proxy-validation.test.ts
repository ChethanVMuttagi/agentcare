import { describe, expect, it } from "vitest";

import { hasInvalidPathSegment, isAllowedProxyPath } from "@/app/api/backend/proxy-validation";

const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PRACTITIONER_ID = "22222222-2222-2222-2222-222222222222";
const WORKFLOW_ID = "33333333-3333-3333-3333-333333333333";

describe("hasInvalidPathSegment", () => {
  it("accepts a normal, well-formed path", () => {
    expect(hasInvalidPathSegment(["organizations", ORG_ID, "agent", "execute"])).toBe(false);
  });

  it("rejects a literal '..' segment (path traversal)", () => {
    expect(hasInvalidPathSegment(["..", "..", "admin"])).toBe(true);
  });

  it("rejects a '..' segment anywhere in the path, not just the start", () => {
    expect(hasInvalidPathSegment(["organizations", ORG_ID, "..", "..", "admin"])).toBe(true);
  });

  it("rejects a literal '.' segment", () => {
    expect(hasInvalidPathSegment(["organizations", ".", "agent"])).toBe(true);
  });

  it("rejects an empty segment", () => {
    expect(hasInvalidPathSegment(["organizations", "", "agent"])).toBe(true);
  });

  it("accepts an empty array (no path at all)", () => {
    expect(hasInvalidPathSegment([])).toBe(false);
  });

  it("does not false-positive on segments that merely contain dots", () => {
    // A UUID-shaped or filename-shaped segment containing "." characters
    // is not the same as a literal "." or ".." segment.
    expect(hasInvalidPathSegment(["organizations", "file.pdf"])).toBe(false);
  });
});

describe("isAllowedProxyPath", () => {
  it("allows POST /organizations/{id}/agent/execute", () => {
    expect(isAllowedProxyPath(["organizations", ORG_ID, "agent", "execute"])).toBe(true);
  });

  it("allows GET /organizations/{id}/practitioners/{id}/available-times", () => {
    expect(
      isAllowedProxyPath([
        "organizations",
        ORG_ID,
        "practitioners",
        PRACTITIONER_ID,
        "available-times",
      ]),
    ).toBe(true);
  });

  it("allows GET /organizations/{id}/workflows/{id}/events/stream", () => {
    expect(
      isAllowedProxyPath(["organizations", ORG_ID, "workflows", WORKFLOW_ID, "events", "stream"]),
    ).toBe(true);
  });

  it("rejects a syntactically similar but non-allowlisted path", () => {
    expect(isAllowedProxyPath(["organizations", ORG_ID, "patients"])).toBe(false);
  });

  it("rejects an allowlisted path shape with a non-UUID id segment", () => {
    expect(isAllowedProxyPath(["organizations", "not-a-uuid", "agent", "execute"])).toBe(false);
  });

  it("rejects an otherwise-allowlisted path with extra trailing segments", () => {
    expect(
      isAllowedProxyPath(["organizations", ORG_ID, "agent", "execute", "extra"]),
    ).toBe(false);
  });

  it("rejects a path that tries to reach an unrelated admin-shaped route", () => {
    expect(isAllowedProxyPath(["admin", "users"])).toBe(false);
  });

  it("rejects the empty path", () => {
    expect(isAllowedProxyPath([])).toBe(false);
  });
});
