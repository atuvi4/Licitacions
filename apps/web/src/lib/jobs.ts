/**
 * Peticions de feina llarga: processar plecs i analitzar-ne els requisits.
 *
 * La petició HTTP ja no espera el resultat (el proxy talla als 30 s i una
 * anàlisi en dura minuts). Es demana la feina, es rep `accepted` i es consulta
 * l'estat cada pocs segons fins que acaba. Si la pàgina es recarrega, les
 * peticions actives de l'empresa es recuperen i el seguiment continua.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

export type JobType = "process_tender_documents" | "analyze_tender_requirements";
export type JobRequestStatus = "accepted" | "succeeded" | "failed" | "cancelled";
export type JobOutcome = "executed" | "reused" | "reused_existing" | "partial";

export interface JobRequest {
  id: string;
  job_type: JobType;
  tender_id: string;
  status: JobRequestStatus;
  outcome: JobOutcome | null;
  reused_existing: boolean;
  result_run_id: string | null;
  error_code: string | null;
  requested_at: string;
  finished_at: string | null;
  progress: { done: number; total: number } | null;
  already_requested?: boolean;
}

const POLL_MS = 2500;

const JOB_ERROR_MESSAGES: Record<string, string> = {
  input_changed: "Els plecs han canviat des que es va demanar. Torna-ho a demanar.",
  nothing_analyzable: "Cap document té text extret. Processa els plecs primer.",
  operation_busy: "Ara mateix no es pot. Torna-ho a provar d'aquí a una estona.",
  worker_lost: "L'execució es va interrompre. Torna-ho a demanar.",
  worker_lost_before_start: "L'execució no va arribar a començar. Torna-ho a demanar.",
  lease_expired: "L'execució es va interrompre massa vegades.",
  provider_changed: "El servei d'anàlisi no té el motor que es va demanar.",
  provider_not_configured: "El servei d'anàlisi no està disponible.",
  analysis_in_progress: "Aquesta licitació ja s'està analitzant fora de la cua.",
  all_chunks_failed: "Cap part del plec s'ha pogut analitzar.",
  analysis_too_large: "L'anàlisi supera el límit de chunks del pilot.",
  company_active_limit: "L'empresa ja té el màxim d'anàlisis en marxa. Espera que n'acabi alguna.",
  permission_denied: "El teu rol no pot llançar anàlisis.",
  no_documents: "No hi ha cap document per processar.",
  internal_error: "Error intern en executar la feina.",
};

export function jobErrorMessage(code: string | null): string {
  if (!code) return "La feina no s'ha pogut completar.";
  return JOB_ERROR_MESSAGES[code] ?? `La feina no s'ha pogut completar (${code}).`;
}

export interface AnalysisQuote {
  id: string;
  tender_id: string;
  status: "open" | "confirmed" | "superseded" | "expired";
  planning_version: string;
  profile: string;
  provider: string;
  model: string;
  force: boolean;
  input_fingerprint: string;
  documents: {
    tender_document_id: string;
    name: string;
    document_type: string;
    pages: number;
    chars: number;
  }[];
  skipped_documents: {
    tender_document_id: string;
    name: string;
    document_type: string;
    reason: string;
  }[];
  pages_total: number;
  pages_in_plan: number;
  pages_truncated: boolean;
  chunks: number;
  chunks_to_send: number;
  reusable_run_id: string | null;
  estimated_input_tokens: number | null;
  estimated_output_tokens: number | null;
  max_chunks: number;
  allowed: boolean;
  blocking_reason: string | null;
  expires_at: string;
}

export interface AnalysisLimits {
  max_chunks_per_run: number;
  max_active_per_company: number;
  active_now: number;
  can_confirm: boolean;
  can_process_documents: boolean;
  confirm_roles: string[];
}

/** Preview del volum d'una anàlisi. No crida cap proveïdor ni crea cap feina. */
export function previewAnalysis(companyId: string, tenderId: string, force: boolean) {
  return api.post<AnalysisQuote>(`/companies/${companyId}/tenders/${tenderId}/analysis-quotes`, {
    force,
  });
}

/** Confirma el preview i demana l'anàlisi. */
export function confirmAnalysis(companyId: string, quoteId: string) {
  return api.post<JobRequest & { quote_id: string }>(
    `/companies/${companyId}/analysis-quotes/${quoteId}/confirm`,
    {},
  );
}

export function requestProcessing(companyId: string, tenderId: string, documentIds: string[]) {
  return api.post<JobRequest>(
    `/companies/${companyId}/tenders/${tenderId}/job-requests/process-documents`,
    { document_ids: documentIds },
  );
}

/**
 * Segueix una petició fins que acaba. `onFinished` es crida un sol cop, amb la
 * petició final (també si ha fallat).
 */
export function useJobRequest(
  companyId: string | null,
  tenderId: string,
  jobType: JobType,
  onFinished: (request: JobRequest) => void | Promise<void>,
) {
  const [request, setRequest] = useState<JobRequest | null>(null);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const track = useCallback((next: JobRequest) => {
    setRequest(next);
    if (next.status !== "accepted") void finished.current(next);
  }, []);

  // En carregar la pàgina: si l'empresa ja té aquesta feina en marxa, se'n
  // reprèn el seguiment en lloc d'oferir tornar-la a demanar.
  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    api
      .get<JobRequest[]>(
        `/companies/${companyId}/tenders/${tenderId}/job-requests?job_type=${jobType}&active=true&limit=1`,
      )
      .then((active) => {
        if (!cancelled && active.length > 0) setRequest(active[0]);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId, jobType]);

  useEffect(() => {
    if (!companyId || !request || request.status !== "accepted") return;
    const timer = setTimeout(() => {
      api
        .get<JobRequest>(`/companies/${companyId}/job-requests/${request.id}`)
        .then(track)
        // Un error de xarxa puntual no atura el seguiment: es torna a provar.
        .catch(() => setRequest((current) => (current ? { ...current } : current)));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [companyId, request, track]);

  return {
    request,
    active: request?.status === "accepted",
    track,
  };
}
