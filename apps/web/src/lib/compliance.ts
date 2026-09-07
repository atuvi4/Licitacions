/**
 * Compliment de l'empresa: tipus i etiquetes.
 *
 * Dues dimensions que NO es fusionen mai a la interfície:
 *
 * - **Pre-fit**: "ens interessa?" (metadades de la licitació).
 * - **Elegibilitat**: "podem acreditar que complim?" (requisits del plec).
 *
 * Un pre-fit de 95 amb un bloqueig confirmat continua sent un NO-GO, i el 95
 * s'ha de continuar veient al costat, no dissolt en cap mitjana.
 */

export type ComplianceRunStatus = "pending" | "running" | "completed" | "failed";

export type AssessmentResult =
  | "satisfies"
  | "does_not_satisfy"
  | "unknown"
  | "needs_review"
  | "not_applicable";

export type BlockerStatus = "none" | "potential" | "confirmed";
export type EligibilityResult = "eligible" | "review_required" | "no_go";
export type RecommendationResult = "go_candidate" | "eligible_low_fit" | "review" | "no_go";
export type EvidenceStrength = "strong" | "supporting" | "missing";
export type AssessmentMode = "automatic" | "review_required";
export type ReviewStatus = "unreviewed" | "verified" | "needs_review" | "rejected";
export type RequirementObligation = "mandatory" | "scored" | "informative" | "unknown";

export interface CompanyEvidence {
  id: string;
  entity_type: string;
  entity_id: string;
  document_id: string | null;
  label: string;
  strength: EvidenceStrength;
}

export interface RequirementSource {
  requirement_id: string;
  title: string;
  category: string;
  review_status: ReviewStatus;
  tender_document_id: string | null;
  extraction_id: string | null;
  extraction_revision: number | null;
  page_number: number | null;
  quote: string | null;
  document_name: string | null;
}

export interface Assessment {
  id: string;
  assessment_run_id: string;
  requirement_id: string;
  tender_id: string;
  tender_lot_id: string | null;
  automatic_result: AssessmentResult;
  reviewed_result: AssessmentResult | null;
  effective_result: AssessmentResult;
  assessment_mode: AssessmentMode;
  rule_code: string;
  explanation_code: string;
  explanation_params: Record<string, unknown> | null;
  company_value_json: Record<string, unknown> | null;
  requirement_value_json: Record<string, unknown> | null;
  obligation: RequirementObligation;
  blocker_status: BlockerStatus;
  evidence_strength: EvidenceStrength;
  review_status: ReviewStatus;
  review_note: string | null;
  reviewed_at: string | null;
  calculated_at: string;
  evidence: CompanyEvidence[];
  requirement: RequirementSource | null;
}

export interface ComplianceRun {
  id: string;
  company_id: string;
  tender_id: string;
  status: ComplianceRunStatus;
  is_current: boolean;
  rules_version: string;
  company_profile_fingerprint: string;
  requirements_fingerprint: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  requirements_total: number;
  mandatory_total: number;
  satisfies_count: number;
  does_not_satisfy_count: number;
  unknown_count: number;
  needs_review_count: number;
  not_applicable_count: number;
  potential_blockers_count: number;
  confirmed_blockers_count: number;
  overall_result: EligibilityResult;
  recommendation: RecommendationResult;
  prefit_score: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface Compliance {
  run: ComplianceRun | null;
  assessments: Assessment[];
  is_stale: boolean;
}

export const RESULT_LABELS: Record<AssessmentResult, string> = {
  satisfies: "Compleix",
  does_not_satisfy: "No compleix",
  unknown: "Sense dades",
  needs_review: "Cal revisar",
  not_applicable: "No aplica",
};

export const RESULT_ICONS: Record<AssessmentResult, string> = {
  satisfies: "✅",
  does_not_satisfy: "❌",
  unknown: "❓",
  needs_review: "⚠️",
  not_applicable: "—",
};

export const RESULT_TONES: Record<
  AssessmentResult,
  "neutral" | "brand" | "success" | "warning" | "danger"
> = {
  satisfies: "success",
  does_not_satisfy: "danger",
  unknown: "warning",
  needs_review: "warning",
  not_applicable: "neutral",
};

export const ELIGIBILITY_LABELS: Record<EligibilityResult, string> = {
  eligible: "Elegible",
  review_required: "Cal revisió",
  no_go: "NO-GO",
};

export const ELIGIBILITY_TONES: Record<
  EligibilityResult,
  "neutral" | "brand" | "success" | "warning" | "danger"
> = {
  eligible: "success",
  review_required: "warning",
  no_go: "danger",
};

export const RECOMMENDATION_LABELS: Record<RecommendationResult, string> = {
  go_candidate: "Candidata",
  eligible_low_fit: "Elegible però poc encaix",
  review: "Revisió",
  no_go: "NO-GO",
};

export const BLOCKER_LABELS: Record<BlockerStatus, string> = {
  none: "",
  potential: "Possible bloqueig",
  confirmed: "Bloqueig confirmat",
};

export const STRENGTH_LABELS: Record<EvidenceStrength, string> = {
  strong: "Acreditada",
  supporting: "Suport",
  missing: "Sense document",
};

/** Text de la UI derivat del codi d'explicació. Mai text lliure de la BD. */
export const EXPLANATION_LABELS: Record<string, string> = {
  ECONOMIC_THRESHOLD_MET: "La facturació registrada supera el llindar exigit.",
  ECONOMIC_THRESHOLD_NOT_MET: "La facturació registrada no arriba al llindar exigit.",
  ECONOMIC_DATA_INCOMPLETE: "No hi ha prou dades de facturació al perfil.",
  ECONOMIC_CURRENCY_MISMATCH: "La moneda del plec i la del perfil no coincideixen.",
  ECONOMIC_PERIOD_AMBIGUOUS: "El període que demana el plec no és inequívoc.",
  CERTIFICATION_FOUND_VALID: "La certificació consta al perfil i és vigent.",
  CERTIFICATION_FOUND_EXPIRED: "La certificació caduca abans del termini de presentació.",
  CERTIFICATION_FOUND_NO_EXPIRY: "La certificació no té data de caducitat registrada.",
  CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE:
    "El perfil de certificacions és complet i no conté aquesta certificació.",
  CERTIFICATION_DATA_INCOMPLETE:
    "El perfil de certificacions és incomplet: no consta, però això no vol dir que no la tingueu.",
  REGISTRATION_FOUND_VALID: "El registre consta al perfil i és vigent.",
  REGISTRATION_FOUND_EXPIRED: "El registre caduca abans del termini.",
  REGISTRATION_NOT_FOUND_PROFILE_COMPLETE:
    "El perfil de registres és complet i no conté aquest registre.",
  REGISTRATION_DATA_INCOMPLETE: "El perfil de registres és incomplet.",
  REGISTRATION_CLASSIFICATION_REVIEW:
    "La classificació empresarial (grup, subgrup, categoria) s'ha de revisar a mà.",
  INSURANCE_COVERAGE_MET: "La pòlissa cobreix l'import exigit.",
  INSURANCE_COVERAGE_NOT_MET: "La cobertura de la pòlissa no arriba a l'import exigit.",
  INSURANCE_EXPIRED: "La pòlissa caduca abans del termini.",
  INSURANCE_NOT_FOUND_PROFILE_COMPLETE:
    "El perfil d'assegurances és complet i no conté aquesta pòlissa.",
  INSURANCE_DATA_INCOMPLETE: "El perfil d'assegurances és incomplet.",
  INSURANCE_CURRENCY_MISMATCH: "La moneda de la pòlissa no coincideix amb la del plec.",
  EXPERIENCE_COUNT_MET: "Hi ha prou projectes acreditats que compleixen les condicions.",
  EXPERIENCE_INSUFFICIENT: "No hi ha prou projectes que compleixin les condicions.",
  EXPERIENCE_DATA_INCOMPLETE: "El perfil d'experiència és incomplet.",
  EXPERIENCE_SIMILARITY_REVIEW:
    "El plec demana treballs «similars» sense un criteri objectiu: ho ha de valorar una persona.",
  EXPERIENCE_EVIDENCE_MISSING:
    "Hi ha projectes que encaixen, però els falta document acreditatiu.",
  QUALIFICATION_FOUND: "L'equip acredita la titulació exigida.",
  QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE:
    "El perfil d'equip és complet i no conté aquesta titulació.",
  QUALIFICATION_UNKNOWN: "El perfil d'equip és incomplet.",
  QUALIFICATION_AMBIGUOUS: "La titulació exigida no és prou concreta.",
  PERSONNEL_COUNT_MET: "L'equip arriba al mínim de persones exigit.",
  PERSONNEL_COUNT_NOT_MET: "L'equip no arriba al mínim exigit.",
  PERSONNEL_DATA_INCOMPLETE: "El perfil d'equip és incomplet.",
  RULE_NOT_AUTOMATABLE: "Aquest requisit no es pot comprovar automàticament.",
  REQUIREMENT_VALUE_MISSING:
    "El requisit no té un valor estructurat que es pugui comparar. Revisa'l al plec.",
  SCOPE_NOT_APPLICABLE: "El requisit no aplica a aquest lot.",
  SCOPE_AMBIGUOUS: "No se sap a quin lot aplica el requisit.",
};

export function explanationText(code: string): string {
  return EXPLANATION_LABELS[code] ?? code;
}

/** Avís: això no és assessorament jurídic. */
export const COMPLIANCE_DISCLAIMER =
  "L'anàlisi de compliment compara dades estructurades. Revisa l'evidència del plec i la del " +
  "perfil abans de decidir si us hi presenteu.";
