/**
 * Textos del botó «Actualitza documents de la PSCP» (Bloc 2, etapa 3). Purs: el
 * component (`PortalSync.tsx`) només decideix quin toca.
 */

import { type JobRequest, jobErrorMessage } from "@/lib/jobs";

export const PORTAL_SYNC_ACTION = "Actualitza documents de la PSCP";

/** Per què no es pot sincronitzar la licitació (`TenderPortalSync.reason`). */
export function portalSyncReasonText(reason: string | null): string {
  if (reason === "portal_sync_disabled") return jobErrorMessage("portal_sync_disabled");
  return jobErrorMessage("portal_sync_not_available");
}

/** Quant falta per poder-la tornar a demanar, arrodonit a l'alça al minut. */
export function portalSyncWaitText(remainingMs: number): string {
  if (remainingMs < 60_000) return "d'aquí a menys d'un minut";
  const minutes = Math.ceil(remainingMs / 60_000);
  return minutes === 1 ? "d'aquí a 1 minut" : `d'aquí a ${minutes} minuts`;
}

export interface PortalSyncOutcome {
  tone: "success" | "info" | "error";
  message: string;
  detail?: string;
}

/** El resultat d'una sincronització acabada. `null` mentre no ha acabat. */
export function portalSyncOutcome(request: JobRequest | null): PortalSyncOutcome | null {
  if (!request || request.status === "accepted") return null;
  if (request.status === "failed") return { tone: "error", message: jobErrorMessage(request.error_code) };
  if (request.status === "cancelled") return { tone: "info", message: "La sincronització s'ha cancel·lat: no s'ha canviat cap document." };
  if (request.outcome === "reused") {
    return { tone: "info", message: "El portal de la PSCP no té res de nou: els documents ja estaven al dia." };
  }
  if (request.outcome === "partial") {
    return {
      tone: "info",
      message: "Documents actualitzats amb el portal de la PSCP, amb avisos.",
      detail: "Algun fitxer del portal no s'ha pogut desar: en veuràs l'error a la seva fila.",
    };
  }
  return { tone: "success", message: "Documents actualitzats amb el que publica el portal de la PSCP." };
}

/** Per què no s'ha pogut DEMANAR (la petició HTTP), en català. Cap codi al text. */
export function portalSyncRequestErrorText(code: string | null): string {
  if (code === "permission_denied") return "El teu rol no pot demanar la sincronització amb el portal de la PSCP.";
  if (code && ["portal_sync_disabled", "portal_sync_not_available", "operation_busy"].includes(code)) {
    return jobErrorMessage(code);
  }
  return "No s'ha pogut demanar la sincronització amb el portal de la PSCP. Torna-ho a provar.";
}
