import type { ErrorResponse } from "@/types/api";

/**
 * Thrown by every `services/*` function on a non-2xx backend response.
 * `code`/`status` let callers branch (e.g. show a specific message for
 * `appointment_conflict`) without string-matching `message`, which is
 * meant for display, not for logic. Mirrors the backend's own
 * `ErrorResponse` shape (`app/schemas/common.py`) — see
 * `lib/api-fetch.ts` for where this is constructed.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isValidation(): boolean {
    return this.status === 422;
  }
}

/** Best-effort parse of a FastAPI error body — falls back to a generic
 * message if the response isn't the expected `ErrorResponse` shape
 * (e.g. an upstream proxy/gateway error that never reached FastAPI). */
export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  let code = "unknown_error";
  let message = `Request failed with status ${response.status}.`;
  try {
    const body = (await response.json()) as ErrorResponse;
    if (body?.error?.code) code = body.error.code;
    if (body?.error?.message) message = body.error.message;
  } catch {
    // Non-JSON body — keep the generic message above.
  }
  return new ApiError(response.status, code, message);
}

/** A single, human-readable line for any error — used by the shared
 * `<ErrorState>` component so every page renders failures consistently. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}
