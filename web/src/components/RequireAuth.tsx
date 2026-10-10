import { LoaderCircle } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router";

import { useAuth } from "@/lib/auth";

import { EmptyState } from "./EmptyState";

/** Wraps every screen except /login. Unauthenticated visitors are sent to the login page. */
export function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return <EmptyState icon={LoaderCircle} title="Wczytuję…" className="mt-24 [&_svg]:animate-spin" />;
  }
  if (!user) {
    return <Navigate to="/logowanie" replace state={{ from: location.pathname + location.search + location.hash }} />;
  }
  return <Outlet />;
}
