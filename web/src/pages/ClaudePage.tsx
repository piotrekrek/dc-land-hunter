import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ExternalLink, LoaderCircle, ServerCrash } from "lucide-react";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { EXAMPLE_PROMPTS, claudeCodeCommand, invitation } from "@/lib/claude";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const CONNECTIONS = ["oauth", "connections"] as const;
const TEAM = ["team"] as const;
const HELP_URL = "https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp";

/** The steps for claude.ai (web, desktop, phone), in Claude's current wording (Anthropic help, 11.11.2026). */
const WEB_STEPS = [
  "Otwórz claude.ai (lub aplikację Claude na komputerze).",
  "Wejdź w Customize → Connectors, kliknij + Add, potem Add custom connector.",
  "Nazwa: DC Land Hunter. Adres: powyższy. Kliknij Add.",
  "Kliknij Connect, zaloguj się do DC Land Hunter i wybierz Zezwól.",
];

/** The "Claude" tab: connect Claude to the app, what to ask, connected apps and (admins) the team's access. */
export function ClaudePage() {
  const { user } = useAuth();
  const connections = useQuery({ queryKey: CONNECTIONS, queryFn: () => unwrap(api.GET("/api/oauth/connections")) });
  const url = connections.data?.connector_url ?? null;

  return (
    <PageScroll label="Claude">
      <div className="mx-auto max-w-3xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Claude</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Podłącz Claude do DC Land Hunter i pytaj o lokalizacje zwykłym językiem: w przeglądarce, w aplikacji na
          komputerze i na telefonie.
        </p>
        {connections.isPending && <EmptyState icon={LoaderCircle} title="Wczytuję…" className="[&_svg]:animate-spin" />}
        {connections.isError && (
          <EmptyState icon={ServerCrash} tone="bad" title="Nie udało się pobrać informacji o połączeniu z Claude" />
        )}
        {connections.data && url === null && (
          <Card className="mt-4">
            <CardContent className="pt-6 text-sm">
              Połączenie z Claude jest wyłączone na tym serwerze. Administrator włącza je ustawieniem DCLH_PUBLIC_URL.
            </CardContent>
          </Card>
        )}
        {connections.data && url !== null && (
          <div className="mt-4 flex flex-col gap-4">
            <Card>
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
                <CardTitle>Jak podłączyć</CardTitle>
                {user &&
                  (user.mcp_access ? (
                    <Badge variant="ok" role="status">
                      Twoje konto ma dostęp
                    </Badge>
                  ) : (
                    <Badge variant="warn" role="status">
                      Brak dostępu: poproś administratora o włączenie
                    </Badge>
                  ))}
              </CardHeader>
              <CardContent className="text-sm">
                <p className="text-muted-foreground">Adres konektora</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <code className="rounded-md border bg-surface-2 px-2 py-1 break-all select-all">{url}</code>
                  <CopyButton text={url} label="Kopiuj adres" />
                </div>
                <Setup url={url} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Wypróbuj</CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                <ul className="m-0 flex list-none flex-col gap-2 p-0">
                  {EXAMPLE_PROMPTS.map((prompt) => (
                    <li key={prompt} className="flex items-start justify-between gap-2 rounded-md border p-2">
                      <span>{prompt}</span>
                      <CopyButton text={prompt} label="Kopiuj" iconOnly />
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Co widzi Claude</CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                <ul className="list-disc pl-5">
                  <li>Lokalizacje, oceny, pokrycie powiatów i etapy projektów. Notatek z projektów nie widzi nigdy.</li>
                  <li>To, co Claude odczyta, trafia do Anthropic, jak każda rozmowa z Claude.</li>
                  <li>
                    Obserwowanie lokalizacji, sprawdzenie działki, zmianę etapu projektu, usunięcie projektu (razem z
                    jego notatką) i zmianę priorytetów w kolejce Claude zrobi tylko na Twoją prośbę. Kolejka
                    priorytetów jest wspólna dla całego zespołu.
                  </li>
                  <li>Natychmiastowe wyszukiwanie powiatu może zlecić tylko administrator, do 3 razy dziennie.</li>
                  <li>Ocena sieci to szacunek, nie potwierdzona moc przyłączeniowa. Wiążą tylko warunki od OSD.</li>
                </ul>
              </CardContent>
            </Card>
            <Connections items={connections.data.items} />
            {user?.role === "admin" && <Team url={url} />}
          </div>
        )}
      </div>
    </PageScroll>
  );
}

function Setup({ url }: { url: string }) {
  const [app, setApp] = useState<"web" | "code">("web");
  const tab = (id: typeof app, label: string) => (
    <Button variant={app === id ? "default" : "outline"} size="sm" aria-pressed={app === id} onClick={() => setApp(id)}>
      {label}
    </Button>
  );
  return (
    <div className="mt-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Aplikacja">
        {tab("web", "claude.ai, komputer, telefon")}
        {tab("code", "Claude Code")}
      </div>
      {app === "web" ? (
        <>
          <ol className="mt-3 list-decimal pl-5">
            {WEB_STEPS.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <p className="mt-2 text-muted-foreground">
            Telefon: aplikacja Claude użyje konektora dodanego w przeglądarce. Darmowy plan Claude pozwala na jeden
            własny konektor. W planie Team lub Enterprise konektor dodaje właściciel organizacji.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="outline" size="sm" asChild>
              <a href="https://claude.ai" target="_blank" rel="noreferrer">
                <ExternalLink aria-hidden />
                Otwórz claude.ai
              </a>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <a href={HELP_URL} target="_blank" rel="noreferrer">
                Pomoc Anthropic (po angielsku)
              </a>
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="mt-3">W terminalu uruchom:</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <code className="rounded-md border bg-surface-2 px-2 py-1 break-all select-all">{claudeCodeCommand(url)}</code>
            <CopyButton text={claudeCodeCommand(url)} label="Kopiuj polecenie" />
          </div>
          <p className="mt-2 text-muted-foreground">Potem w Claude Code wpisz /mcp, wybierz dclh i zaloguj się.</p>
        </>
      )}
    </div>
  );
}

function Connections({ items }: { items: { client_id: string; client_name: string; connected_at: string }[] }) {
  const client = useQueryClient();
  const disconnect = useMutation({
    mutationFn: async (clientId: string) => {
      const { response } = await api.DELETE("/api/oauth/connections/{client_id}", {
        params: { path: { client_id: clientId } },
      });
      if (!response.ok) throw new Error(`API ${response.status}`);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: CONNECTIONS }),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Połączone aplikacje</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {items.length === 0 ? (
          <p className="text-muted-foreground">Nie masz połączonych aplikacji.</p>
        ) : (
          <ul className="m-0 list-none p-0">
            {items.map((c) => (
              <li key={c.client_id} className="flex items-center justify-between gap-2 py-1">
                <span>
                  {c.client_name}
                  <span className="text-muted-foreground"> · od {formatDate(c.connected_at)}</span>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={disconnect.isPending}
                  onClick={() => disconnect.mutate(c.client_id)}
                >
                  Odłącz
                </Button>
              </li>
            ))}
          </ul>
        )}
        {disconnect.isError && (
          <p role="alert" className="mt-2 text-bad">
            Nie udało się odłączyć. Spróbuj ponownie.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** Admins: the same switch as `dclh user mcp-access`; off also signs that person's Claude out. */
function Team({ url }: { url: string }) {
  const client = useQueryClient();
  const team = useQuery({ queryKey: TEAM, queryFn: () => unwrap(api.GET("/api/team")) });
  const toggle = useMutation({
    mutationFn: async ({ email, on }: { email: string; on: boolean }) =>
      unwrap(api.PUT("/api/team/{email}/mcp-access", { params: { path: { email } }, body: { on } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: TEAM });
      void client.invalidateQueries({ queryKey: CONNECTIONS });
    },
  });

  return (
    <Card data-testid="team-access">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Dostęp zespołu</CardTitle>
        <CopyButton text={invitation(url)} label="Kopiuj zaproszenie" />
      </CardHeader>
      <CardContent className="text-sm">
        <p className="text-muted-foreground">
          Widzi to tylko administrator. Wyłączenie dostępu od razu wylogowuje Claude tej osoby. Nowe konto zakłada się
          poleceniem dclh user create.
        </p>
        {team.isPending && <p className="mt-2 text-muted-foreground">Wczytuję…</p>}
        {team.isError && (
          <p role="alert" className="mt-2 text-bad">
            Nie udało się pobrać listy użytkowników.
          </p>
        )}
        {team.data && (
          <ul className="m-0 mt-2 list-none p-0">
            {team.data.map((m) => (
              <li key={m.email} className="flex flex-wrap items-center justify-between gap-2 border-t py-2 first:border-t-0">
                <span className="min-w-0 break-all">
                  {m.email}
                  {m.role === "admin" && <span className="text-muted-foreground"> · admin</span>}
                  <span className="block text-xs text-muted-foreground">
                    {m.mcp_access ? "Dostęp włączony" : "Dostęp wyłączony"}
                    {m.connections > 0 && ` · połączone aplikacje: ${m.connections}`}
                  </span>
                </span>
                <Button
                  variant={m.mcp_access ? "outline" : "default"}
                  size="sm"
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate({ email: m.email, on: !m.mcp_access })}
                >
                  {m.mcp_access ? "Wyłącz dostęp" : "Włącz dostęp"}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {toggle.isError && (
          <p role="alert" className="mt-2 text-bad">
            Nie udało się zmienić dostępu. Spróbuj ponownie.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function CopyButton({ text, label, iconOnly = false }: { text: string; label: string; iconOnly?: boolean }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // No clipboard (plain http, an old browser): the text stays selectable on the page.
    }
  }
  const name = copied ? "Skopiowano" : label;
  return (
    <Button
      variant="outline"
      size={iconOnly ? "icon" : "sm"}
      className={cn(iconOnly && "shrink-0")}
      onClick={() => void copy()}
      aria-label={iconOnly ? name : undefined}
      title={iconOnly ? name : undefined}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {!iconOnly && name}
    </Button>
  );
}
