"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { MatchSummary } from "@/lib/matching";
import type { CompletenessSection, DashboardSummary } from "@/lib/types";
import type { WorkspaceStatus, WorkspaceSummary } from "@/lib/workspace";

/**
 * Recomptes de la barra lateral (handoff L4.6). Tot es deriva de crides que
 * ja existeixen; si una falla, el seu recompte no es mostra: mai un «0» per
 * error.
 */

const CLOSED: ReadonlySet<WorkspaceStatus> = new Set<WorkspaceStatus>(["submitted", "withdrawn", "no_go_approved"]);

/** Decisió pendent (regla d'«Avui», handoff L5.6): sense decisió o ajornada, i encara oberta. */
export function isPendingDecision(workspace: WorkspaceSummary): boolean {
  return (
    (workspace.human_decision === null || workspace.human_decision === "defer") && !CLOSED.has(workspace.status)
  );
}

export function countPendingDecisions(workspaces: WorkspaceSummary[]): number {
  return workspaces.filter(isPendingDecision).length;
}

/** Peu de la barra lateral: què li falta al perfil, si hi falta res. */
export function profileFooterLine(sections: CompletenessSection[]): string | null {
  const pending = sections.filter((section) => section.status !== "complete");
  if (pending.length === 0) return null;
  if (pending.length === 1) {
    return pending[0].status === "empty" ? "1 àrea del perfil és buida" : "1 àrea del perfil és parcial";
  }
  return `${pending.length} àrees del perfil no estan al dia`;
}

/**
 * Les «oportunitats obertes» del menú i de la cerca: les candidates del pre-fit.
 *
 * Si aquesta empresa encara no ha calculat mai el pre-fit, no se sap quantes
 * n'hi ha: dir «0 oportunitats obertes» seria afirmar que no n'hi ha cap quan hi
 * pot haver licitacions obertes sense puntuar. Aleshores no hi ha xifra.
 */
export function openOpportunities(summary: MatchSummary): number | undefined {
  return summary.last_calculated_at ? summary.candidates : undefined;
}

export interface ShellCounts {
  avui?: number;
  oportunitats?: number;
  /** Licitacions visibles al catàleg (no les candidates): el comptador de la cerca global. */
  catalogue?: number;
  candidatures?: number;
  /** «96 %»: la completitud del perfil, que a L8 només viu aquí i al peu de la taula. */
  perfil?: string;
  profileLine?: string | null;
}

/**
 * Es recarrega en canviar d'empresa i a cada navegació, perquè els recomptes
 * reflecteixin el que s'acaba de fer en una altra pantalla.
 */
export function useShellCounts(companyId: string, pathname: string): ShellCounts {
  const [counts, setCounts] = useState<ShellCounts>({});
  const lastCompany = useRef(companyId);

  useEffect(() => {
    if (lastCompany.current !== companyId) {
      // Canviar d'empresa recarrega tot el que en depèn: cap recompte de l'anterior.
      lastCompany.current = companyId;
      setCounts({});
    }
    let cancelled = false;
    const base = `/companies/${companyId}`;
    void Promise.allSettled([
      api.get<WorkspaceSummary[]>(`${base}/workspaces`),
      api.get<MatchSummary>(`${base}/matches/summary`),
      api.get<DashboardSummary>(`${base}/summary`),
    ]).then(([workspaces, summary, profile]) => {
      if (cancelled) return;
      setCounts({
        avui: workspaces.status === "fulfilled" ? countPendingDecisions(workspaces.value) : undefined,
        candidatures: workspaces.status === "fulfilled" ? workspaces.value.length : undefined,
        catalogue: summary.status === "fulfilled" ? summary.value.total_tenders : undefined,
        oportunitats: summary.status === "fulfilled" ? openOpportunities(summary.value) : undefined,
        perfil: profile.status === "fulfilled" ? `${profile.value.completeness.score} %` : undefined,
        profileLine: profile.status === "fulfilled" ? profileFooterLine(profile.value.completeness.sections) : undefined,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [companyId, pathname]);

  return counts;
}
