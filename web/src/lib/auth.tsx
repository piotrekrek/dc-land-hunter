import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api, ApiError, setUnauthorizedHandler, unwrap, type User } from "@/api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  /** The session check failed with a server or network error: we do not know whether the user is signed in. */
  sessionError: boolean;
  retrySession: () => void;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Polish message when the last logout request failed; the user stays signed in. */
  logoutError: string | null;
}

const AuthContext = createContext<AuthState | null>(null);

export const LOGOUT_ERROR = "Nie udało się wylogować. Pozostajesz zalogowany. Spróbuj ponownie.";

export class LoginError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function fetchMe(): Promise<User | null> {
  try {
    return await unwrap(api.GET("/api/auth/me"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 5 * 60_000, retry: false });

  // Any 401 from the API ends the session. Setting the user to null sends RequireAuth to the login page,
  // which keeps the current path as the return address.
  useEffect(() => {
    setUnauthorizedHandler(() => client.setQueryData(["me"], null));
    return () => setUnauthorizedHandler(null);
  }, [client]);

  const loginMutation = useMutation({
    mutationFn: async ({ email, password }: { email: string; password: string }) => {
      const { data, error, response } = await api.POST("/api/auth/login", { body: { email, password } });
      if (!data) {
        const detail = (error as { detail?: unknown } | undefined)?.detail;
        throw new LoginError(typeof detail === "string" ? detail : "Nie udało się zalogować", response.status);
      }
      return data;
    },
    onSuccess: (user) => {
      client.clear(); // never show data cached for a previous user
      client.setQueryData(["me"], user);
    },
  });

  const logoutMutation = useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/api/auth/logout");
      // 401: the server session had already ended, so the user is signed out either way.
      if (!response.ok && response.status !== 401) throw new ApiError(LOGOUT_ERROR, response.status);
    },
    onSuccess: () => {
      client.clear();
      client.setQueryData(["me"], null);
    },
  });

  const value: AuthState = {
    user: me.data ?? null,
    loading: me.isPending,
    sessionError: me.isError,
    retrySession: () => {
      void me.refetch();
    },
    login: async (email, password) => {
      await loginMutation.mutateAsync({ email, password });
    },
    logout: async () => {
      try {
        await logoutMutation.mutateAsync();
      } catch {
        // Shown through logoutError; the user is still signed in, so there is nothing else to do here.
      }
    },
    logoutError: logoutMutation.isError ? LOGOUT_ERROR : null,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
