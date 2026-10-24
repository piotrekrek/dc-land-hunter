import { CircleAlert, LoaderCircle, MapPin } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { ResolveOut } from "@/api/client";
import { OutlineSvg } from "@/components/check/OutlineSvg";
import { RecentChecks } from "@/components/check/RecentChecks";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRecentChecks, useResolve, useStartCheck } from "@/lib/check";
import { formatHa } from "@/lib/format";

// The map pulls in MapLibre, so it loads only when someone asks for it.
const MapPicker = lazy(() => import("@/components/check/MapPicker").then((m) => ({ default: m.MapPicker })));

function Issues({ issues, id }: { issues: ResolveOut["issues"]; id: string }) {
  const ref = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (issues.length > 1) ref.current?.focus(); // several problems: bring the summary into view for keyboard and screen reader users
  }, [issues]);
  if (issues.length === 0) return null;
  return (
    <ul ref={ref} id={id} tabIndex={-1} role="alert" className="mt-3 flex list-none flex-col gap-1.5 p-0 text-sm" data-testid="issues">
      {issues.map((issue) => (
        <li key={issue.text + issue.message_pl} className="flex gap-2 text-bad">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            <b className="font-medium">„{issue.text}”:</b> {issue.message_pl}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Confirm({ resolved, onBack, onStart, starting, error }: { resolved: ResolveOut; onBack: () => void; onStart: () => void; starting: boolean; error: string | null }) {
  const many = resolved.items.length > 1;
  return (
    <section aria-labelledby="confirm-h" data-testid="confirm">
      <h2 id="confirm-h" className="text-lg font-semibold">
        {many ? "Czy to te działki?" : "Czy to ta działka?"}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">Sprawdzenie zwykle trwa około minuty. Upewnij się, że obrys się zgadza.</p>
      <ul className="mt-4 grid list-none gap-4 p-0 sm:grid-cols-2">
        {resolved.items.map((parcel) => (
          <li key={parcel.parcel_id} className="flex flex-col gap-2" data-testid="confirm-parcel">
            <OutlineSvg parcel={parcel} />
            <div className="text-sm">
              <div className="font-cond text-base font-medium break-all">{parcel.parcel_id}</div>
              <div className="text-muted-foreground">
                {parcel.commune}, obręb {parcel.obreb}, {parcel.county}, {parcel.voivodeship}
              </div>
              <div className="tabular-nums">{formatHa(parcel.area_ha)} (z geometrii ewidencji)</div>
              {parcel.in_aoi === false && <Badge variant="warn">poza obszarem zainteresowania</Badge>}
            </div>
          </li>
        ))}
      </ul>
      <Issues issues={resolved.issues} id="confirm-issues" />
      {(resolved.duplicates > 0 || resolved.dropped > 0) && (
        <p className="mt-3 text-sm text-muted-foreground">
          {resolved.duplicates > 0 && `Powtórzone numery policzono raz (${resolved.duplicates}). `}
          {resolved.dropped > 0 && `Sprawdzimy pierwsze 20; pominięto ${resolved.dropped}.`}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {error}
        </p>
      )}
      <div className="mt-5 flex gap-2">
        <Button type="button" onClick={onStart} disabled={starting}>
          {starting ? "Uruchamiam…" : many ? "Sprawdź działki" : "Sprawdź działkę"}
        </Button>
        <Button type="button" variant="outline" onClick={onBack} disabled={starting}>
          Zmień
        </Button>
      </div>
    </section>
  );
}

/** Wskaż działkę: paste what you were sent, see which parcel it is, then start the check. */
export function SprawdzDzialkePage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  const [resolved, setResolved] = useState<ResolveOut | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const resolve = useResolve();
  const start = useStartCheck();
  const recent = useRecentChecks();
  const prefilled = useRef(false);

  const run = (value: string) => {
    resolve.mutate(value, {
      onSuccess: (data) => {
        setResolved(data);
        setConfirming(data.items.length > 0);
      },
    });
  };

  useEffect(() => {
    // From the site card ("Sprawdź tę działkę"): the id arrives in the address and is looked up at once.
    if (prefilled.current || !params.get("q")) return;
    prefilled.current = true;
    run(params.get("q")!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const begin = () => {
    if (!resolved) return;
    start.mutate({ parcels: resolved.items.map((p) => p.parcel_id) }, { onSuccess: ({ id }) => navigate(`/sprawdz-dzialke/${id}`) });
  };

  const onPick = (lat: number, lon: number) => {
    const next = `${text.trim() ? `${text.trim()}\n` : ""}${lat.toFixed(5)} ${lon.toFixed(5)}`;
    setText(next);
    run(next);
  };

  const issues = resolved?.issues ?? [];
  const lookupError = resolve.error instanceof Error ? resolve.error.message : null;

  return (
    <PageScroll label="Sprawdź działkę">
      <div className="mx-auto max-w-5xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Sprawdź działkę</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Ktoś zaproponował działkę? Wklej numer od pośrednika, współrzędne albo link z Map Google. Możesz wkleić kilka naraz.
        </p>

        {confirming && resolved ? (
          <div className="mt-6">
            <Confirm
              resolved={resolved}
              onBack={() => setConfirming(false)}
              onStart={begin}
              starting={start.isPending}
              error={start.error instanceof Error ? start.error.message : null}
            />
          </div>
        ) : (
          <form
            className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) run(text);
            }}
          >
            <div>
              <label htmlFor="parcel-text" className="text-sm font-medium">
                Numer działki, współrzędne lub link
              </label>
              <textarea
                id="parcel-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={5}
                placeholder={"306201_1.0002.1573\n52,2297 20,9511"}
                aria-describedby={issues.length > 0 ? "input-issues" : "input-hint"}
                aria-invalid={issues.length > 0}
                className="mt-1.5 w-full rounded-md border bg-surface p-3 font-cond text-base focus-visible:outline-2 focus-visible:outline-accent"
              />
              <p id="input-hint" className="mt-1 text-xs text-muted-foreground">
                Każdy numer w osobnym wierszu. Pełny identyfikator wygląda tak: 306201_1.0002.1573.
              </p>
              <Issues issues={issues} id="input-issues" />
              {lookupError && (
                <p role="alert" className="mt-3 text-sm text-bad">
                  {lookupError}
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <Button type="submit" disabled={!text.trim() || resolve.isPending}>
                  {resolve.isPending ? (
                    <>
                      <LoaderCircle className="animate-spin" aria-hidden /> Szukam…
                    </>
                  ) : (
                    "Znajdź działkę"
                  )}
                </Button>
                <Button type="button" variant="outline" aria-pressed={showMap} onClick={() => setShowMap((v) => !v)}>
                  <MapPin aria-hidden /> Wskaż na mapie
                </Button>
              </div>
            </div>
            {showMap && (
              <Suspense fallback={<p className="text-sm text-muted-foreground">Wczytuję mapę…</p>}>
                <MapPicker onPick={onPick} />
              </Suspense>
            )}
          </form>
        )}

        <section className="mt-10" aria-labelledby="recent-h">
          <h2 id="recent-h" className="mb-2 text-sm font-semibold">
            Ostatnie sprawdzenia
          </h2>
          {recent.data ? <RecentChecks items={recent.data.items} /> : <p className="text-sm text-muted-foreground">Wczytuję…</p>}
        </section>
      </div>
    </PageScroll>
  );
}
