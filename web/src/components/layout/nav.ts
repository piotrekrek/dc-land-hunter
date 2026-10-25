import { Activity, Bell, Download, Map, MapPinned, Search, Settings, SquareKanban, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Shown to admins only. */
  adminOnly?: boolean;
  /** Desktop: an icon on the right of the bar instead of a labelled link on the left. */
  iconOnly?: boolean;
  /** The screen is still a placeholder: shown muted with a "wkrótce" tag. The route stays, so links keep working. */
  soon?: boolean;
}

/** The eight screens from brief 8.1 (Karta lokalizacji opens from the list, so it has no nav entry). */
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Mapa i lista", icon: Map },
  { to: "/kolejka", label: "Kolejka", icon: MapPinned },
  { to: "/sprawdz-dzialke", label: "Sprawdź działkę", icon: Search },
  { to: "/projekty", label: "Moje projekty", icon: SquareKanban, soon: true },
  { to: "/eksport", label: "Eksport", icon: Download, soon: true },
  { to: "/nowosci", label: "Nowości", icon: Bell, iconOnly: true, soon: true },
  { to: "/ustawienia", label: "Ustawienia", icon: Settings, iconOnly: true },
  { to: "/zadania", label: "Zadania", icon: Activity, adminOnly: true, iconOnly: true },
];

export const APP_NAME = "DC Land Hunter";
export const SOON_TAG = "wkrótce";

/** Screens that are not in the navigation: [path prefix, title]. */
const EXTRA_TITLES: [string, string][] = [
  ["/logowanie", "Logowanie"],
  ["/lokalizacje", "Karta lokalizacji"],
];

/** The browser tab title for a path, for example "Kolejka · DC Land Hunter"; unknown paths read as the 404 page. */
export function pageTitle(pathname: string): string {
  const path = pathname.replace(/\/+$/, "") || "/";
  const nav = NAV_ITEMS.find((i) => (i.to === "/" ? path === "/" : path === i.to || path.startsWith(`${i.to}/`)));
  const extra = EXTRA_TITLES.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`));
  const title = nav?.label ?? extra?.[1] ?? "Nie znaleziono strony";
  return `${title} · ${APP_NAME}`;
}
