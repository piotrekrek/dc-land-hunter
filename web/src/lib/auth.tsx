import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { api, ApiError, unwrap, type User } from "@/api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

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
      await api.POST("/api/auth/logout");
    },
    onSettled: () => {
      client.clear();
      client.setQueryData(["me"], null);
    },
  });

  const value: AuthState = {
    user: me.data ?? null,
    loading: me.isPending,
    login: async (email, password) => {
      await loginMutation.mutateAsync({ email, password });
    },
    logout: async () => {
      await logoutMutation.mutateAsync();
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
