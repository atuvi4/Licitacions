/**
 * Diccionari únic de la interfície (handoff Sprint 1, secció 3).
 *
 * Un sol mòdul que **reexporta** els mapes d'etiquetes que ja existeixen i hi
 * afegeix el que faltava: el **nivell** de StatusPill de cada valor de cada
 * enum, l'**acció** de cada codi d'error, els textos d'estat compostos, la
 * projecció dels estats de candidatura sobre els tres passos i els rols
 * humanitzats. No es mou cap constant de lloc.
 *
 * Regla: cap pantalla decideix un color per compte propi ni mostra un codi
 * intern. El nivell surt sempre d'aquí, mai d'un `if` al JSX. Els tons antics i
 * els tons antics dels mòduls d'origen queden substituïts per aquests mapes i
 * s'eliminen a L15.
 */

import type { Level } from "@/components/ui/StatusPill";
import type {
  AnalysisBasis,
  AssessmentResult,
  BlockerStatus,
  EligibilityResult,
  EvidenceStrength,
  RecommendationResult,
} from "@/lib/compliance";
import { formatDate } from "@/lib/format";
import { jobErrorMessage, type JobRequest, type JobRequestStatus } from "@/lib/jobs";
import type { MatchStatus } from "@/lib/matching";
import type { AnalysisState, RequirementAnalysis, ReviewStatus } from "@/lib/requirements";
import type { DocumentState, TenderStatus } from "@/lib/tenders";
import type {
  AvailabilityStatus,
  CompletenessSection,
  TemporalStatus,
  VerificationStatus,
} from "@/lib/types";
import type {
  ChecklistItemStatus,
  DocumentValidity,
  DossierCoverageState,
  EvidenceItemStatus,
  RelevanceState,
  SubmissionPackageStatus,
  TaskStatus,
  WorkspaceDecisionType,
  WorkspaceReadiness,
  WorkspaceStatus,
} from "@/lib/workspace";

/* -------------------------------------------------------------------------- */
/* Reexportacions: els mapes existents, sense moure'ls                          */
/* -------------------------------------------------------------------------- */

export {
  ANALYSIS_BASIS_HINTS,
  ANALYSIS_BASIS_LABELS,
  BLOCKER_LABELS,
  COMPLIANCE_DISCLAIMER,
  ELIGIBILITY_HINTS,
  ELIGIBILITY_LABELS,
  EXPLANATION_LABELS,
  RECOMMENDATION_LABELS,
  RESULT_LABELS,
  STRENGTH_LABELS,
  explanationText,
  isTrustworthyAnalysis,
} from "@/lib/compliance";
export {
  ANALYSIS_DISCLAIMER,
  ANALYSIS_STATE_LABELS,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  CONFIDENCE_LABELS,
  EVALUATION_LABELS,
  EXTRACTION_STATUS_LABELS,
  OBLIGATION_LABELS,
  SKIP_REASON_LABELS,
  analysisState,
} from "@/lib/requirements";
export {
  CONTRACT_TYPE_LABELS,
  DOCUMENT_STATE_LABELS,
  PROCEDURE_TYPE_LABELS,
  TENDER_DOCUMENT_LABELS,
  TENDER_STATUS_LABELS,
  daysUntilDeadline,
  documentState,
  formatBytes,
  formatDateTime,
} from "@/lib/tenders";
export {
  CHECKLIST_STATUS_LABELS,
  COVERAGE_LABELS,
  DECISION_LABELS,
  DECISION_REASON_LABELS,
  EVIDENCE_STATUS_LABELS,
  GROUP_LABELS,
  NOTHING_TO_DO_LABELS,
  OUTDATED_LABELS,
  PACKAGE_STATUS_LABELS,
  READINESS_BLOCKER_LABELS,
  READINESS_LABELS,
  RELEVANCE_ESTABLISHED,
  RELEVANCE_HINTS,
  RELEVANCE_LABELS,
  STALE_REASON_LABELS,
  TASK_STATUS_LABELS,
  VALIDITY_LABELS,
  WARNING_LABELS,
  WORKSPACE_STATUS_LABELS,
  checklistReconciliationMessage,
  deadlineLabel,
  reconciliationMessage,
} from "@/lib/workspace";
export {
  DIMENSION_LABELS,
  DIMENSION_ORDER,
  MATCH_STATUS_HELP,
  MATCH_STATUS_LABELS,
  PREFIT_DISCLAIMER,
  REASON_LABELS,
  lotSummary,
  reasonLabel,
  shortSummary,
} from "@/lib/matching";
export { CHANGE_LABELS, IMPACT_LABELS, changeLabel, describeChange, triggerLabel } from "@/lib/tender-changes";
export {
  AVAILABILITY_LABELS,
  CLIENT_TYPE_LABELS,
  DOCUMENT_CATEGORY_LABELS,
  ENTITY_TYPE_LABELS,
  INSURANCE_LIMIT_KIND_LABELS,
  INSURANCE_TYPE_LABELS,
  PROJECT_STATUS_LABELS,
  QUALIFICATION_TYPE_LABELS,
  REGISTRATION_TYPE_LABELS,
  RELATIONSHIP_LABELS,
  SPECIALIZATION_LABELS,
  TEMPORAL_STATUS_LABELS,
  TERRITORY_TYPE_LABELS,
  VERIFICATION_STATUS_LABELS,
} from "@/lib/format";
export { jobErrorMessage } from "@/lib/jobs";
export type { Level } from "@/components/ui/StatusPill";

/* -------------------------------------------------------------------------- */
/* 3.1 · Mapes de nivell                                                        */
/* -------------------------------------------------------------------------- */

export const RESULT_LEVEL: Record<AssessmentResult, Level> = {
  satisfies: "ready",
  does_not_satisfy: "blocking",
  // «Sense dades» no és un no: és l'absència de dada al perfil.
  unknown: "attention",
  needs_review: "attention",
  not_applicable: "neutral",
};

export const ELIGIBILITY_LEVEL: Record<EligibilityResult, Level> = {
  eligible: "ready",
  review_required: "attention",
  no_go: "blocking",
  // Mai verd ni bloqueig: no s'ha avaluat res.
  undetermined: "neutral",
};

export const RECOMMENDATION_LEVEL: Record<RecommendationResult, Level> = {
  go_candidate: "ready",
  eligible_low_fit: "neutral",
  review: "attention",
  no_go: "blocking",
  undetermined: "neutral",
};

export const WORKSPACE_STATUS_LEVEL: Record<WorkspaceStatus, Level> = {
  analysis: "running",
  needs_review: "attention",
  awaiting_decision: "attention",
  go_approved: "ready",
  no_go_approved: "neutral",
  preparing_bid: "running",
  ready_for_final_review: "ready",
  approved_for_submission: "ready",
  submitted: "ready",
  withdrawn: "neutral",
};

export const DECISION_LEVEL: Record<WorkspaceDecisionType, Level> = {
  go: "ready",
  no_go: "neutral",
  defer: "attention",
};

/** Nivell d'una decisió que encara no s'ha pres. */
export const DECISION_PENDING_LEVEL: Level = "attention";

export const READINESS_LEVEL: Record<WorkspaceReadiness, Level> = {
  not_started: "neutral",
  incomplete: "attention",
  needs_review: "attention",
  ready_for_final_review: "ready",
};

export const COVERAGE_LEVEL: Record<DossierCoverageState, Level> = {
  covered: "ready",
  missing_document: "blocking",
  needs_review: "attention",
  not_applicable: "neutral",
};

export const EVIDENCE_STATUS_LEVEL: Record<EvidenceItemStatus, Level> = {
  suggested: "neutral",
  // Triat no és acreditat: la cobertura mana.
  selected: "neutral",
  missing: "blocking",
  needs_review: "attention",
  not_applicable: "neutral",
};

export const VALIDITY_LEVEL: Record<DocumentValidity, Level> = {
  valid: "ready",
  expires_before_deadline: "blocking",
  expired: "blocking",
  no_expiry: "neutral",
  no_document: "neutral",
};

export const RELEVANCE_LEVEL: Record<RelevanceState, Level> = {
  linked_evidence: "ready",
  human_confirmed: "ready",
  relevance_unproven: "attention",
  category_incompatible: "blocking",
  category_unknown: "attention",
  insufficient_set: "attention",
};

export const PACKAGE_STATUS_LEVEL: Record<SubmissionPackageStatus, Level> = {
  draft: "neutral",
  frozen: "running",
  approved: "ready",
  superseded: "neutral",
};

/** Nivell d'un estat de paquet. Un de desconegut queda neutre, mai en «ready». */
export function packageStatusLevel(status: string): Level {
  return (PACKAGE_STATUS_LEVEL as Record<string, Level>)[status] ?? "neutral";
}

export const ANALYSIS_STATE_LEVEL: Record<AnalysisState, Level> = {
  not_analyzed: "neutral",
  analyzing: "running",
  completed: "ready",
  // Parcial no és complet, mai.
  partial: "attention",
  error: "blocking",
  stale: "attention",
};

export const ANALYSIS_BASIS_LEVEL: Record<AnalysisBasis, Level> = {
  real: "ready",
  simulation: "attention",
  provider_not_configured: "attention",
};

export const DOCUMENT_STATE_LEVEL: Record<DocumentState, Level> = {
  not_downloaded: "neutral",
  downloaded: "neutral",
  extracted: "ready",
  ocr_candidate: "attention",
  unsupported: "neutral",
  error: "blocking",
};

export const MATCH_STATUS_LEVEL: Record<MatchStatus, Level> = {
  candidate: "ready",
  low_fit: "neutral",
  excluded: "neutral",
  insufficient_data: "attention",
  closed: "neutral",
};

export const TENDER_STATUS_LEVEL: Record<TenderStatus, Level> = {
  open: "ready",
  pending_award: "neutral",
  awarded: "neutral",
  resolved: "neutral",
  cancelled: "blocking",
  pre_announcement: "neutral",
  unknown: "neutral",
};

export const TEMPORAL_LEVEL: Record<TemporalStatus, Level> = {
  valid: "ready",
  expiring_soon: "attention",
  expired: "blocking",
  unknown: "neutral",
};

export const AVAILABILITY_LEVEL: Record<AvailabilityStatus, Level> = {
  available: "ready",
  partially_available: "attention",
  unavailable: "blocking",
  unknown: "neutral",
};

export const VERIFICATION_LEVEL: Record<VerificationStatus, Level> = {
  verified: "ready",
  pending_verification: "neutral",
};

export type CompletenessStatus = CompletenessSection["status"];

/** Estat de cada àrea del perfil (`completeness.sections[].status`). */
export const COMPLETENESS_LEVEL: Record<CompletenessStatus, Level> = {
  complete: "ready",
  partial: "attention",
  empty: "blocking",
};

/** Un booleà «actiu» no és un `if` al JSX: el nivell surt d'aquí. */
export function activeLevel(active: boolean): Level {
  return active ? "ready" : "neutral";
}

/** Preferència CPV: exclosa mana sobre activa. */
export function cpvPreferenceLevel(preference: { active: boolean; excluded: boolean }): Level {
  if (preference.excluded) return "blocking";
  return activeLevel(preference.active);
}

/** Senyal de l'extracció d'un requisit extret (no la revisió de l'empresa). */
export const REVIEW_LEVEL: Record<ReviewStatus, Level> = {
  unreviewed: "neutral",
  verified: "ready",
  needs_review: "attention",
  rejected: "neutral",
};

/** Força de l'evidència de l'empresa per a un requisit. «Sense document» demana atenció, no és un no. */
export const STRENGTH_LEVEL: Record<EvidenceStrength, Level> = {
  strong: "ready",
  supporting: "neutral",
  missing: "attention",
};

/** QA del text extret d'un plec (`qa_status` de l'extracció). */
export const QA_LEVEL: Record<"good" | "warning" | "poor", Level> = {
  good: "ready",
  warning: "attention",
  poor: "blocking",
};

/** Un canvi material de la licitació demana atenció; un d'informatiu, no. */
export function changeLevel(event: { is_material: boolean }): Level {
  return event.is_material ? "attention" : "neutral";
}

export const CHECKLIST_LEVEL: Record<ChecklistItemStatus, Level> = {
  pending: "neutral",
  in_progress: "running",
  completed: "ready",
  blocked: "blocking",
  not_applicable: "neutral",
};

export const TASK_LEVEL: Record<TaskStatus, Level> = {
  pending: "neutral",
  in_progress: "running",
  completed: "ready",
  cancelled: "neutral",
};

/** `none` no porta pastilla: un requisit sense bloqueig no diu res. */
export const BLOCKER_LEVEL: Record<BlockerStatus, Level | null> = {
  none: null,
  potential: "attention",
  confirmed: "blocking",
};

export const JOB_LEVEL: Record<JobRequestStatus, Level> = {
  accepted: "running",
  succeeded: "ready",
  failed: "blocking",
  cancelled: "neutral",
};

/** Una feina acabada amb `outcome: partial` cau a Atenció, no a A punt. */
export function jobLevel(request: Pick<JobRequest, "status" | "outcome">): Level {
  if (request.outcome === "partial") return "attention";
  return JOB_LEVEL[request.status];
}

/**
 * Nivell del comptador de dies (abans, un to per mòdul): un termini passat
 * o a menys d'una setmana bloqueja; a tres setmanes demana atenció.
 */
export function deadlineLevel(days: number | null): Level {
  if (days === null) return "neutral";
  if (days <= 7) return "blocking";
  if (days <= 21) return "attention";
  return "neutral";
}

/* -------------------------------------------------------------------------- */
/* Passos de la candidatura                                                     */
/* -------------------------------------------------------------------------- */

export type WorkspaceStep = "decidir" | "preparar" | "tancar" | "tancada";

const STEP_OF: Record<WorkspaceStatus, WorkspaceStep> = {
  analysis: "decidir",
  needs_review: "decidir",
  awaiting_decision: "decidir",
  go_approved: "preparar",
  preparing_bid: "preparar",
  ready_for_final_review: "tancar",
  approved_for_submission: "tancar",
  submitted: "tancada",
  no_go_approved: "tancada",
  withdrawn: "tancada",
};

/** Els deu estats del backend projectats sobre Decidir → Preparar → Tancar. */
export function stepOf(status: WorkspaceStatus): WorkspaceStep {
  return STEP_OF[status];
}

export const STEP_LABELS: Record<WorkspaceStep, string> = {
  decidir: "Per decidir",
  preparar: "Preparant",
  tancar: "Per tancar",
  tancada: "Tancades",
};

/* -------------------------------------------------------------------------- */
/* 3.2 · Accions per codi d'error                                               */
/* -------------------------------------------------------------------------- */

export type JobErrorActionKind = "retry" | "choose_documents" | "process_first" | "wait" | "job_center" | "none";

export interface JobErrorAction {
  /** Frase visible. Mai el codi. */
  title: string;
  action: { kind: JobErrorActionKind; label?: string };
}

const RETRY = { kind: "retry", label: "Torna-ho a demanar" } as const;
const NONE = { kind: "none" } as const;

/**
 * Per a cada codi de `JOB_ERROR_MESSAGES` (que continua a `lib/jobs.ts`), el
 * títol visible i l'acció que el resol. El detall tècnic (`error_code`,
 * `request.id`, `finished_at`) va sempre al `technical` d'ErrorState, mai al text.
 */
export const JOB_ERROR_ACTIONS: Record<string, JobErrorAction> = {
  analysis_too_large: {
    title: "El plec és massa gran per llegir-lo d'un cop",
    // C-1: la selecció només governa el processament; no limita quins documents usa l'anàlisi.
    action: { kind: "choose_documents", label: "Tria menys documents" },
  },
  company_active_limit: {
    title: "L'empresa ja té el màxim d'anàlisis en marxa",
    action: { kind: "wait", label: "Espera que n'acabi una" },
  },
  input_changed: { title: "El plec ha canviat mentre es llegia", action: RETRY },
  nothing_analyzable: {
    title: "Cap document té text per analitzar",
    action: { kind: "process_first", label: "Processa els plecs primer" },
  },
  no_documents: {
    title: "Cap document té text per analitzar",
    action: { kind: "process_first", label: "Processa els plecs primer" },
  },
  worker_lost: { title: jobErrorMessage("worker_lost"), action: RETRY },
  worker_lost_before_start: { title: jobErrorMessage("worker_lost_before_start"), action: RETRY },
  lease_expired: { title: jobErrorMessage("lease_expired"), action: RETRY },
  operation_busy: { title: jobErrorMessage("operation_busy"), action: RETRY },
  internal_error: { title: jobErrorMessage("internal_error"), action: RETRY },
  all_chunks_failed: { title: jobErrorMessage("all_chunks_failed"), action: RETRY },
  // PermissionNote amb `confirm_roles`, no un botó gris.
  permission_denied: { title: "El teu rol no pot llançar anàlisis", action: NONE },
  provider_changed: { title: jobErrorMessage("provider_changed"), action: NONE },
  provider_not_configured: { title: jobErrorMessage("provider_not_configured"), action: NONE },
  analysis_in_progress: { title: jobErrorMessage("analysis_in_progress"), action: NONE },
};

const UNKNOWN_JOB_ERROR: JobErrorAction = { title: "No s'ha pogut completar", action: NONE };

/** Codi desconegut o absent → «No s'ha pogut completar» + detall tècnic. */
export function jobErrorAction(code: string | null | undefined): JobErrorAction {
  if (!code) return UNKNOWN_JOB_ERROR;
  return JOB_ERROR_ACTIONS[code] ?? UNKNOWN_JOB_ERROR;
}

/* -------------------------------------------------------------------------- */
/* 3.3 · Textos d'estat compostos                                               */
/* -------------------------------------------------------------------------- */

const IN_PROGRESS_TEXT = "En marxa · la font no informa del progrés";

/**
 * Una sola frase per a l'estat de l'anàlisi de requisits, a partir de
 * l'anàlisi vigent i, si n'hi ha, de la petició en curs.
 */
export function ANALYSIS_STATUS_TEXT(analysis: RequirementAnalysis | null, jobRequest: JobRequest | null): string {
  if (jobRequest && jobRequest.status === "accepted") {
    return jobRequest.progress
      ? `Analitzant · ${jobRequest.progress.done} de ${jobRequest.progress.total}`
      : IN_PROGRESS_TEXT;
  }
  const run = analysis?.run;
  if (!run) return "Plec sense analitzar";
  if (run.status === "pending" || run.status === "running") return IN_PROGRESS_TEXT;
  if (run.status === "failed") return "L'anàlisi ha fallat";
  const when = formatDate(run.completed_at ?? run.started_at);
  if (analysis?.is_stale) return `Anàlisi del ${when} · el plec ha canviat`;
  if (run.status === "partial") {
    // Dos fets diferents i tots dos certs: un document sencer que no s'ha pogut
    // llegir i una part d'un document que ha fallat. No es barregen.
    const skipped = run.skipped_documents?.length ?? 0;
    const failed = run.chunks_failed ?? 0;
    const parts = [
      skipped > 0 ? `${skipped} ${skipped === 1 ? "document omès" : "documents omesos"}` : null,
      failed > 0 ? `${failed} ${failed === 1 ? "part no processada" : "parts no processades"}` : null,
    ].filter(Boolean);
    return parts.length > 0 ? `Anàlisi parcial · ${parts.join(" · ")}` : "Anàlisi parcial";
  }
  const extracted = run.requirements_extracted;
  return `Anàlisi completa · ${extracted} ${extracted === 1 ? "requisit" : "requisits"} · ${when}`;
}

/* -------------------------------------------------------------------------- */
/* Rols                                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Els cinc valors de `MembershipRole` d'`apps/api/app/models/enums.py`
 * (owner, admin, bid_manager, technical, viewer). Fallback: el valor cru.
 */
export const ROLE_LABELS: Record<string, string> = {
  owner: "Propietari/ària",
  admin: "Administrador/a",
  bid_manager: "Responsable de candidatures",
  technical: "Tècnic/a",
  viewer: "Consulta",
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return ROLE_LABELS[role] ?? role;
}

/** Els rols que el backend deixa signar decisions i avançar el dossier (`DECISION_ROLES`). */
export const DECISION_ROLES = ["owner", "admin", "bid_manager"] as const;

/** «qui té el rol de …», per a un `PermissionNote`. Rol, mai nom de persona. */
export function whoCanDecide(): string {
  const labels = DECISION_ROLES.map((role) => ROLE_LABELS[role]);
  return `qui té el rol de ${labels.slice(0, -1).join(", ")} o ${labels[labels.length - 1]}`;
}
