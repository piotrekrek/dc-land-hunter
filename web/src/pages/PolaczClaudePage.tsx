import { CircleAlert, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isSafeRedirect, useConsent, useDecision } from "@/lib/claude";

const GONE = "Prośba o połączenie wygasła lub została już użyta. Zacznij łączenie w Claude od nowa.";
const BAD_REDIRECT = "Nieprawidłowy adres powrotu do Claude. Zacznij łączenie od nowa.";

/** Claude sends the browser here from its authorization request; the user approves or refuses (PLAN.md, Task 3). */
export function PolaczClaudePage({
  onRedirect = (url: string) => window.location.assign(url),
}: {
  onRedirect?: (url: string) => void;
}) {
  const reqId = useSearchParams()[0].get("req");
  const consent = useConsent(reqId);

  if (reqId === null || consent.isError) {
    return (
      <Shell>
        <EmptyState icon={CircleAlert} tone="bad" title="Nie można połączyć z Claude">
          <p>{consent.error?.message ?? GONE}</p>
        </EmptyState>
      </Shell>
    );
  }
  if (!consent.data) {
    return (
      <Shell>
        <EmptyState icon={LoaderCircle} title="Wczytuję…" className="[&_svg]:animate-spin" />
      </Shell>
    );
  }
  return <Consent reqId={reqId} info={consent.data} onRedirect={onRedirect} />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-full items-center justify-center p-4">
      <Card className="w-full max-w-md p-6">
        <div className="mb-5 flex items-center gap-2 font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-6" />
          DC Land Hunter
        </div>
        {children}
      </Card>
    </main>
  );
}

function Consent({
  reqId,
  info,
  onRedirect,
}: {
  reqId: string;
  info: NonNullable<ReturnType<typeof useConsent>["data"]>;
  onRedirect: (url: string) => void;
}) {
  const decision = useDecision(reqId);
  const [refused, setRefused] = useState<string | null>(null);

  function decide(approve: boolean) {
    setRefused(null);
    decision.mutate(approve, {
      onSuccess: (redirectTo) => {
        if (isSafeRedirect(redirectTo)) onRedirect(redirectTo);
        else setRefused(BAD_REDIRECT);
      },
    });
  }

  const error = refused ?? decision.error?.message ?? null;
  const busy = decision.isPending || decision.isSuccess;
  return (
    <Shell>
      <h1 className="text-lg font-semibold">Połączyć Claude z DC Land Hunter?</h1>
      <p className="mt-3 text-sm">
        Aplikacja: <b>{info.client_name}</b>
      </p>
      {info.can_connect ? (
        <>
          <p className="mt-3 text-sm">
            Claude będzie działać w Twoim imieniu (konto: {info.user_email}) i będzie mógł:
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm">
            {info.scopes.map((scope) => (
              <li key={scope.id}>{scope.label}</li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-muted-foreground">
            Dane, które Claude odczyta, trafią do Anthropic. Notatki z projektów nie są udostępniane. Połączenie możesz
            odłączyć w Ustawieniach.
          </p>
        </>
      ) : (
        <p role="status" className="mt-3 rounded-md border border-warn/40 bg-surface-2 px-3 py-2 text-sm">
          Twoje konto nie ma włączonego dostępu dla Claude. Poproś administratora.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-4 rounded-md border border-bad/40 bg-bad-soft px-3 py-2 text-sm text-bad">
          {error}
        </p>
      )}
      <div className="mt-5 flex gap-2">
        {info.can_connect && (
          <Button onClick={() => decide(true)} disabled={busy}>
            {decision.isPending && <LoaderCircle className="animate-spin" aria-hidden />}
            Zezwól
          </Button>
        )}
        <Button variant="outline" onClick={() => decide(false)} disabled={busy}>
          Odmów
        </Button>
      </div>
    </Shell>
  );
}
