import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler | null = null;

/** AuthProvider registers a handler: a 401 from any API call means the session has ended. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

// A 401 here is an answer, not an expired session: a wrong password, or the session check itself (fetchMe).
const UNAUTHORIZED_IS_ANSWER = new Set(["/api/auth/login", "/api/auth/me"]);

/** Typed API client generated from api/openapi.json (`pnpm gen:api`). */
// `fetch` is resolved per call (not captured at import) so tests can stub it.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  fetch: async (request) => {
    const response = await globalThis.fetch(request);
    if (response.status === 401 && !UNAUTHORIZED_IS_ANSWER.has(new URL(request.url, "http://localhost").pathname)) {
      onUnauthorized?.();
    }
    return response;
  },
});

export type Health = components["schemas"]["Health"];
export type SourceOut = components["schemas"]["SourceOut"];
export type SourcesList = components["schemas"]["SourcesList"];

export type User = components["schemas"]["UserOut"];
export type OpsJobs = components["schemas"]["OpsJobs"];
export type JobItem = components["schemas"]["JobItem"];
export type PowiatItem = components["schemas"]["PowiatItem"];
export type ResolveOut = components["schemas"]["ResolveOut"];
export type ResolvedParcel = components["schemas"]["ResolvedParcelOut"];
export type CheckOut = components["schemas"]["CheckOut"];
export type CheckStep = components["schemas"]["StepOut"];
export type CheckSiteSummary = components["schemas"]["CheckSiteSummary"];
export type CheckSiteDetail = components["schemas"]["CheckSiteDetail"];
export type CheckListItem = components["schemas"]["CheckListItem"];
export type Sheet = components["schemas"]["SheetOut"];
export type Verdict = components["schemas"]["VerdictOut"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await promise;
  if (error !== undefined || data === undefined) {
    throw new ApiError(`API ${response.status}`, response.status);
  }
  return data;
}

/**
 * React Query retry rule: retry network and server errors, never a 4xx. A 401 (expired session) or 404
 * (missing site) does not change on retry, so retrying only delays the answer.
 */
export function shouldRetry(failureCount: number, error: unknown, maxRetries = 3): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < maxRetries;
}
