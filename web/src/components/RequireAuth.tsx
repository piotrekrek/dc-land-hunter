import { CircleAlert, LoaderCircle } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

import { EmptyState } from "./EmptyState";

/** Wraps every screen except /login. Unauthenticated visitors are sent to the login page. */
export function RequireAuth() {
  const { user, loading, sessionError, retrySession } = useAuth();
  const location = useLocation();
  if (loading) {
    return <EmptyState icon={LoaderCircle} title="Wczytuję…" className="mt-24 [&_svg]:animate-spin" />;
  }
  if (!user) {
    // A failed session check is not a logout: offer a retry instead of sending the user to the login page.
    if (sessionError) {
      return (
        <EmptyState icon={CircleAlert} tone="bad" title="Nie udało się sprawdzić logowania" className="mt-24">
          <p>Serwer nie odpowiedział. Sprawdź połączenie i spróbuj ponownie.</p>
          <Button className="mt-4" onClick={retrySession}>
            Spróbuj ponownie
          </Button>
        </EmptyState>
      );
    }
    return <Navigate to="/logowanie" replace state={{ from: location.pathname + location.search + location.hash }} />;
  }
  return <Outlet />;
}
