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

import type { EligibilityResult, RecommendationResult } from "@/lib/compliance";

export type WorkspaceStatus =
  | "analysis"
  | "needs_review"
  | "awaiting_decision"
  | "go_approved"
  | "no_go_approved"
  | "preparing_bid"
  | "ready_for_final_review"
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
  prefit_score: number | null;
  confirmed_blockers: number;
  potential_blockers: number;
  mandatory_unknown: number;
  mandatory_needs_review: number;
  automated_recommendation: string | null;
  deadline_passed: boolean;
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
  analysis_outdated: boolean;
  outdated_reasons: string[];
  checklist: ChecklistItem[];
  tasks: WorkspaceTask[];
  notes: WorkspaceNote[];
  decisions: WorkspaceDecisionEntry[];
  members: Person[];
  can_decide: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCreateResult {
  created: boolean;
  checklist_items_created: number;
  workspace: WorkspaceSummary;
}

type Tone = "neutral" | "brand" | "success" | "warning" | "danger";

export const WORKSPACE_STATUS_LABELS: Record<WorkspaceStatus, string> = {
  analysis: "En anàlisi",
  needs_review: "Cal revisió",
  awaiting_decision: "Pendent de decisió",
  go_approved: "GO aprovat",
  no_go_approved: "NO-GO aprovat",
  preparing_bid: "Preparant oferta",
  ready_for_final_review: "A revisió final",
  submitted: "Presentada",
  withdrawn: "Retirada",
};

export const WORKSPACE_STATUS_TONES: Record<WorkspaceStatus, Tone> = {
  analysis: "neutral",
  needs_review: "warning",
  awaiting_decision: "warning",
  go_approved: "success",
  no_go_approved: "danger",
  preparing_bid: "brand",
  ready_for_final_review: "brand",
  submitted: "success",
  withdrawn: "neutral",
};

export const DECISION_LABELS: Record<WorkspaceDecisionType, string> = {
  go: "GO",
  no_go: "NO-GO",
  defer: "Ajornada",
};

export const DECISION_TONES: Record<WorkspaceDecisionType, Tone> = {
  go: "success",
  no_go: "danger",
  defer: "warning",
};

export const CHECKLIST_STATUS_LABELS: Record<ChecklistItemStatus, string> = {
  pending: "Pendent",
  in_progress: "En curs",
  completed: "Fet",
  blocked: "Bloquejat",
  not_applicable: "No aplica",
};

export const CHECKLIST_STATUS_TONES: Record<ChecklistItemStatus, Tone> = {
  pending: "neutral",
  in_progress: "brand",
  completed: "success",
  blocked: "danger",
  not_applicable: "neutral",
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "Pendent",
  in_progress: "En curs",
  completed: "Feta",
  cancelled: "Cancel·lada",
};

export const TASK_STATUS_TONES: Record<TaskStatus, Tone> = {
  pending: "neutral",
  in_progress: "brand",
  completed: "success",
  cancelled: "neutral",
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
};

export const OUTDATED_LABELS: Record<string, string> = {
  requirement_run_changed: "Hi ha una anàlisi de requisits més recent que la del workspace.",
  compliance_run_changed: "Hi ha un càlcul de compliment més recent que el del workspace.",
  compliance_run_missing: "El càlcul de compliment de referència ja no és vigent.",
};

/** Text del comptador de dies. Mai s'amaga un termini passat. */
export function deadlineLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return `Termini tancat fa ${Math.abs(days)} dia${Math.abs(days) === 1 ? "" : "s"}`;
  if (days === 0) return "Venç avui";
  return `${days} dia${days === 1 ? "" : "s"} restants`;
}

export function deadlineTone(days: number | null): Tone {
  if (days === null) return "neutral";
  if (days < 0) return "danger";
  if (days <= 7) return "warning";
  return "neutral";
}
