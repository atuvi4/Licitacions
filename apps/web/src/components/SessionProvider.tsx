"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api } from "@/lib/api";
import type { ClientConfig, CurrentUser, Membership } from "@/lib/types";

interface SessionValue {
  user: CurrentUser;
  membership: Membership;
  /** Limits que decideix el backend (mida i tipus d'upload). */
  config: ClientConfig;
  companyId: string;
  companies: Membership[];
  selectCompany: (companyId: string) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

const STORAGE_KEY = "licitacions.activeCompany";

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [config, setConfig] = useState<ClientConfig | null>(null);
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [me, clientConfig] = await Promise.all([
        api.get<CurrentUser>("/auth/me"),
        api.get<ClientConfig>("/config"),
      ]);
      setUser(me);
      setConfig(clientConfig);
      setCompanyId((current) => {
        if (current && me.memberships.some((m) => m.company_id === current)) return current;
        const stored = typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null;
        if (stored && me.memberships.some((m) => m.company_id === stored)) return stored;
        const preferred = me.memberships.find((m) => m.is_default) ?? me.memberships[0];
        return preferred?.company_id ?? null;
      });
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        // La cookie existeix pero el backend la rebutja (token caducat, secret
        // rotat, usuari esborrat). Cal esborrar-la abans de tornar al login:
        // si no, el middleware la torna a donar per bona i es forma un bucle
        // de redireccions entre /login i /dashboard.
        try {
          await api.post("/auth/logout", {});
        } catch {
          // Si tampoc es pot tancar sessio, igualment anem al login.
        }
        router.replace("/login");
        return;
      }
      setError(caught instanceof Error ? caught.message : "Error desconegut");
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectCompany = useCallback((next: string) => {
    setCompanyId(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout", {});
    window.localStorage.removeItem(STORAGE_KEY);
    router.replace("/login");
  }, [router]);

  const value = useMemo<SessionValue | null>(() => {
    if (!user || !companyId || !config) return null;
    const membership = user.memberships.find((m) => m.company_id === companyId);
    if (!membership) return null;
    return {
      user,
      membership,
      config,
      companyId,
      companies: user.memberships,
      selectCompany,
      refresh: load,
      logout,
    };
  }, [user, config, companyId, selectCompany, load, logout]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-8">
        <p className="text-sm text-danger">{error}</p>
      </div>
    );
  }

  if (!value) {
    if (user && user.memberships.length === 0) {
      return (
        <div className="flex min-h-screen items-center justify-center p-8 text-center">
          <div>
            <p className="text-sm font-medium text-ink">Aquest usuari no té cap empresa assignada.</p>
            <p className="mt-1 text-[13px] text-muted">
              Demana a un administrador que et doni accés a una empresa.
            </p>
          </div>
        </div>
      );
    }
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-brand border-r-transparent" />
      </div>
    );
  }

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession s'ha d'utilitzar dins de SessionProvider");
  }
  return value;
}

/** Prefix de totes les rutes d'aquesta empresa. Evita construir URLs a ma. */
export function useCompanyPath(): (suffix: string) => string {
  const { companyId } = useSession();
  return useCallback((suffix: string) => `/companies/${companyId}${suffix}`, [companyId]);
}
