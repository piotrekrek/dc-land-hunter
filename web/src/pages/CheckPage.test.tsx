import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ID, check, jsonResponse, routeFetch, site, step } from "@/test/checkFixtures";
import { renderWithProviders } from "@/test/render";

import { CheckPage } from "./CheckPage";

const page = (route = "/sprawdz-dzialke/c1") =>
  renderWithProviders(
    <Routes>
      <Route path="/sprawdz-dzialke" element={<p>formularz</p>} />
      <Route path="/sprawdz-dzialke/:checkId" element={<CheckPage />} />
    </Routes>,
    { route },
  );

const runningSteps = [
  step("parcel", "done", { label_pl: "Obrys działki (ULDK)", finding_pl: "Konin (miasto), obręb 0002." }),
  step("flood", "done", { label_pl: "Strefy zalewowe", finding_pl: "Działka poza strefami zalewowymi." }),
  step("mining", "running", { label_pl: "Obszary górnicze" }),
  step("zoning", "waiting", { label_pl: "Plany miejscowe" }),
];

afterEach(() => vi.restoreAllMocks());

describe("CheckPage: running", () => {
  it("shows real progress, the estimate, what each step found, and that the page can be left", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ status: "running", steps: runningSteps, estimate_seconds: 45, sites: [], finished_at: null }) });
    page();
    expect(await screen.findByRole("heading", { name: new RegExp(`Sprawdzam działkę ${ID}`) })).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "2");
    expect(bar).toHaveAttribute("aria-valuetext", "2 z 4 kroków, ok. 45 s");
    expect(screen.getByText("Działka poza strefami zalewowymi.")).toBeInTheDocument();
    expect(screen.getByText(/Możesz wyjść z tej strony/)).toBeInTheDocument();
    expect(screen.getByRole("status", { hidden: true, name: "" })).toBeTruthy();
  });

  it("puts the state in the browser tab so people can switch tabs", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ status: "running", steps: runningSteps, sites: [] }) });
    page();
    await screen.findByRole("progressbar");
    expect(document.title).toBe("(2/4) Sprawdzam…");
  });

  it("announces the running step once, in a polite live region", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ status: "running", steps: runningSteps, sites: [] }) });
    page();
    await screen.findByRole("progressbar");
    const live = document.querySelector('[aria-live="polite"]')!;
    expect(live).toHaveTextContent("Obszary górnicze. Krok 3 z 4.");
  });

  it("says a check waiting for a worker is queued", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ status: "pending", steps: [], sites: [], started_at: null, finished_at: null, estimate_seconds: 60 }) });
    page();
    expect(await screen.findByText(/czeka w kolejce/)).toBeInTheDocument();
  });

  it("turns into the result in place when the check finishes, without leaving the address", async () => {
    let calls = 0;
    routeFetch({
      "GET /api/checks/c1": () => (++calls < 2 ? check({ status: "running", steps: runningSteps, sites: [] }) : check()),
      "GET /api/checks/c1/sites/0": () => site(),
    });
    page();
    await screen.findByRole("progressbar");
    expect(await screen.findByTestId("verdict", {}, { timeout: 6000 })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  }, 10_000);
});

describe("CheckPage: result", () => {
  const done = () =>
    routeFetch({
      "GET /api/checks/c1": () => check(),
      "GET /api/checks/c1/sites/0": () => site(),
    });

  it("leads with the verdict sentence, then the ruler with the potential", async () => {
    done();
    page();
    const verdict = await screen.findByTestId("verdict");
    expect(verdict).toHaveTextContent("Odpada: strefa zalewowa 10% lub 1%: 62% terenu (próg 0%).");
    expect(verdict).toHaveAttribute("data-kind", "odpada");
    expect(screen.getByRole("img", { name: "Linijka wyniku: 38, potencjał 61, progi 40 i 70" })).toBeInTheDocument();
    expect(document.title).toMatch(/^Gotowe: Odpada/);
  });

  it("lists what is not known, the next steps with copyable letters, and the Protokół", async () => {
    done();
    const user = userEvent.setup(); // installs a clipboard that records what is copied
    page();
    const unknowns = await screen.findByTestId("unknowns");
    expect(within(unknowns).getByText(/Nie sprawdzono: rejestr zabytków/)).toBeInTheDocument();
    const steps = screen.getByTestId("next-steps");
    await user.click(within(steps).getByRole("button", { name: "Kopiuj treść" }));
    expect(await navigator.clipboard.readText()).toBe("Dzień dobry, proszę o informację o przeznaczeniu działki.");
    expect(await within(steps).findByRole("button", { name: "Skopiowano" })).toBeInTheDocument();
    expect(within(steps).getByLabelText(/księgę wieczystą/)).not.toBeChecked();
    expect(screen.getByTestId("protokol")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Warstwy rysunku" })).toBeInTheDocument();
  });

  it("carries the title block, the label that this is not a search result, and the layer ages", async () => {
    done();
    page();
    const block = await screen.findByTestId("title-block");
    expect(within(block).getByText(ID)).toBeInTheDocument();
    expect(within(block).getByText("Konin (miasto)")).toBeInTheDocument();
    expect(within(block).getByText("1,2 ha")).toBeInTheDocument();
    expect(within(block).getByText("ala@example.org")).toBeInTheDocument();
    expect(within(block).getByText("Sprawdzenie jednej działki, poza wyszukiwaniem.")).toBeInTheDocument();
    expect(screen.getByTestId("layer-ages")).toHaveTextContent("sieć (OSM) 12.10.2026");
    expect(screen.getByTestId("layer-ages")).not.toHaveTextContent("strefy zalewowe"); // a layer with no date is not claimed
  });

  it("labels a parcel outside the area of interest instead of hiding it", async () => {
    routeFetch({ "GET /api/checks/c1": () => check(), "GET /api/checks/c1/sites/0": () => site({ outside_aoi: true }) });
    page();
    expect(await screen.findByText("poza obszarem zainteresowania")).toBeInTheDocument();
  });

  it("starts the check again with the same parcels and goes to the new one", async () => {
    const { calls } = routeFetch({
      "GET /api/checks/c1": () => check(),
      "GET /api/checks/c1/sites/0": () => site(),
      "POST /api/checks": () => ({ id: "c2" }),
      "GET /api/checks/c2": () => check({ id: "c2", status: "running", steps: runningSteps, sites: [] }),
    });
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole("button", { name: /Sprawdź ponownie/ }));
    await waitFor(() => expect(calls.find((c) => c.key === "POST /api/checks")?.body).toEqual({ parcels: [ID], points: [] }));
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  });

  it("does not offer a re-check for a check that was started from a map point", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ parcels: ["52.10000 N, 20.90000 E"] }), "GET /api/checks/c1/sites/0": () => site() });
    page();
    await screen.findByTestId("verdict");
    expect(screen.queryByRole("button", { name: /Sprawdź ponownie/ })).not.toBeInTheDocument();
  });
});

describe("CheckPage: several parcels and failures", () => {
  const second = { ...check().sites[0]!, n: 1, parcel_id: "142104_2.0013.562/2", commune: "Michałowice", score: 71, upside_score: 80, excluded: false, verdict: { kind: "warto" as const, text_pl: "Warto sprawdzać dalej: wynik 71 pkt, dane kompletne w 90%." } };

  it("compares the parcels in a list, and opens the one that is picked", async () => {
    const { calls } = routeFetch({
      "GET /api/checks/c1": () => check({ parcels: [ID, second.parcel_id], sites: [check().sites[0]!, second] }),
      "GET /api/checks/c1/sites/0": () => site(),
      "GET /api/checks/c1/sites/1": () => site({ n: 1, parcel_ids: [second.parcel_id], parcel: { ...site().parcel, parcel_id: second.parcel_id, commune: "Michałowice" }, verdict: second.verdict, excluded: false, exclusions: [] }),
    });
    const user = userEvent.setup();
    page();
    const list = await screen.findByTestId("parcel-list");
    expect(within(list).getAllByRole("button")).toHaveLength(2);
    expect(await screen.findByTestId("verdict")).toHaveTextContent("Odpada");
    await user.click(within(list).getByRole("button", { name: new RegExp(second.parcel_id) }));
    await waitFor(() => expect(screen.getByTestId("verdict")).toHaveTextContent("Warto sprawdzać dalej"));
    expect(calls.some((c) => c.key === "GET /api/checks/c1/sites/1")).toBe(true);
  });

  it("opens the parcel named in the address", async () => {
    routeFetch({
      "GET /api/checks/c1": () => check({ parcels: [ID, second.parcel_id], sites: [check().sites[0]!, second] }),
      "GET /api/checks/c1/sites/1": () => site({ n: 1, verdict: second.verdict }),
    });
    page("/sprawdz-dzialke/c1?n=1");
    expect(await screen.findByTestId("verdict")).toHaveTextContent("Warto sprawdzać dalej");
  });

  it("shows the finished parcels and the reason when a check failed on a later one", async () => {
    routeFetch({
      "GET /api/checks/c1": () => check({ status: "failed", error: "RuntimeError: no powiat with TERYT '1421' in the PRG snapshot" }),
      "GET /api/checks/c1/sites/0": () => site(),
    });
    page();
    const failed = await screen.findByTestId("failed");
    expect(failed).toHaveTextContent("Sprawdzenie nie powiodło się do końca.");
    expect(failed).toHaveTextContent("no powiat with TERYT");
    expect(await screen.findByTestId("verdict")).toBeInTheDocument();
    expect(document.title).toBe("Sprawdzenie nie powiodło się");
  });

  it("says why nothing could be checked, and offers to try again", async () => {
    routeFetch({ "GET /api/checks/c1": () => check({ status: "failed", sites: [], error: "Sprawdzenie przerwane przez restart serwera. Uruchom je ponownie." }) });
    page();
    expect(await screen.findByTestId("failed")).toHaveTextContent("Sprawdzenie przerwane przez restart serwera");
    expect(screen.getByRole("button", { name: /Sprawdź ponownie/ })).toBeInTheDocument();
    expect(screen.queryByTestId("verdict")).not.toBeInTheDocument();
  });

  it("says so when there is no such check", async () => {
    routeFetch({ "GET /api/checks/c1": () => jsonResponse(404, { detail: "Nie ma takiego sprawdzenia." }) });
    page();
    expect(await screen.findByText("Nie ma takiego sprawdzenia")).toBeInTheDocument();
  });
});
