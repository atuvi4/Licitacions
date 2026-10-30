/**
 * Els tres sobres de la candidatura (A, B i C): el model mental amb què una empresa prepara una oferta.
 *
 * Tot el que hi ha aquí són **lectures** del que la interfície ja rep (checklist, compliment, solucions,
 * oferta i preparació final). Cap veredicte nou: si el servidor no diu que una cosa compleix, aquí tampoc.
 *
 * - **Sobre A** (documentació administrativa): solvència, certificacions, classificació, assegurances,
 *   personal i mitjans adscrits, i la resta de documentació administrativa.
 * - **Sobre B** (memòria tècnica, judici de valor): els requisits tècnics.
 * - **Sobre C** (proposta econòmica): cap categoria de requisit hi va. La proposta és l'oferta econòmica.
 * - **Execució i altres**: les condicions d'execució (obligacions si s'adjudica, que no van a cap sobre),
 *   els ítems manuals i el que no té categoria coneguda.
 */
import type { AssessmentDisplay, AssessmentResult, Compliance } from "@/lib/compliance";
import { consolidateAssessments } from "@/lib/compliance";
import type { EconomicOffer, Money } from "@/lib/economic";
import { activeLots, lotLimitVerdict, type LimitVerdict } from "@/lib/economic";
import {
  findingCopy,
  type FindingDestination,
  type FindingDomain,
  type ReadinessFinding,
} from "@/lib/readiness";
import type { TechnicalSolution } from "@/lib/technical";
import type { ChecklistItem, DossierCoverageCounters } from "@/lib/workspace";

export type EnvelopeKey = "A" | "B" | "C" | "execucio";

export const ENVELOPE_ORDER: readonly EnvelopeKey[] = ["A", "B", "C", "execucio"];

export const ENVELOPE_LABELS: Record<EnvelopeKey, { title: string; subtitle: string }> = {
  A: { title: "Sobre A", subtitle: "Documentació administrativa per presentar" },
  B: { title: "Sobre B", subtitle: "Proposta tècnica" },
  C: { title: "Sobre C", subtitle: "Proposta econòmica" },
  execucio: {
    title: "Condicions d'execució",
    subtitle: "Obligacions contractuals post-adjudicació; no van a cap sobre",
  },
};

/** Categories de requisit que s'acrediten al Sobre A. */
const ENVELOPE_A = new Set([
  "administrative_documentation",
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
  "deadline",
]);
/** Categories que es responen a la memòria tècnica. */
const ENVELOPE_B = new Set(["technical_requirement"]);

/** A quin sobre va un ítem de la checklist. Sense categoria coneguda, a «Execució i altres»: mai s'endevina. */
export function envelopeOf(item: Pick<ChecklistItem, "category">): EnvelopeKey {
  if (ENVELOPE_A.has(item.category)) return "A";
  if (ENVELOPE_B.has(item.category)) return "B";
  return "execucio";
}

export interface ChecklistFolder {
  key: EnvelopeKey;
  items: ChecklistItem[];
  total: number;
  done: number;
  mandatoryOpen: number;
  blocked: number;
}

/** Les quatre carpetes, sempre en el mateix ordre i encara que n'hi hagi de buides. L'ordre de dins és el de l'API. */
export function groupChecklist(items: ChecklistItem[]): ChecklistFolder[] {
  return ENVELOPE_ORDER.map((key) => {
    const own = items.filter((item) => envelopeOf(item) === key);
    return {
      key,
      items: own,
      total: own.length,
      done: own.filter((item) => item.status === "completed").length,
      mandatoryOpen: own.filter((item) => item.mandatory && item.status !== "completed").length,
      blocked: own.filter((item) => item.status === "blocked").length,
    };
  });
}

/** «4 de 5 a punt · 1 obligatori pendent». */
export function folderSummary(folder: ChecklistFolder): string {
  if (folder.total === 0) return "Cap ítem";
  const parts = [`${folder.done} de ${folder.total} a punt`];
  if (folder.mandatoryOpen > 0) {
    parts.push(`${folder.mandatoryOpen} ${folder.mandatoryOpen === 1 ? "obligatori pendent" : "obligatoris pendents"}`);
  }
  if (folder.blocked > 0) parts.push(`${folder.blocked} ${folder.blocked === 1 ? "bloquejat" : "bloquejats"}`);
  return parts.join(" · ");
}

/* ------------------------------------------------------------------------- */
/* Sobre A: solvència i requisits clau                                         */
/* ------------------------------------------------------------------------- */

export type TopicTone = "ready" | "attention" | "blocking" | "neutral";

export interface SolvencyTopic {
  key: string;
  label: string;
  /** Exigències (files consolidades) del tema. */
  total: number;
  satisfies: number;
  doesNotSatisfy: number;
  pending: number;
  notApplicable: number;
  /** El que el compliment diu en una frase curta. */
  text: string;
  tone: TopicTone;
  /** El que l'empresa té declarat i el motor ha llegit (p. ex. «ENS categoria Mitjana»), si el diu. */
  companyHas: string | null;
}

const TOPICS: Array<{ key: string; label: string; categories: string[] }> = [
  { key: "economic", label: "Solvència econòmica", categories: ["economic_solvency"] },
  { key: "technical", label: "Solvència tècnica i experiència", categories: ["technical_solvency", "experience"] },
  { key: "certification", label: "Certificacions (ENS i altres)", categories: ["certification"] },
  { key: "classification", label: "Classificació i registres", categories: ["business_classification", "registration"] },
  { key: "insurance", label: "Assegurances", categories: ["insurance"] },
];

const PENDING: AssessmentResult[] = ["needs_review", "unknown"];

function categoryOf(row: AssessmentDisplay): string {
  return row.assessment.requirement?.category ?? "";
}

function companyCertifications(rows: AssessmentDisplay[]): string | null {
  for (const row of rows) {
    const value = row.deciding.explanation_params?.company_certifications;
    if (Array.isArray(value) && value.length > 0) return value.map(String).join(", ");
  }
  return null;
}

function topicText(satisfies: number, doesNotSatisfy: number, pending: number, total: number): { text: string; tone: TopicTone } {
  if (total === 0) return { text: "El plec no en demana", tone: "neutral" };
  const parts: string[] = [];
  if (satisfies > 0) parts.push(satisfies === total ? "Compleix" : `${satisfies} compleix`);
  if (doesNotSatisfy > 0) parts.push(`${doesNotSatisfy} no compleix`);
  if (pending > 0) parts.push(`${pending} per revisar`);
  const text = parts.length > 0 ? parts.join(" · ") : "No aplica";
  const tone: TopicTone = doesNotSatisfy > 0 ? "blocking" : pending > 0 ? "attention" : satisfies > 0 ? "ready" : "neutral";
  return { text, tone };
}

/** Els temes clau del Sobre A, a partir del compliment vigent (una fila per exigència, com la llista). */
export function solvencyTopics(compliance: Compliance | null): SolvencyTopic[] {
  if (!compliance?.run) return [];
  const rows = consolidateAssessments(compliance.assessments, compliance.groups ?? []).filter(
    (row) => row.assessment.obligation === "mandatory",
  );
  return TOPICS.map((topic) => {
    const own = rows.filter((row) => topic.categories.includes(categoryOf(row)));
    const satisfies = own.filter((row) => row.effectiveResult === "satisfies").length;
    const doesNotSatisfy = own.filter((row) => row.effectiveResult === "does_not_satisfy").length;
    const pending = own.filter((row) => PENDING.includes(row.effectiveResult)).length;
    const notApplicable = own.filter((row) => row.effectiveResult === "not_applicable").length;
    const { text, tone } = topicText(satisfies, doesNotSatisfy, pending, own.length);
    return {
      key: topic.key,
      label: topic.label,
      total: own.length,
      satisfies,
      doesNotSatisfy,
      pending,
      notApplicable,
      text,
      tone,
      companyHas: topic.key === "certification" ? companyCertifications(own) : null,
    };
  });
}

export interface DocumentsSummary {
  text: string;
  tone: TopicTone;
}

/** Els documents obligatoris del dossier: «12 de 140 coberts · 128 sense document». */
export function documentsSummary(coverage: DossierCoverageCounters | null): DocumentsSummary | null {
  if (!coverage || coverage.mandatory_total === null) return null;
  const parts = [`${coverage.covered} de ${coverage.mandatory_total} obligatoris coberts`];
  if (coverage.missing_document > 0) parts.push(`${coverage.missing_document} sense document`);
  if (coverage.needs_review > 0) parts.push(`${coverage.needs_review} per revisar`);
  if (coverage.expired_documents > 0) parts.push(`${coverage.expired_documents} caducats`);
  const open = coverage.missing_document + coverage.needs_review + coverage.expired_documents;
  return { text: parts.join(" · "), tone: open > 0 ? "attention" : "ready" };
}

/* ------------------------------------------------------------------------- */
/* Sobre B: memòria tècnica                                                   */
/* ------------------------------------------------------------------------- */

/** «[Cal confirmar: …]»: el que la persona que ha escrit l'esborrany ha deixat pendent de confirmar. */
const CONFIRM_MARKER = /\[\s*Cal confirmar\b[^\]]*\]/gi;
/** «(8 punts)» al títol d'una solució: els punts del criteri que respon. */
const POINTS = /\((\d+(?:[.,]\d+)?)\s*punts?\)/i;

export interface TechnicalSummary {
  total: number;
  drafted: number;
  validated: number;
  awaitingValidation: number;
  /** Validacions que ja no valen (el text o el requisit han canviat). */
  stale: number;
  /** Punts dels criteris que diuen els títols; `null` si cap títol no en diu. */
  points: number | null;
  confirmations: number;
}

export function technicalSummary(solutions: TechnicalSolution[] | null): TechnicalSummary {
  const list = solutions ?? [];
  let points: number | null = null;
  let confirmations = 0;
  for (const solution of list) {
    const match = POINTS.exec(solution.title);
    if (match) points = (points ?? 0) + Number(match[1].replace(",", "."));
    confirmations += (solution.proposed_solution ?? "").match(CONFIRM_MARKER)?.length ?? 0;
  }
  return {
    total: list.length,
    drafted: list.filter((solution) => (solution.proposed_solution ?? "").trim() !== "").length,
    validated: list.filter((solution) => solution.status === "validated" && !solution.validation_stale).length,
    awaitingValidation: list.filter((solution) => solution.status === "needs_validation").length,
    stale: list.filter((solution) => solution.status === "validated" && solution.validation_stale).length,
    points,
    confirmations,
  };
}

/** Els requisits de personal que el compliment no dona per bons: el que l'equip ha d'identificar o adscriure. */
export function personnelPending(compliance: Compliance | null, limit = 2): { total: number; titles: string[] } {
  if (!compliance?.run) return { total: 0, titles: [] };
  const rows = consolidateAssessments(compliance.assessments, compliance.groups ?? []).filter(
    (row) =>
      row.assessment.obligation === "mandatory" &&
      ["personnel", "professional_qualification"].includes(categoryOf(row)) &&
      row.effectiveResult !== "satisfies" &&
      row.effectiveResult !== "not_applicable",
  );
  return {
    total: rows.length,
    titles: rows.slice(0, limit).map((row) => row.assessment.requirement?.title ?? "Requisit de personal"),
  };
}

/* ------------------------------------------------------------------------- */
/* Sobre C: proposta econòmica                                                */
/* ------------------------------------------------------------------------- */

export interface OfferSummary {
  revenue: Money;
  /** Pressupost base sense IVA del lot, si n'hi ha un de sol i el plec el dona. */
  tenderMax: Money;
  /** Baixa sobre el pressupost base, en tant per cent amb dos decimals (text, com la resta d'imports). */
  discountPct: string | null;
  verdict: LimitVerdict | null;
  lines: number;
  incompleteLines: number;
  /** Marge de les línies de l'oferta (preus i quantitats que s'hi han escrit). `null` si no es pot veure. */
  margin: Money;
  marginPct: Money;
  hasNotes: boolean;
}

/**
 * Xifres clau de l'oferta. El marge és el que calcula el motor sobre les línies: si les línies són la cistella
 * de l'annex del plec, és el marge de la cistella, no el de la facturació real.
 */
export function offerSummary(offer: EconomicOffer | null): OfferSummary | null {
  if (!offer) return null;
  const lots = activeLots(offer);
  const single = lots.length === 1 ? lots[0] : null;
  const revenue = offer.totals.total_revenue;
  const tenderMax = single?.limit.tender_max ?? null;
  let discountPct: string | null = null;
  if (revenue !== null && tenderMax !== null && Number(tenderMax) > 0) {
    discountPct = (((Number(tenderMax) - Number(revenue)) / Number(tenderMax)) * 100).toFixed(2);
  }
  return {
    revenue,
    tenderMax,
    discountPct,
    verdict: single ? lotLimitVerdict(single) : null,
    lines: offer.totals.line_count,
    incompleteLines: offer.totals.incomplete_revenue_lines + offer.totals.incomplete_vat_lines,
    margin: offer.internal_economics?.margin_amount ?? null,
    marginPct: offer.internal_economics?.margin_on_sales_pct ?? null,
    hasNotes: Boolean(offer.notes && offer.notes.trim()),
  };
}

/* ------------------------------------------------------------------------- */
/* Què et falta per tancar l'oferta?                                          */
/* ------------------------------------------------------------------------- */

export interface NextAction {
  key: string;
  label: string;
  count: number;
  /**
   * Si `count` és un recompte de debò. Un avís agregat del dossier (`mandatory_unresolved`, sense entitat) arriba
   * una sola vegada encara que hi hagi cent requisits darrere: un «1» al costat diria una cosa falsa.
   */
  countable: boolean;
  hint?: string;
  action?: string;
  destination?: FindingDestination;
  domain: FindingDomain | "compliance" | "technical";
}

const DOMAIN_ORDER: Array<NextAction["domain"]> = ["dossier", "technical", "economic", "compliance", "workspace"];

/**
 * Els bloquejos de la preparació final, un per codi amb el seu recompte («9 solucions sense validar» i no nou
 * línies iguals), més el que el compliment diu que pot bloquejar. Els avisos i la informació no hi són: aquí
 * només hi ha el que impedeix tancar.
 */
export function nextActions(
  findings: ReadinessFinding[],
  compliance: Compliance | null,
  technical: TechnicalSummary | null,
): NextAction[] {
  const byCode = new Map<string, NextAction>();
  for (const finding of findings) {
    if (finding.severity !== "blocker") continue;
    const existing = byCode.get(finding.code);
    if (existing) {
      existing.count += 1;
      existing.countable = true;
      continue;
    }
    const copy = findingCopy(finding.code);
    byCode.set(finding.code, {
      key: finding.code,
      label: copy?.label ?? finding.code,
      count: 1,
      countable: finding.entity_id !== null,
      hint: copy?.hint,
      action: copy?.action,
      destination: copy?.destination,
      domain: finding.domain,
    });
  }
  const actions = [...byCode.values()];
  const potential = compliance?.run?.potential_blockers_count ?? 0;
  const confirmed = compliance?.run?.confirmed_blockers_count ?? 0;
  if (potential + confirmed > 0) {
    actions.push({
      key: "compliance_blockers",
      label: confirmed > 0 ? "El compliment té bloquejos confirmats" : "El compliment té un possible bloqueig per revisar",
      count: potential + confirmed,
      countable: true,
      hint: "Revisa'l amb el plec: pot ser un requisit mal llegit o una mancança real.",
      action: "Obre el compliment",
      destination: { kind: "tender", tab: "requisits" },
      domain: "compliance",
    });
  }
  if (technical && technical.confirmations > 0) {
    actions.push({
      key: "technical_confirmations",
      label: "Hi ha punts marcats «Cal confirmar» a la memòria tècnica",
      count: technical.confirmations,
      countable: true,
      hint: "Resol-los abans de validar les solucions.",
      action: "Obre la solució tècnica",
      destination: { kind: "area", area: "solucio" },
      domain: "technical",
    });
  }
  return actions.sort((a, b) => DOMAIN_ORDER.indexOf(a.domain) - DOMAIN_ORDER.indexOf(b.domain));
}
