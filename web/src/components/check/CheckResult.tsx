import type { CheckSiteDetail } from "@/api/client";
import { ProfileCards } from "@/components/site/ProfileCards";
import { Protokol } from "@/components/site/Protokol";
import { Ruler } from "@/components/site/Ruler";
import { Mark } from "@/components/site/Mark";
import { formatDate, formatHa, formatMW } from "@/lib/format";
import { LAYER_AGE_LABEL } from "@/lib/check";

import { NextSteps } from "./NextSteps";
import { Sheet } from "./Sheet";
import { TitleBlock } from "./TitleBlock";
import { Verdict } from "./Verdict";

/** One checked parcel: the verdict first, then why, what is unknown, what to do next, and the drawing. */
export function CheckResult({ site }: { site: CheckSiteDetail }) {
  const ages = Object.entries(site.layer_ages).filter(([, v]) => v);
  return (
    <article aria-label={`Wynik dla działki ${site.parcel.parcel_id}`}>
      <Verdict verdict={site.verdict} />
      <ProfileCards profiles={site.profiles} className="mt-6" />
      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="flex flex-col gap-7">
          <section aria-label="Wynik">
            <Ruler score={site.score} upside={site.upside_score} dim={site.excluded} />
            <p className="-mt-6 text-xs text-muted-foreground">
              Szacowana moc {formatMW(site.estimated_mw)}: {formatHa(site.area_ha)} × gęstość z ustawień. To szacunek z powierzchni, nie moc
              przyłączeniowa.
            </p>
          </section>
          {site.unknowns.length > 0 && (
            <section aria-labelledby="unknowns-h">
              <h2 id="unknowns-h" className="mb-2 text-sm font-semibold">
                Nie wiemy
              </h2>
              <ul className="m-0 list-none border-t p-0" data-testid="unknowns">
                {site.unknowns.map((u) => (
                  <li key={u.label_pl} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2 border-b py-2 text-sm">
                    <Mark kind="unk" />
                    <span>
                      <b className="font-medium">{u.label_pl}.</b> <span className="text-muted-foreground">{u.why_pl}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section aria-labelledby="next-h">
            <h2 id="next-h" className="mb-2 text-sm font-semibold">
              Następne kroki
            </h2>
            <NextSteps steps={site.next_steps} />
          </section>
        </div>
        <div className="flex flex-col gap-3">
          <Sheet sheet={site.sheet} photoUrl={`/api/checks/${site.check_id}/sites/${site.n}/orthophoto`} />
          <TitleBlock site={site} />
        </div>
      </div>
      <h2 className="mt-8 mb-3 text-base font-semibold">Protokół sprawdzenia</h2>
      <Protokol site={site} />
      {ages.length > 0 && (
        <p className="mt-4 text-xs text-muted-foreground" data-testid="layer-ages">
          Dane z: {ages.map(([key, value]) => `${LAYER_AGE_LABEL[key] ?? key} ${formatDate(value)}`).join(", ")}.
        </p>
      )}
    </article>
  );
}
