import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PowiatItem } from "@/api/client";
import { renderWithProviders } from "@/test/render";

import { PowiatyPage } from "./PowiatyPage";

function powiat(over: Partial<PowiatItem> & Pick<PowiatItem, "teryt" | "name">): PowiatItem {
  return {
    voivodeship_teryt: "08",
    voivodeship_name: "lubuskie",
    checked: false,
    last_ok_at: null,
    last_attempt_at: null,
    status: null,
    sites: null,
    error: null,
    priority: false,
    ...over,
  };
}

const items: PowiatItem[] = [
  powiat({ teryt: "0805", name: "powiat słubicki" }),
  powiat({ teryt: "0801", name: "powiat gorzowski", checked: true, last_ok_at: "2026-10-20T01:30:00Z", status: "ok", sites: 4 }),
  powiat({ teryt: "1421", name: "powiat pruszkowski", voivodeship_teryt: "14", voivodeship_name: "mazowieckie", priority: true }),
  powiat({ teryt: "1465", name: "powiat Warszawa", voivodeship_teryt: "14", voivodeship_name: "mazowieckie", status: "failed", error: "boom" }),
];

const me = { email: "ala@example.org", role: "member" };

/** Answers the GETs and records the PUT body; the PUT echoes the list back with the new priorities. */
function mockApi(list: PowiatItem[] = items, nextUp: string[] = ["0805", "1465"]) {
  const puts: unknown[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const path = new URL(request.url, "http://localhost").pathname;
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (path === "/api/auth/me") return json(me);
    if (path === "/api/powiats" && request.method === "GET") return json({ items: list, next_up: nextUp });
    if (path === "/api/powiats/priority" && request.method === "PUT") {
      const body = (await request.json()) as { teryts: string[] };
      puts.push(body);
      return json({ items: list.map((i) => ({ ...i, priority: body.teryts.includes(i.teryt) })), next_up: nextUp });
    }
    return new Response("not found", { status: 404 });
  });
  return { puts, spy };
}

afterEach(() => vi.restoreAllMocks());

describe("PowiatyPage", () => {
  it("groups powiats by voivodeship and shows checked and not checked", async () => {
    mockApi();
    renderWithProviders(<PowiatyPage />);
    expect(await screen.findByTestId("checked-count")).toHaveTextContent("Sprawdzone: 1 z 4");
    const groups = screen.getAllByTestId("voivodeship");
    expect(groups.map((g) => within(g).getByText(/^(lubuskie|mazowieckie)$/).textContent)).toEqual(["lubuskie", "mazowieckie"]);
    expect(within(groups[0]!).getByText("Sprawdzony")).toBeInTheDocument();
    expect(within(groups[0]!).getByText("Niesprawdzony")).toBeInTheDocument();
    expect(within(groups[1]!).getByText("Błąd wyszukiwania")).toBeInTheDocument();
  });

  it("offers the priority button only for powiats that are not checked", async () => {
    mockApi();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    expect(screen.queryByRole("button", { name: "Priorytet: powiat gorzowski" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Priorytet: powiat słubicki" })).toHaveAttribute("aria-pressed", "false");
  });

  it("stages priority changes, saves the whole list, and can undo", async () => {
    const { puts } = mockApi();
    const user = userEvent.setup();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    // 1421 starts on the saved list, so its section is open; open lubuskie to reach 0805.
    await user.click(screen.getByText("lubuskie"));
    const save = screen.getByRole("button", { name: "Zapisz" });
    expect(save).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Priorytet: powiat słubicki" }));
    expect(screen.getByRole("button", { name: "Zapisz (2)" })).toBeEnabled();
    expect(within(screen.getByTestId("priority-queue")).getAllByRole("listitem")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Cofnij" }));
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
    expect(within(screen.getByTestId("priority-queue")).getAllByRole("listitem")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Priorytet: powiat słubicki" }));
    await user.click(screen.getByRole("button", { name: "Zapisz (2)" }));
    await waitFor(() => expect(puts).toEqual([{ teryts: ["1421", "0805"] }]));
    expect(await screen.findByText(/Zapisano/)).toBeInTheDocument();
  });

  it("filters to the powiats that are not checked", async () => {
    mockApi();
    const user = userEvent.setup();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    await user.click(screen.getByRole("button", { name: "Niesprawdzone" }));
    expect(screen.queryByText("powiat gorzowski")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("powiat-row")).toHaveLength(3);
  });

  it("keeps the staged choice and says so when saving fails", async () => {
    mockApi();
    const user = userEvent.setup();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    await user.click(screen.getByText("lubuskie"));
    await user.click(screen.getByRole("button", { name: "Priorytet: powiat słubicki" }));
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("nope", { status: 500 }));
    await user.click(screen.getByRole("button", { name: "Zapisz (2)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się zapisać");
    expect(screen.getByRole("button", { name: "Zapisz (2)" })).toBeEnabled();
  });

  it("lists the next powiats the planner will take, tagged priority or rotation", async () => {
    mockApi(items, ["1421", "0805"]);
    renderWithProviders(<PowiatyPage />);
    const next = await screen.findByTestId("next-up");
    const rows = within(next).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(["1.powiat pruszkowskipriorytet", "2.powiat słubickirotacja"]);
  });

  it("says the preview updates after saving when there are unsaved changes", async () => {
    mockApi();
    const user = userEvent.setup();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    expect(screen.queryByText(/Zaktualizuje się po zapisaniu/)).not.toBeInTheDocument();
    await user.click(screen.getByText("lubuskie"));
    await user.click(screen.getByRole("button", { name: "Priorytet: powiat słubicki" }));
    expect(screen.getByText(/Zaktualizuje się po zapisaniu/)).toBeInTheDocument();
  });
});

const searchItems: PowiatItem[] = [
  powiat({ teryt: "3011", name: "powiat koniński", voivodeship_teryt: "30", voivodeship_name: "wielkopolskie" }),
  powiat({ teryt: "3062", name: "powiat Konin", voivodeship_teryt: "30", voivodeship_name: "wielkopolskie" }),
  powiat({ teryt: "3021", name: "powiat poznański", voivodeship_teryt: "30", voivodeship_name: "wielkopolskie" }),
  powiat({ teryt: "1006", name: "powiat łódzki wschodni", voivodeship_teryt: "10", voivodeship_name: "łódzkie" }),
  powiat({ teryt: "0805", name: "powiat słubicki" }),
];

describe("PowiatyPage search", () => {
  async function setup() {
    mockApi(searchItems, []);
    const user = userEvent.setup();
    renderWithProviders(<PowiatyPage />);
    await screen.findByTestId("checked-count");
    return { user, box: screen.getByRole("searchbox", { name: "Szukaj powiatu" }) };
  }

  it("does nothing below three letters", async () => {
    const { user, box } = await setup();
    await user.type(box, "ko");
    expect(screen.getAllByTestId("voivodeship")).toHaveLength(3);
    expect(screen.queryByTestId("search-summary")).not.toBeInTheDocument();
  });

  it("filters as you type from the third letter and opens only the matching województwo", async () => {
    const { user, box } = await setup();
    await user.type(box, "kon");
    const groups = screen.getAllByTestId("voivodeship");
    expect(groups).toHaveLength(1);
    expect(groups[0]).toHaveAttribute("open");
    expect(within(groups[0]!).getAllByTestId("powiat-row")).toHaveLength(2);
    expect(screen.queryByText("powiat poznański")).not.toBeInTheDocument();
    expect(screen.getByTestId("search-summary")).toHaveTextContent("Znaleziono 2 powiatów w 1 województwie");
  });

  it("ignores case and Polish accents", async () => {
    const { user, box } = await setup();
    await user.type(box, "LODZ");
    expect(screen.getByText("powiat łódzki wschodni")).toBeInTheDocument();
    expect(screen.getAllByTestId("powiat-row")).toHaveLength(1);
  });

  it("keeps every powiat of a województwo whose name matches", async () => {
    const { user, box } = await setup();
    await user.type(box, "wielkopol");
    expect(screen.getAllByTestId("powiat-row")).toHaveLength(3);
  });

  it("finds a powiat by its code", async () => {
    const { user, box } = await setup();
    await user.type(box, "0805");
    expect(screen.getAllByTestId("powiat-row")).toHaveLength(1);
    expect(screen.getByText("powiat słubicki")).toBeInTheDocument();
  });

  it("combines with the checked filter and says when nothing matches", async () => {
    const { user, box } = await setup();
    await user.type(box, "kon");
    await user.click(screen.getByRole("button", { name: "Sprawdzone" }));
    expect(screen.getByTestId("search-summary")).toHaveTextContent("Brak wyników dla „kon”");
    expect(screen.queryByTestId("voivodeship")).not.toBeInTheDocument();
  });

  it("restores the full list when the box is cleared", async () => {
    const { user, box } = await setup();
    await user.type(box, "kon");
    await user.clear(box);
    expect(screen.getAllByTestId("voivodeship")).toHaveLength(3);
    expect(screen.queryByTestId("search-summary")).not.toBeInTheDocument();
  });
});
