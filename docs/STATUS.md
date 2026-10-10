# Status

Last updated: 10.10.2026 · Phase 0 (setup and source discovery)

## Phase 0: what works

- Repo layout, `CLAUDE.md`, `docs/PLAN.md` (phases, decisions, risks).
- `docker compose -f infra/docker-compose.yml up --build` starts db (PostGIS), migrate, api, worker,
  scheduler, martin and web (Caddy). Verified locally: all services healthy, `/api/health` reports PostGIS,
  `/tiles/health` OK, deep links work, security headers set.
- Python workspace (`pipeline` + `api`): typed config loaders for `scoring.yaml` / `sources.yaml`,
  polite HTTP client (User-Agent + contact, 1 req/s per host, robots.txt, backoff), content-addressed
  snapshot store, connector base class, runner with graceful degradation (last good snapshot + `SOURCE_FAILURE`),
  job queue worker, `dclh discover` spike tool. 50 tests (45 without a database).
- API: `/api/health`, `/api/sources`, `/api/config/scoring`; OpenAPI exported and TS types generated.
- Web (Polish UI): shell with all screens, MapLibre map centred on the AOI (position kept in URL),
  Źródła danych and Ustawienia (read-only) pages, light/dark themes, mobile layout, Polish formatters.
  12 unit tests; Playwright smoke + axe tests pass on desktop and mobile.
- CI workflow (`.github/workflows/ci.yml`): ruff, mypy, pytest with PostGIS, eslint, tsc, vitest, build,
  generated-types check, image build, compose + Playwright smoke. **Not yet run on GitHub** (nothing pushed before).

## Phase 0: NOT done

- **Source verification (the main acceptance item) is not done.** The cloud environment this was built in
  cannot reach any Polish public host (403 from its egress policy), and many of those services block foreign
  IPs anyway. All 24 MVP sources are "not yet checked" in `docs/SOURCES.md`.
  **Next step for the owner:** run `uv run dclh discover --capture-network --write-docs` from a machine in
  Poland and commit `data/discovery/` + `docs/SOURCES.md` (instructions in `docs/SOURCES.md`).
- Golden-set coordinates are not researched (left empty on purpose, `tests/golden/sites.yaml`).

## Stubbed / placeholders

- Home list is an empty state; Nowości, Sprawdź działkę, Moje projekty, Eksport are placeholder pages.
- No auth yet (planned with the user tables in Phase 1/4). The stack must not be exposed publicly as is.
- `tiles` schema is empty, so the map shows only the basemap.

## Known issues / caveats

- Martin image: this sandbox could not pull `ghcr.io`, so Martin was tested with the official 1.16.1 release
  binary in a local stand-in image. The compose file uses the official `ghcr.io/maplibre/martin:1.16.1` image;
  CI will exercise it for real.
- Basemap is the free OpenFreeMap style; it could not be loaded in the sandbox (blocked), so only the
  no-basemap fallback was seen. Check it renders on a normal network.
- Docker Hub rate limits (429) can slow image pulls on shared IPs.
- The GitHub repository is public: keep secrets and personal data out (`.env` is gitignored).

## Demo script

1. `docker compose -f infra/docker-compose.yml up --build`, open http://localhost:8080.
2. Mapa i lista: Polish shell, map, empty-state list. Toggle the theme (top right); resize to phone width.
3. Źródła danych: 28 sources with status; run `docker compose -f infra/docker-compose.yml exec api dclh enqueue discover`
   and refresh after ~30 s to see the discovery result appear.
4. Ustawienia: scoring weights and profile read from `config/scoring.yaml`.

## Manual review loop (top 30 sites)

Starts in Phase 2.
