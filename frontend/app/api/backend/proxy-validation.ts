/**
 * Path validation for the `/api/backend/*` proxy (see
 * `[...path]/route.ts`) — pulled into its own plainly-named module (the
 * dynamic segment `[...path]` in the route's own directory name is
 * awkward as an import specifier from test files) so both the route
 * handler and its Vitest suite can import these pure functions directly.
 */

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isUuid(segment: string): boolean {
  return UUID_PATTERN.test(segment);
}

/** `.`/`..`/an empty segment anywhere in the path — the shapes a path
 * traversal attempt collapses to once the URL parser normalizes
 * `api/v1/../../admin` down to `/admin`, escaping the backend's
 * `/api/v1` prefix this proxy is meant to be confined to. Checked
 * independently of (and before) the allowlist below, so it stands on
 * its own even if a future allowlist entry were ever misconfigured. */
export function hasInvalidPathSegment(segments: string[]): boolean {
  return segments.some((segment) => segment === "" || segment === "." || segment === "..");
}

type PathMatcher = (segments: string[]) => boolean;

/**
 * The exact, narrow set of backend paths this proxy forwards — matching
 * the route's own stated purpose (see that file's module docstring):
 * the AI Assistant's client-side calls, the practitioner
 * available-times lookup, and the workflow event SSE stream. Any other
 * path — even a syntactically well-formed one — is rejected, never
 * silently forwarded with the caller's real bearer token attached.
 */
const ALLOWED_PATH_MATCHERS: PathMatcher[] = [
  // POST /organizations/{organizationId}/agent/execute
  (segments) =>
    segments.length === 4 &&
    segments[0] === "organizations" &&
    isUuid(segments[1]) &&
    segments[2] === "agent" &&
    segments[3] === "execute",
  // GET /organizations/{organizationId}/practitioners/{practitionerId}/available-times
  (segments) =>
    segments.length === 5 &&
    segments[0] === "organizations" &&
    isUuid(segments[1]) &&
    segments[2] === "practitioners" &&
    isUuid(segments[3]) &&
    segments[4] === "available-times",
  // GET /organizations/{organizationId}/workflows/{workflowId}/events/stream
  (segments) =>
    segments.length === 6 &&
    segments[0] === "organizations" &&
    isUuid(segments[1]) &&
    segments[2] === "workflows" &&
    isUuid(segments[3]) &&
    segments[4] === "events" &&
    segments[5] === "stream",
];

export function isAllowedProxyPath(segments: string[]): boolean {
  return ALLOWED_PATH_MATCHERS.some((matches) => matches(segments));
}
