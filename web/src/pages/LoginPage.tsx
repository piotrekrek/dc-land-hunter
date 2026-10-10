import { LoaderCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LoginError, useAuth } from "@/lib/auth";

export function LoginPage() {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (!loading && user) return <Navigate to={from} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof LoginError ? err.message : "Nie udało się połączyć z serwerem");
      setPassword("");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2 font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-6" />
          DC Land Hunter
        </div>
        <h1 className="text-lg font-semibold">Logowanie</h1>
        <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            E-mail
            <input
              type="email"
              name="email"
              autoComplete="username"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-9 rounded-md border bg-surface px-3 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            Hasło
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="h-9 rounded-md border bg-surface px-3 text-sm"
            />
          </label>
          {error && (
            <p role="alert" className="rounded-md border border-bad/40 bg-bad-soft px-3 py-2 text-sm text-bad">
              {error}
            </p>
          )}
          <Button type="submit" disabled={pending}>
            {pending && <LoaderCircle className="animate-spin" aria-hidden />}
            Zaloguj się
          </Button>
        </form>
        <p className="mt-4 text-xs text-muted-foreground">
          Konta zakłada administrator. Nie ma samodzielnej rejestracji.
        </p>
      </Card>
    </main>
  );
}
