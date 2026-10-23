import { Activity, Bell, Database, Download, Map, MapPinned, Search, Settings, SquareKanban, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Shown to admins only. */
  adminOnly?: boolean;
  /** Desktop: an icon on the right of the bar instead of a labelled link on the left. */
  iconOnly?: boolean;
}

/** The eight screens from brief 8.1 (Karta lokalizacji opens from the list, so it has no nav entry). */
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Mapa i lista", icon: Map },
  { to: "/sprawdz-dzialke", label: "Sprawdź działkę", icon: Search },
  { to: "/projekty", label: "Moje projekty", icon: SquareKanban },
  { to: "/eksport", label: "Eksport", icon: Download },
  { to: "/powiaty", label: "Powiaty", icon: MapPinned },
  { to: "/zrodla", label: "Źródła danych", icon: Database },
  { to: "/nowosci", label: "Nowości", icon: Bell, iconOnly: true },
  { to: "/ustawienia", label: "Ustawienia", icon: Settings, iconOnly: true },
  { to: "/zadania", label: "Zadania", icon: Activity, adminOnly: true, iconOnly: true },
];
