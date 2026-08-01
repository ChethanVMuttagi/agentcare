import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getSession } from "@/lib/session";

import { GET, POST } from "./route";

vi.mock("@/lib/session", () => ({
  getSession: vi.fn(),
}));

const mockGetSession = vi.mocked(getSession);

const ORG_ID = "11111111-1111-1111-1111-111111111111";
const WORKFLOW_ID = "33333333-3333-3333-3333-333333333333";

function requestFor(
  pathSuffix: string,
  init?: ConstructorParameters<typeof NextRequest>[1],
): NextRequest {
  return new NextRequest(`http://localhost/api/backend/${pathSuffix}`, init);
}

function contextFor(path: string[]): { params: Promise<{ path: string[] }> } {
  return { params: Promise.resolve({ path }) };
}

describe("/api/backend/[...path] proxy route", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    mockGetSession.mockReset();
  });

  describe("auth behavior", () => {
    it("returns 401 and never calls the backend when there is no session", async () => {
      mockGetSession.mockResolvedValue(null);

      const response = await GET(
        requestFor(`organizations/${ORG_ID}/agent/execute`),
        contextFor(["organizations", ORG_ID, "agent", "execute"]),
      );

      expect(response.status).toBe(401);
      const body = await response.json();
      expect(body.error.code).toBe("unauthorized");
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  describe("path traversal rejection", () => {
    it("returns 400 and never calls the backend for a '..' segment", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });

      const response = await GET(
        requestFor("..%2F..%2Fadmin"),
        contextFor(["..", "..", "admin"]),
      );

      expect(response.status).toBe(400);
      const body = await response.json();
      expect(body.error.code).toBe("invalid_path");
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("returns 400 for an empty path segment", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });

      const response = await GET(
        requestFor("organizations//agent"),
        contextFor(["organizations", "", "agent"]),
      );

      expect(response.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  describe("allowlist enforcement", () => {
    it("returns 403 and never calls the backend for a non-allowlisted path", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });

      const response = await GET(
        requestFor(`organizations/${ORG_ID}/patients`),
        contextFor(["organizations", ORG_ID, "patients"]),
      );

      expect(response.status).toBe(403);
      const body = await response.json();
      expect(body.error.code).toBe("forbidden_path");
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("returns 403 for a path outside the backend's own API entirely", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });

      const response = await GET(requestFor("admin/users"), contextFor(["admin", "users"]));

      expect(response.status).toBe(403);
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  describe("allowlisted requests are forwarded unchanged", () => {
    it("forwards an allowlisted POST with the bearer token attached", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });
      fetchSpy.mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      );

      const response = await POST(
        requestFor(`organizations/${ORG_ID}/agent/execute`, {
          method: "POST",
          body: JSON.stringify({ request_text: "hello" }),
          headers: { "content-type": "application/json" },
        }),
        contextFor(["organizations", ORG_ID, "agent", "execute"]),
      );

      expect(response.status).toBe(201);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [calledUrl, calledInit] = fetchSpy.mock.calls[0]!;
      expect(String(calledUrl)).toContain(`/organizations/${ORG_ID}/agent/execute`);
      const headers = calledInit?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer synthetic-test-token");
    });

    it("forwards an allowlisted GET request", async () => {
      mockGetSession.mockResolvedValue({ token: "synthetic-test-token" });
      fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

      const response = await GET(
        requestFor(`organizations/${ORG_ID}/workflows/${WORKFLOW_ID}/events/stream`),
        contextFor(["organizations", ORG_ID, "workflows", WORKFLOW_ID, "events", "stream"]),
      );

      expect(response.status).toBe(200);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
  });
});
