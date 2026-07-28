import "server-only";

import { config } from "@/lib/config";
import { apiErrorFromResponse } from "@/lib/errors";

export interface ApiFetchOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE" | "PUT";
  /** Bearer token to attach — the caller (a `services/*` function)
   * decides where it comes from (usually `getSession()`), keeping this
   * module free of a `cookies()` dependency so it stays trivially usable
   * from contexts that already have a raw token (e.g. the login flow,
   * before any session cookie exists yet). */
  token?: string | null;
  /** JSON-serializable body, or a `FormData` instance for multipart
   * uploads (the `Content-Type`/boundary is then left to `fetch` itself
   * — never set it manually for FormData, see `services/documents.ts`). */
  body?: unknown;
  searchParams?: Record<string, string | number | boolean | undefined | null>;
}

/**
 * The one place every JSON call to the FastAPI backend goes through.
 * Always `cache: "no-store"`: this is an operational/clinical-admin
 * dashboard, not a marketing site — showing a stale appointment or
 * approval status would be actively misleading, so freshness always
 * wins over the performance a cache would buy here.
 *
 * Throws `ApiError` (see `lib/errors.ts`) for any non-2xx response, so
 * callers can `try/catch` once instead of checking `.ok` everywhere.
 */
export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const url = new URL(`${config.apiBaseUrl}${path}`);
  if (options.searchParams) {
    for (const [key, value] of Object.entries(options.searchParams)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers: Record<string, string> = {};
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }

  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    body = options.body;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body,
    cache: "no-store",
  });

  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/**
 * For endpoints that return raw bytes rather than JSON (document
 * download). Returns the still-open `Response` so the caller can stream
 * or buffer it as appropriate — never buffers into memory itself, since
 * a document could legitimately be tens of MB (see the backend's
 * `document_max_upload_bytes` setting).
 */
export async function apiFetchRaw(
  path: string,
  options: { token?: string | null } = {},
): Promise<Response> {
  const url = `${config.apiBaseUrl}${path}`;
  const headers: Record<string, string> = {};
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  const response = await fetch(url, { headers, cache: "no-store" });
  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
  return response;
}
