import { useId, useState } from "react";

import type { Sheet as SheetData } from "@/api/client";
import { cn } from "@/lib/utils";

/**
 * The survey drawing: the parcel and what lies around it, as thin ink lines and hatches (not colour fills), with a
 * scale bar and a north arrow. The server sends paths in metres, north up; the SVG only flips the vertical axis.
 * Hatching carries the meaning, so the drawing is readable without colour and prints well.
 */

interface LayerStyle {
  fill: string; // a pattern id suffix, a CSS colour, or "none"
  stroke: string;
  width: number; // px, constant on screen
  dash?: string;
  opacity?: number;
}

const STYLES: Record<string, LayerStyle> = {
  neighbours: { fill: "none", stroke: "var(--ink-3)", width: 0.75 },
  flood: { fill: "url(#PREFIX-flood)", stroke: "var(--layer-flood)", width: 1 },
  flood_02: { fill: "url(#PREFIX-flood02)", stroke: "var(--layer-flood)", width: 0.75, dash: "4 3" },
  forest: { fill: "url(#PREFIX-forest)", stroke: "var(--layer-forest)", width: 0.75 },
  housing: { fill: "var(--layer-housing)", stroke: "var(--layer-housing)", width: 0.5, opacity: 0.85 },
  mining: { fill: "url(#PREFIX-mining)", stroke: "var(--ink-2)", width: 0.75 },
};
const ZONING: LayerStyle = { fill: "var(--fill-2)", stroke: "var(--ink-2)", width: 0.75, dash: "5 3", opacity: 0.8 };

function styleOf(key: string, prefix: string): LayerStyle {
  const base = STYLES[key] ?? ZONING;
  return { ...base, fill: base.fill.replace("PREFIX", prefix) };
}

const NICE_M = [20, 50, 100, 200, 250, 500, 1000];

function niceLength(widthM: number): number {
  const target = widthM / 4;
  return [...NICE_M].reverse().find((v) => v <= target) ?? NICE_M[0]!;
}

function pathData(rings: number[][][]): string {
  return rings
    .map((ring) => {
      const [first, ...rest] = ring;
      if (!first) return "";
      if (rest.length === 0) return `M${first[0]} ${-first[1]!}h0`; // a point
      return `M${first[0]} ${-first[1]!}${rest.map(([x, y]) => `L${x} ${-y!}`).join("")}${ring.length > 2 ? "Z" : ""}`;
    })
    .join("");
}

/** Hatch patterns, in metres so they scale with the drawing. Shared by the drawing and the legend swatches. */
function Patterns({ prefix }: { prefix: string }) {
  return (
    <defs>
      <pattern id={`${prefix}-flood`} width="10" height="10" patternUnits="userSpaceOnUse">
        <path d="M0 3 Q2.5 0 5 3 T10 3 M0 8 Q2.5 5 5 8 T10 8" fill="none" stroke="var(--layer-flood)" strokeWidth="0.8" />
      </pattern>
      <pattern id={`${prefix}-flood02`} width="10" height="10" patternUnits="userSpaceOnUse">
        <path d="M0 5 Q2.5 2 5 5 T10 5" fill="none" stroke="var(--layer-flood)" strokeWidth="0.6" opacity="0.7" />
      </pattern>
      <pattern id={`${prefix}-forest`} width="9" height="9" patternUnits="userSpaceOnUse">
        <circle cx="2" cy="2" r="0.9" fill="var(--layer-forest)" />
        <circle cx="6.5" cy="6" r="0.9" fill="var(--layer-forest)" />
      </pattern>
      <pattern id={`${prefix}-mining`} width="8" height="8" patternUnits="userSpaceOnUse">
        <path d="M0 0L8 8M8 0L0 8" stroke="var(--ink-2)" strokeWidth="0.6" />
      </pattern>
    </defs>
  );
}

function Swatch({ layerKey, prefix }: { layerKey: string; prefix: string }) {
  const style = styleOf(layerKey, prefix);
  return (
    <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden className="shrink-0">
      <rect x="0.5" y="0.5" width="21" height="13" fill={style.fill} stroke={style.stroke} strokeWidth="1" strokeDasharray={style.dash} opacity={style.opacity} />
    </svg>
  );
}

type PhotoState = "off" | "loading" | "shown" | "failed";

/**
 * `photoUrl` is the orthophoto for exactly the drawing's box (the server asks the Geoportal). It is requested only
 * when the person turns it on, so a sheet nobody looks at on a photo costs nothing.
 */
export function Sheet({ sheet, photoUrl, className }: { sheet: SheetData; photoUrl?: string; className?: string }) {
  const prefix = useId().replace(/:/g, "");
  const [x0, y0, x1, y1] = sheet.bbox_m as [number, number, number, number];
  const width = x1 - x0;
  const height = y1 - y0;
  const drawable = sheet.layers.filter((l) => l.paths.length > 0 || l.truncated);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [photo, setPhoto] = useState<PhotoState>("off");
  const scale = niceLength(width);
  const unit = Math.min(width, height) / 40; // font and mark size that scales with the drawing
  const label = `Rysunek: obrys działki i okolica, bok rysunku ok. ${Math.round(width)} m. ${drawable
    .filter((l) => !l.truncated)
    .map((l) => l.label_pl)
    .join(", ")}.`;

  return (
    <figure className={cn("m-0", className)} data-testid="sheet">
      <div className="overflow-hidden border border-ink bg-paper">
        <svg viewBox={`${x0} ${-y1} ${width} ${height}`} role="img" aria-label={label} className="block h-auto w-full" preserveAspectRatio="xMidYMid meet">
          <Patterns prefix={prefix} />
          {photoUrl && (photo === "loading" || photo === "shown") && (
            <g data-testid="orthophoto">
              <image
                href={photoUrl}
                x={x0}
                y={-y1}
                width={width}
                height={height}
                preserveAspectRatio="none"
                onLoad={() => setPhoto("shown")}
                onError={() => setPhoto("failed")}
              />
              {/* A pale veil: the ink lines and hatches stay readable on a dark photo. */}
              <rect x={x0} y={-y1} width={width} height={height} fill="var(--paper)" opacity="0.35" />
            </g>
          )}
          {sheet.layers.map((layer) => {
            if (hidden[layer.key] || layer.paths.length === 0) return null;
            const style = styleOf(layer.key, prefix);
            return (
              <path
                key={layer.key}
                data-layer={layer.key}
                d={pathData(layer.paths)}
                fill={style.fill}
                fillRule="evenodd"
                stroke={style.stroke}
                strokeWidth={style.width}
                strokeDasharray={style.dash}
                opacity={style.opacity}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
          <path
            data-layer="parcel"
            d={pathData(sheet.parcel)}
            fill="var(--accent-soft)"
            fillOpacity="0.55"
            fillRule="evenodd"
            stroke="var(--ink)"
            strokeWidth="2.25"
            vectorEffect="non-scaling-stroke"
          />
          <g aria-hidden fontFamily="var(--font-cond)" fill="var(--ink-2)" fontSize={unit * 1.3}>
            <g transform={`translate(${x0 + unit * 2} ${-y0 - unit * 3})`}>
              <path d={`M0 0H${scale}`} stroke="var(--ink)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
              <path d={`M0 ${-unit * 0.6}V${unit * 0.6}M${scale} ${-unit * 0.6}V${unit * 0.6}`} stroke="var(--ink)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
              <text x={scale / 2} y={-unit * 1.1} textAnchor="middle" data-testid="scale-label">
                {scale >= 1000 ? `${scale / 1000} km` : `${scale} m`}
              </text>
            </g>
            <g transform={`translate(${x1 - unit * 3} ${-y1 + unit * 6})`}>
              <path d={`M0 ${unit * 2.4}V${-unit * 2.4}M${-unit * 0.9} ${-unit * 1.1}L0 ${-unit * 2.6}L${unit * 0.9} ${-unit * 1.1}`} fill="none" stroke="var(--ink)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
              <text y={unit * 4.4} textAnchor="middle">
                N
              </text>
            </g>
          </g>
        </svg>
      </div>
      <figcaption className="mt-2">
        {photoUrl && (
          <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            <button
              type="button"
              aria-pressed={photo === "loading" || photo === "shown"}
              onClick={() => setPhoto((p) => (p === "off" || p === "failed" ? "loading" : "off"))}
              className={cn(
                "inline-flex min-h-8 items-center rounded-md border px-2 py-1 text-xs hover:bg-surface-2",
                photo === "loading" || photo === "shown" ? "text-foreground" : "text-muted-foreground",
              )}
            >
              Ortofotomapa
            </button>
            {photo === "loading" && (
              <span role="status" className="text-xs text-muted-foreground">
                Wczytuję ortofotomapę…
              </span>
            )}
            {photo === "failed" && (
              <span role="alert" className="text-xs text-bad">
                Nie udało się pobrać ortofotomapy. Spróbuj za chwilę.
              </span>
            )}
            {photo === "shown" && <span className="text-xs text-muted-foreground">Źródło ortofotomapy: Geoportal, GUGiK.</span>}
          </div>
        )}
        <div role="group" aria-label="Warstwy rysunku" className="flex flex-wrap gap-1.5">
          {drawable.map((layer) => (
            <button
              key={layer.key}
              type="button"
              aria-pressed={!hidden[layer.key] && !layer.truncated}
              disabled={layer.truncated}
              title={layer.truncated ? "Warstwa jest za duża, żeby ją narysować" : undefined}
              onClick={() => setHidden((h) => ({ ...h, [layer.key]: !h[layer.key] }))}
              className={cn(
                "inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
                "hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60",
                hidden[layer.key] ? "text-muted-foreground line-through" : "text-foreground",
              )}
            >
              <Swatch layerKey={layer.key} prefix={prefix} />
              {layer.label_pl}
              {layer.truncated && <span className="text-muted-foreground"> (nie narysowana)</span>}
            </button>
          ))}
        </div>
      </figcaption>
    </figure>
  );
}
