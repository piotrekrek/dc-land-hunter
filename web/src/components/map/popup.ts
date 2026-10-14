/**
 * Click popups for the grid layers. Properties come straight from the Martin tiles (`tiles.substations`,
 * `tiles.lines`), so no API call is needed. Names and operators are free text from OpenStreetMap: the popup is
 * built with DOM nodes and `textContent`, never with an HTML string.
 */
import { formatNumber } from "@/lib/format";

/** Mirrors `WAY_ID_OFFSET` in the pipeline: ways are stored with this added to their OSM id. */
const WAY_ID_OFFSET = 1_000_000_000_000;

export type TileProps = Record<string, unknown>;

export interface PopupRow {
  label: string;
  value: string;
}

export interface PopupLink {
  label: string;
  href: string;
}

export interface PopupModel {
  title: string;
  rows: PopupRow[];
  links: PopupLink[];
  /** "lat; lng" in Polish notation, for the copy button. */
  coordinates: string;
}

const CLASS_LABELS: Record<string, string> = {
  GPZ_110_SN: "GPZ 110 kV/SN",
  NODE_220_400: "Węzeł PSE 220/400 kV",
  UNKNOWN_VOLTAGE: "Stacja, nieznane napięcie",
};

const DSO_LABELS: Record<string, string> = {
  enea: "Enea Operator",
  energa: "Energa-Operator",
  tauron: "Tauron Dystrybucja",
  pge: "PGE Dystrybucja",
  stoen: "Stoen Operator",
  pse: "PSE",
};

const CONFIDENCE_LABELS: Record<string, string> = {
  verified: "zweryfikowane",
  estimated: "szacowane",
  missing: "brak danych",
};

/** Vector tiles flatten arrays: `voltages` may arrive as an array, a JSON string or "{110000,15000}". */
export function parseVoltages(raw: unknown): number[] {
  let items: unknown[] = [];
  if (Array.isArray(raw)) items = raw;
  else if (typeof raw === "string") items = raw.match(/\d+/g) ?? [];
  else if (typeof raw === "number") items = [raw];
  return items.map(Number).filter((v) => Number.isFinite(v) && v > 0);
}

export const formatKv = (volts: number) => `${formatNumber(volts / 1000, 1)}\u00a0kV`;

const text = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
};

/** Tile booleans may arrive as true/"true"/1. */
const truthy = (value: unknown) => value === true || value === "true" || value === 1;

export const formatCoordinates = (lng: number, lat: number) => `${formatNumber(lat, 5)}; ${formatNumber(lng, 5)}`;

export function osmUrl(osmId: number): string {
  return osmId >= WAY_ID_OFFSET
    ? `https://www.openstreetmap.org/way/${osmId - WAY_ID_OFFSET}`
    : `https://www.openstreetmap.org/node/${osmId}`;
}

export const mapsUrl = (lng: number, lat: number) => `https://www.google.com/maps?q=${lat.toFixed(6)},${lng.toFixed(6)}`;

function operatorRow(props: TileProps): PopupRow | null {
  const operator = text(props.operator);
  const dso = text(props.dso);
  if (!operator && !dso) return null;
  const parts = [operator ?? DSO_LABELS[dso as string]];
  if (dso && DSO_LABELS[dso] && operator !== DSO_LABELS[dso]) parts.push(`OSD: ${DSO_LABELS[dso]}`);
  if (truthy(props.foreign_operator)) parts.push("obcy operator (możliwy brownfield z własnym przyłączem)");
  return { label: "Operator", value: parts.join(" · ") };
}

function confidenceRow(props: TileProps): PopupRow {
  const confidence = text(props.confidence) ?? "missing";
  return {
    label: "Wiarygodność",
    value: `${CONFIDENCE_LABELS[confidence] ?? confidence} · źródło: OpenStreetMap`,
  };
}

function links(osmId: number | null, lng: number, lat: number): PopupLink[] {
  const out: PopupLink[] = [{ label: "Otwórz w Mapach Google", href: mapsUrl(lng, lat) }];
  if (osmId !== null && Number.isFinite(osmId)) out.push({ label: "Obiekt w OpenStreetMap", href: osmUrl(osmId) });
  return out;
}

export function stationPopup(props: TileProps, lng: number, lat: number): PopupModel {
  const cls = text(props.cls) ?? "";
  const voltages = parseVoltages(props.voltages);
  const rows: PopupRow[] = [{ label: "Rodzaj", value: CLASS_LABELS[cls] ?? "Stacja" }];
  if (voltages.length) rows.push({ label: "Napięcia", value: voltages.map(formatKv).join(", ") });
  const operator = operatorRow(props);
  if (operator) rows.push(operator);
  rows.push(confidenceRow(props));
  rows.push({
    label: "Położenie",
    value: "wg OpenStreetMap, nie pomiar geodezyjny",
  });
  const osmId = Number(props.osm_id);
  return {
    title: text(props.name) ?? "Stacja bez nazwy",
    rows,
    links: links(Number.isFinite(osmId) ? osmId : null, lng, lat),
    coordinates: formatCoordinates(lng, lat),
  };
}

export function linePopup(props: TileProps, lng: number, lat: number): PopupModel {
  const volts = Number(props.voltage);
  const rows: PopupRow[] = [];
  if (Number.isFinite(volts) && volts > 0) rows.push({ label: "Napięcie", value: formatKv(volts) });
  const circuits = Number(props.circuits);
  if (Number.isFinite(circuits) && circuits > 0) rows.push({ label: "Obwody", value: formatNumber(circuits, 0) });
  const operator = operatorRow(props);
  if (operator) rows.push(operator);
  rows.push(confidenceRow(props));
  const osmId = Number(props.osm_id);
  return {
    title: Number.isFinite(volts) && volts > 0 ? `Linia ${formatKv(volts)}` : "Linia WN",
    rows,
    links: links(Number.isFinite(osmId) ? osmId : null, lng, lat),
    coordinates: formatCoordinates(lng, lat),
  };
}

/** Builds the popup body. Every string from the data goes through `textContent`. */
export function renderPopup(model: PopupModel, isLine = false): HTMLElement {
  const root = document.createElement("div");
  root.className = "dclh-popup";
  root.setAttribute("data-testid", "map-popup");

  const title = document.createElement("strong");
  title.textContent = model.title;
  root.append(title);

  const list = document.createElement("dl");
  for (const row of model.rows) {
    const dt = document.createElement("dt");
    dt.textContent = row.label;
    const dd = document.createElement("dd");
    dd.textContent = row.value;
    list.append(dt, dd);
  }
  root.append(list);

  const coords = document.createElement("div");
  const coordText = document.createElement("span");
  coordText.textContent = `${isLine ? "Klik" : "Współrzędne"}: ${model.coordinates}`;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Kopiuj";
  copy.addEventListener("click", () => {
    void navigator.clipboard?.writeText(model.coordinates).then(
      () => {
        copy.textContent = "Skopiowano";
      },
      () => {
        copy.textContent = "Nie udało się";
      },
    );
  });
  coords.append(coordText, " ", copy);
  root.append(coords);

  for (const link of model.links) {
    const a = document.createElement("a");
    a.href = link.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = link.label;
    const wrap = document.createElement("div");
    wrap.append(a);
    root.append(wrap);
  }
  return root;
}
