import type { Level } from "@/components/ui/StatusPill";

/**
 * Revisió canònica per empresa (API `/companies/{c}/tenders/{t}/review`).
 *
 * La interfície només mostra les ETIQUETES que ja retorna l'API (`*_label`):
 * cap codi d'extracció, cap estat intern. Els pocs codis que arriben
 * (`verification_status`, `role`, `code` dels avisos) només serveixen per triar
 * un nivell visual o ordenar, mai com a text.
 */

export interface CountLabel {
  code: string;
  label: string;
  count: number;
}

export interface ReviewOverview {
  tender_id: string;
  review_started: boolean;
  total_requirements: number;
  pending_materialisation: number;
  needs_review_count: number;
  verification_counts: CountLabel[];
  provenance_counts: CountLabel[];
}

export interface CanonicalValue {
  key: string;
  operator: string | null;
  value: string | number | boolean | null;
  unit_label: string | null;
  source_label: string;
  status_label: string;
}

export interface CanonicalEvidence {
  document: string | null;
  page: number | null;
  section: string | null;
  quote: string | null;
  status_label: string;
}

export interface CanonicalRelation {
  relation_id: string;
  direction: "outgoing" | "incoming";
  relation_type: string;
  relation_label: string;
  other_item_id: string;
  other_item_title: string | null;
  note: string | null;
  foldable: boolean;
  created_at: string;
  archived_at?: string | null;
}

export interface CanonicalRequirement {
  canonical_id: string;
  title: string;
  description: string | null;
  category: string;
  category_label: string;
  obligation_label: string;
  provenance: string;
  provenance_label: string;
  verification_status: string;
  verification_label: string;
  needs_review: boolean;
  values: CanonicalValue[];
  evidence_label: string;
  evidence: CanonicalEvidence[];
  documents: string[];
  pages: number[];
  sections: string[];
  review_revision: number;
  visible_relations: CanonicalRelation[];
  folded_relations: CanonicalRelation[];
}

export interface AwardItemRef {
  canonical_id: string;
  name: string;
  points: number | null;
  documents: string[];
  pages: number[];
}

export interface CanonicalAwardCriterion {
  canonical_id: string;
  name: string;
  description: string | null;
  points: number | null;
  role: string | null;
  role_label: string;
  counts_in_total: boolean;
  parent: AwardItemRef | null;
  children: AwardItemRef[];
  folded_into: AwardItemRef | null;
  restatements: AwardItemRef[];
  mechanism_status: string;
  mechanism_label: string;
  formula: string | null;
  formula_status: string | null;
  formula_label: string | null;
  provenance: string;
  provenance_label: string;
  verification_status: string;
  verification_label: string;
  evidence: CanonicalEvidence[];
  documents: string[];
  pages: number[];
  review_revision: number;
}

export interface AwardCheck {
  code: string;
  label: string;
  item_ids: string[];
  details: Record<string, unknown>;
}

export interface AwardCriteriaView {
  view: "scoring" | "all";
  criteria: CanonicalAwardCriterion[];
  scored_count: number;
  total_points: number;
  checks: AwardCheck[];
}

export function reviewPath(companyId: string, tenderId: string, suffix = ""): string {
  return `/companies/${companyId}/tenders/${tenderId}/review${suffix}`;
}

// --------------------------------------------------------------------------- //
// Format                                                                        //
// --------------------------------------------------------------------------- //

/** Els operadors arriben com a codi (`gte`, del document) o com a símbol (`>=`, del revisor). */
const OPERATOR_SYMBOL: Record<string, string> = {
  gte: "≥",
  ">=": "≥",
  gt: ">",
  ">": ">",
  lte: "≤",
  "<=": "≤",
  lt: "<",
  "<": "<",
  eq: "=",
  "=": "=",
  "!=": "≠",
};

const NUMBER = new Intl.NumberFormat("ca-ES", { maximumFractionDigits: 2 });

function scalar(value: CanonicalValue["value"]): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "number") return NUMBER.format(value);
  return value;
}

/** «≥ 64 trucades», «Sí», «720 p». Mai la clau interna del valor. */
export function formatValue(value: CanonicalValue): string {
  const symbol = value.operator ? OPERATOR_SYMBOL[value.operator] : undefined;
  const parts = [symbol, scalar(value.value), value.unit_label].filter((part): part is string => Boolean(part));
  return parts.join(" ");
}

export function formatPoints(points: number | null): string {
  if (points === null) return "Sense punts";
  return `${NUMBER.format(points)} ${points === 1 ? "punt" : "punts"}`;
}

/** «PPT» per a «PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf»; si no, el nom sense extensió. */
export function shortDocument(name: string): string {
  const acronym = /^([A-ZÀ-Ú]{2,6})\.\s/.exec(name);
  if (acronym) return acronym[1];
  return name.replace(/\.(pdf|docx?|xlsx?|odt)$/i, "");
}

/** «PPT · p. 23» o «PPT · p. 22, 31». */
export function whereLine(documents: string[], pages: number[]): string | null {
  if (documents.length === 0 && pages.length === 0) return null;
  const docs = documents.map(shortDocument).join(", ");
  const where = pages.length > 0 ? `p. ${pages.join(", ")}` : null;
  return [docs || null, where].filter(Boolean).join(" · ");
}

// --------------------------------------------------------------------------- //
// Nivells visuals (el text sempre és l'etiqueta de l'API)                      //
// --------------------------------------------------------------------------- //

export const VERIFICATION_LEVEL: Record<string, Level> = {
  unreviewed: "neutral",
  needs_review: "attention",
  verified: "ready",
  partial: "attention",
  rejected: "neutral",
};

export function verificationLevel(status: string): Level {
  return VERIFICATION_LEVEL[status] ?? "neutral";
}

/**
 * Avisos de coherencia dels criteris. `info`: el que cal saber però no és cap
 * error (una fórmula sense verificar, una reiteració que no es compta dues
 * vegades). `attention`: el total o un bloc no quadren.
 */
export type CheckTone = "info" | "attention";

const INFO_CHECKS = new Set(["formula_not_evidence_verified", "restatement_excluded"]);

export function checkTone(check: AwardCheck): CheckTone {
  return INFO_CHECKS.has(check.code) ? "info" : "attention";
}

// --------------------------------------------------------------------------- //
// Requisits: filtres                                                            //
// --------------------------------------------------------------------------- //

export interface RequirementFilters {
  verification: string;
  provenance: string;
  obligation: string;
  category: string;
  text: string;
}

export const NO_FILTERS: RequirementFilters = {
  verification: "",
  provenance: "",
  obligation: "",
  category: "",
  text: "",
};

export interface FilterOption {
  value: string;
  label: string;
  count: number;
}

function options(items: CanonicalRequirement[], pick: (item: CanonicalRequirement) => [string, string]): FilterOption[] {
  const found = new Map<string, FilterOption>();
  for (const item of items) {
    const [value, label] = pick(item);
    const current = found.get(value);
    if (current) current.count += 1;
    else found.set(value, { value, label, count: 1 });
  }
  return [...found.values()].sort((a, b) => a.label.localeCompare(b.label, "ca"));
}

export function filterOptions(items: CanonicalRequirement[]) {
  return {
    verification: options(items, (item) => [item.verification_status, item.verification_label]),
    provenance: options(items, (item) => [item.provenance, item.provenance_label]),
    // L'obligació només arriba com a etiqueta: és el seu propi valor.
    obligation: options(items, (item) => [item.obligation_label, item.obligation_label]),
    category: options(items, (item) => [item.category, item.category_label]),
  };
}

function normalise(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function haystack(item: CanonicalRequirement): string {
  return normalise(
    [item.title, item.description ?? "", item.category_label, ...item.values.map(formatValue)].join(" "),
  );
}

export function filterRequirements(items: CanonicalRequirement[], filters: RequirementFilters): CanonicalRequirement[] {
  const words = normalise(filters.text).split(/\s+/).filter(Boolean);
  return items.filter(
    (item) =>
      (!filters.verification || item.verification_status === filters.verification) &&
      (!filters.provenance || item.provenance === filters.provenance) &&
      (!filters.obligation || item.obligation_label === filters.obligation) &&
      (!filters.category || item.category === filters.category) &&
      (words.length === 0 || words.every((word) => haystack(item).includes(word))),
  );
}

// --------------------------------------------------------------------------- //
// Criteris: agrupació                                                           //
// --------------------------------------------------------------------------- //

export type ScoringBlock =
  | { kind: "single"; row: CanonicalAwardCriterion }
  | { kind: "group"; parent: AwardItemRef; rows: CanonicalAwardCriterion[] };

/** Els criteris que puntuen, amb els fills sota el seu bloc («Criteris qualitatius 20 punts»). */
export function scoringBlocks(view: AwardCriteriaView): ScoringBlock[] {
  const blocks: ScoringBlock[] = [];
  const groups = new Map<string, { kind: "group"; parent: AwardItemRef; rows: CanonicalAwardCriterion[] }>();
  for (const row of view.criteria) {
    if (!row.parent) {
      blocks.push({ kind: "single", row });
      continue;
    }
    let group = groups.get(row.parent.canonical_id);
    if (!group) {
      group = { kind: "group", parent: row.parent, rows: [] };
      groups.set(row.parent.canonical_id, group);
      blocks.push(group);
    }
    group.rows.push(row);
  }
  return blocks;
}

export interface StructureSection {
  key: string;
  title: string;
  description: string;
  counts: boolean;
  rows: CanonicalAwardCriterion[];
}

const SECTIONS: Array<Omit<StructureSection, "rows">> = [
  { key: "counts", title: "Compten al total", description: "Sumen els punts de l'adjudicació.", counts: true },
  {
    key: "parent_total",
    title: "Totals de bloc",
    description: "El total d'un bloc ja és la suma dels seus criteris: no es torna a sumar.",
    counts: false,
  },
  {
    key: "restatement",
    title: "Repeticions en altres documents",
    description: "El mateix criteri dit una altra vegada (per exemple, a l'informe de necessitats): no es compta dues vegades.",
    counts: false,
  },
  {
    key: "offer_form_view",
    title: "Model d'oferta",
    description: "El bloc vist des del formulari de l'oferta.",
    counts: false,
  },
  { key: "not_scored", title: "No puntuen", description: "Text del plec sobre l'adjudicació que no dona punts.", counts: false },
  {
    key: "desirable",
    title: "Millores valorables",
    description: "Característiques que el plec valora sense assignar-los punts.",
    counts: false,
  },
  { key: "unassigned", title: "Paper per assignar", description: "Encara no s'ha decidit si puntuen.", counts: false },
];

function sectionOf(row: CanonicalAwardCriterion): string {
  if (row.counts_in_total) return "counts";
  if (row.role === "parent_total" && !row.folded_into) return "parent_total";
  if (row.role === "offer_form_view") return "offer_form_view";
  if (row.folded_into) return "restatement";
  if (row.role === "not_scored" || row.role === "desirable") return row.role;
  return "unassigned";
}

/** Totes les files, repartides pel que fan amb el total. Les seccions buides no hi surten. */
export function structureSections(view: AwardCriteriaView): StructureSection[] {
  const byKey = new Map<string, CanonicalAwardCriterion[]>();
  for (const row of view.criteria) {
    const key = sectionOf(row);
    byKey.set(key, [...(byKey.get(key) ?? []), row]);
  }
  return SECTIONS.map((section) => ({ ...section, rows: byKey.get(section.key) ?? [] })).filter(
    (section) => section.rows.length > 0,
  );
}

/** Noms dels criteris d'un avís, resolts amb el que ja ha arribat (files i referències). */
export function checkItemNames(view: AwardCriteriaView, check: AwardCheck): string[] {
  const names = new Map<string, string>();
  const remember = (ref: AwardItemRef | null) => {
    if (ref) names.set(ref.canonical_id, ref.name);
  };
  for (const row of view.criteria) {
    names.set(row.canonical_id, row.name);
    remember(row.parent);
    remember(row.folded_into);
    row.children.forEach(remember);
    row.restatements.forEach(remember);
  }
  return check.item_ids.map((id) => names.get(id)).filter((name): name is string => Boolean(name));
}
