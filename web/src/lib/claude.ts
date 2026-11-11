import { useMutation, useQuery } from "@tanstack/react-query";

import { api, type ConsentInfo } from "@/api/client";
import { messageFromDetail } from "@/lib/check";

/** A request the API refused, with the Polish reason it gave (an expired request, a missing admin switch). */
export class ConsentError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * Where the browser may be sent after the decision: https (Claude's callback) or http on this machine (Claude
 * Code, the MCP Inspector). Anything else, such as a javascript: URL, is never followed.
 */
export function isSafeRedirect(target: string): boolean {
  try {
    const url = new URL(target);
    return url.protocol === "https:" || (url.protocol === "http:" && LOOPBACK.has(url.hostname));
  } catch {
    return false;
  }
}

export function useConsent(reqId: string | null) {
  return useQuery({
    queryKey: ["oauth-request", reqId],
    enabled: reqId !== null,
    // A request is used once: refetching after the decision (or on a focus change) would only show "expired".
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: async (): Promise<ConsentInfo> => {
      const { data, error, response } = await api.GET("/api/oauth/requests/{req_id}", {
        params: { path: { req_id: reqId ?? "" } },
      });
      if (data) return data;
      throw new ConsentError(messageFromDetail(error, response.status), response.status);
    },
  });
}

export function useDecision(reqId: string) {
  return useMutation({
    mutationFn: async (approve: boolean): Promise<string> => {
      const { data, error, response } = await api.POST("/api/oauth/requests/{req_id}/decision", {
        params: { path: { req_id: reqId } },
        body: { approve },
      });
      if (data) return data.redirect_to;
      throw new ConsentError(messageFromDetail(error, response.status), response.status);
    },
  });
}

// The Claude tab (PLAN.md, "Claude tab").

export const EXAMPLE_PROMPTS = [
  "Pokaż 5 najlepszych lokalizacji pod DC budowane w Wielkopolsce i wyjaśnij, co trzeba jeszcze sprawdzić.",
  "Porównaj dwie najlepsze lokalizacje w dolnośląskim: odległość do GPZ, powierzchnia, ryzyka.",
  "Ile jest zielonych i żółtych lokalizacji w województwie śląskim? Które powiaty nie są jeszcze przeszukane?",
  "Obserwuj najlepszą lokalizację w powiecie wrocławskim.",
  "Pokaż kolejkę wyszukiwania i dodaj priorytet dla powiatu leszczyńskiego.",
];

export const claudeCodeCommand = (url: string) => `claude mcp add --transport http dclh ${url}`;

/** The message an admin pastes into an email or a chat to invite a partner. */
export function invitation(connectorUrl: string): string {
  const app = connectorUrl.replace(/\/mcp$/, "");
  return [
    "Cześć! Możesz pytać Claude o lokalizacje z DC Land Hunter.",
    "",
    `1. Zaloguj się w aplikacji: ${app} i otwórz zakładkę Claude.`,
    "2. W claude.ai wejdź w Customize → Connectors → + Add → Add custom connector.",
    `3. Nazwa: DC Land Hunter, adres: ${connectorUrl}`,
    "4. Kliknij Connect, zaloguj się i wybierz Zezwól.",
    "",
    "Na telefonie konektor działa sam, gdy dodasz go w przeglądarce. W Claude Code:",
    claudeCodeCommand(connectorUrl),
  ].join("\n");
}
