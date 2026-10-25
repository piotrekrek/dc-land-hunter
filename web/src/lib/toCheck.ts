import type { SiteDetail } from "@/lib/sites";

/** Who to ask and how, per score item. Static text: the app never promises an answer (CLAUDE.md section 3). */
export const WHO_TO_ASK: Readonly<Record<string, string>> = {
  zoning:
    "Gmina (wydział planowania przestrzennego): złóż wniosek o wypis i wyrys z MPZP lub informację o planie ogólnym. Podaj numery działek.",
  grid_proximity:
    "Operator sieci (Enea, Energa, Tauron lub PGE Dystrybucja): zapytaj o linie 110 kV i GPZ przy działce, na przykład mailem do biura przyłączeń.",
  grid_n1:
    "Operator sieci: zapytaj, czy GPZ ma drugie, niezależne zasilanie 110 kV.",
  grid_node: "PSE: zapytaj o najbliższy węzeł 220/400 kV i jego plany rozbudowy (dotyczy mocy powyżej ok. 150 MW).",
  grid_planned_station: "PSE: sprawdź w planie rozwoju sieci przesyłowej, czy w okolicy jest planowana stacja.",
  grid_headroom:
    "Operator sieci: złóż zapytanie o warunki przyłączenia na żądaną moc. Opublikowane wolne moce to tylko wskazówka dla SN.",
  grid_odb: "Operator sieci: sprawdź w wykazie wydanych warunków przyłączenia (ODB), czy w pobliżu są duże przyłączenia.",
  grid_brownfield:
    "Gmina i właściciel terenu: zapytaj, czy na terenie jest własna stacja 110 kV i czy przyłącze może być użyte ponownie.",
  grid_closed: "Operator sieci (OSD): zapytaj o status stacji i pól 110 kV w pobliżu (wykaz rozdzielni zamkniętych).",
  land_size: "Starostwo powiatowe (wydział geodezji): sprawdź powierzchnie działek w ewidencji gruntów.",
  land_compactness: "Starostwo powiatowe: pobierz wypis z mapy ewidencyjnej, żeby ocenić kształt terenu.",
  land_parcels: "Starostwo powiatowe: sprawdź w ewidencji gruntów, ile działek i ilu właścicieli obejmuje teren.",
  land_expansion: "Gmina: zapytaj o plan dla terenów sąsiednich (MPZP) i możliwość rozbudowy.",
  conn_telecom:
    "UKE (Hurtowa Platforma Informacyjna) i operatorzy światłowodowi: sprawdź trasy światłowodów przy działce.",
  conn_city: "Sprawdź odległość do miasta na mapie; dojazd potwierdź w zarządzie dróg.",
  bonus_heat: "Gmina i lokalny zakład ciepłowniczy: zapytaj o sieć ciepłowniczą w pobliżu (odzysk ciepła, EED art. 26).",
  bonus_psi: "Gmina (wydział rozwoju): sprawdź, czy teren leży w strefie ekonomicznej lub parku przemysłowym.",
  bonus_res: "Operator sieci i gmina: zapytaj o przyłączone OZE i stacje w pobliżu.",
  bonus_env: "Gmina: sprawdź w Bazie OOŚ decyzje środowiskowe dla działek i sąsiedztwa.",
  bonus_seller: "Właściciel lub pośrednik: ustal, czy właściciel chce sprzedać i ilu jest współwłaścicieli.",
  pen_flood_02: "RZGW (Wody Polskie): sprawdź mapy zagrożenia powodziowego dla działek.",
  pen_landscape: "Regionalna Dyrekcja Ochrony Środowiska: sprawdź park krajobrazowy i obszar chronionego krajobrazu.",
  pen_mining_terrain: "Okręgowy Urząd Górniczy: zapytaj o obszary i tereny górnicze na działkach.",
  pen_soil_iii: "Starostwo powiatowe: pobierz wypis z klasyfikacji gleb (klasy bonitacyjne) dla działek.",
  pen_residential: "Gmina: sprawdź zabudowę mieszkaniową w sąsiedztwie (MPZP, ewidencja budynków) i odległości.",
  pen_gas: "Operator gazociągu (Gaz-System lub OSD gazowy): zapytaj o gazociągi i strefy kontrolowane przy działce.",
  pen_airport: "Urząd Lotnictwa Cywilnego i zarządca lotniska: zapytaj o obszary ograniczeń wysokości (OLS).",
};

/** For a key the table above does not know yet, so a new score item still shows up with something to do. */
export const GENERIC_WHO_TO_ASK = "Sprawdź ręcznie w źródle tych danych albo zapytaj gminę.";

export interface ToCheckEntry {
  key: string;
  label: string;
  /** Points the site would gain if the answer is the best case; the list is sorted by it. */
  gain: number;
  who: string;
}

type Item = Pick<SiteDetail["items"][number], "key" | "label_pl" | "points" | "upside_points" | "confidence">;

/** Every score item without data, biggest possible gain first (ties keep the engine's order). */
export function toCheck(items: readonly Item[]): ToCheckEntry[] {
  return items
    .filter((i) => i.confidence === "missing")
    .map((i) => ({
      key: i.key,
      label: i.label_pl,
      gain: Math.max(0, i.upside_points - i.points),
      who: WHO_TO_ASK[i.key] ?? GENERIC_WHO_TO_ASK,
    }))
    .sort((a, b) => b.gain - a.gain);
}
