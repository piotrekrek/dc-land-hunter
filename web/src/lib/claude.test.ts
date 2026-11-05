import { describe, expect, it } from "vitest";

import { isSafeRedirect } from "./claude";

describe("isSafeRedirect", () => {
  it.each([
    "https://claude.ai/api/mcp/auth_callback?code=abc&state=xyz",
    "http://localhost:33418/callback?code=abc",
    "http://127.0.0.1:6274/oauth/callback?error=access_denied",
    "http://[::1]:8080/cb",
  ])("follows %s", (target) => {
    expect(isSafeRedirect(target)).toBe(true);
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "http://evil.example/cb",
    "http://localhost.evil.example/cb",
    "ftp://localhost/cb",
    "/relative/path",
    "",
  ])("never follows %s", (target) => {
    expect(isSafeRedirect(target)).toBe(false);
  });
});
