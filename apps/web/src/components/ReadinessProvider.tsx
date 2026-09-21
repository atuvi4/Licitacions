"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useResource } from "@/lib/hooks";
import type { FinalReadiness } from "@/lib/readiness";

export interface ReadinessState {
  data: FinalReadiness | null;
  error: string | null;
  loading: boolean;
  /**
   * Torna a preguntar la preparació final al servidor.
   *
   * L'ha de cridar qualsevol mutació que la pugui canviar. No hi ha cap
   * sondeig: si res no ha passat, no es pregunta res.
   */
  invalidate: () => Promise<void>;
}

const ReadinessContext = createContext<ReadinessState | null>(null);

/**
 * L'única lectura de la preparació final d'una candidatura.
 *
 * Viu en un context i no dins del control del capçal perquè el distintiu i el
 * calaix han de sortir de la MATEIXA lectura: amb dues crides separades hi
 * hauria dos veredictes possibles a la mateixa pantalla, que és precisament el
 * que S6 va venir a impedir al backend. I perquè les mutacions de qualsevol
 * àrea —el dossier avui, la solució tècnica i l'oferta quan existeixin— puguin
 * invalidar-la sense haver-se de passar cap funció de mà en mà.
 */
export function ReadinessProvider({
  companyId,
  workspaceId,
  children,
}: {
  companyId: string | null;
  workspaceId: string;
  children: ReactNode;
}) {
  const { data, error, loading, reload } = useResource<FinalReadiness>(
    companyId ? `/companies/${companyId}/workspaces/${workspaceId}/final-readiness` : null,
  );

  const value = useMemo<ReadinessState>(
    () => ({
      data,
      error,
      loading,
      invalidate: async () => {
        await reload();
      },
    }),
    [data, error, loading, reload],
  );

  return <ReadinessContext.Provider value={value}>{children}</ReadinessContext.Provider>;
}

function useReadinessContext(caller: string): ReadinessState {
  const state = useContext(ReadinessContext);
  if (!state) {
    // Millor petar aquí que ensenyar un capçal mut o deixar una mutació sense
    // refrescar la preparació sense que ningú se n'assabenti.
    throw new Error(`${caller} necessita un <ReadinessProvider> per sobre.`);
  }
  return state;
}

/** La preparació final tal com l'ha dita el servidor. */
export function useReadiness(): ReadinessState {
  return useReadinessContext("useReadiness");
}

/**
 * Només l'invalidador, per a qui muta coses i no ha de llegir el veredicte.
 * És el que han de cridar les seccions de Preparar després d'un desat correcte.
 */
export function useInvalidateReadiness(): () => Promise<void> {
  return useReadinessContext("useInvalidateReadiness").invalidate;
}
