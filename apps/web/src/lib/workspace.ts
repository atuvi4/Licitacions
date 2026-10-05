/**
 * Workspace de candidatura: tipus i etiquetes.
 *
 * Quatre coses que la interfície **no fusiona mai**:
 *
 * - **Pre-fit**: "ens interessa?" (metadades de la licitació).
 * - **Elegibilitat**: "podem acreditar que complim?" (requisits del plec).
 * - **Recomanació automàtica**: el que suggereix el sistema.
 * - **Decisió humana**: el que ha decidit una persona. És l'única oficial.
 *
 * Un 91 de pre-fit amb recomanació REVIEW i decisió PENDENT són tres coses
 * diferents i s'han de veure com a tres coses diferents.
 */

import type { AnalysisBasis, EligibilityResult, RecommendationResult } from "@/lib/compliance";
import type { TenderChangeEvent } from "@/lib/tender-changes";

export type WorkspaceStatus =
  | "analysis"
  | "needs_review"
  | "awaiting_decision"
  | "go_approved"
  | "no_go_approved"
  | "preparing_bid"
  | "ready_for_final_review"
  | "approved_for_submission"
  | "submitted"
  | "withdrawn";

export type WorkspaceDecisionType = "go" | "no_go" | "defer";
export type ChecklistItemStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "blocked"
  | "not_applicable";
export type ChecklistItemSource = "requirement" | "manual";
export type TaskStatus = "pending" | "in_progress" | "completed" | "cancelled";

export interface Person {
  id: string;
  email: string;
  full_name: string | null;
}

export interface WorkspaceDecisionEntry {
  id: string;
  decision: WorkspaceDecisionType;
  decided_by: string;
  decided_by_name: string | null;
  decided_at: string;
  reason: string | null;
  compliance_run_id: string | null;
  requirement_run_id: string | null;
  /** Contra quin estat de la licitació es va congelar. `null` = no consta. */
  tender_revision_number: number | null;
  tender_normalized_hash_short: string | null;
  prefit_score: number | null;
  confirmed_blockers: number;
  potential_blockers: number;
  mandatory_unknown: number;
  mandatory_needs_review: number;
  automated_recommendation: string | null;
  deadline_passed: boolean;
  /**
   * Els avisos que hi havia oberts quan es va decidir, tal com es van desar.
   * `null` vol dir que la decisió és anterior a aquest registre: no es va
   * anotar, que NO és el mateix que dir que no n'hi havia cap.
   */
  open_warnings: string[] | null;
}

export interface ChecklistItem {
  id: string;
  workspace_id: string;
  requirement_id: string | null;
  title: string;
  category: string;
  mandatory: boolean;
  status: ChecklistItemStatus;
  source: ChecklistItemSource;
  assigned_to: string | null;
  assigned_to_name: string | null;
  due_at: string | null;
  note: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface WorkspaceTask {
  id: string;
  workspace_id: string;
  title: string;
  description: string | null;
  assigned_to: string | null;
  mandatory: boolean;
  assigned_to_name: string | null;
  due_at: string | null;
  status: TaskStatus;
  created_by: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface WorkspaceNote {
  id: string;
  workspace_id: string;
  author_id: string | null;
  author_name: string | null;
  text: string;
  created_at: string;
}

export interface ComplianceCounters {
  total: number;
  satisfies: number;
  does_not_satisfy: number;
  unknown: number;
  needs_review: number;
  not_applicable: number;
  /** El desglossament `mandatory_*` suma exactament `mandatory_total`. */
  mandatory_total: number;
  mandatory_satisfies: number;
  mandatory_does_not_satisfy: number;
  mandatory_unknown: number;
  mandatory_needs_review: number;
  mandatory_not_applicable: number;
  potential_blockers: number;
  confirmed_blockers: number;
}

export interface WorkspaceSummary {
  id: string;
  company_id: string;
  tender_id: string;
  tender_lot_id: string | null;
  status: WorkspaceStatus;
  tender_title: string | null;
  expediente_number: string | null;
  buyer_name: string | null;
  deadline_at: string | null;
  days_remaining: number | null;
  deadline_passed: boolean;
  prefit_score: number | null;
  eligibility: EligibilityResult | null;
  recommendation: RecommendationResult | null;
  human_decision: WorkspaceDecisionType | null;
  analysis_basis: AnalysisBasis;
  analysis_outdated: boolean;
  created_at: string;
}

export interface WorkspaceDetail {
  id: string;
  company_id: string;
  tender_id: string;
  tender_lot_id: string | null;
  lot_number: number | null;
  lot_title: string | null;
  status: WorkspaceStatus;
  tender_title: string | null;
  expediente_number: string | null;
  buyer_name: string | null;
  source_url: string | null;
  deadline_at: string | null;
  days_remaining: number | null;
  deadline_passed: boolean;
  prefit_score: number | null;
  prefit_status: string | null;
  eligibility: EligibilityResult | null;
  recommendation: RecommendationResult | null;
  human_decision: WorkspaceDecisionType | null;
  latest_decision: WorkspaceDecisionEntry | null;
  counters: ComplianceCounters;
  open_warnings: string[];
  go_requires_reason: boolean;
  current_compliance_run_id: string | null;
  current_requirement_run_id: string | null;
  /** Sobre QUÈ s'ha construït el que ensenya aquesta candidatura. */
  analysis_basis: AnalysisBasis;
  analysis_outdated: boolean;
  outdated_reasons: string[];
  /** Cert quan l'expedient ja no admet presentació (anul·lat o equivalent). */
  tender_actionable: boolean;
  /** Cert quan el lot d'aquesta candidatura ja no és al feed. */
  lot_removed: boolean;
  /** Les dues anteriors juntes: si encara té sentit preparar-la. */
  workspace_actionable: boolean;
  /**
   * Cert quan la decisió humana no es pot donar per vigent. La decisió no es
   * toca mai ni es torna a signar: el que caduca és la seva vigència.
   */
  decision_outdated: boolean;
  /** Cert quan no se sap sobre quin estat es va decidir. Vegeu DECISION_REASONS. */
  decision_basis_unknown: boolean;
  decision_reasons: string[];
  changes_since_decision: TenderChangeEvent[];
  readiness: WorkspaceReadiness;
  readiness_blockers: string[];
  dossier_coverage: DossierCoverageCounters | null;
  checklist: ChecklistItem[];
  tasks: WorkspaceTask[];
  notes: WorkspaceNote[];
  decisions: WorkspaceDecisionEntry[];
  members: Person[];
  can_decide: boolean;
  created_at: string;
  updated_at: string;
}

/* -------------------------------------------------------------------------- */
/* Dossier documental (5B)                                                     */
/* -------------------------------------------------------------------------- */

/**
 * No hi ha cap `expired`: la caducitat es deriva del document i del termini
 * (`DocumentValidity`), i aquella és l'única font de veritat.
 */
export type EvidenceItemStatus =
  | "suggested"
  | "selected"
  | "missing"
  | "needs_review"
  | "not_applicable";
export type EvidenceItemSource = "compliance" | "manual";
export type DocumentValidity =
  | "valid"
  | "expires_before_deadline"
  | "expired"
  | "no_expiry"
  | "no_document";
export type DossierCoverageState =
  | "covered"
  | "missing_document"
  | "needs_review"
  | "not_applicable";
export type WorkspaceReadiness =
  | "not_started"
  | "incomplete"
  | "needs_review"
  | "ready_for_final_review";

export interface DossierDocument {
  id: string;
  name: string;
  category: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  issued_at: string | null;
  expires_at: string | null;
  uploaded_at: string;
}

export interface DossierItem {
  id: string;
  workspace_id: string;
  requirement_id: string | null;
  checklist_item_id: string | null;
  requirement_title: string | null;
  requirement_category: string | null;
  requirement_quote: string | null;
  /** Que en deia el compliment. Recordatori que cobertura != compliment. */
  assessment_result: string | null;
  mandatory: boolean;
  label: string;
  status: EvidenceItemStatus;
  source: EvidenceItemSource;
  company_evidence_type: string | null;
  company_evidence_id: string | null;
  document: DossierDocument | null;
  /** Vigència respecte del TERMINI, no d'avui. */
  validity: DocumentValidity;
  coverage: DossierCoverageState | null;
  /**
   * Si aquest document acredita —o encara no acredita— el requisit. Triar un
   * paper i confirmar que és l'adequat són dues afirmacions diferents.
   */
  relevance: RelevanceState;
  selected_by: string | null;
  selected_by_name: string | null;
  selected_at: string | null;
  relevance_confirmed_by_name: string | null;
  relevance_confirmed_at: string | null;
  relevance_note: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

/** Com de sostinguda està la pertinença d'un document per a un requisit. */
export type RelevanceState =
  | "linked_evidence"
  | "human_confirmed"
  | "relevance_unproven"
  | "category_incompatible"
  | "category_unknown"
  | "insufficient_set";

export const RELEVANCE_LABELS: Record<RelevanceState, string> = {
  linked_evidence: "Vinculat a l'evidència del compliment",
  human_confirmed: "Pertinença confirmada",
  relevance_unproven: "Pertinença per acreditar",
  category_incompatible: "Aquest tipus de document no acredita això",
  category_unknown: "No sabem què acredita aquest requisit",
  insufficient_set: "Falten documents per al conjunt",
};

/**
 * Per què cada estat és el que és. La frase importa més que l'etiqueta: el que
 * ha d'entendre qui ho llegeix és que ningú no ha dit encara que aquest paper
 * serveixi, no que el sistema hagi trobat un error.
 */
export const RELEVANCE_HINTS: Record<RelevanceState, string> = {
  linked_evidence:
    "El document penja de la mateixa dada corporativa que el compliment va fer servir.",
  human_confirmed: "Una persona ha confirmat que aquest document acredita el requisit.",
  relevance_unproven:
    "La categoria encaixa, però res no lliga aquest document amb aquest requisit. Confirma-ho si és el correcte.",
  category_incompatible:
    "La categoria del document no correspon al que demana el plec. Revisa-ho abans de comptar-lo.",
  category_unknown:
    "El sistema no sap quina mena de document acredita aquest requisit: ho ha de dir una persona.",
  insufficient_set: "El plec en demana més d'un i encara no hi són tots.",
};

/** Els estats que donen cobertura documental. La resta demana una persona. */
export const RELEVANCE_ESTABLISHED: ReadonlySet<RelevanceState> = new Set<RelevanceState>([
  "linked_evidence",
  "human_confirmed",
]);

export interface DossierGroup {
  group: string;
  items: DossierItem[];
}

export interface DossierCoverageCounters {
  mandatory_total: number;
  covered: number;
  missing_document: number;
  needs_review: number;
  not_applicable: number;
  expired_documents: number;
  optional_total: number;
  optional_covered: number;
}

export interface Readiness {
  state: WorkspaceReadiness;
  blockers: string[];
  /** Explícit: estar a punt per a la revisió final NO és haver presentat res. */
  is_submitted: boolean;
}

export interface Dossier {
  workspace_id: string;
  workspace_status: WorkspaceStatus;
  deadline_at: string | null;
  coverage: DossierCoverageCounters;
  readiness: Readiness;
  groups: DossierGroup[];
  /** Files que pengen d'una extracció superada: desades, però ja no pendents. */
  superseded_count: number;
}

export interface DossierSyncResult {
  suggested: number;
  gaps: number;
  total: number;
  /** Per què no s'ha proposat res. Codi; el text viu a `NOTHING_TO_DO_LABELS`. */
  reason: string | null;
  dossier: Dossier;
}

export interface ChecklistRegenerateResult {
  created: number;
  /** Items que ja hi eren i que ara pengen del requisit vigent equivalent. */
  carried: number;
  /** Quants items han quedat fora del que el plec demana ara. És un estat, no
   *  un efecte d'aquesta crida: qui els va deixar enrere va ser la reanàlisi. */
  historic: number;
  total: number;
  reason: string | null;
}

export const GROUP_LABELS: Record<string, string> = {
  economic_solvency: "Solvència econòmica",
  experience: "Experiència i solvència tècnica",
  certifications: "Certificacions",
  insurances: "Assegurances",
  team: "Equip",
  registrations: "Registres i classificació",
  administrative: "Documentació administrativa",
  other: "Altres",
};

export const EVIDENCE_STATUS_LABELS: Record<EvidenceItemStatus, string> = {
  suggested: "Proposat",
  selected: "Seleccionat",
  missing: "Falta",
  needs_review: "Cal revisar",
  not_applicable: "No aplica",
};

export const VALIDITY_LABELS: Record<DocumentValidity, string> = {
  valid: "Vigent el dia del termini",
  expires_before_deadline: "Caduca ABANS del termini",
  expired: "Caducat",
  no_expiry: "Sense data de caducitat",
  no_document: "Sense document",
};

export const COVERAGE_LABELS: Record<DossierCoverageState, string> = {
  covered: "Cobert",
  missing_document: "Falta document",
  needs_review: "Cal revisar",
  not_applicable: "No aplica",
};

export const READINESS_LABELS: Record<WorkspaceReadiness, string> = {
  not_started: "Sense començar",
  incomplete: "Incomplet",
  needs_review: "Cal revisar",
  ready_for_final_review: "A punt per a la revisió final",
};

/** Text de cada blocador. Derivat del codi que envia l'API, mai text lliure. */
export const READINESS_BLOCKER_LABELS: Record<string, string> = {
  decision_not_go: "Encara no hi ha una decisió humana de GO.",
  mandatory_unresolved: "Hi ha requisits obligatoris sense resoldre al compliment.",
  mandatory_checklist_blocked: "Hi ha items obligatoris de la checklist marcats com a bloquejats.",
  mandatory_evidence_missing: "Falten documents per a requisits obligatoris.",
  mandatory_document_expired:
    "Algun document seleccionat caduca abans del termini de presentació.",
  mandatory_tasks_open: "Hi ha tasques obligatòries pendents.",
  evidence_needs_review: "Hi ha evidències marcades per revisar.",
  mandatory_relevance_unproven:
    "Hi ha documents triats per a requisits obligatoris sense que ningú hagi confirmat que els acrediten.",
  analysis_outdated:
    "Hi ha una anàlisi de requisits o un càlcul de compliment més recents: cal reconciliar el dossier.",
};

/**
 * Text d'un blocador de readiness. `blockers` és `string[]` al contracte: un
 * codi que el diccionari no conegui es diu en humà i el codi queda al `title`.
 */
export function readinessBlockerLabel(code: string): string {
  return READINESS_BLOCKER_LABELS[code] ?? "Bloqueig de preparació no reconegut";
}

/** Text d'un motiu d'obsolescència del paquet. Un codi desconegut no arriba al text. */
export function staleReasonLabel(code: string): string {
  return STALE_REASON_LABELS[code] ?? "Motiu d'obsolescència no reconegut";
}

export interface WorkspaceCreateResult {
  created: boolean;
  checklist_items_created: number;
  workspace: WorkspaceSummary;
}

export const WORKSPACE_STATUS_LABELS: Record<WorkspaceStatus, string> = {
  analysis: "En anàlisi",
  needs_review: "Cal revisió",
  awaiting_decision: "Pendent de decisió",
  go_approved: "GO aprovat",
  no_go_approved: "NO-GO aprovat",
  preparing_bid: "Preparant oferta",
  ready_for_final_review: "A revisió final",
  approved_for_submission: "Aprovada per presentar",
  submitted: "Presentada",
  withdrawn: "Retirada",
};

export const DECISION_LABELS: Record<WorkspaceDecisionType, string> = {
  go: "GO",
  no_go: "NO-GO",
  defer: "Ajornada",
};

export const CHECKLIST_STATUS_LABELS: Record<ChecklistItemStatus, string> = {
  pending: "Pendent",
  in_progress: "En curs",
  completed: "Fet",
  blocked: "Bloquejat",
  not_applicable: "No aplica",
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "Pendent",
  in_progress: "En curs",
  completed: "Feta",
  cancelled: "Cancel·lada",
};

/**
 * Text de cada avís obert. Derivat del codi que envia l'API, mai text lliure:
 * el motiu pel qual un GO necessita justificació ha de ser el mateix al
 * servidor i a la pantalla.
 */
export const WARNING_LABELS: Record<string, string> = {
  confirmed_blockers: "Hi ha bloqueigs confirmats: un requisit obligatori no es compleix.",
  potential_blockers:
    "Hi ha possibles bloqueigs: un requisit obligatori sembla no complir-se, però l'extracció no està verificada.",
  mandatory_unknown:
    "Hi ha requisits obligatoris sense dades al perfil: no se'n pot dir ni que sí ni que no.",
  mandatory_needs_review: "Hi ha requisits obligatoris que una persona ha de revisar.",
  deadline_passed: "El termini de presentació ja ha passat.",
  missing_compliance: "Encara no s'ha calculat el compliment d'aquesta licitació.",
  undetermined_eligibility:
    "No s'ha avaluat cap requisit del plec: «cap bloqueig» aquí no vol dir que no n'hi hagi, vol dir que no s'ha mirat.",
  unreliable_analysis:
    "L'anàlisi de requisits no surt de llegir el plec (simulació o cap motor configurat).",
  outdated_analysis:
    "La licitació ha canviat després de l'anàlisi: el que es mostra descriu un estat anterior.",
};

/** Etiqueta d'un codi d'avís; un codi sense etiqueta mai no es mostra cru (spec F-4 §6). */
export function warningLabel(code: string): string {
  return Object.hasOwn(WARNING_LABELS, code) ? WARNING_LABELS[code] : "Avís del compliment anterior";
}

export const OUTDATED_LABELS: Record<string, string> = {
  requirement_run_changed: "Hi ha una anàlisi de requisits més recent que la del workspace.",
  compliance_run_changed: "Hi ha un càlcul de compliment més recent que el del workspace.",
  compliance_run_missing: "El càlcul de compliment de referència ja no és vigent.",
  tender_not_actionable: "L'òrgan ha anul·lat aquesta licitació.",
  lot_removed: "El lot d'aquesta candidatura ja no és a la licitació.",
  prefit_outdated: "La licitació ha canviat en alguna cosa que afecta l'encaix previ.",
  requirements_outdated: "Els plecs han canviat: cal tornar a analitzar-los.",
  compliance_outdated: "Els imports de la licitació han canviat: cal recalcular el compliment.",
};

/**
 * Per què una decisió humana no es pot donar per vigent.
 *
 * Són dos motius molt diferents i barrejar-los seria un error: en un sabem què
 * ha canviat i en l'altre no sabem ni de què partíem. El segon és el de les
 * decisions anteriors a l'historial de canvis.
 */
export const DECISION_REASON_LABELS: Record<string, string> = {
  tender_changed_since_decision: "La licitació ha canviat després de la decisió.",
  revision_basis_unknown:
    "Aquesta decisió és anterior a l'historial de canvis: no consta sobre quin estat de la licitació es va prendre.",
};

/** Text del comptador de dies. Mai s'amaga un termini passat. */
export function deadlineLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return `Termini tancat fa ${Math.abs(days)} ${Math.abs(days) === 1 ? "dia" : "dies"}`;
  if (days === 0) return "Venç avui";
  return `${days} ${days === 1 ? "dia" : "dies"} restants`;
}


/* -------------------------------------------------------------------------- */
/* Paquet de presentació (5C)                                                  */
/* -------------------------------------------------------------------------- */

export type SubmissionPackageStatus = "draft" | "frozen" | "approved" | "superseded";
export type PackageApprovalDecision = "approved_for_submission" | "rejected";

export interface PackageItem {
  id: string;
  document_id: string | null;
  workspace_evidence_item_id: string | null;
  requirement_id: string | null;
  requirement_title: string | null;
  category: string;
  filename: string;
  mime_type: string;
  file_size: number;
  /** El hash dels BYTES aprovats, copiat en congelar. */
  document_sha256: string;
  position: number;
  validity: DocumentValidity | null;
  document_expires_at: string | null;
  /** Cert si el document d'ara ja no és el que es va congelar. */
  changed_since_freeze: boolean;
}

export interface PackageApproval {
  id: string;
  package_id: string;
  package_manifest_hash: string;
  decision: PackageApprovalDecision;
  approved_by: string;
  approved_by_name: string | null;
  approved_at: string;
  comment: string | null;
  readiness_snapshot: WorkspaceReadiness;
  blockers_snapshot: string[];
}

export interface PackageSummary {
  id: string;
  workspace_id: string;
  revision_number: number;
  status: SubmissionPackageStatus;
  manifest_hash: string | null;
  manifest_hash_short: string | null;
  document_count: number;
  mandatory_total: number;
  mandatory_covered: number;
  deadline_at: string | null;
  frozen_at: string | null;
  created_at: string;
  is_approved: boolean;
  approved_at: string | null;
  approved_by_name: string | null;
  /**
   * Cert si la licitació ha canviat materialment des que es va congelar. Un
   * paquet aprovat continua aprovat: això diu si encara és presentable.
   */
  tender_changed_since_freeze: boolean;
}

export interface PackageDetail {
  id: string;
  workspace_id: string;
  company_id: string;
  revision_number: number;
  status: SubmissionPackageStatus;
  manifest_hash: string | null;
  manifest_hash_short: string | null;
  tender_title: string | null;
  expediente_number: string | null;
  lot_number: number | null;
  deadline_at: string | null;
  days_remaining: number | null;
  compliance_run_id: string | null;
  requirement_run_id: string | null;
  prefit_score: number | null;
  coverage: { mandatory_total: number; mandatory_covered: number; document_count: number };
  items: PackageItem[];
  approvals: PackageApproval[];
  approval: PackageApproval | null;
  readiness: WorkspaceReadiness;
  readiness_blockers: string[];
  stale_reasons: string[];
  is_stale: boolean;
  /**
   * Si el paquet encara descriu una presentació possible. Independent
   * d'`is_approved`: una aprovació no es desfà mai, però un paquet aprovat
   * contra un plec que després han substituït ja no s'envia.
   */
  coherent_for_submission: boolean;
  /** Sobre quines menes d'anàlisi descansa el paquet (requisits i compliment). */
  analysis_bases: string[];
  /**
   * Cert quan alguna de les anàlisis que el sostenen no surt de llegir cap
   * plec. Congelar-lo sí que es pot —cal, per provar la pantalla— però mai
   * arriba a l'aprovació final.
   */
  simulation_only: boolean;
  can_approve: boolean;
  can_sign: boolean;
  workspace_status: WorkspaceStatus;
  /** Aprovar el paquet NO és haver presentat res. */
  is_submitted: boolean;
  frozen_at: string | null;
  created_at: string;
}

export interface FreezeResult {
  created: boolean;
  package: PackageDetail;
}

export const PACKAGE_STATUS_LABELS: Record<SubmissionPackageStatus, string> = {
  draft: "Esborrany",
  frozen: "Congelat",
  approved: "Aprovat per presentar",
  superseded: "Superat",
};

/**
 * Nom d'un estat de paquet. Un estat que aquesta versió no conegui es mostra
 * amb el seu codi: una pastilla muda no diria res, i inventar-li un nom seria
 * pitjor. El que no fa mai és passar per aprovat.
 */
export function packageStatusLabel(status: string): string {
  return (PACKAGE_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

/** Per què el paquet ja no descriu el que hi ha. Codis, mai text lliure. */
export const STALE_REASON_LABELS: Record<string, string> = {
  manifest_changed: "La candidatura actual ja no coincideix amb aquesta revisió congelada.",
  manifest_version_outdated:
    "Aquesta revisió es va congelar amb un format anterior, que no descrivia ni la solució tècnica ni l'oferta econòmica.",
  compliance_run_changed: "S'ha recalculat el compliment després de congelar.",
  requirement_run_changed: "Hi ha una anàlisi de requisits més recent.",
  document_expired_for_deadline:
    "Algun document del paquet ja no serà vigent el dia del termini.",
  superseded: "Aquesta revisió ha quedat superada per una de posterior.",
  tender_changed: "La licitació ha canviat des que es va congelar el paquet.",
  tender_revision_unknown:
    "Aquest paquet és anterior a l'historial de canvis: no consta contra quin estat de la licitació es va congelar.",
  tender_not_actionable: "L'òrgan ha anul·lat aquesta licitació.",
  lot_removed: "El lot d'aquesta candidatura ja no és a la licitació.",
};


/**
 * Text del resultat d'una reconciliació.
 *
 * Un 200 que no canvia res ha de poder dir per què. El codi el decideix el
 * servidor (`nothing_to_do_reason`) perquè la raó sigui la mateixa als dos
 * costats i no dues versions que divergeixin.
 */
export const NOTHING_TO_DO_LABELS: Record<string, string> = {
  no_requirements_analysed:
    "No hi ha cap requisit analitzat en aquest plec: no hi ha res d'on derivar-ho.",
  already_in_sync: "Ja estava al dia: no hi havia res de nou per incorporar.",
};

/**
 * Què ha passat en reconciliar la checklist amb l'anàlisi vigent.
 *
 * Crear items nous no és l'única cosa que pot passar, i durant molt temps era
 * l'única que es deia: una reconciliació pot moure feina d'abans al requisit
 * equivalent d'ara i pot deixar-ne d'altra com a història. Qui prem el botó ha
 * de saber què li ha passat als seus papers, no només quants forats nous té.
 */
export function checklistReconciliationMessage(
  result: ChecklistRegenerateResult,
): { message: string; detail?: string; tone: "info" | "success" } {
  const parts: string[] = [];
  if (result.created > 0) {
    parts.push(`${result.created} ${result.created === 1 ? "item nou" : "items nous"}`);
  }
  if (result.carried > 0) {
    parts.push(
      `${result.carried} ${result.carried === 1 ? "item conservat" : "items conservats"}`,
    );
  }
  // L'històric no és una cosa que hagi passat en prémer el botó: són items que
  // el plec ja no demana des que es va reanalitzar. Va al detall, mai al
  // recompte del que s'acaba de fer, perquè si no cada premuda diria el mateix
  // com si tornés a passar.
  const detail =
    result.historic > 0
      ? `${result.historic} ${result.historic === 1 ? "item queda" : "items queden"} a l'històric: ` +
        "el plec ja no ho demana. No s'han esborrat."
      : undefined;

  if (parts.length === 0) {
    return {
      message: "Cap canvi: la checklist ja treballa contra l'anàlisi vigent.",
      detail: detail ?? (result.reason ? NOTHING_TO_DO_LABELS[result.reason] ?? result.reason : undefined),
      tone: "info",
    };
  }
  return { message: `${parts.join(" · ")}.`, detail, tone: "success" };
}

export function reconciliationMessage(
  created: number,
  reason: string | null,
  noun: { one: string; many: string },
): { message: string; detail?: string; tone: "info" | "success" } {
  if (created > 0) {
    return {
      message: `${created} ${created === 1 ? noun.one : noun.many}.`,
      tone: "success",
    };
  }
  return {
    message: `Cap ${noun.one} nou.`,
    detail: reason ? NOTHING_TO_DO_LABELS[reason] ?? reason : undefined,
    tone: "info",
  };
}
