import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

/** Typed API client generated from api/openapi.json (`pnpm gen:api`). */
// `fetch` is resolved per call (not captured at import) so tests can stub it.
export const api = createClient<paths>({ baseUrl: globalThis.location?.origin ?? "", fetch: (request) => globalThis.fetch(request) });

export type Health = components["schemas"]["Health"];
export type SourceOut = components["schemas"]["SourceOut"];
export type SourcesList = components["schemas"]["SourcesList"];

export class ApiError extends Error {}

export async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await promise;
  if (error !== undefined || data === undefined) {
    throw new ApiError(`API ${response.status}`);
  }
  return data;
}
