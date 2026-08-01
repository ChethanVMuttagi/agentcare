import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { hasInvalidPathSegment, isAllowedProxyPath } from "@/app/api/backend/proxy-validation";
import { config } from "@/lib/config";
import { getSession } from "@/lib/session";

/**
 * Generic authenticated proxy from the browser to the FastAPI backend.
 *
 * The backend has no CORS middleware configured (confirmed — zero
 * `CORSMiddleware` in `app/main.py`), and adding it would be a new
 * backend feature, which this project is explicitly not allowed to add.
 * Every OTHER page in this app avoids the problem entirely by only ever
 * calling the backend from the Next.js server (Server Components/Server
 * Actions), which isn't subject to browser CORS. This route exists only
 * for the handful of interactions that genuinely need client-side
 * fetches — the AI Assistant chat and the practitioner available-times
 * lookup while booking an appointment — and forwards them the same way:
 * read the session cookie server-side, attach the real bearer token, and
 * relay the request/response bytes as-is. The browser never sees the
 * token; it only ever talks to same-origin `/api/backend/*`.
 *
 * Milestone B: this is also the transport for the Server-Sent Events
 * workflow stream (`.../workflows/{id}/events/stream`) — the browser's
 * native `EventSource` can't attach an `Authorization` header itself, so
 * routing it through this same cookie-authenticated proxy is what makes
 * a plain `new EventSource("/api/backend/...")` work at all. Streaming
 * bodies pass through untouched in both directions: `request.body` is
 * forwarded as-is for a POST/PATCH, and `backendResponse.body` is
 * returned as-is here — Next.js does not buffer a `ReadableStream`
 * response body, so `text/event-stream` chunks reach the browser as the
 * backend emits them, not all at once at the end.
 *
 * Sprint 2: `path` is validated against `proxy-validation.ts` before
 * anything else — a `.`/`..`/empty segment (400) closes off the path-
 * traversal escape a naive `new URL(base + "/" + path.join("/"))` is
 * otherwise vulnerable to (e.g. `["..","..","admin"]` collapsing past
 * the intended `/api/v1` prefix), and an explicit allowlist (403) keeps
 * this proxy confined to the three routes it actually exists for, even
 * though the real bearer token would make any other backend path
 * reachable too — see that module's docstring.
 */
async function proxyToBackend(request: NextRequest, path: string[]): Promise<NextResponse> {
  const session = await getSession();
  if (!session) {
    return NextResponse.json(
      { error: { code: "unauthorized", message: "Not signed in." } },
      { status: 401 },
    );
  }

  if (hasInvalidPathSegment(path)) {
    return NextResponse.json(
      { error: { code: "invalid_path", message: "Invalid path segment." } },
      { status: 400 },
    );
  }

  if (!isAllowedProxyPath(path)) {
    return NextResponse.json(
      {
        error: {
          code: "forbidden_path",
          message: "This path is not permitted through the proxy.",
        },
      },
      { status: 403 },
    );
  }

  const backendUrl = new URL(`${config.apiBaseUrl}/${path.join("/")}`);
  request.nextUrl.searchParams.forEach((value, key) => {
    backendUrl.searchParams.set(key, value);
  });

  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.token}`,
  };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;
  // `EventSource` sets this automatically on an SSE reconnect — forwarding
  // it lets `stream_workflow_events` resume from where the browser left
  // off instead of replaying the whole history (see that endpoint's
  // docstring).
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  const backendResponse = await fetch(backendUrl, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    duplex: hasBody ? "half" : undefined,
    cache: "no-store",
  } as RequestInit);

  const responseHeaders = new Headers();
  for (const key of ["content-type", "content-disposition", "content-length", "cache-control"]) {
    const value = backendResponse.headers.get(key);
    if (value) responseHeaders.set(key, value);
  }

  return new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    headers: responseHeaders,
  });
}

async function handler(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await context.params;
  return proxyToBackend(request, path);
}

export { handler as GET, handler as POST, handler as PATCH, handler as DELETE, handler as PUT };
