/**
 * Legal basis behind each profile exclusion, keyed by the blocker key from `pipeline/dclh_pipeline/profiles.py`.
 * Desk-checked 03.11.2026 against public law texts; articles are cited only as far as they were confirmed.
 * The minimum area (`area`) is an investor rule, not a law, so it has no entry.
 */
export const LEGAL_BASIS: Record<string, string> = {
  forest:
    "Lasem jest grunt o zwartej powierzchni co najmniej 0,10 ha pokryty roślinnością leśną (art. 3 ustawy o lasach), dlatego mniejszy skrawek nie wyklucza terenu. " +
    "Budowa na gruncie leśnym wymaga zmiany jego przeznaczenia na cele nieleśne w planie miejscowym, za zgodą z art. 7 ust. 2 ustawy o ochronie gruntów rolnych i leśnych, " +
    "oraz decyzji o wyłączeniu gruntu z produkcji leśnej (art. 11 tej ustawy).",
  nature:
    "W parku narodowym i rezerwacie przyrody obowiązuje zakaz budowy obiektów budowlanych (art. 15 ust. 1 pkt 1 ustawy o ochronie przyrody). " +
    "Na obszarze Natura 2000 zakazane są działania mogące znacząco negatywnie oddziaływać na cele ochrony (art. 33 ust. 1); wyjątek wymaga zezwolenia z art. 34.",
  flood:
    "Strefy wody 10% i 1% to obszary szczególnego zagrożenia powodzią (art. 16 pkt 34 Prawa wodnego). " +
    "Nowy obiekt budowlany w takiej strefie wymaga pozwolenia wodnoprawnego (art. 390 Prawa wodnego), a art. 77 wprowadza tam dodatkowe zakazy.",
  mining:
    "Obszary i tereny górnicze uwzględnia się w planie ogólnym i planie miejscowym gminy (art. 104 Prawa geologicznego i górniczego). " +
    "Teren jest narażony na szkodliwe wpływy robót górniczych, a zabudowa musi spełniać warunki ustalone w planie.",
  heritage:
    "Roboty budowlane przy zabytku wpisanym do rejestru wymagają pozwolenia wojewódzkiego konserwatora zabytków (art. 36 ust. 1 ustawy o ochronie zabytków i opiece nad zabytkami). " +
    "Pozwolenie konserwatora nie zastępuje pozwolenia na budowę.",
  zoning:
    "Plan miejscowy jest aktem prawa miejscowego (art. 14 ust. 8 ustawy o planowaniu i zagospodarowaniu przestrzennym), a projekt budowlany musi być z nim zgodny. " +
    "Przeznaczenie inne niż produkcyjne lub usługowe oznacza, że inwestycja wymaga zmiany planu.",
};
