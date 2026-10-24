import type { CheckOut, CheckSiteDetail, CheckStep, ResolveOut } from "@/api/client";

export const ID = "306201_1.0002.1573";

export const sheet: CheckSiteDetail["sheet"] = {
  origin_2180: [449000, 485850],
  bbox_m: [-550, -550, 550, 550],
  parcel: [[[-50, -50], [50, -50], [50, 50], [-50, 50], [-50, -50]]],
  layers: [
    { key: "flood", label_pl: "Strefa zalewowa 1%", kind: "polygon", paths: [[[0, 0], [200, 0], [200, 100], [0, 100]]], truncated: false },
    { key: "forest", label_pl: "Las", kind: "polygon", paths: [], truncated: true },
    { key: "housing", label_pl: "Budynki mieszkalne", kind: "polygon", paths: [[[300, 300], [310, 300], [310, 310]]], truncated: false },
    { key: "mining", label_pl: "Obszar górniczy", kind: "polygon", paths: [], truncated: false },
  ],
};

export function site(over: Partial<CheckSiteDetail> = {}): CheckSiteDetail {
  return {
    site_id: "s1",
    zone_id: null,
    area_ha: 1.2,
    estimated_mw: 7.2,
    score: 38,
    upside_score: 61,
    completeness: 0.6,
    excluded: true,
    grid_badge: "Bliskość sieci",
    large_campus: false,
    split_suggested: false,
    flags: [],
    lon: 18,
    lat: 52,
    mw_fact: { value: 7.2, source_id: "egib", as_of: "2026-10-25T09:00:00+00:00", confidence: "estimated" },
    parcel_ids: [ID],
    scored_at: "2026-10-25T09:00:00+00:00",
    exclusions: [{ key: "flood", reason_pl: "strefa zalewowa 10% lub 1%: 62% terenu (próg 0%)", mode: "exclude", penalty_points: 30 }],
    categories: [{ category: "grid", points: 20, max_points: 45, upside_points: 40 }],
    items: [
      { key: "line110", category: "grid", label_pl: "Linia 110 kV", points: 20, max_points: 27, upside_points: 27, confidence: "estimated", source_id: "osm_power", as_of: null, explanation_pl: "Linia 110 kV w odległości 1,4 km.", value: 1.4 },
    ],
    features: {},
    geometry: { type: "Polygon", coordinates: [] },
    n: 0,
    check_id: "c1",
    requested_by: "ala@example.org",
    checked_at: "2026-10-25T09:00:00+00:00",
    verdict: { kind: "odpada", text_pl: "Odpada: strefa zalewowa 10% lub 1%: 62% terenu (próg 0%)." },
    outside_aoi: false,
    unknowns: [{ label_pl: "Nie sprawdzono: rejestr zabytków", why_pl: "brak publicznego źródła danych, ten warunek nie został sprawdzony" }],
    next_steps: [
      { key: "mpzp", title_pl: "Zapytaj gminę Konin (miasto) o MPZP lub plan ogólny dla działki", copy_text: "Dzień dobry, proszę o informację o przeznaczeniu działki." },
      { key: "kw", title_pl: "Sprawdź księgę wieczystą ręcznie w EKW", copy_text: null },
    ],
    sheet,
    layer_ages: { osm_power: "2026-10-12T06:00:00+00:00", isok: null },
    parcel: { parcel_id: ID, voivodeship: "wielkopolskie", county: "powiat Konin", commune: "Konin (miasto)", obreb: "0002", powiat_teryt: "3062", registered_area_ha: 1.2 },
    ...over,
  };
}

export const step = (key: string, status: CheckStep["status"], over: Partial<CheckStep> = {}): CheckStep => ({
  parcel: 0,
  key,
  label_pl: key,
  status,
  seconds: status === "waiting" ? null : 1.5,
  finding_pl: null,
  error: null,
  ...over,
});

export function check(over: Partial<CheckOut> = {}): CheckOut {
  return {
    id: "c1",
    status: "done",
    requested_by: "ala@example.org",
    created_at: "2026-10-25T09:00:00+00:00",
    started_at: "2026-10-25T09:00:01+00:00",
    finished_at: "2026-10-25T09:01:00+00:00",
    error: null,
    parcels: [ID],
    steps: [],
    estimate_seconds: null,
    sites: [
      {
        n: 0,
        parcel_id: ID,
        commune: "Konin (miasto)",
        obreb: "0002",
        area_ha: 1.2,
        estimated_mw: 7.2,
        score: 38,
        upside_score: 61,
        completeness: 0.6,
        excluded: true,
        verdict: { kind: "odpada", text_pl: "Odpada: strefa zalewowa 10% lub 1%: 62% terenu (próg 0%)." },
        outside_aoi: false,
      },
    ],
    ...over,
  };
}

export function resolved(over: Partial<ResolveOut> = {}): ResolveOut {
  return {
    items: [
      {
        parcel_id: ID,
        powiat_teryt: "3062",
        voivodeship: "wielkopolskie",
        county: "powiat Konin",
        commune: "Konin (miasto)",
        obreb: "0002",
        area_ha: 1.2,
        lat: 52.22,
        lon: 18.26,
        in_aoi: true,
        source_text: ID,
        outline: { type: "Polygon", coordinates: [[[18.2598, 52.2198], [18.2602, 52.2198], [18.2602, 52.2202], [18.2598, 52.2202], [18.2598, 52.2198]]] },
      },
    ],
    issues: [],
    dropped: 0,
    duplicates: 0,
    ...over,
  };
}

type Handler = (body: unknown) => unknown;

/**
 * A fetch stub that tells methods apart ("GET /api/checks" and "POST /api/checks" are different requests).
 * A handler returns JSON, or a Response for a status other than 200. `/api/auth/me` is answered for the login.
 */
export function routeFetch(handlers: Record<string, Handler>) {
  const calls: { key: string; body: unknown }[] = [];
  const all: Record<string, Handler> = { "GET /api/auth/me": () => ({ email: "ala@example.org", role: "member" }), ...handlers };
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const key = `${request.method} ${new URL(request.url, "http://localhost").pathname}`;
    const body = request.method === "GET" ? null : await request.json().catch(() => null);
    calls.push({ key, body });
    const handler = all[key];
    if (!handler) return new Response("not found", { status: 404 });
    const out = handler(body);
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return { calls, spy };
}

export const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
