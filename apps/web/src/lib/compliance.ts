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
export type EligibilityResult =
  | "eligible"
  | "review_required"
  | "no_go"
  /** No s'ha pogut avaluar res. NO es un grau de compliment: es l'absencia de
   *  veredicte, i mai s'ha de pintar en verd ni tractar com un NO-GO. */
  | "undetermined";
export type RecommendationResult =
  | "go_candidate"
  | "eligible_low_fit"
  | "review"
  | "no_go"
  | "undetermined";

/** Sobre QUE s'ha construit una analisi. `status` nomes diu que el proces ha
 *  acabat; aixo diu si darrere hi ha una lectura real del plec. */
export type AnalysisBasis = "real" | "simulation" | "provider_not_configured";
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
  /** Qui va revisar. Un veredicte humà sense signatura no és un veredicte humà. */
  reviewed_by: string | null;
  reviewed_by_name: string | null;
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

// A14: cap veredicte automàtic es diu «GO» ni «NO-GO»: això només ho diu una persona.
export const ELIGIBILITY_LABELS: Record<EligibilityResult, string> = {
  eligible: "Elegible",
  review_required: "Cal revisió",
  no_go: "No elegible",
  undetermined: "Sense determinar",
};

/** Per que no hi ha veredicte. Es diu al costat de l'etiqueta, perque
 *  "Sense determinar" tot sol convida a llegir-ho com un problema de l'empresa
 *  i no com el que es: encara no s'ha mirat res. */
export const ELIGIBILITY_HINTS: Record<EligibilityResult, string> = {
  eligible: "Podem acreditar que complim?",
  review_required: "Hi ha punts que ha de mirar una persona.",
  no_go: "Hi ha un impediment acreditat.",
  undetermined: "Encara no s'ha avaluat cap requisit del plec.",
};

/** El resultat de l'anàlisi del motor (A14): mai una «recomanació» ni un GO/NO-GO. */
export const RECOMMENDATION_LABELS: Record<RecommendationResult, string> = {
  go_candidate: "Candidata",
  eligible_low_fit: "Elegible però poc encaix",
  review: "Revisió",
  no_go: "Bloqueig confirmat",
  undetermined: "Sense resultat",
};

/** Que hi ha darrere d'una analisi, dit sense embuts. La diferencia entre
 *  aquestes tres coses es la diferencia entre una afirmacio sobre el plec i una
 *  afirmacio sobre res. */
export const ANALYSIS_BASIS_LABELS: Record<AnalysisBasis, string> = {
  real: "Anàlisi del plec",
  simulation: "Simulació de desenvolupament",
  provider_not_configured: "Motor d'anàlisi no configurat",
};

export const ANALYSIS_BASIS_HINTS: Record<AnalysisBasis, string> = {
  real: "Extreta dels documents publicats per l'òrgan de contractació.",
  simulation:
    "Contingut inventat per provar el flux. No s'ha llegit cap plec: no serveix per decidir.",
  provider_not_configured:
    "No hi ha cap motor d'anàlisi configurat. Res del que es mostra surt de llegir el plec.",
};

/** Nomes una analisi real sosté una afirmacio sobre el plec. */
export function isTrustworthyAnalysis(basis: AnalysisBasis | null | undefined): boolean {
  return basis === "real";
}

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

/** Un valor estructurat, en text. Sense fallback a JSON: el JSON va al `title`. */
export function valueLine(value: Record<string, unknown> | null): { text: string; raw: string | null } {
  if (!value) return { text: "—", raw: null };
  const parts: string[] = [];
  if (typeof value.amount === "number") {
    parts.push(`${value.amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  }
  if (typeof value.year === "number") parts.push(`(${value.year})`);
  if (typeof value.name === "string") parts.push(value.name);
  if (typeof value.coverage_amount === "number") {
    parts.push(`${value.coverage_amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  }
  if (typeof value.accredited_projects === "number") {
    parts.push(`${value.accredited_projects} de ${value.required_projects} projectes`);
  }
  if (typeof value.minimum_projects === "number" && parts.length === 0) {
    parts.push(`${value.minimum_projects} projectes`);
  }
  if (typeof value.holders === "number") parts.push(`${value.holders} persona(es)`);
  if (parts.length > 0) return { text: parts.join(" "), raw: null };
  return { text: "Valor no comparable automàticament", raw: JSON.stringify(value) };
}

const ENUM_TOKEN = /^[a-z]+(?:_[a-z]+)+$/;

/**
 * Humanitza l'etiqueta d'una evidència del compliment. El backend la construeix
 * amb codis (`civil_liability: unknown 1000000.00 EUR`); el text de negoci no
 * pot ensenyar-los. Els codis coneguts es tradueixen amb els diccionaris; un
 * codi desconegut es diu com a tal i queda al `raw` (per al `title`).
 */
export function evidenceLabel(
  label: string,
  dictionaries: { types: Record<string, string>; kinds: Record<string, string> },
): { text: string; raw: string | null } {
  let changed = false;
  const words = label.split(/(\s+)/).map((token) => {
    const bare = token.replace(/:$/, "");
    const suffix = token.endsWith(":") ? ":" : "";
    const isCode = ENUM_TOKEN.test(bare) || bare in dictionaries.kinds || bare in dictionaries.types;
    if (!isCode) return token;
    changed = true;
    const known = dictionaries.types[bare] ?? dictionaries.kinds[bare];
    return (known ?? "valor sense etiqueta") + suffix;
  });
  const text = words.join("").replace(/(\d+)\.(\d{2})\s+([A-Z]{3})/g, (_match, whole: string, cents: string, currency: string) => {
    const amount = Number(`${whole}.${cents}`);
    return Number.isNaN(amount)
      ? `${whole}.${cents} ${currency}`
      : new Intl.NumberFormat("ca-ES", { style: "currency", currency, maximumFractionDigits: 0 }).format(amount);
  });
  return { text, raw: changed ? label : null };
}
