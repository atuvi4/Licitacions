/**
 * Anàlisi de plecs: tipus i etiquetes.
 *
 * Aquesta capa respon "què exigeix el plec", no "l'empresa ho compleix". Per
 * això aquí no hi ha cap noció de compliment, de blocador ni de puntuació de
 * l'empresa: aquesta comparació és una fase posterior.
 */

import type { AnalysisBasis } from "@/lib/compliance";

export type AnalysisRunStatus = "pending" | "running" | "completed" | "partial" | "failed";

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

export interface Requirement {
  id: string;
  /** `null` als escrits a mà: no venen de cap execució d'anàlisi. */
  run_id: string | null;
  origin: RequirementOrigin;
  archived_at: string | null;
  /** La provinença dels manuals. Els extrets la porten a `evidence`. */
  citations: SourceRefCitation[];
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
}

export interface RequirementAnalysis {
  run: ExtractionRun | null;
  requirements: Requirement[];
  award_criteria: AwardCriterion[];
  /** Els documents han canviat des de l'anàlisi vigent. */
  is_stale: boolean;
}

/** Estat visible de l'anàlisi. Es deriva aquí perquè és pura presentació. */
export type AnalysisState =
  | "not_analyzed"
  | "analyzing"
  | "completed"
  | "partial"
  | "error"
  | "stale";

export function analysisState(analysis: RequirementAnalysis | null): AnalysisState {
  if (!analysis?.run) return "not_analyzed";
  const { status } = analysis.run;
  if (status === "pending" || status === "running") return "analyzing";
  if (status === "failed") return "error";
  if (analysis.is_stale) return "stale";
  if (status === "partial") return "partial";
  return "completed";
}

export const ANALYSIS_STATE_LABELS: Record<AnalysisState, string> = {
  not_analyzed: "No analitzat",
  analyzing: "Analitzant",
  completed: "Complet",
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

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  unreviewed: "No revisat",
  verified: "Verificat",
  needs_review: "Cal revisar",
  rejected: "Rebutjat",
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
