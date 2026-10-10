# CLAUDE.md: DC Land Hunter

Working context for Claude Code. The full specification is in `PROJECT_BRIEF.md`; sections 0–3 are copied below.
The phase plan is in `docs/PLAN.md` and the current state is in `docs/STATUS.md`.

## Repo conventions

- Layout: `pipeline/` (Python ETL + scoring, package `dclh_pipeline`), `api/` (FastAPI, package `dclh_api`),
  `web/` (React + Vite), `config/`, `data/`, `docs/`, `infra/`, `tests/`.
- Python 3.12, managed with `uv` as one workspace (root `pyproject.toml`). Run `uv sync` once.
- Fast checks before every commit:
  - `uv run ruff check . && uv run ruff format --check . && uv run mypy`
  - `uv run pytest`
  - `cd web && pnpm lint && pnpm typecheck && pnpm test`
- Everything runs locally with `docker compose -f infra/docker-compose.yml up --build` (see `docs/RUNBOOK.md`).
- All geometry work happens in EPSG:2180 (PUWG 1992). Store WGS84 (EPSG:4326) only for display and tiles.
- Every derived value carries `source_id`, `as_of` and `confidence` (`verified` | `estimated` | `missing`).
- Code, comments and docs are in English. All user-facing UI text is in Polish.
- Polish formatting in the UI: `12,5 ha`, `8 MW`, `2,3 km`, dates `dd.mm.rrrr` (helpers in `web/src/lib/format.ts`).
- Source endpoints are unverified until `docs/SOURCES.md` marks them ✓. Many Polish public services block
  non-Polish IPs, so the discovery command (`uv run dclh discover`) must run from a machine in Poland.

## 0. How to work on this project

- Read the whole brief first. Start in plan mode, write `docs/PLAN.md` (phases, tasks, risks), then build phase by phase (section 11).
- After every phase: run all tests, update `docs/STATUS.md` (what works, what is stubbed, known issues) and give a short demo script.
- Every URL and endpoint in this brief comes from desk research done in October 2026. Treat them as **unverified until Phase 0 confirms them**. Record the verified ones in `docs/SOURCES.md`.
- Ask the owner before you add a paid service, automate a source marked "manual only", or store personal data.
- Code, comments and docs are in English. **The whole UI is in Polish.**
- Prefer boring, well-known tools. No custom frameworks.

## 1. Goal

The app helps a small investor team **hunt for land west of the Vistula that can host a small AI data centre**, and find it before the market does.

It produces a ranked, explained shortlist of sites, a weekly "what changed" feed and a lightweight deal pipeline. It does not promise grid capacity. It finds the most promising plots and tells the user exactly what still needs checking by phone or letter.

**Users:** 2–5 Polish-speaking people, a product manager and investor partners. They research on a desktop and check alerts on a phone.

**Jobs to be done:**
- "Show me the best sites in region X for an AI data centre of 60 MW or more, and why."
- "What changed since last week?" (new zones, new grid data, new land offers, score changes)
- "Someone offered me this plot. Is it any good?"
- "Track the deals I'm working on."
- "Export a one-pager to send to a partner."

## 2. Scope

### 2.1 Area of interest (AOI)

The AOI is Poland on the left (west and north) bank of the Vistula: from the German border to the river.

How to build it: take the Polish boundary from PRG (GUGiK) and the Vistula centreline from OSM (`waterway=river`, `name=Wisła`, main stem only). Extend the upstream end to the national border near the source in Beskid Śląski, then split the country polygon. Keep the western/northern part.

By this rule, Warsaw's left bank, Kraków north of the river, Silesia, Łódź, Wielkopolska, Pomorze west of the Vistula and Świętokrzyskie are **in**. Bielsko-Biała and Podbeskidzie (south of the upper Vistula) are **out**.

Store the AOI as `data/aoi/aoi.geojson`. The owner can edit it in Settings, for example to add Bielsko-Biała back.

### 2.2 Target profile: a small AI data centre of 60 MW or more

The owner's rule of thumb is **60 MW needs about 10 ha of industrial land**, which is about 6 MW/ha. "Small" here means not hyperscale, so there is no hard upper limit.

| Parameter | Value |
|---|---|
| Connection power | **min 60 MW**. More is better: 60–150 MW is the sweet spot, and above 150 MW is flagged "duży kampus" but not excluded. |
| Land | **min 10 ha** of industrial land (a cluster of adjacent parcels is fine). 10–30 ha is ideal; expansion land next to the site is a bonus. |
| Estimated capacity | `szacowana_moc_MW = area_ha × 6` (density configurable), shown on every site |
| Grid | **110 kV**: a new GPZ 110/SN on site, a dedicated 110 kV bay in an existing GPZ, or a tap from a 110 kV line. Two independent 110 kV feeds is a plus. For more than about 150 MW, a 220/400 kV PSE node nearby is a strong plus. |
| Key distances | 110 kV line ≤ 1 km is ideal, ≤ 3 km acceptable. GPZ with 110 kV ≤ 5 km. 220/400 kV node ≤ 25 km is a bonus. |
| Typical use | AI inference campus, regional GPU cloud, colocation with AI halls |
| Cooling | Closed-loop liquid or dry coolers; **no river water** |

Out of scope by design: dense urban sites under 10 ha (for example Warsaw colocation on 4–5 ha) and hyperscale projects above 500 MW.

### 2.3 Non-goals for the MVP

- No hyperscale (>100 MW) screening.
- No automated land and mortgage register (EKW) lookups: CAPTCHA, terms, personal data. Provide a manual link only.
- No scraping of property portals (Otodom, OLX and similar) until their terms are checked.
- No BIP (municipal bulletin) crawler. That is v2.
- No owner contact data collection. Users may type notes manually; treat them as personal data (section 9.6).

## 3. Domain notes (must-know)

- **The grid is the bottleneck.** PSE (the transmission operator) reports zero free load capacity at existing 220/400 kV stations. Its queue is full of speculative applications. Public data gives proxies, not guarantees, so always show the confidence of grid data.
- **60–150 MW connects to the 110 kV network.** In Poland that network belongs to the distribution operators (OSD/DSO), not PSE. Above about 150 MW, PSE's 220/400 kV network usually comes into play.
- **DSOs west of the Vistula:** **Enea Operator** (west), **Energa-Operator** (north), **Tauron Dystrybucja** (south-west and south) and **PGE Dystrybucja** (Łódź and the centre).
- **What they publish:** Enea and Energa are the only DSOs that publish available capacity **for loads**, and mostly at medium voltage per GPZ. Treat that as a signal of GPZ headroom, not a 60 MW answer. Tauron publishes planned capacity, which it calls "directional". PGE publishes nothing for loads.
- **Grid score at 60 MW+ is mostly proxy-based:**
  - Distance to 110 kV lines and GPZs.
  - Number of independent lines nearby.
  - PSE planned load stations, its closed-switchyard list and nearby ODB entries.
  - Powered brownfields.

  Label it honestly in the UI.
- **Connection rules since 30 Apr 2026** (Prawo energetyczne amendment):
  - Conditions are valid for 12 months.
  - Application fee: 1 PLN/kW, max 100k PLN.
  - Connection advance: 60 PLN/kW, max 6 mln PLN.
  - Security: 30 PLN/kW up to 100 MW.
  - The agreement expires if building-permit milestones are missed.
  - New flexible and configurable connection agreements.
  - The operator re-verifies conditions before signing.
  - Example: securing 60 MW costs about 60k PLN in fees and ties up about 3.6 mln PLN in advance plus 1.8 mln PLN in security. That is roughly 5.4 mln PLN frozen while the project waits.
  - Show these numbers in a calculator on the site card. **The figures need legal verification and are configurable.**
- **Transferring grid conditions to a buyer is legally contested.** A 2013 Court of Appeal ruling treated them as public-law rights that cannot be assigned. The usual workaround is holding a site in an SPV and selling shares. The app only flags this; it gives no legal advice.
- **Zoning:**
  - Best: MPZP with production/service designations (P, PU, P/U, U/P, also E or technical infrastructure).
  - Next: a plan ogólny "strefa gospodarcza" (SG).
  - WZ decisions are a weak route for new sites: infill only and 5-year validity.
  - The Rejestr Urbanistyczny is incomplete until 30 Nov 2026, so a missing plan means **unknown**, not **bad**.
- **Heat reuse:** EU EED Art. 26 requires data centres above 1 MW to reuse waste heat unless shown infeasible. A district heating network nearby is a plus.
- **"Powered brownfields"** are gold. These are closed or underused large industrial sites (steel, chemicals, paper, cement, glass, former power plants and mines) that already have their own 110 kV substation, so an existing connection may be reusable. Detect them explicitly.
- **Investment value drivers:** grid conditions > zoning > size and shape > few owners > access and fibre > environmental decision. **Deal killers:** flood zone, Natura 2000, forest, top soil classes, mining area, housing too close, too many owners.

