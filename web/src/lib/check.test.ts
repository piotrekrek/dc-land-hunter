import { describe, expect, it } from "vitest";

import { ApiError } from "@/api/client";

import { checkPollInterval } from "./check";

describe("checkPollInterval", () => {
  it("keeps asking every 2 s while the check runs, and before the first answer", () => {
    expect(checkPollInterval({ data: { status: "running" }, error: null })).toBe(2000);
    expect(checkPollInterval({ data: undefined, error: null })).toBe(2000);
  });

  it("stops when the check is done or failed", () => {
    expect(checkPollInterval({ data: { status: "done" }, error: null })).toBe(false);
    expect(checkPollInterval({ data: { status: "failed" }, error: null })).toBe(false);
  });

  it("stops on a 4xx: a missing check does not appear by asking again", () => {
    expect(checkPollInterval({ data: undefined, error: new ApiError("API 404", 404) })).toBe(false);
  });

  it("keeps asking after a server error or a dropped connection, which can pass", () => {
    expect(checkPollInterval({ data: undefined, error: new ApiError("API 503", 503) })).toBe(2000);
    expect(checkPollInterval({ data: undefined, error: new TypeError("Failed to fetch") })).toBe(2000);
  });
});
