import { LogOut, Menu, Moon, Sun, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useOpsJobs } from "@/lib/ops";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { ApiStatus } from "./ApiStatus";
import { NAV_ITEMS } from "./nav";

function Brand() {
  return (
    <NavLink to="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <img src="/favicon.svg" alt="" className="size-6" />
      <span>DC Land Hunter</span>
    </NavLink>
  );
}

function AttentionBadge({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null;
  return (
    <span
      className={cn("rounded-full bg-bad px-1.5 text-xs font-semibold leading-5 text-white", className)}
      aria-label={`${count} do sprawdzenia`}
      data-testid="jobs-attention"
    >
      {count}
    </span>
  );
}

const iconLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "relative flex size-9 items-center justify-center rounded-md transition-colors",
    isActive ? "bg-accent-soft text-accent-soft-foreground" : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
  );

const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
    isActive ? "bg-accent-soft font-medium text-accent-soft-foreground" : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
  );

export function AppShell() {
  const { theme, toggle } = useTheme();
  const { user, logout, logoutError } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const isAdmin = user?.role === "admin";
  const { data: ops } = useOpsJobs(isAdmin);
  const items = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);
  const labelled = items.filter((item) => !item.iconOnly);
  const icons = items.filter((item) => item.iconOnly);
  const attention = (to: string) => (to === "/zadania" ? (ops?.attention ?? 0) : 0);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b bg-surface px-4">
        <Brand />
        <nav aria-label="Główna nawigacja" className="hidden flex-1 items-center gap-1 lg:flex">
          {labelled.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={linkClass} end={to === "/"}>
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <nav aria-label="Skróty" className="hidden items-center gap-1 lg:flex">
            {icons.map(({ to, label, icon: Icon }) => {
              const count = attention(to);
              return (
                <NavLink
                  key={to}
                  to={to}
                  className={iconLinkClass}
                  title={label}
                  aria-label={count > 0 ? `${label}: ${count} do sprawdzenia` : label}
                >
                  <Icon className="size-5" aria-hidden />
                  <AttentionBadge count={count} className="absolute -right-1 -top-1 min-w-5 px-1 text-center text-[11px] leading-5" />
                </NavLink>
              );
            })}
          </nav>
          <ApiStatus />
          {user && (
            <span className="hidden text-xs text-muted-foreground xl:inline" data-testid="current-user">
              {user.email}
              {user.role === "admin" && " · admin"}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={theme === "dark" ? "Włącz jasny motyw" : "Włącz ciemny motyw"}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
          <Button variant="ghost" size="icon" onClick={() => void logout()} aria-label="Wyloguj się">
            <LogOut />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMenuOpen((o) => !o)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? "Zamknij menu" : "Otwórz menu"}
          >
            {menuOpen ? <X /> : <Menu />}
          </Button>
        </div>
      </header>
      {logoutError && (
        <p role="alert" className="border-b border-bad/40 bg-bad-soft px-4 py-2 text-sm text-bad">
          {logoutError}
        </p>
      )}
      {menuOpen && (
        <nav id="mobile-nav" aria-label="Główna nawigacja" className="flex flex-col gap-1 border-b bg-surface p-2 lg:hidden">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} className={linkClass} onClick={() => setMenuOpen(false)}>
              <Icon className="size-4" aria-hidden />
              {label}
              <AttentionBadge count={attention(to)} />
            </NavLink>
          ))}
        </nav>
      )}
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}
