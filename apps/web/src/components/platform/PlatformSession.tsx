"use client";

/**
 * Sessió de l'Administració Tuvi. Separada de `SessionProvider` (l'app client):
 * aquí no hi ha empresa activa ni pertinences, només l'operador i l'entorn.
 *
 * Guarda: sense sessió → login; amb contrasenya temporal → canviar-la; un compte
 * d'empresa (també un propietari) → «no hi tens accés». El backend ho torna a
 * comprovar a cada crida `/platform`.
 */

import { useRouter } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

import { StandalonePage } from "@/components/StandalonePage";
import { LinkButton, PageSkeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { type ClientConfig, type CurrentUser, isTuviAdmin } from "@/lib/types";

interface PlatformSessionValue {
  operator: CurrentUser;
  /** `ENVIRONMENT` del backend: `pilot`, `local`, `production`... */
  environment: string | null;
  logout: () => Promise<void>;
  /** Total d'empreses clients per a la barra lateral; el publica el llistat. */
  companiesCount: number | undefined;
  setCompaniesCount: (count: number) => void;
}

const PlatformSessionContext = createContext<PlatformSessionValue | null>(null);

export function usePlatformSession(): PlatformSessionValue {
  const value = useContext(PlatformSessionContext);
  if (!value) throw new Error("usePlatformSession s'ha d'utilitzar dins de PlatformSessionProvider");
  return value;
}

export function PlatformSessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [operator, setOperator] = useState<CurrentUser | null>(null);
  const [environment, setEnvironment] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [companiesCount, setCompaniesCount] = useState<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.get<CurrentUser>("/auth/me");
        if (cancelled) return;
        if (me.must_change_password) {
          router.replace("/compte/contrasenya");
          return;
        }
        if (!isTuviAdmin(me)) {
          setDenied(true);
          return;
        }
        setOperator(me);
        // L'entorn és informatiu: si no es pot llegir, la consola funciona igual.
        const config = await api.get<ClientConfig>("/config").catch(() => null);
        if (!cancelled) setEnvironment(config?.environment ?? null);
      } catch (caught) {
        if (caught instanceof ApiError && caught.isUnauthorized) router.replace("/login");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout", {});
    } finally {
      router.replace("/login");
    }
  }, [router]);

  if (denied) {
    return (
      <StandalonePage>
        <h1 className="mb-1 text-base font-semibold text-ink">Administració Tuvi</h1>
        <p className="mb-5 text-[13px] text-muted">
          Aquesta àrea és de l&apos;Administració Tuvi. Els comptes d&apos;empresa, també els propietaris, no hi tenen
          accés: la vostra empresa s&apos;administra des de l&apos;aplicació.
        </p>
        <LinkButton href="/avui" variant="primary" size="md">
          Torna a l&apos;aplicació
        </LinkButton>
      </StandalonePage>
    );
  }

  if (!operator) {
    return (
      <main className="min-h-screen bg-canvas px-6 py-8">
        <PageSkeleton variant="table" title="Administració Tuvi" context="comprovant l'accés…" />
      </main>
    );
  }

  return (
    <PlatformSessionContext.Provider value={{ operator, environment, logout, companiesCount, setCompaniesCount }}>
      {children}
    </PlatformSessionContext.Provider>
  );
}
