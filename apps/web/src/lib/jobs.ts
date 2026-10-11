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
  /** Mentre és `accepted`: `false` = a la cua, encara no ha començat. */
  started?: boolean | null;
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
  public_tender_canonical_only:
    "En una licitació pública l'anàlisi és la canònica: no es poden triar documents, perfil ni forçar-la.",
  forced_not_canonical: "Anàlisi forçada per l'operador: no és mai la vigent.",
  requirement_run_superseded: "Hi ha una anàlisi més nova del plec: revisa-la abans de continuar.",
  requirements_outdated: "Els plecs han canviat des d'aquesta anàlisi: cal una anàlisi nova.",
};

export function jobErrorMessage(code: string | null): string {
  if (!code) return "La feina no s'ha pogut completar.";
  if (Object.hasOwn(JOB_ERROR_MESSAGES, code)) return JOB_ERROR_MESSAGES[code];
  // Confirmar una quota bloquejada respon 422 amb el codi del motiu (3B-2 §6.1).
  if (Object.hasOwn(BLOCKING_REASON_TEXT, code)) return BLOCKING_REASON_TEXT[code];
  return `La feina no s'ha pogut completar (${code}).`;
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
  /** L'únic motiu prioritzat pel qual no es pot iniciar (3B-2 §6.1). */
  blocking_reason: BlockingReason | null;
  /** Prerequisits tal com es van calcular. `null` als previews anteriors a 3B-2. */
  prerequisites: QuotePrerequisites | null;
  /** Sobre què es faria l'anàlisi: lectura real, simulació o sense motor. */
  provider_basis: ProviderBasis | null;
  /** Avisos que no bloquegen (p. ex. `previous_run_completed_empty`). */
  warnings: string[];
  /** Cost estimat en dòlars (sostre). `null` sense preu del model. */
  estimated_cost_usd: number | null;
  accept_incomplete_package: boolean;
  expires_at: string;
}

/** `real` · `simulation` · `unconfigured`: sobre què es faria (i si pot ser mai vigent). */
export type ProviderBasis = "real" | "simulation" | "unconfigured" | "gold";

/** Codis de `blocking_reason` (`BLOCKING_PRIORITY` del servidor) i `nothing_analyzable`. */
export type BlockingReason =
  | "provider_not_real"
  | "model_not_pinned"
  | "price_unknown"
  | "budget_exhausted"
  | "budget_exceeded"
  | "package_incomplete"
  | "quality_unknown"
  | "quality_warning"
  | "selection_not_canonical"
  | "analysis_too_large"
  | "nothing_analyzable";

export type QualityStatus = "good" | "warning" | "poor" | "unknown";

/** Un motiu concret de paquet incomplet (`prerequisites.package.issues`). */
export interface QuotePackageIssue {
  code: string;
  tender_document_id?: string | null;
  tender_document_ids?: string[];
  reason?: string;
  finding_id?: string;
  gap_code?: string;
  gap_id?: string;
}

/**
 * Prerequisits de la quota (3B-2 §6.1), tal com els desa el servidor. Cap import
 * global del pressupost: només l'estimació pròpia i booleans.
 */
export interface QuotePrerequisites {
  version: string;
  provider_basis: ProviderBasis;
  simulation_allowed?: boolean;
  /** Cert si la quota faria despesa real: llavors paquet, qualitat i pressupost bloquegen. */
  enforced?: boolean;
  public_tender?: boolean;
  canonical?: boolean;
  accept_incomplete_package?: boolean;
  acknowledge_quality?: boolean;
  model_pinned?: boolean;
  price_known?: boolean;
  package?: { status: "complete" | "incomplete" | "unknown"; issues: QuotePackageIssue[]; accepted: boolean };
  quality?: { status: QualityStatus; documents: { tender_document_id: string; qa_status: string }[]; acknowledged: boolean };
  budget?: {
    estimated_cost_usd: number | null;
    estimated_input_tokens?: number | null;
    estimated_output_tokens?: number | null;
    basis?: string;
    within_run_cap?: boolean;
    within_total_cap?: boolean;
    tripped?: boolean;
  };
  /** Els que bloquegen, per prioritat (el primer és `blocking_reason`). */
  failed: string[];
  /** Els que no bloquegen perquè la quota no faria despesa real. */
  advisory?: string[];
  warnings?: string[];
}

/** El motiu de bloqueig, en català. Cap codi arriba mai al text. */
export const BLOCKING_REASON_TEXT: Record<string, string> = {
  provider_not_real:
    "El servei d'anàlisi no té cap model real configurat: una simulació no llegeix el plec i mai seria vigent.",
  model_not_pinned: "El model d'anàlisi no està fixat a una versió autoritzada.",
  price_unknown: "No es coneix el preu del model: no es pot estimar ni limitar el cost.",
  budget_exhausted: "El pressupost d'anàlisi de la plataforma està esgotat fins a una nova autorització.",
  budget_exceeded: "El cost estimat d'aquesta anàlisi supera el límit per anàlisi.",
  package_incomplete:
    "Al paquet documental hi falten documents: només es pot analitzar acceptant-ho explícitament.",
  quality_unknown:
    "La qualitat del text d'algun plec és dolenta o desconeguda: cal reprocessar-lo o revisar-lo abans d'analitzar.",
  quality_warning: "El text d'algun plec té avisos de qualitat: cal acceptar-ho explícitament.",
  selection_not_canonical:
    "En una licitació pública l'anàlisi és la canònica: no es poden triar documents, perfil ni forçar-la.",
  analysis_too_large: "Aquesta anàlisi supera el límit de fragments del pilot i no es pot llançar.",
  nothing_analyzable: "Cap plec té text analitzable: processa els plecs primer.",
};

export function blockingReasonText(code: string | null | undefined): string {
  if (!code) return "";
  return Object.hasOwn(BLOCKING_REASON_TEXT, code) ? BLOCKING_REASON_TEXT[code] : "No es pot iniciar l'anàlisi.";
}

export const PROVIDER_BASIS_TEXT: Record<ProviderBasis, string> = {
  real: "Lectura real del plec",
  simulation: "Simulació: no llegeix el plec i el resultat no serà mai vigent",
  unconfigured: "Sense motor d'anàlisi configurat: no es pot llegir el plec",
  gold: "Dades de referència: no és una lectura del plec",
};

/** Avisos de la quota que no bloquegen. */
export const QUOTE_WARNING_TEXT: Record<string, string> = {
  previous_run_completed_empty:
    "L'última anàlisi d'aquests plecs va quedar com a anàlisi acabada sense resultats: cal revisar-la abans de tornar-la a demanar.",
};

/** Acceptacions explícites de qui confirma (§6.1.3-4). Entren a la quota nova. */
export interface QuoteAcceptance {
  accept_incomplete_package?: boolean;
  acknowledge_quality?: boolean;
}

export interface AnalysisLimits {
  max_chunks_per_run: number;
  max_active_per_company: number;
  active_now: number;
  can_confirm: boolean;
  can_process_documents: boolean;
  confirm_roles: string[];
}

/**
 * Preview del volum d'una anàlisi. No crida cap proveïdor ni crea cap feina.
 * `acceptance`: les acceptacions explícites (paquet incomplet, qualitat), que
 * formen part de la identitat de la quota i s'han de tornar a calcular.
 */
export function previewAnalysis(companyId: string, tenderId: string, force: boolean, acceptance?: QuoteAcceptance) {
  return api.post<AnalysisQuote>(`/companies/${companyId}/tenders/${tenderId}/analysis-quotes`, {
    force,
    ...acceptance,
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
