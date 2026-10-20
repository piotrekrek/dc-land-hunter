import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";

import { shouldRetry } from "@/api/client";
import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { AuthProvider } from "@/lib/auth";
import { LoginPage } from "@/pages/LoginPage";
import { HomePage } from "@/pages/HomePage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { SiteCardPage } from "@/pages/SiteCardPage";
import { SourcesPage } from "@/pages/SourcesPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="logowanie" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route
          path="nowosci"
          element={
            <PlaceholderPage
              title="Nowości"
              phase={5}
              description="Co się zmieniło od zeszłego tygodnia."
              bullets={[
                "Nowe lokalizacje i zmiany punktacji o co najmniej 10 pkt",
                "Nowe strefy MPZP / planu ogólnego i procedury w toku",
                "Zmiany dostępnej mocy w GPZ, nowe wpisy PSE",
                "Nowe oferty gruntów (KOWR, gminy, SSE)",
                "Obserwowane lokalizacje, gminy i GPZ; cotygodniowy e-mail",
              ]}
            />
          }
        />
        <Route
          path="sprawdz-dzialke"
          element={
            <PlaceholderPage
              title="Sprawdź działkę"
              phase={4}
              description="Ktoś zaproponował działkę? Sprawdź, czy nadaje się pod centrum danych."
              bullets={[
                "Wklej identyfikator działki (TERYT) lub kilka identyfikatorów",
                "Albo kliknij działkę na mapie",
                "Pełna ocena i karta lokalizacji w mniej niż 30 sekund",
              ]}
            />
          }
        />
        <Route
          path="projekty"
          element={
            <PlaceholderPage
              title="Moje projekty"
              phase={4}
              description="Lejek transakcji w formie tablicy kanban."
              bullets={[
                "Kolumny: Nowe, Obserwuję, Analiza, Kontakt z właścicielem, Negocjacje, Odrzucone, Kupione/Zabezpieczone",
                "Przeciąganie kart, liczba lokalizacji i suma hektarów w kolumnie",
                "Notatki i historia działań",
              ]}
            />
          }
        />
        <Route
          path="eksport"
          element={
            <PlaceholderPage
              title="Eksport"
              phase={4}
              description="Materiały do wysłania partnerom."
              bullets={["XLSX z bieżącego widoku listy", "Jednostronicowy PDF dla lokalizacji: mapa, punktacja, kluczowe fakty, następne kroki, źródła"]}
            />
          }
        />
        <Route path="lokalizacje/:siteId" element={<SiteCardPage />} />
        <Route path="zrodla" element={<SourcesPage />} />
        <Route path="ustawienia" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      </Route>
    </Routes>
  );
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false, retry: shouldRetry } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
