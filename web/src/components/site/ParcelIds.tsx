import { Copy, ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { geoportalParcelUrl } from "@/lib/sites";

/** Past this many the list is a wall of text; the full list stays one click away in "Kopiuj wszystkie". */
const SHOWN = 12;

function CopyButton({ text, label, children }: { text: string; label: string; children: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* the ids stay selectable; copying is a convenience */
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Button type="button" size="sm" variant="outline" onClick={copy} aria-label={label}>
      <Copy aria-hidden /> {copied ? "Skopiowano" : children}
    </Button>
  );
}

/** The cadastral parcel ids of the site, each with a copy button and a Geoportal link: what to send a gmina or a notary. */
export function ParcelIds({ ids }: { ids: readonly string[] }) {
  const [all, setAll] = useState(false);
  if (ids.length === 0) return null;
  const shown = all ? ids : ids.slice(0, SHOWN);
  return (
    <section className="mt-6" aria-label="Numery działek">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Numery działek ({ids.length})</h2>
        {ids.length > 1 && (
          <CopyButton text={ids.join("\n")} label="Kopiuj wszystkie numery działek">
            Kopiuj wszystkie
          </CopyButton>
        )}
      </div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {shown.map((id) => (
          <li key={id} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-cond font-medium break-all">{id}</span>
            <CopyButton text={id} label={`Kopiuj numer działki ${id}`}>
              Kopiuj
            </CopyButton>
            <a
              href={geoportalParcelUrl(id)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
            >
              Geoportal <ExternalLink className="size-3" aria-hidden />
            </a>
          </li>
        ))}
      </ul>
      {ids.length > SHOWN && (
        <Button type="button" variant="ghost" size="sm" className="mt-1" onClick={() => setAll((v) => !v)}>
          {all ? "Pokaż mniej" : `Pokaż wszystkie (${ids.length})`}
        </Button>
      )}
    </section>
  );
}
