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

/** Una cita verificada del plec. */
export interface RequirementCitation {
  evidence_id: string;
  tender_document_id: string | null;
  extraction_id: string | null;
  extraction_revision: number | null;
  page_number: number | null;
  quote: string;
  document_name: string | null;
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
  /** Totes les cites del requisit (la primera és la de dalt). */
  citations?: RequirementCitation[];
}

/** D'on surt un camp del valor comparat: el valor extret o una cita verificada. */
export interface ComparedProvenance {
  field: string;
  source: "value_json" | "quote" | "title" | "description" | string;
  raw: string;
  evidence_id?: string;
}

/** A quina exigència pertany una avaluació quan el plec la repeteix (duplicats). */
export interface AssessmentConsolidation {
  canonical_requirement_id: string;
  is_canonical: boolean;
  member_requirement_ids: string[];
  divergent: boolean;
}

/** Una exigència amb duplicats, resolta com el recompte del càlcul (el més prudent). */
export interface AssessmentGroup {
  canonical_assessment_id: string;
  canonical_requirement_id: string;
  member_assessment_ids: string[];
  member_requirement_ids: string[];
  effective_result: AssessmentResult;
  blocker_status: BlockerStatus;
  divergent: boolean;
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
  consolidation?: AssessmentConsolidation | null;
  /** El valor que la regla ha COMPARAT (complement product-v2), amb procedència. El cru és `requirement_value_json`. */
  compared_value?: Record<string, unknown> | null;
  compared_provenance?: ComparedProvenance[];
  normalization_status?: string | null;
  normalization_reason?: string | null;
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
  /** Una entrada per exigència amb duplicats. Els requisits sols no hi són. */
  groups?: AssessmentGroup[];
  is_stale: boolean;
}

/** El que la llista pinta: una fila per EXIGÈNCIA. */
export interface AssessmentDisplay {
  /** L'avaluació del requisit canònic (o la del requisit sol). */
  assessment: Assessment;
  /** Les avaluacions dels altres requisits que diuen la mateixa exigència. */
  members: Assessment[];
  effectiveResult: AssessmentResult;
  blockerStatus: BlockerStatus;
  /** Els membres no diuen el mateix: mana el més prudent. */
  divergent: boolean;
  /** L'avaluació que dona el resultat de la fila (la més prudent del grup): la seva
   *  explicació i el seu valor de l'empresa són els que quadren amb la pastilla. */
  deciding: Assessment;
}

const SEVERITY: Record<AssessmentResult, number> = {
  does_not_satisfy: 4,
  needs_review: 3,
  unknown: 2,
  satisfies: 1,
  not_applicable: 0,
};

/**
 * Una fila per exigència. Els duplicats d'un grup no es perden: viatgen com a
 * `members`, amb les seves cites, i el resultat de la fila és el del grup (el més
 * prudent, com el recompte del càlcul). L'ordre és el de l'API.
 */
export function consolidateAssessments(
  assessments: Assessment[],
  groups: AssessmentGroup[] = [],
): AssessmentDisplay[] {
  const byId = new Map(assessments.map((item) => [item.id, item]));
  const groupOf = new Map<string, AssessmentGroup>();
  for (const group of groups) for (const id of group.member_assessment_ids) groupOf.set(id, group);
  const done = new Set<string>();
  const rows: AssessmentDisplay[] = [];
  for (const item of assessments) {
    if (done.has(item.id)) continue;
    const group = groupOf.get(item.id);
    if (!group) {
      done.add(item.id);
      rows.push({ assessment: item, members: [], effectiveResult: item.effective_result, blockerStatus: item.blocker_status, divergent: false, deciding: item });
      continue;
    }
    const canonical = byId.get(group.canonical_assessment_id) ?? item;
    const members = group.member_assessment_ids
      .filter((id) => id !== canonical.id)
      .map((id) => byId.get(id))
      .filter((member): member is Assessment => Boolean(member));
    for (const id of group.member_assessment_ids) done.add(id);
    const all = [canonical, ...members];
    const deciding =
      canonical.effective_result === group.effective_result
        ? canonical
        : (all.find((member) => member.effective_result === group.effective_result) ??
          all.reduce((worst, member) => (SEVERITY[member.effective_result] > SEVERITY[worst.effective_result] ? member : worst)));
    rows.push({ assessment: canonical, members, effectiveResult: group.effective_result, blockerStatus: group.blocker_status, divergent: group.divergent, deciding });
  }
  return rows;
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
  CERTIFICATION_LEVEL_NOT_STATED:
    "No consta la categoria de l'ENS (al plec o a la certificació de l'empresa): no se'n suposa cap. Cal revisar-ho.",
  CERTIFICATION_LEVEL_BELOW: "La categoria de l'ENS de l'empresa és inferior a la que exigeix el plec.",
  CERTIFICATION_LEVEL_HIGHER_REVIEW:
    "L'empresa té l'ENS en una categoria superior a la demanada i el plec no diu «o superior»: cal revisar-ho.",
  CERTIFICATION_LEVEL_HIGHER_MET:
    "L'empresa té l'ENS en una categoria superior a la que exigeix el plec: la compleix.",
  CERTIFICATION_NOT_CURRENT:
    "La certificació consta al perfil, però en tràmit o retirada: avui no acredita res.",
  CERTIFICATION_EQUIVALENT_NOT_DECLARED:
    "El plec admet equivalents i el perfil no té aquesta norma. Bloqueig potencial: confirma'l o desfés-lo si una altra certificació hi equival.",
  CERTIFICATION_ALL_FOUND: "Totes les normes que demana el plec consten al perfil.",
  CERTIFICATION_SOME_MISSING: "Falta alguna de les normes que demana el plec.",
  CERTIFICATION_EQUIVALENCE_REVIEW:
    "El plec admet equivalents: quina certificació hi equival ho ha de decidir una persona.",
  REGISTRATION_FOUND_VALID: "El registre consta al perfil i és vigent.",
  REGISTRATION_FOUND_EXPIRED: "El registre caduca abans del termini.",
  REGISTRATION_NOT_FOUND_PROFILE_COMPLETE:
    "El perfil de registres és complet i no conté aquest registre.",
  REGISTRATION_DATA_INCOMPLETE: "El perfil de registres és incomplet.",
  REGISTRATION_CLASSIFICATION_REVIEW:
    "La classificació empresarial (grup, subgrup, categoria) s'ha de revisar a mà.",
  REGISTRATION_DOCUMENTATION_ONLY:
    "Sembla documentació per a qui ja és inscrit al RELI/ROLECE; comprova al plec si cal estar-hi inscrit.",
  REGISTRATION_DOCUMENTATION_AVAILABLE:
    "L'empresa és inscrita al RELI o al ROLECE: en pot aportar el certificat d'inscripció.",
  REGISTRATION_DOCUMENTATION_NOT_APPLICABLE:
    "La clàusula parla de qui ja és inscrit al RELI/ROLECE i l'empresa no ho és: no li aplica.",
  REGISTRATION_NOT_CURRENT:
    "El registre consta al perfil, però en tràmit o retirat: avui no acredita res.",
  CLASSIFICATION_MET: "La classificació de l'empresa és del mateix grup i subgrup i d'una categoria igual o superior.",
  CLASSIFICATION_CATEGORY_BELOW:
    "L'empresa té el grup i el subgrup, però en una categoria inferior a l'exigida.",
  CLASSIFICATION_CATEGORY_AMBIGUOUS:
    "El plec dona una categoria antiga que equival a dues d'actuals (RD 773/2015) i la de l'empresa és entre totes dues: cal revisar-ho.",
  CLASSIFICATION_NOT_HELD: "L'empresa no té cap classificació en aquest grup i subgrup.",
  CLASSIFICATION_NOT_STATED:
    "El plec no diu quin grup i subgrup de classificació demana: cal revisar-ho.",
  CLASSIFICATION_AMBIGUOUS:
    "El plec dona diverses classificacions (per lots o per import) i no se sap quina aplica: cal revisar-ho.",
  CLASSIFICATION_COMPANY_UNREADABLE:
    "Hi ha una classificació al perfil que no es pot llegir (grup, subgrup o categoria): cal revisar-la.",
  CLASSIFICATION_SUBSTITUTES_SOLVENCY:
    "Contracte de serveis: la classificació acreditada substitueix aquesta solvència (art. 77.1.b LCSP).",
  CLASSIFICATION_OPTIONAL_IN_SERVICES:
    "Contracte de serveis: la classificació no és exigible. Si no s'acredita, la solvència s'acredita pels altres mitjans i no bloqueja.",
  ALTERNATIVE_GROUP_SATISFIED: "Una altra via de la mateixa exigència ja l'acredita.",
  ALTERNATIVE_GROUP_NOT_SATISFIED:
    "Cap de les vies alternatives no acredita l'exigència: n'hi ha un sol bloqueig per a tot el grup.",
  ALTERNATIVE_GROUP_INCOMPLETE:
    "Alguna via alternativa no s'ha pogut resoldre, o no consta que hi siguin totes: cal revisar-ho.",
  ECONOMIC_PERIOD_INCOMPLETE: "Falten exercicis del període que demana el plec per poder-ho afirmar.",
  PERCENTAGE_BASE_UNAVAILABLE:
    "El llindar és un percentatge d'una base que no consta a l'expedient: cal revisar-ho.",
  INSURANCE_LIMITS_MET: "La pòlissa cobreix cadascun dels límits exigits.",
  INSURANCE_LIMIT_BELOW: "Un dels límits de la pòlissa és inferior al que exigeix el plec.",
  INSURANCE_LIMIT_KIND_AMBIGUOUS: "El plec parla d'un sublímit que no se sap a quin límit correspon: cal revisar-ho.",
  INSURANCE_CONDITION_NOT_CHECKED:
    "Els imports encaixen, però el plec imposa una condició que no es comprova automàticament (p. ex. mantenir la pòlissa durant tota l'execució).",
  INSURANCE_ADDITIONAL_INSURED_UNKNOWN: "El plec exigeix l'òrgan com a assegurat addicional i la pòlissa no ho declara.",
  INSURANCE_LIMIT_UNKNOWN:
    "La pòlissa no declara un dels límits que exigeix el plec (per sinistre, anual, per víctima…).",
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
  REQUIREMENT_NOT_REQUIRED: "El plec diu que no s'exigeix: no hi ha res a acreditar.",
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

const LIMIT_KIND_LABELS: Record<string, string> = {
  total: "cobertura total",
  per_claim: "per sinistre",
  per_victim: "per víctima",
  annual_aggregate: "agregat anual",
  sublimit: "sublímit",
  employer_per_victim: "RC patronal per víctima",
  unknown: "sense especificar",
};

function toNumber(raw: unknown): number | null {
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw === "string" && /^-?\d+(?:\.\d+)?$/.test(raw.trim())) return Number(raw);
  return null;
}

/** «91.904,74 €». Només per a imports que ja són un nombre (o el text d'un `Decimal`). */
export function formatAmount(raw: unknown, currency: unknown = "EUR"): string | null {
  const amount = toNumber(raw);
  if (amount === null) return null;
  const code = typeof currency === "string" && /^[A-Z]{3}$/.test(currency) ? currency : "EUR";
  return new Intl.NumberFormat("ca-ES", { style: "currency", currency: code, maximumFractionDigits: 2 }).format(amount);
}

function limitsLine(limits: unknown): string | null {
  if (!Array.isArray(limits) || limits.length === 0) return null;
  const parts = limits
    .map((limit) => {
      if (!limit || typeof limit !== "object") return null;
      const item = limit as Record<string, unknown>;
      const amount = formatAmount(item.amount, item.currency);
      if (!amount) return null;
      return `${amount} ${LIMIT_KIND_LABELS[String(item.kind)] ?? "límit sense etiqueta"}`;
    })
    .filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(" · ") : null;
}

/** Un valor estructurat, en text. Sense fallback a JSON: el JSON va al `title`. */
export function valueLine(value: Record<string, unknown> | null): { text: string; raw: string | null } {
  if (!value) return { text: "—", raw: null };
  const parts: string[] = [];
  if (typeof value.amount === "number") {
    parts.push(`${value.amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  } else if (typeof value.amount === "string" && value.amount.trim()) {
    // El text tal com el va escriure l'extractor (p. ex. «150.000 €»).
    parts.push(value.amount.trim());
  }
  if (typeof value.year === "number") parts.push(`(${value.year})`);
  if (typeof value.name === "string") parts.push(value.name);
  if (typeof value.coverage_amount === "number") {
    parts.push(`${value.coverage_amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  }
  const limits = limitsLine(value.limits);
  if (limits) parts.push(limits);
  if (typeof value.accumulated === "number") {
    parts.push(`${formatAmount(value.accumulated, value.currency) ?? value.accumulated} acumulats`);
  }
  if (typeof value.accredited_projects === "number") {
    parts.push(`${value.accredited_projects} de ${value.required_projects} projectes`);
  }
  if (typeof value.minimum_projects === "number" && parts.length === 0) {
    parts.push(`${value.minimum_projects} projectes`);
  }
  if (typeof value.holders === "number") parts.push(`${value.holders} persona(es)`);
  if (typeof value.unit === "string" && parts.length > 0) parts.push(value.unit);
  if (parts.length > 0) return { text: parts.join(" "), raw: null };
  return { text: "Valor no comparable automàticament", raw: JSON.stringify(value) };
}

const ENS_LEVEL_LABELS: Record<string, string> = { basica: "bàsica", mitjana: "mitjana", alta: "alta" };

function yearsPhrase(period: unknown, aggregation: unknown, noun: string): string | null {
  const years = period && typeof period === "object" ? (period as Record<string, unknown>).years : null;
  if (typeof years !== "number") return null;
  if (aggregation === "max") return `millor ${noun} dels ${years} últims`;
  if (aggregation === "each_year") return `cada un dels ${years} últims ${noun === "any" ? "anys" : "exercicis"}`;
  return `darrers ${years} ${noun === "any" ? "anys" : "exercicis"}`;
}

/**
 * El valor que la regla ha COMPARAT (el normalitzat del complement product-v2), en
 * text: «≥ 91.904,74 € · millor exercici dels 3 últims». `null` si no n'hi ha o si
 * la forma no es coneix (llavors es mostra el cru).
 */
export function comparedLine(value: Record<string, unknown> | null | undefined): string | null {
  if (!value) return null;
  const parts: string[] = [];
  if (value.kind === "economic_solvency") {
    const threshold = (value.threshold ?? {}) as Record<string, unknown>;
    const amount = formatAmount(threshold.amount, threshold.currency);
    if (amount) parts.push(`≥ ${amount}`);
    else if (threshold.percentage) parts.push(`${threshold.percentage} % d'una base del contracte`);
    const years = yearsPhrase(value.reference_period, value.aggregation, "exercici");
    if (years) parts.push(years);
  } else if (value.kind === "insurance") {
    const limits = limitsLine(value.limits);
    if (limits) parts.push(limits);
  } else if (value.kind === "experience") {
    const accumulated = value.min_accumulated_amount as Record<string, unknown> | null;
    const individual = value.min_individual_amount as Record<string, unknown> | null;
    if (accumulated) {
      const amount = formatAmount(accumulated.amount, accumulated.currency);
      if (amount) parts.push(value.aggregation === "max" ? `≥ ${amount} acumulats l'any de major execució` : `≥ ${amount} acumulats`);
    }
    if (individual) {
      const amount = formatAmount(individual.amount, individual.currency);
      if (amount) parts.push(`≥ ${amount} per contracte`);
    }
    if (typeof value.min_count === "number") parts.push(`${value.min_count} contractes`);
    const years = yearsPhrase(value.reference_period, null, "any");
    if (years) parts.push(years);
  } else if (value.kind === "ens") {
    const level = typeof value.level === "string" ? ENS_LEVEL_LABELS[value.level] ?? value.level : null;
    parts.push(level ? `ENS categoria ${level}${value.or_higher ? " o superior" : ""}` : "ENS · el plec no indica la categoria");
  } else if (typeof value.minimum_people === "number") {
    parts.push(`${value.minimum_people} persones`);
  } else if (value.type === "sector_operator_registry") {
    parts.push("Registre d'operadors (CNMC)");
  }
  return parts.length ? parts.join(" · ") : null;
}

/** D'on surt el valor comparat: «de la cita, p. 17» o «del valor extret». */
export function provenanceLine(
  provenance: ComparedProvenance[] | undefined,
  citations: RequirementCitation[] | undefined,
): string | null {
  const main = (provenance ?? []).find((item) => /amount|limits|threshold|minimum_people|min_count|certification.level/.test(item.field)) ?? (provenance ?? [])[0];
  if (!main) return null;
  if (main.source === "quote") {
    const page = (citations ?? []).find((citation) => citation.evidence_id === main.evidence_id)?.page_number;
    return page ? `de la cita, p. ${page}` : "d'una cita verificada";
  }
  if (main.source === "value_json") return "del valor extret";
  return null;
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
