import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { LoginPage } from "./LoginPage";

afterEach(() => vi.restoreAllMocks());

function stubFetch(handler: (path: string, init?: RequestInit) => Response) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return handler(new URL(url, "http://localhost").pathname, init);
  });
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("LoginPage", () => {
  it("shows the server's message on a wrong password and clears the password field", async () => {
    stubFetch((path) =>
      path === "/api/auth/login" ? json({ detail: "Nieprawidłowy e-mail lub hasło" }, 401) : json({}, 401),
    );
    renderWithProviders(<LoginPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("E-mail"), "ala@example.org");
    await user.type(screen.getByLabelText("Hasło"), "zle-haslo-123");
    await user.click(screen.getByRole("button", { name: "Zaloguj się" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nieprawidłowy e-mail lub hasło");
    expect(screen.getByLabelText("Hasło")).toHaveValue("");
  });

  it("shows the lockout message", async () => {
    stubFetch((path) => (path === "/api/auth/login" ? json({ detail: "Zbyt wiele prób logowania." }, 429) : json({}, 401)));
    renderWithProviders(<LoginPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("E-mail"), "ala@example.org");
    await user.type(screen.getByLabelText("Hasło"), "cokolwiek-12345");
    await user.click(screen.getByRole("button", { name: "Zaloguj się" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Zbyt wiele prób");
  });
});
