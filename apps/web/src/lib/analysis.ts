/**
 * L'anàlisi determinista d'un plec (motor 0051/0052), per a la pestanya «Anàlisi».
 *
 * - Els textos d'explicació són els CANÒNICS del backend (`EXPLANATIONS`), tal com són.
 * - Els valors d'enum els rotula el frontend (`LABELS`); la cobertura es prova contra el
 *   JSON generat des del registre (`analysis-vocabulary.generated.json`).
 * - Cap score, percentatge ni recomanació: només els comptadors que dona el motor.
 */

import type { Level } from "@/components/ui";

import vocabulary from "./analysis-vocabulary.generated.json";

export type RenderedGroup = keyof typeof vocabulary.rendered;

export interface FitAssessment {
  id: string;
  requirement_id: string;
  fit_result: string;
  knowledge_source: string;
  proof_status: string;
  territory_verdict: string;
  verification_status: string;
  rule_code: string;
  explanation_code: string;
  support_json: Record<string, unknown> | null;
}

export interface CriterionAssessment {
  id: string;
  award_criterion_id: string;
  criterion_result: string;
  knowledge_source: string;
  proof_status: string;
  points_status: string;
  max_points: number;
  secured_points: number;
  at_risk_points: number;
  lost_points: number;
  not_assessable_points: number;
  rule_code: string;
  explanation_code: string;
  support_json: Record<string, unknown> | null;
}

export interface DeliveryCandidate {
  id: string;
  scope_item_id: string;
  company_service_id: string | null;
  partner_role_id: string | null;
  candidate_type: string;
  origin: string;
  readiness: string;
  territory_verdict: string;
  area_verdicts_json: unknown[] | null;
  rule_code: string;
  explanation_code: string;
}

export interface Finding {
  id: string;
  finding_type: string;
  rule_code: string;
  explanation_code: string;
  requirement_id: string | null;
  criterion_id: string | null;
  scope_item_id: string | null;
  details_json: Record<string, unknown> | null;
}

export interface AnalysisQuestion {
  id: string;
  audience: "buyer" | "company";
  question_code: string;
  rule_code: string;
  requirement_id: string | null;
  criterion_id: string | null;
  scope_item_id: string | null;
  params_json: Record<string, unknown> | null;
  /** Ja redactada al backend (`question_text`): es mostra tal com arriba. */
  text: string;
}

export interface AnalysisSummary {
  fit: { satisfies: number; does_not_satisfy: number; unknown: number; not_applicable: number };
  blocker_potential_count: number;
  review_required_count: number;
  secured_points: number;
  at_risk_points: number;
  lost_points: number;
  not_assessable_points: number;
  max_points: number;
  delivery: { available: number; pending: number; unavailable: number };
  questions: { buyer: number; company: number };
}

export interface AnalysisRun {
  id: string;
  tender_id: string;
  rules_version: string;
  is_current: boolean;
  created_at: string;
  stale: boolean | null;
  summary: Partial<AnalysisSummary>;
  fit_assessments: FitAssessment[];
  need_assessments: unknown[];
  criterion_assessments: CriterionAssessment[];
  delivery_candidates: DeliveryCandidate[];
  findings: Finding[];
  questions: AnalysisQuestion[];
}

export type AnalysisWriteResult = AnalysisRun & { reused: boolean };

export interface ScopeItemView {
  id: string;
  service_line_code: string;
  block_id: string | null;
  site_id: string | null;
}

export interface TenderStructureView {
  blocks: Array<{ id: string; code: string; name: string }>;
  sites: Array<{ id: string; code: string | null; name: string | null }>;
  scope_items: ScopeItemView[];
}

export function analysisPath(companyId: string, tenderId: string): string {
  return `/companies/${companyId}/tenders/${tenderId}/analysis`;
}

/* ---------------------------------------------------------------- etiquetes */

export const LABELS: Record<RenderedGroup, Record<string, string>> = {
  fit_result: { satisfies: "Compleix", does_not_satisfy: "No compleix", unknown: "No se sap", not_applicable: "No aplica" },
  proof_status: { sufficient: "Prova suficient", partial: "Prova parcial", missing: "Falta la prova", unknown: "Prova: no se sap", not_required: "No cal prova" },
  analysis_knowledge_source: {
    current_verified: "Coneixement actual de l'empresa",
    current_declared: "Coneixement actual de l'empresa",
    historical: "Només històric",
    none: "Cap dada de l'empresa",
    mixed: "Fonts mixtes",
    not_applicable: "Font: no aplica",
  },
  assessment_verification: { verified: "Verificat", not_verified: "No verificat", unknown: "Verificació: no se sap", not_applicable: "Verificació: no aplica" },
  territory_verdict: { matched: "Territori coincident", unmatched: "Territori no coincident", unknown: "Territori: no se sap", not_applicable: "Sense territori" },
  criterion_result: { achievable: "Assolible", not_achievable: "No assolible", needs_review: "Cal revisar", not_assessable: "No avaluable" },
  points_status: { secured: "Assegurats", at_risk: "En risc", lost: "Perduts", mixed: "Mixtos", not_assessable: "No avaluables" },
  candidate_type: { own_current: "Propi", partner_current: "Partner", subcontract_unknown: "Subcontractació: no se sap", own_future: "Propi (hipòtesi)" },
  candidate_origin: { current_knowledge: "Coneixement actual", hypothesis: "Hipòtesi" },
  candidate_readiness: {
    available: "Disponible",
    pending_coverage: "Pendent de cobertura",
    pending_partner: "Pendent de partner",
    pending_validation: "Pendent de validació",
    unavailable: "No disponible",
  },
  finding_type: {
    blocker_potential: "Possible bloqueig (no confirmat)",
    review_required: "Cal revisar",
    evidence_gap: "Falta evidència",
    missing_information: "Falta informació de l'empresa",
    territory_gap: "Mancança de territori",
    delivery_gap: "Mancança d'execució",
    warning: "Avís",
  },
};

export const LEVELS: Partial<Record<RenderedGroup, Record<string, Level>>> = {
  fit_result: { satisfies: "ready", does_not_satisfy: "blocking", unknown: "neutral", not_applicable: "neutral" },
  points_status: { secured: "ready", at_risk: "attention", lost: "blocking", mixed: "attention", not_assessable: "neutral" },
  candidate_readiness: { available: "ready", pending_coverage: "attention", pending_partner: "attention", pending_validation: "attention", unavailable: "neutral" },
  candidate_type: { own_current: "ready", partner_current: "ready", subcontract_unknown: "neutral", own_future: "neutral" },
  finding_type: { blocker_potential: "attention", review_required: "attention", evidence_gap: "attention", missing_information: "neutral", territory_gap: "neutral", delivery_gap: "neutral", warning: "neutral" },
};

export function label(group: RenderedGroup, value: string): string {
  return LABELS[group]?.[value] ?? "Valor no disponible";
}

export type DimensionGroup = "proof_status" | "analysis_knowledge_source" | "assessment_verification" | "territory_verdict";

/**
 * Valors curts (spec §5.5.3) per a les quatre dimensions de Requisits, que ja porten la seva
 * etiqueta visible al davant («Prova: suficient»). NOMÉS per a Requisits: la resta de
 * superfícies fan servir `LABELS`, que es llegeixen sols.
 */
export const DIMENSION_VALUES: Record<DimensionGroup, Record<string, string>> = {
  proof_status: { sufficient: "suficient", partial: "parcial", missing: "falta", unknown: "no se sap", not_required: "no cal" },
  analysis_knowledge_source: {
    current_verified: "coneixement actual de l'empresa",
    current_declared: "coneixement actual de l'empresa",
    historical: "només històric",
    none: "cap dada de l'empresa",
    mixed: "fonts mixtes",
    not_applicable: "no aplica",
  },
  assessment_verification: { verified: "verificat", not_verified: "no verificat", unknown: "no se sap", not_applicable: "no aplica" },
  territory_verdict: { matched: "coincideix", unmatched: "no coincideix", unknown: "no se sap", not_applicable: "no aplica" },
};

export function dimensionValue(group: DimensionGroup, value: string): string {
  return DIMENSION_VALUES[group][value] ?? "valor no disponible";
}

export function levelOf(group: RenderedGroup, value: string): Level {
  return LEVELS[group]?.[value] ?? "neutral";
}

/** El text canònic del backend, sense reescriure'l. Un codi desconegut: text neutre. */
export function explanation(code: string): string {
  return (vocabulary.explanations as Record<string, string>)[code] ?? "Explicació no disponible.";
}

/* ---------------------------------------------------------- on es resol */

export interface ResolveDestination {
  href: string;
  label: string;
}

const Q = (seccio: string, label: string): ResolveDestination => ({
  href: `/empresa/coneixement?tab=qualificacio&seccio=${seccio}`,
  label: `Coneixement › Qualificació › ${label}`,
});
const CURRENT: ResolveDestination = { href: "/empresa/coneixement?tab=actual", label: "Coneixement › Coneixement actual" };
const PENDING: ResolveDestination = { href: "/empresa/coneixement?tab=pendents", label: "Coneixement › Pendents de validar" };
const QUESTIONS: ResolveDestination = { href: "#preguntes", label: "Preguntes generades" };

/** Només codis amb un destí inequívoc. La resta no té botó. */
export const RESOLVE: Record<string, ResolveDestination> = {
  FACT_UNKNOWN: CURRENT,
  FACT_HISTORIC_ONLY: CURRENT,
  FACT_CONTRADICTS: CURRENT,
  FACT_TRUE_TERRITORY_UNMATCHED: CURRENT,
  CERT_UNKNOWN: CURRENT,
  CERT_HISTORIC_ONLY: CURRENT,
  CERT_LEVEL_BELOW: CURRENT,
  CAPABILITY_UNKNOWN: CURRENT,
  CAPABILITY_HISTORIC_ONLY: CURRENT,
  CAPABILITY_NOT_SUPPORTED: CURRENT,
  CAPABILITY_BELOW_REQUIRED: CURRENT,
  NEEDS_UNKNOWN: CURRENT,
  NEEDS_CONTRADICTED: CURRENT,
  DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES: CURRENT,
  DELIVERY_NO_SERVICE: CURRENT,
  DELIVERY_MODE_UNKNOWN: CURRENT,
  DELIVERY_NO_PARTNER_ROLE: CURRENT,
  NO_AVAILABLE_DELIVERY: CURRENT,
  TERRITORY_UNKNOWN: CURRENT,
  TERRITORY_UNMATCHED: CURRENT,
  FACT_TRUE_DECLARED: PENDING,
  CERT_CURRENT_DECLARED: PENDING,
  DECLARED_NOT_VERIFIED: PENDING,
  TURNOVER_UNKNOWN: Q("solvencia", "Solvència econòmica"),
  TURNOVER_BELOW: Q("solvencia", "Solvència econòmica"),
  EXECUTION_UNKNOWN: Q("experiencia", "Experiència"),
  EXECUTION_BELOW: Q("experiencia", "Experiència"),
  INSURANCE_UNKNOWN: Q("assegurances", "Assegurances"),
  INSURANCE_BELOW: Q("assegurances", "Assegurances"),
  INSURANCE_EXPIRED: Q("assegurances", "Assegurances"),
  REGISTRATION_UNKNOWN: Q("registres", "Registres"),
  REGISTRATION_SCOPE_UNKNOWN: Q("registres", "Registres"),
  REGISTRATION_SCOPE_NOT_COVERED: Q("registres", "Registres"),
  REGISTRATION_ONLY_OPERATOR_FACT: Q("registres", "Registres"),
  PROFILES_MISSING: Q("persones", "Persones"),
  EQUIPMENT_MISSING: Q("equips", "Equips"),
  EQUIPMENT_CALIBRATION_UNKNOWN: Q("equips", "Equips"),
  EQUIPMENT_CALIBRATION_EXPIRED: Q("equips", "Equips"),
  FACT_NUMBER_BELOW: Q("recursos", "Recursos quantitatius"),
  TERRITORY_LIST_UNAVAILABLE: QUESTIONS,
  CRITERION_TIMING_NOT_CONCLUSIVE: QUESTIONS,
};

export function resolveFor(code: string): ResolveDestination | null {
  return RESOLVE[code] ?? null;
}

/* ------------------------------------------------------- ordre i resum */

export const FINDING_ORDER = [
  "blocker_potential",
  "review_required",
  "evidence_gap",
  "missing_information",
  "territory_gap",
  "delivery_gap",
  "warning",
];

export function sortFindings(findings: Finding[]): Finding[] {
  const rank = (type: string) => {
    const index = FINDING_ORDER.indexOf(type);
    return index === -1 ? FINDING_ORDER.length : index;
  };
  return [...findings].sort((a, b) => rank(a.finding_type) - rank(b.finding_type));
}

const n = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/** El resum de l'API amb zeros on falti una clau (anàlisis antigues). Cap càlcul nou. */
export function summaryOf(run: Pick<AnalysisRun, "summary">): AnalysisSummary {
  const s = (run.summary ?? {}) as Partial<AnalysisSummary>;
  return {
    fit: { satisfies: n(s.fit?.satisfies), does_not_satisfy: n(s.fit?.does_not_satisfy), unknown: n(s.fit?.unknown), not_applicable: n(s.fit?.not_applicable) },
    blocker_potential_count: n(s.blocker_potential_count),
    review_required_count: n(s.review_required_count),
    secured_points: n(s.secured_points),
    at_risk_points: n(s.at_risk_points),
    lost_points: n(s.lost_points),
    not_assessable_points: n(s.not_assessable_points),
    max_points: n(s.max_points),
    delivery: { available: n(s.delivery?.available), pending: n(s.delivery?.pending), unavailable: n(s.delivery?.unavailable) },
    questions: { buyer: n(s.questions?.buyer), company: n(s.questions?.company) },
  };
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** L'únic formatador de punts: la pestanya, el resum i la targeta de Decidir diuen el mateix número. */
export function formatPoints(value: number): string {
  return value.toLocaleString("ca-ES", { maximumFractionDigits: 2 });
}

/** Frases derivades NOMÉS dels comptadors existents. */
export function summarySentences(summary: AnalysisSummary): string[] {
  const out: string[] = [];
  if (summary.blocker_potential_count > 0) {
    out.push(`${plural(summary.blocker_potential_count, "possible bloqueig", "possibles bloqueigs")} a revisar: cap no està confirmat.`);
  }
  if (summary.review_required_count > 0) {
    out.push(`${plural(summary.review_required_count, "element marcat", "elements marcats")} com a «${label("finding_type", "review_required")}».`);
  }
  if (summary.fit.does_not_satisfy > 0) {
    out.push(`${plural(summary.fit.does_not_satisfy, "requisit consta", "requisits consten")} com a «No compleix» en l'anàlisi actual.`);
  }
  if (summary.fit.unknown > 0) {
    out.push(
      `${plural(summary.fit.unknown, "requisit no es pot", "requisits no es poden")} avaluar amb la informació actual (no se sap): no vol dir que no es compleixin.`,
    );
  }
  if (summary.not_assessable_points > 0) {
    out.push(`${formatPoints(summary.not_assessable_points)} punts no es poden avaluar en aquest pas.`);
  }
  return out;
}

/** Codis de `details.missing` del 409 `analysis_prerequisites_missing`. */
export const MISSING_TEXT: Record<string, string> = {
  requirements: "Encara no hi ha requisits per analitzar: processa els plecs i analitza'n els requisits, o afegeix-ne de manuals.",
};

/** On es resol cada codi de `details.missing`. `tab` és la clau que llegeix la pàgina (`tabFromParam`). */
export const MISSING_DESTINATION: Record<string, (tenderId: string) => ResolveDestination> = {
  requirements: (tenderId) => ({ href: `/licitacions/${tenderId}?tab=requisits`, label: "Obre la pestanya Requisits extrets" }),
};
