/**
 * Anàlisi de plecs: tipus i etiquetes.
 *
 * Aquesta capa respon "què exigeix el plec", no "l'empresa ho compleix". Per
 * això aquí no hi ha cap noció de compliment, de blocador ni de puntuació de
 * l'empresa: aquesta comparació és una fase posterior.
 */

import type { AnalysisBasis } from "@/lib/compliance";

/**
 * `completed_empty` (3B-2 §6.3): una lectura real acabada sense cap requisit ni
 * criteri sobre prou pàgines. Mai és vigent i demana una persona que ho revisi.
 */
export type AnalysisRunStatus = "pending" | "running" | "completed" | "completed_empty" | "partial" | "failed";

/** Sobre què es va construir un run de requisits (0061). Només `real` pot ser vigent. */
export type RequirementRunBasis = "real" | "simulation" | "unconfigured" | "gold";

/** Qui pot decidir quina anàlisi és la base de l'empresa (`ADOPTION_ROLES` del servidor). */
export const ADOPTION_ROLES = ["owner", "admin", "bid_manager"] as const;

export function canAdoptAnalysis(role: string | null | undefined): boolean {
  return role !== null && role !== undefined && (ADOPTION_ROLES as readonly string[]).includes(role);
}

export type RequirementCategory =
  | "deadline"
  | "economic_solvency"
  | "technical_solvency"
  | "experience"
  | "certification"
  | "registration"
  | "business_classification"
  | "insurance"
  | "personnel"
  | "professional_qualification"
  | "equipment_or_resources"
  | "administrative_documentation"
  | "technical_requirement"
  | "contract_condition"
  | "other";

export type RequirementObligation = "mandatory" | "scored" | "informative" | "unknown";
export type RequirementScope = "tender" | "lot" | "multiple_lots" | "unknown";
export type ExtractionConfidence = "high" | "medium" | "low";
export type ReviewStatus = "unreviewed" | "verified" | "needs_review" | "rejected";
export type AwardEvaluationType = "automatic" | "judgment" | "mixed" | "unknown";

export interface RequirementEvidence {
  id: string;
  extraction_id: string;
  page_id: string;
  tender_document_id: string | null;
  /** 1-based, com a tot el sistema. */
  page_number: number;
  quote: string;
  char_start: number | null;
  char_end: number | null;
  section: string | null;
  evidence_role: "primary" | "supporting";
  /** Revisió citada. La vista de text hi ha d'anar exactament. */
  extraction_revision: number | null;
  document_name: string | null;
}

/** Qui ha escrit el requisit. Canvia què se'n pot afirmar, no només la fitxa. */
export type RequirementOrigin = "extracted" | "manual";

export type { TenderDocumentMeta } from "@/lib/tenders";

/** Una cita immutable: de quins bytes, quina versió i —si n'hi ha— quina pàgina. */
export interface SourceRefCitation {
  id: string;
  ref_kind: "extracted_page" | "document_file";
  document_id: string | null;
  document_name: string | null;
  file_id: string | null;
  version_number: number | null;
  content_sha256: string;
  extraction_id: string | null;
  page_id: string | null;
  page_number: number | null;
  location_label: string | null;
  quote: string | null;
  char_start: number | null;
  char_end: number | null;
  created_at: string;
}

/**
 * On es treballa un requisit dins d'una candidatura. La regla la decideix el
 * servidor (`app/workspace/requirement_routing.py`); la interfície només la
 * llegeix i no en calcula cap.
 */
export type RequirementRouting = "compliance_dossier" | "technical_solution" | "work_plan" | "review_routing";

export type RequirementTiming = "at_submission" | "before_formalization" | "at_execution" | "ambiguous" | "unknown";

export interface Requirement {
  id: string;
  /** `null` als escrits a mà: no venen de cap execució d'anàlisi. */
  run_id: string | null;
  origin: RequirementOrigin;
  archived_at: string | null;
  /** La provinença dels manuals. Els extrets la porten a `evidence`. */
  citations: SourceRefCitation[];
  /** Quan s'ha de complir segons el plec; `unknown` si no es sap. */
  timing: RequirementTiming;
  tender_id: string;
  tender_lot_id: string | null;
  category: RequirementCategory;
  obligation: RequirementObligation;
  operator: string | null;
  scope: RequirementScope;
  title: string;
  description: string | null;
  value_json: Record<string, unknown> | null;
  raw_lot_labels: unknown[] | null;
  /** `null` als manuals: no hi ha cap model que hagi declarat res. */
  confidence: ExtractionConfidence | null;
  review_status: ReviewStatus;
  review_note: string | null;
  manual_override_json: Record<string, unknown> | null;
  reviewed_at: string | null;
  has_conflict: boolean;
  conflict_group: string | null;
  created_at: string;
  evidence: RequirementEvidence[];
  routing: RequirementRouting;
}

export interface AwardCriterion {
  id: string;
  run_id: string;
  tender_id: string;
  tender_lot_id: string | null;
  category: string | null;
  name: string;
  description: string | null;
  points: number | null;
  weight_percent: number | null;
  formula: string | null;
  evaluation_type: AwardEvaluationType;
  confidence: ExtractionConfidence;
  review_status: ReviewStatus;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
  evidence: RequirementEvidence[];
  /** Cites manuals o de l'abast del criteri (mateixa forma que les dels requisits). */
  citations: SourceRefCitation[];
}

export interface ExtractionRun {
  id: string;
  tender_id: string;
  status: AnalysisRunStatus;
  is_current: boolean;
  input_fingerprint: string;
  extractor_version: string;
  prompt_version: string;
  schema_version: string;
  provider: string;
  model: string;
  /** Sobre QUÈ s'ha construït aquesta anàlisi. `status` només diu que el procés
   *  ha acabat; això diu si darrere hi ha una lectura real del plec. */
  analysis_basis: AnalysisBasis;
  started_at: string;
  completed_at: string | null;
  documents_analyzed: number;
  pages_analyzed: number;
  chunks_total: number;
  chunks_completed: number;
  chunks_failed: number;
  requirements_extracted: number;
  requirements_rejected: number;
  award_criteria_extracted: number;
  evidence_rejected: number;
  validation_errors: number;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: number | null;
  duration_ms: number | null;
  skipped_documents: Array<{
    tender_document_id: string;
    document_name: string;
    document_type: string;
    reason: string;
  }> | null;
  error_code: string | null;
  error_message: string | null;
  /** La base desada del run (0061). Una simulació (`simulation`) mai és vigent. */
  run_basis: RequirementRunBasis;
  /** Cert en un `completed_empty`: «anàlisi acabada sense resultats: cal revisar». */
  needs_review: boolean;
}

export interface RequirementAnalysis {
  run: ExtractionRun | null;
  requirements: Requirement[];
  award_criteria: AwardCriterion[];
  award_candidates?: AwardCriterion[];
  /** Els documents han canviat des de l'anàlisi vigent. */
  is_stale: boolean;
  /** 3B-2 §6.5: la run que llegeix l'empresa (el seu punter o la canònica). */
  effective_run_id?: string | null;
  /** La canònica global, si n'hi ha. */
  canonical_run_id?: string | null;
  /** Hi ha una anàlisi canònica nova que l'empresa encara no ha adoptat. */
  adoption_available?: boolean;
  /**
   * «Hi ha anàlisi?» segons el servidor, amb el MATEIX càlcul que el dossier
   * (3B-2 §7.1.3): sense run però amb requisits escrits a mà és `current`.
   */
  analysis_state?: RequirementAnalysisState;
}

/** `absent` (cap anàlisi), `current` (vigent, també la humana) o `stale` (obsoleta). */
export type RequirementAnalysisState = "absent" | "current" | "stale";

/**
 * L'única lectura de «hi ha anàlisi del plec?» a la fitxa (M1). Mana el que diu
 * el servidor (`analysis_state`, el mateix càlcul que el dossier). Només si el
 * servidor encara no ho envia (API anterior) es dedueix amb la mateixa regla, per
 * no deixar la fitxa muda: una run obsoleta és `stale`, una run és `current`, i
 * sense run els requisits escrits a mà vius també són una anàlisi.
 */
export function requirementAnalysisState(analysis: RequirementAnalysis | null | undefined): RequirementAnalysisState {
  if (!analysis) return "absent";
  if (analysis.analysis_state) return analysis.analysis_state;
  if (analysis.run) return analysis.is_stale ? "stale" : "current";
  return liveManualRequirements(analysis) > 0 ? "current" : "absent";
}

/** Requisits escrits a mà i no arxivats. */
export function liveManualRequirements(analysis: RequirementAnalysis): number {
  return analysis.requirements.filter((item) => item.origin === "manual" && !item.archived_at).length;
}

/** Resposta d'adoptar una anàlisi com la base de l'empresa. */
export interface RequirementRunAdoption {
  effective_run_id: string;
  previous_run_id: string | null;
  /** La revisió de l'empresa representava una altra run: cal reconciliar-la. */
  reconciliation_required: boolean;
}

/** Estat visible de l'anàlisi. Es deriva aquí perquè és pura presentació. */
export type AnalysisState =
  | "not_analyzed"
  | "analyzing"
  | "completed"
  | "completed_empty"
  | "partial"
  | "error"
  | "stale";

export function analysisState(analysis: RequirementAnalysis | null): AnalysisState {
  if (!analysis?.run) return "not_analyzed";
  const { status } = analysis.run;
  if (status === "pending" || status === "running") return "analyzing";
  if (status === "failed") return "error";
  if (status === "completed_empty") return "completed_empty";
  if (analysis.is_stale) return "stale";
  if (status === "partial") return "partial";
  return "completed";
}

/** El copy de `completed_empty` (3B-2 §6.3): el mateix a tot arreu. */
export const COMPLETED_EMPTY_TEXT = "Anàlisi acabada sense resultats: cal revisar";
/** Una simulació (`fake`) mai és vigent (3B-2 §6.3). */
export const SIMULATION_NOT_CURRENT_TEXT = "Anàlisi de simulació: no és vigent";

/** Quantes passades de l'historial es demanen per explicar-lo (P2-01): el valor per defecte de l'API (màxim 100). */
export const RUN_HISTORY_LIMIT = 20;

/**
 * Si l'última run no vigent ja té el seu avís propi (simulació o acabada sense
 * resultats, 3B-2 §6.3): llavors l'historial no la torna a comptar.
 */
export function hasOwnNotCurrentBanner(item: ExtractionRun): boolean {
  return item.status === "completed_empty" || (item.run_basis !== "real" && item.run_basis !== "gold");
}

/**
 * Les anàlisis d'aquests plecs que existeixen però no són la vigent (P2-01): es
 * poden consultar com a historial, però mai es promouen ni es barregen amb el
 * conjunt efectiu. Les que encara corren no hi són (d'aquestes en parla la feina),
 * ni la que ja té el seu avís (`exceptId`).
 */
export function historicRuns(
  runs: ExtractionRun[] | null | undefined,
  effectiveRunId: string | null,
  exceptId: string | null = null,
): ExtractionRun[] {
  if (!Array.isArray(runs)) return [];
  return runs.filter(
    (item) =>
      item.id !== effectiveRunId &&
      item.id !== exceptId &&
      !item.is_current &&
      item.status !== "pending" &&
      item.status !== "running",
  );
}

/** Per què una anàlisi de l'historial no és la vigent, en llenguatge de producte. */
export function notCurrentReason(item: ExtractionRun): string {
  if (item.run_basis !== "real" && item.run_basis !== "gold") return "Simulació: no ha llegit el plec i mai no pot ser vigent.";
  if (item.error_code === "experimental_profile_not_current")
    return "Perfil experimental: es conserva com a historial, però mai no pot ser la base vigent.";
  if (item.error_code === "forced_not_canonical") return "Anàlisi forçada per l'operador: mai no és la vigent.";
  if (item.error_code === "selection_not_canonical")
    return "Feta amb una selecció de documents que no és la canònica: no pot ser la vigent.";
  if (item.error_code === "input_changed" || item.error_code === "stale") return "Els plecs han canviat des d'aquesta anàlisi.";
  if (item.error_code === "budget_exceeded") return "Aturada pel límit de consum: no es va completar.";
  if (item.error_code === "chunks_not_selected") return "No va llegir tots els fragments del plec.";
  if (item.status === "failed")
    return item.chunks_completed > 0
      ? "Ha fallat abans d'acabar la lectura del plec."
      : "Ha fallat: cap part del plec s'ha pogut analitzar.";
  if (item.status === "partial") return "Lectura parcial: alguns fragments del plec han fallat.";
  if (item.status === "completed_empty") return "Acabada sense cap requisit ni criteri: ho ha de mirar una persona.";
  return "No és la vigent: n'hi ha una de posterior o els plecs han canviat.";
}

/** Estat d'una passada de l'historial, tal com el desa el run. */
export const RUN_STATUS_LABELS: Record<AnalysisRunStatus, string> = {
  pending: "Pendent",
  running: "En curs",
  completed: "Completada",
  completed_empty: "Sense resultats",
  partial: "Parcial",
  failed: "Fallida",
};

export const ANALYSIS_STATE_LABELS: Record<AnalysisState, string> = {
  not_analyzed: "No analitzat",
  analyzing: "Analitzant",
  completed: "Complet",
  completed_empty: "Sense resultats · cal revisar",
  partial: "Parcial",
  error: "Error",
  stale: "Obsolet",
};

/** Ordre de presentació: primer el que pot excloure. */
export const CATEGORY_ORDER: RequirementCategory[] = [
  "economic_solvency",
  "technical_solvency",
  "experience",
  "certification",
  "registration",
  "business_classification",
  "insurance",
  "personnel",
  "professional_qualification",
  "equipment_or_resources",
  "administrative_documentation",
  "deadline",
  "technical_requirement",
  "contract_condition",
  "other",
];

export const CATEGORY_LABELS: Record<RequirementCategory, string> = {
  economic_solvency: "Solvència econòmica",
  technical_solvency: "Solvència tècnica",
  experience: "Experiència",
  certification: "Certificacions",
  registration: "Registres",
  business_classification: "Classificació empresarial",
  insurance: "Assegurances",
  personnel: "Equip humà",
  professional_qualification: "Titulacions",
  equipment_or_resources: "Equipament i recursos",
  administrative_documentation: "Documentació",
  deadline: "Terminis",
  technical_requirement: "Condicions tècniques",
  contract_condition: "Condicions del contracte",
  other: "Altres",
};

export const OBLIGATION_LABELS: Record<RequirementObligation, string> = {
  mandatory: "Obligatori",
  scored: "Puntuable",
  informative: "Informatiu",
  unknown: "No determinat",
};

export const CONFIDENCE_LABELS: Record<ExtractionConfidence, string> = {
  high: "Alta",
  medium: "Mitjana",
  low: "Baixa",
};

/**
 * `Requirement.review_status` és un senyal de l'EXTRACCIÓ, no la revisió de
 * l'empresa (que viu a `/revisio`). L'anàlisi hi deixa `needs_review` quan
 * marca una incidència i `unreviewed` si no; `verified`/`rejected` només els
 * escrivien els antics botons per fila. Cap etiqueta reutilitza el vocabulari
 * de la revisió canònica («Cal revisar», «Pendent de revisar», «Verificat»).
 */
export const EXTRACTION_STATUS_LABELS: Record<ReviewStatus, string> = {
  unreviewed: "Extracció sense validar",
  verified: "Extracció validada",
  needs_review: "Extracció amb incidència",
  rejected: "Extracció descartada",
};

export const EVALUATION_LABELS: Record<AwardEvaluationType, string> = {
  automatic: "Automàtic",
  judgment: "Judici de valor",
  mixed: "Mixt",
  unknown: "No determinat",
};

export const SKIP_REASON_LABELS: Record<string, string> = {
  document_not_processed: "Encara no processat",
  document_not_analyzable: "Sense text extraïble (format no admès)",
  document_without_text: "Processat però sense text",
};

/** Avís obligatori: això no és assessorament jurídic. */
export const ANALYSIS_DISCLAIMER =
  "L'anàlisi automàtica extreu informació dels plecs. Revisa l'evidència abans de " +
  "prendre una decisió de presentació.";
