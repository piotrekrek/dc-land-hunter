import { Check, CircleHelp, TriangleAlert, X } from "lucide-react";

import type { ProfileVerdict } from "@/api/client";
import { Mark } from "@/components/site/Mark";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const CATEGORY = {
  green: { icon: Check, tone: "text-ok border-ok", word: "Warto" },
  yellow: { icon: TriangleAlert, tone: "text-warn border-warn", word: "Warunkowo" },
  red: { icon: X, tone: "text-bad border-bad", word: "Odpada" },
  manual: { icon: CircleHelp, tone: "text-muted-foreground border-border", word: "Do weryfikacji" },
} as const;

const PROFILE_LABEL = { built: "DC budowane", container: "DC kontenerowe" } as const;

/** One line under the chip: the deciding reason first (a blocker, then what blocks the answer, then the best sign). */
function headline(p: ProfileVerdict): string {
  const blocker = p.blockers[0];
  if (blocker) return blocker.text_pl;
  const open = p.to_check.find((c) => c.blocking);
  if (open) return open.text_pl;
  const positive = p.positives[0];
  if (positive) return positive.text_pl;
  return "Brak rozstrzygających danych.";
}

function ProfileCard({ profile }: { profile: ProfileVerdict }) {
  const { icon: Icon, tone, word } = CATEGORY[profile.category];
  const label = PROFILE_LABEL[profile.profile];
  return (
    <section
      className="rounded-md border p-4"
      aria-label={`Profil: ${label}`}
      data-testid={`profile-${profile.profile}`}
      data-category={profile.category}
    >
      <div className="flex items-center gap-3">
        <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-full border-2", tone)} role="img" aria-label={word}>
          <Icon className="size-4" strokeWidth={2.5} aria-hidden />
        </span>
        <div>
          <h3 className="m-0 text-sm font-semibold">{label}</h3>
          <p className="m-0 text-base font-medium">{word}</p>
        </div>
      </div>
      <p className="mt-2 mb-0 text-sm">{headline(profile)}</p>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer py-1 font-medium">Dlaczego?</summary>
        <ReasonList title="Co wyklucza" kind="bad" items={profile.blockers} />
        <ReasonList title="Co przemawia za" kind="ok" items={profile.positives} />
        {profile.to_check.length > 0 && (
          <div className="mt-2">
            <h4 className="m-0 text-xs font-semibold text-muted-foreground">Do sprawdzenia</h4>
            <ul className="m-0 list-none p-0">
              {profile.to_check.map((c) => (
                <li key={c.key} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2 py-1">
                  <Mark kind="unk" />
                  <span>
                    {c.text_pl}
                    {c.blocking && <b className="ml-1 font-medium">(od tego zależy wynik)</b>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="mt-2 mb-0 text-xs text-muted-foreground">Wynik cząstkowy {Math.round(profile.score)} z 100.</p>
      </details>
    </section>
  );
}

function ReasonList({ title, kind, items }: { title: string; kind: "ok" | "bad"; items: ProfileVerdict["blockers"] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2">
      <h4 className="m-0 text-xs font-semibold text-muted-foreground">{title}</h4>
      <ul className="m-0 list-none p-0">
        {items.map((r) => (
          <li key={r.key} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2 py-1">
            <Mark kind={kind} />
            <span>
              {r.text_pl}
              {r.source_id && (
                <span className="text-muted-foreground">
                  {" "}
                  (źródło: {r.source_id}
                  {r.as_of ? `, stan na ${formatDate(r.as_of)}` : ""})
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Two investor profiles side by side. Nothing renders for a site stored before profiles existed. */
export function ProfileCards({ profiles, className }: { profiles?: Record<string, ProfileVerdict> | null; className?: string }) {
  const built = profiles?.built;
  const container = profiles?.container;
  if (!built || !container) return null;
  return (
    <section aria-label="Profile inwestora" className={className} data-testid="profiles">
      <div className="grid gap-4 md:grid-cols-2">
        <ProfileCard profile={built} />
        <ProfileCard profile={container} />
      </div>
      <p className="mt-2 mb-0 text-xs text-muted-foreground">
        Progi to założenia robocze. Moc z sieci jest szacunkowa; wiąże ekspertyza do warunków przyłączenia. Ocena prawna wymaga
        prawnika i urbanisty.
      </p>
    </section>
  );
}
