import { Activity, Bell, Database, Download, Map, Search, Settings, SquareKanban, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Shown to admins only. */
  adminOnly?: boolean;
}

/** The eight screens from brief 8.1 (Karta lokalizacji opens from the list, so it has no nav entry). */
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Mapa i lista", icon: Map },
  { to: "/nowosci", label: "Nowości", icon: Bell },
  { to: "/sprawdz-dzialke", label: "Sprawdź działkę", icon: Search },
  { to: "/projekty", label: "Moje projekty", icon: SquareKanban },
  { to: "/eksport", label: "Eksport", icon: Download },
  { to: "/zrodla", label: "Źródła danych", icon: Database },
  { to: "/zadania", label: "Zadania", icon: Activity, adminOnly: true },
  { to: "/ustawienia", label: "Ustawienia", icon: Settings },
];
