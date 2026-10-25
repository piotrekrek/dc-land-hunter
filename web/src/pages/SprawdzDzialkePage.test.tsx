import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CheckListItem } from "@/api/client";
import { ID, jsonResponse, resolved, routeFetch } from "@/test/checkFixtures";
import { renderWithProviders } from "@/test/render";

import { SprawdzDzialkePage } from "./SprawdzDzialkePage";

vi.mock("@/components/check/MapPicker", () => ({
  MapPicker: ({ onPick }: { onPick: (lat: number, lon: number) => void }) => (
    <button type="button" onClick={() => onPick(52.1, 20.9)}>
      kliknij mapę
    </button>
  ),
}));

const page = (route = "/sprawdz-dzialke") =>
  renderWithProviders(
    <Routes>
      <Route path="/sprawdz-dzialke" element={<SprawdzDzialkePage />} />
      <Route path="/sprawdz-dzialke/:checkId" element={<p>strona wyniku</p>} />
    </Routes>,
    { route },
  );

const recent: CheckListItem[] = [
  { id: "c9", status: "done", requested_by: "ola@example.org", created_at: "2026-10-24T10:00:00+00:00", parcels: ["143206_5.0005.141/9"], verdict: { kind: "odpada", text_pl: "Odpada: za mała powierzchnia." }, score: 12 },
  { id: "c8", status: "running", requested_by: "ala@example.org", created_at: "2026-10-25T08:00:00+00:00", parcels: [ID], verdict: null, score: null },
];

afterEach(() => vi.restoreAllMocks());

describe("Sprawdź działkę: input", () => {
  it("cannot be submitted empty, and says so when nothing was checked yet", async () => {
    routeFetch({ "GET /api/checks": () => ({ items: [] }) });
    page();
    expect(await screen.findByText(/Jeszcze nic nie sprawdzano/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Znajdź działkę" })).toBeDisabled();
  });

  it("asks the server what was pasted, shows the parcel for confirmation, then starts the check", async () => {
    const { calls } = routeFetch({
      "GET /api/checks": () => ({ items: [] }),
      "POST /api/parcels/resolve": () => resolved(),
      "POST /api/checks": () => ({ id: "c1" }),
    });
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText("Numer działki, współrzędne lub link"), ID);
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));

    const confirm = await screen.findByTestId("confirm");
    expect(within(confirm).getByRole("heading", { name: "Czy to ta działka?" })).toBeInTheDocument();
    expect(within(confirm).getByText(ID)).toBeInTheDocument();
    expect(within(confirm).getByText(/Konin \(miasto\), obręb 0002/)).toBeInTheDocument();
    expect(within(confirm).getByRole("img", { name: `Obrys działki ${ID}` })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/parcels/resolve")?.body).toEqual({ text: ID }); // parsed on the server

    await user.click(screen.getByRole("button", { name: "Sprawdź działkę" }));
    expect(await screen.findByText("strona wyniku")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/checks")?.body).toEqual({ parcels: [ID], points: [] });
  });

  it("says what is wrong with an entry in words the person can act on, and stays on the form", async () => {
    routeFetch({
      "GET /api/checks": () => ({ items: [] }),
      "POST /api/parcels/resolve": () => ({ items: [], issues: [{ text: "141/9", message_pl: "Brakuje obrębu. Dopisz obręb albo wklej pełny identyfikator." }], dropped: 0, duplicates: 0 }),
    });
    const user = userEvent.setup();
    page();
    const field = await screen.findByLabelText("Numer działki, współrzędne lub link");
    await user.type(field, "141/9");
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));
    const issues = await screen.findByTestId("issues");
    expect(issues).toHaveTextContent("„141/9”: Brakuje obrębu");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAttribute("aria-describedby", "input-issues");
    expect(screen.queryByTestId("confirm")).not.toBeInTheDocument();
  });

  it("shows the server's reason when the lookup itself fails", async () => {
    routeFetch({ "GET /api/checks": () => ({ items: [] }), "POST /api/parcels/resolve": () => jsonResponse(503, { detail: "Serwer nie ma ustawionego adresu kontaktowego." }) });
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText("Numer działki, współrzędne lub link"), ID);
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Serwer nie ma ustawionego adresu kontaktowego.");
  });

  it("lets the person go back and change the entry, which is kept", async () => {
    routeFetch({ "GET /api/checks": () => ({ items: [] }), "POST /api/parcels/resolve": () => resolved() });
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText("Numer działki, współrzędne lub link"), ID);
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));
    await user.click(await screen.findByRole("button", { name: "Zmień" }));
    expect(screen.getByLabelText("Numer działki, współrzędne lub link")).toHaveValue(ID);
  });

  it("warns when the parcel is outside the area of interest, but does not refuse it", async () => {
    const outside = resolved();
    outside.items[0]!.in_aoi = false;
    routeFetch({ "GET /api/checks": () => ({ items: [] }), "POST /api/parcels/resolve": () => outside });
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText("Numer działki, współrzędne lub link"), ID);
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));
    expect(await screen.findByText("poza obszarem zainteresowania")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sprawdź działkę" })).toBeEnabled();
  });

  it("shows why a start was refused (too many open checks)", async () => {
    routeFetch({
      "GET /api/checks": () => ({ items: [] }),
      "POST /api/parcels/resolve": () => resolved(),
      "POST /api/checks": () => jsonResponse(429, { detail: "Masz już 3 sprawdzenia w toku. Poczekaj, aż się skończą." }),
    });
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText("Numer działki, współrzędne lub link"), ID);
    await user.click(screen.getByRole("button", { name: "Znajdź działkę" }));
    await user.click(await screen.findByRole("button", { name: "Sprawdź działkę" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Masz już 3 sprawdzenia w toku");
    expect(screen.queryByText("strona wyniku")).not.toBeInTheDocument();
  });

  it("looks the parcel up at once when it arrives in the address (from a site card)", async () => {
    const { calls } = routeFetch({ "GET /api/checks": () => ({ items: [] }), "POST /api/parcels/resolve": () => resolved() });
    page(`/sprawdz-dzialke?q=${encodeURIComponent(ID)}`);
    expect(await screen.findByTestId("confirm")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "POST /api/parcels/resolve")).toHaveLength(1);
  });

  it("adds a point picked on the map to the entry and looks it up", async () => {
    const { calls } = routeFetch({ "GET /api/checks": () => ({ items: [] }), "POST /api/parcels/resolve": () => resolved() });
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole("button", { name: "Wskaż na mapie" }));
    await user.click(await screen.findByRole("button", { name: "kliknij mapę" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/parcels/resolve")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/parcels/resolve")?.body).toEqual({ text: "52.10000 20.90000" });
  });

  it("tells a member they see only their own checks, and an admin that they see everyone's", async () => {
    routeFetch({ "GET /api/checks": () => ({ items: [] }) });
    page();
    expect(await screen.findByText("Widzisz tylko swoje sprawdzenia.")).toBeInTheDocument();
    vi.restoreAllMocks();
    routeFetch({ "GET /api/checks": () => ({ items: [] }), "GET /api/auth/me": () => ({ email: "pi@example.org", role: "admin" }) });
    page();
    expect(await screen.findByText("Widzisz sprawdzenia wszystkich użytkowników.")).toBeInTheDocument();
  });

  it("lists the recent checks with their state or verdict", async () => {
    routeFetch({ "GET /api/checks": () => ({ items: recent }) });
    page();
    const list = await screen.findByTestId("recent-checks");
    const rows = within(list).getAllByRole("link");
    expect(rows[0]).toHaveAttribute("href", "/sprawdz-dzialke/c9");
    expect(rows[0]).toHaveTextContent("Odpada: za mała powierzchnia.");
    expect(rows[1]).toHaveTextContent("w trakcie");
    expect(rows[1]).toHaveTextContent("ala@example.org");
  });
});
