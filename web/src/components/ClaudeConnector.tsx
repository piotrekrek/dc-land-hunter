import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";

const KEY = ["oauth", "connections"] as const;

/** How to connect Claude to this app (PLAN.md, "Claude connector"), and the apps this user already connected. */
export function ClaudeConnector({ className }: { className?: string }) {
  const { user, loading } = useAuth();
  const client = useQueryClient();
  const connections = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET("/api/oauth/connections")) });
  const disconnect = useMutation({
    mutationFn: async (clientId: string) => {
      const { response } = await api.DELETE("/api/oauth/connections/{client_id}", {
        params: { path: { client_id: clientId } },
      });
      if (!response.ok) throw new Error(`API ${response.status}`);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: KEY }),
  });

  return (
    <Card className={className} data-testid="claude-connector">
      <CardHeader>
        <CardTitle>Połączenie z Claude</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {connections.isPending && <p className="text-muted-foreground">Wczytuję…</p>}
        {connections.isError && (
          <p role="alert" className="text-bad">
            Nie udało się pobrać informacji o połączeniu z Claude.
          </p>
        )}
        {connections.data &&
          (connections.data.connector_url === null ? (
            <p>Połączenie z Claude jest wyłączone na tym serwerze. Administrator włącza je ustawieniem DCLH_PUBLIC_URL.</p>
          ) : (
            <>
              {!loading && (
                <p role="status" className="font-medium">
                  {user?.mcp_access
                    ? "Twoje konto ma dostęp."
                    : "Poproś administratora o włączenie dostępu dla Claude."}
                </p>
              )}
              <Steps url={connections.data.connector_url} />
              <p className="mt-2 text-muted-foreground">Claude na telefonie użyje tego samego konektora.</p>
              <h4 className="mt-4 mb-1 text-xs font-semibold text-muted-foreground">Połączone aplikacje</h4>
              {connections.data.items.length === 0 ? (
                <p className="text-muted-foreground">Nie masz połączonych aplikacji.</p>
              ) : (
                <ul className="m-0 list-none p-0">
                  {connections.data.items.map((c) => (
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
            </>
          ))}
      </CardContent>
    </Card>
  );
}

function Steps({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // No clipboard (plain http, an old browser): the address stays selectable on the page.
    }
  }

  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="rounded-md border bg-surface-2 px-2 py-1 select-all">{url}</code>
        <Button variant="outline" size="sm" onClick={copy}>
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          {copied ? "Skopiowano" : "Kopiuj adres"}
        </Button>
      </div>
      <ol className="mt-3 list-decimal pl-5">
        <li>W Claude otwórz Ustawienia → Konektory → Dodaj własny konektor.</li>
        <li>Nazwa: DC Land Hunter. Adres: powyższy.</li>
        <li>Kliknij Połącz, zaloguj się i wybierz Zezwól.</li>
      </ol>
    </>
  );
}
