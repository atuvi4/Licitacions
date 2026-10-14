/**
 * Oferta econòmica: el contracte d'S4/S5 i com es llegeix.
 *
 * Dues regles governen aquest fitxer, i totes dues venen del backend.
 *
 * **1. Aquí no es calcula cap import.** Totals, subtotals, IVA, marge, markup,
 * límits i pressupostos els dona el servidor (S1). El frontend només els
 * formata. Recalcular-los aquí seria tenir dues comptabilitats.
 *
 * **2. Els imports són cadenes.** El backend serialitza `Decimal` com a text
 * (`"1850.0000"`), i així viatgen: al formulari, a la comparació i al cos de la
 * petició. `Number` només s'hi aplica per pintar-los, mai per decidir-los.
 *
 * I una tercera que ve d'S5: **ocult no és desconegut**. Un cost que ningú no ha
 * escrit és `unit_cost_ex_vat: null`; un cost que aquest rol no pot veure és
 * l'absència del bloc `internal_economics` sencer.
 */

import { ROLE_LABELS } from "@/lib/labels";

export type PricingBasis = "one_off" | "monthly" | "annual" | "per_unit";

/** Els imports i percentatges arriben com a text per no perdre precisió. */
export type Money = string | null;

export interface Totals {
  total_revenue: Money;
  vat_amount: Money;
  total_with_vat: Money;
  line_count: number;
  incomplete_revenue_lines: number;
  incomplete_vat_lines: number;
}

export interface InternalTotals {
  total_cost: Money;
  margin_amount: Money;
  margin_on_sales_pct: Money;
  markup_on_cost_pct: Money;
  incomplete_cost_lines: number;
}

export interface LineInternal extends InternalTotals {
  unit_cost_ex_vat: Money;
  monthly_equivalent_cost: Money;
  /** Objectiu comercial: markup **sobre cost**, en tant per cent. */
  target_markup_pct: Money;
  /** `cost × (1 + markup/100)`. Derivat: mai és el preu ofert. */
  proposed_unit_price_ex_vat: Money;
  /** `null` quan falta alguna de les tres dades: no ho sabem ≠ no coincideix. */
  price_matches_target: boolean | null;
}

export interface LotLimit {
  tender_max: Money;
  difference_to_maximum: Money;
  /** `null` quan el plec no dona màxim: desconegut no és «no el supera». */
  exceeds_lot_max: boolean | null;
}

/**
 * Veredicte de l'oferta contra el màxim del lot, en tres estats.
 *
 * `unknown` quan no es pot dir: sense màxim, sense total (cap línia o imports no
 * calculables) o sense diferència. Només `within` és favorable, i només amb un
 * `false` explícit de l'API i una diferència calculada: desconegut no és «sota el màxim».
 */
export type LimitVerdict = "unknown" | "within" | "exceeds";

export function limitVerdict(limit: LotLimit): LimitVerdict {
  if (limit.exceeds_lot_max === true) return "exceeds";
  if (
    limit.exceeds_lot_max === false &&
    limit.tender_max !== null &&
    limit.difference_to_maximum !== null
  ) {
    return "within";
  }
  return "unknown";
}

/**
 * El veredicte d'un lot sencer. L'API suma només les seccions amb línies (una secció
 * buida «no hi contribueix gens»), de manera que el total pot quedar per sota del
 * màxim mentre falten seccions per preparar. Amb alguna secció activa sense línies,
 * el veredicte és `unknown`: mai «sota el màxim» amb línies que falten. Una de
 * superada continua sent «exceeds»: ja se sap que no hi cap.
 */
export function lotLimitVerdict(lot: Pick<OfferLot, "limit" | "sections">): LimitVerdict {
  const verdict = limitVerdict(lot.limit);
  if (verdict === "exceeds") return verdict;
  const emptySection = lot.sections.some((section) => section.archived_at === null && section.totals.line_count === 0);
  return emptySection ? "unknown" : verdict;
}

export const LIMIT_VERDICT_LABEL: Record<LimitVerdict, string> = {
  unknown: "Pendent de calcular: falten línies o imports",
  within: "Sota el màxim",
  exceeds: "Supera el pressupost",
};

export interface OfferLine {
  id: string;
  section_id: string;
  concept: string;
  /**
   * D'on ve la línia, si ve d'una opció de solució.
   *
   * Només de lectura: la provinença es guanya adoptant una opció, no enviant-ne
   * l'identificador. I la línia és un **snapshot**: que després canviï l'opció
   * no la toca.
   */
  solution_option_id: string | null;
  requirement_id: string | null;
  technical_solution_id: string | null;
  supplier_name: string | null;
  product_name: string | null;
  quantity: string;
  pricing_basis: PricingBasis;
  duration_months_override: number | null;
  offered_unit_price_ex_vat: Money;
  tender_max_unit_price_ex_vat: Money;
  vat_rate: Money;
  notes: string | null;
  position: number;
  /**
   * Els camps en què el snapshot ja no diu el que diu l'opció viva.
   *
   * Només els NOMS: entre ells hi ha el cost, i el seu valor aquí el
   * filtraria a qui no el pot veure.
   */
  diverged_fields: string[];
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  /** Resolta pel servidor (C-3): la pròpia, o la del lot. */
  effective_duration_months: number | null;
  periods: Money;
  monthly_equivalent_revenue: Money;
  exceeds_unit_max: boolean | null;
  totals: Totals;
  internal_economics: LineInternal | null;
}

export interface OfferSection {
  id: string;
  offer_lot_id: string;
  title: string;
  position: number;
  archived_at: string | null;
  totals: Totals;
  internal_economics: InternalTotals | null;
  lines: OfferLine[];
}

export interface OfferLot {
  id: string;
  tender_lot_id: string | null;
  /** Fotografia del moment de triar-lo (C-13). No es recalcula mai aquí. */
  label: string;
  duration_months: number | null;
  position: number;
  archived_at: string | null;
  totals: Totals;
  internal_economics: InternalTotals | null;
  limit: LotLimit;
  sections: OfferSection[];
}

export interface EconomicOffer {
  id: string;
  workspace_id: string;
  currency: string;
  notes: string | null;
  /** El número que cal tornar a enviar com a `expected_revision`. */
  revision: number;
  can_view_internal_economics: boolean;
  /** Cert només al cas C de C-1: plec amb lots i candidatura global. */
  can_select_lots: boolean;
  totals: Totals;
  internal_economics: InternalTotals | null;
  lots: OfferLot[];
}

export interface OfferCreateResult {
  created: boolean;
  offer: EconomicOffer;
}

/* ------------------------------------------------------------------------- */
/* Etiquetes                                                                  */
/* ------------------------------------------------------------------------- */

export const PRICING_BASIS_LABELS: Record<PricingBasis, string> = {
  one_off: "Preu tancat",
  monthly: "Mensual",
  annual: "Anual",
  per_unit: "Per unitat",
};

export const PRICING_BASES: readonly PricingBasis[] = ["one_off", "per_unit", "monthly", "annual"];

/** Les bases que multipliquen pel temps. Les altres no tenen durada (C-3). */
export function usesDuration(basis: PricingBasis): boolean {
  return basis === "monthly" || basis === "annual";
}

/** Sufix del preu unitari segons la base. Text, no càlcul. */
export function unitSuffix(basis: PricingBasis): string | null {
  if (basis === "monthly") return "al mes";
  if (basis === "annual") return "a l'any";
  return null;
}

/* ------------------------------------------------------------------------- */
/* Permisos                                                                   */
/* ------------------------------------------------------------------------- */

/**
 * `ECONOMIC_EDIT_ROLES` del backend. `technical` no hi és: prepara la solució
 * tècnica, no compromet el marge.
 *
 * Qui pot VEURE els números de dins no es dedueix d'aquí: ho diu el servidor
 * amb `can_view_internal_economics`. Al backend són dues constants separades a
 * propòsit, i aquí també.
 */
export const ECONOMIC_EDIT_ROLES = ["owner", "admin", "bid_manager"] as const;

export function canEditOffer(role: string | null | undefined): boolean {
  return role !== null && role !== undefined && (ECONOMIC_EDIT_ROLES as readonly string[]).includes(role);
}

export function whoCanEditOffer(): string {
  const labels = ECONOMIC_EDIT_ROLES.map((role) => ROLE_LABELS[role]);
  return `qui té el rol de ${labels.slice(0, -1).join(", ")} o ${labels[labels.length - 1]}`;
}

/* ------------------------------------------------------------------------- */
/* Format                                                                     */
/* ------------------------------------------------------------------------- */

/**
 * Els imports es pinten SENSE passar per coma flotant.
 *
 * El backend els serialitza com a text amb fins a quatre decimals, i
 * `Number("1.005")` ja no és 1,005: arrodonir-ho després dona 1,00 en comptes
 * d'1,01. Com que un document d'oferta ha de quadrar amb el que calcula el
 * servidor, aquí s'arrodoneix sobre els dígits, amb la mateixa regla que S1
 * (`ROUND_HALF_UP`).
 *
 * Això és presentació i prou. No hi ha cap suma, cap divisió i cap total: els
 * números els continua donant el backend, i el valor que torna a una petició és
 * sempre la cadena original, mai aquesta.
 */

interface DecimalParts {
  negative: boolean;
  whole: string;
  fraction: string;
}

/** Perquè incrementar un dígit no necessiti aritmètica de cap mena. */
const NEXT_DIGIT: Record<string, string> = { "0": "1", "1": "2", "2": "3", "3": "4", "4": "5", "5": "6", "6": "7", "7": "8", "8": "9" };

function parseDecimal(value: string): DecimalParts | null {
  const text = value.trim();
  if (!/^[+-]?\d+(\.\d+)?$/.test(text)) return null;
  const negative = text.startsWith("-");
  const [whole, fraction = ""] = text.replace(/^[+-]/, "").split(".");
  return { negative, whole: whole.replace(/^0+(?=\d)/, ""), fraction };
}

/** Suma 1 a l'últim dígit propagant la portadora. Només text. */
function increment(digits: string): string {
  const out = digits.split("");
  for (let index = out.length - 1; index >= 0; index -= 1) {
    if (out[index] === "9") {
      out[index] = "0";
      continue;
    }
    out[index] = NEXT_DIGIT[out[index]];
    return out.join("");
  }
  return `1${out.join("")}`;
}

function roundHalfUp(parts: DecimalParts, places: number): DecimalParts {
  if (parts.fraction.length <= places) {
    return { ...parts, fraction: parts.fraction.padEnd(places, "0") };
  }
  const kept = parts.fraction.slice(0, places);
  // Comparació de dígits, no de números.
  if (parts.fraction[places] < "5") return { ...parts, fraction: kept };
  const bumped = increment(`${parts.whole}${kept}`);
  const cut = bumped.length - places;
  return { negative: parts.negative, whole: bumped.slice(0, cut) || "0", fraction: bumped.slice(cut) };
}

function groupThousands(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/**
 * Un decimal del servidor amb la precisió que toca a pantalla, en format
 * català. Torna `null` si el valor no hi és o no és un decimal.
 */
export function formatDecimal(value: Money, places: number): string | null {
  if (value === null || value.trim() === "") return null;
  const parts = parseDecimal(value);
  if (parts === null) return null;
  const rounded = roundHalfUp(parts, places);
  const body = places === 0 ? groupThousands(rounded.whole) : `${groupThousands(rounded.whole)},${rounded.fraction}`;
  // Un «-0,00» no és cap import: és soroll d'arrodoniment.
  const isZero = rounded.whole === "0" && !/[1-9]/.test(rounded.fraction);
  return `${rounded.negative && !isZero ? "-" : ""}${body}`;
}

function currencySymbol(currency: string): string {
  return currency === "EUR" ? "€" : currency;
}

/** Un import, per pintar. Si el servidor envia una cosa que no és un decimal, es mostra tal qual. */
export function formatAmount(value: Money, currency = "EUR"): string {
  if (value === null || value.trim() === "") return "—";
  const body = formatDecimal(value, 2);
  if (body === null) return value.trim();
  return `${body} ${currencySymbol(currency)}`;
}

/** Un percentatge que ja ve en tant per cent del servidor (`18.0000` → 18,0 %). */
export function formatPercent(value: Money): string {
  if (value === null || value.trim() === "") return "—";
  const body = formatDecimal(value, 1);
  return body === null ? value.trim() : `${body} %`;
}

/** Una quantitat sense moneda: sense decimals inútils («6», «6,5»). */
export function formatQuantity(value: string): string {
  const parts = parseDecimal(value);
  if (parts === null) return value;
  const fraction = parts.fraction.replace(/0+$/, "");
  const body = fraction === "" ? groupThousands(parts.whole) : `${groupThousands(parts.whole)},${fraction}`;
  const isZero = parts.whole === "0" && fraction === "";
  return `${parts.negative && !isZero ? "-" : ""}${body}`;
}

/**
 * Dos decimals són el mateix número, sense passar per coma flotant.
 *
 * `"1850"` i `"1850.0000"` són iguals; comparar-los com a text diria que no, i
 * enviaríem una edició que no canvia res.
 */
export function sameDecimal(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a === b;
  const norm = (value: string): string => {
    const parts = parseDecimal(value);
    if (parts === null) return value.trim();
    const fraction = parts.fraction.replace(/0+$/, "");
    const digits = fraction === "" ? parts.whole : `${parts.whole}.${fraction}`;
    const isZero = parts.whole === "0" && fraction === "";
    return `${parts.negative && !isZero ? "-" : ""}${digits}`;
  };
  return norm(a) === norm(b);
}

/* ------------------------------------------------------------------------- */
/* Durada (C-3)                                                               */
/* ------------------------------------------------------------------------- */

export type DurationSource = "not_applicable" | "override" | "inherited" | "unknown";

export interface DurationDisplay {
  source: DurationSource;
  months: number | null;
  label: string;
  hint: string | null;
}

/**
 * Què diu una línia sobre quant dura. Mai s'hi inventa cap número: sense durada
 * efectiva, la resposta és que no se sap.
 */
export function durationOf(line: Pick<OfferLine, "pricing_basis" | "duration_months_override" | "effective_duration_months">): DurationDisplay {
  if (!usesDuration(line.pricing_basis)) {
    return { source: "not_applicable", months: null, label: "—", hint: null };
  }
  const months = line.effective_duration_months;
  if (months === null) {
    return { source: "unknown", months: null, label: "Durada pendent", hint: "Sense durada no es pot calcular el total." };
  }
  const plural = months === 1 ? "mes" : "mesos";
  if (line.duration_months_override !== null) {
    return { source: "override", months, label: `${months} ${plural}`, hint: "Personalitzada" };
  }
  return { source: "inherited", months, label: `${months} ${plural}`, hint: "Heretada del lot" };
}

/* ------------------------------------------------------------------------- */
/* Lectura de l'arbre                                                         */
/* ------------------------------------------------------------------------- */

export function activeLots(offer: EconomicOffer): OfferLot[] {
  return offer.lots.filter((lot) => lot.archived_at === null);
}

export function archivedCount(offer: EconomicOffer): number {
  let count = 0;
  for (const lot of offer.lots) {
    if (lot.archived_at !== null) count += 1;
    for (const section of lot.sections) {
      if (section.archived_at !== null) count += 1;
      count += section.lines.filter((line) => line.archived_at !== null).length;
    }
  }
  return count;
}

/** Totes les línies de l'oferta, arxivades incloses: per resoldre enllaços. */
export function allLines(offer: EconomicOffer): OfferLine[] {
  return offer.lots.flatMap((lot) => lot.sections.flatMap((section) => section.lines));
}

export type EntityTarget =
  | { kind: "line"; line: OfferLine }
  | { kind: "lot"; lot: OfferLot }
  | { kind: "offer" }
  | null;

/**
 * On porta un `?entity=`. Els problemes econòmics d'S6 assenyalen una línia,
 * un lot o l'oferta sencera; qualsevol altra cosa no es força.
 */
export function resolveEntity(offer: EconomicOffer, entityId: string | null): EntityTarget {
  if (!entityId) return null;
  if (entityId === offer.id) return { kind: "offer" };
  const line = allLines(offer).find((item) => item.id === entityId);
  if (line) return { kind: "line", line };
  const lot = offer.lots.find((item) => item.id === entityId);
  if (lot) return { kind: "lot", lot };
  return null;
}

/* ------------------------------------------------------------------------- */
/* Edició                                                                     */
/* ------------------------------------------------------------------------- */

/** Els camps del formulari, sempre com a text: el `null` del servidor és «». */
export interface LineDraft {
  concept: string;
  quantity: string;
  pricing_basis: PricingBasis;
  supplier_name: string;
  product_name: string;
  duration_months_override: string;
  unit_cost_ex_vat: string;
  offered_unit_price_ex_vat: string;
  tender_max_unit_price_ex_vat: string;
  vat_rate: string;
  target_markup_pct: string;
  notes: string;
}

export function draftFromLine(line: OfferLine): LineDraft {
  return {
    concept: line.concept,
    quantity: line.quantity,
    pricing_basis: line.pricing_basis,
    supplier_name: line.supplier_name ?? "",
    product_name: line.product_name ?? "",
    duration_months_override: line.duration_months_override === null ? "" : String(line.duration_months_override),
    unit_cost_ex_vat: line.internal_economics?.unit_cost_ex_vat ?? "",
    offered_unit_price_ex_vat: line.offered_unit_price_ex_vat ?? "",
    tender_max_unit_price_ex_vat: line.tender_max_unit_price_ex_vat ?? "",
    vat_rate: line.vat_rate ?? "",
    target_markup_pct: line.internal_economics?.target_markup_pct ?? "",
    notes: line.notes ?? "",
  };
}

export function emptyDraft(): LineDraft {
  return {
    concept: "",
    quantity: "1",
    pricing_basis: "one_off",
    target_markup_pct: "",
    supplier_name: "",
    product_name: "",
    duration_months_override: "",
    unit_cost_ex_vat: "",
    offered_unit_price_ex_vat: "",
    tender_max_unit_price_ex_vat: "",
    vat_rate: "",
    notes: "",
  };
}

const TEXT_FIELDS = ["supplier_name", "product_name", "notes"] as const;
const DECIMAL_FIELDS = ["quantity", "unit_cost_ex_vat", "offered_unit_price_ex_vat", "tender_max_unit_price_ex_vat", "vat_rate"] as const;
/** Economia interna: viatgen pel mateix camí que el cost, mai sense permís. */
const INTERNAL_DECIMAL_FIELDS = ["unit_cost_ex_vat", "target_markup_pct"] as const;

export type LinePayload = Record<string, string | number | null>;

/**
 * Només el que ha canviat, i només camps que el servidor deixa escriure.
 *
 * Els derivats —totals, marge, markup, `exceeds_*`, `periods`— no hi són: els
 * calcula ell i enviar-los seria proposar-li una altra comptabilitat. `source`,
 * els identificadors i la firma tampoc no existeixen com a camps.
 *
 * `includeCost` mana sobre el cost unitari: un rol que no el pot veure tampoc
 * no l'ha d'enviar, encara que el seu esborrany en dugui rastre.
 */
export function diffLine(original: OfferLine, draft: LineDraft, includeCost: boolean): LinePayload {
  const payload: LinePayload = {};
  const orNull = (value: string) => (value.trim() === "" ? null : value.trim());

  if (draft.concept.trim() !== original.concept) payload.concept = draft.concept.trim();
  if (draft.pricing_basis !== original.pricing_basis) payload.pricing_basis = draft.pricing_basis;

  for (const field of TEXT_FIELDS) {
    const next = orNull(draft[field]);
    if (next !== original[field]) payload[field] = next;
  }

  for (const field of [...DECIMAL_FIELDS, "target_markup_pct" as const]) {
    if ((INTERNAL_DECIMAL_FIELDS as readonly string[]).includes(field) && !includeCost) continue;
    const next = orNull(draft[field]);
    const current = (INTERNAL_DECIMAL_FIELDS as readonly string[]).includes(field)
      ? ((original.internal_economics?.[field as "unit_cost_ex_vat" | "target_markup_pct"] ?? null) as Money)
      : (original[field as keyof OfferLine] as Money);
    if (!sameDecimal(next, current)) payload[field] = next;
  }

  const override = draft.duration_months_override.trim();
  const nextOverride = override === "" ? null : Number.parseInt(override, 10);
  if (nextOverride !== original.duration_months_override && !(nextOverride !== null && Number.isNaN(nextOverride))) {
    payload.duration_months_override = nextOverride;
  }
  return payload;
}

/** El cos d'una línia nova: el que el servidor exigeix, més el que s'hagi escrit. */
export function createPayload(draft: LineDraft, includeCost: boolean): LinePayload {
  const orNull = (value: string) => (value.trim() === "" ? null : value.trim());
  const payload: LinePayload = {
    concept: draft.concept.trim(),
    quantity: draft.quantity.trim() === "" ? "0" : draft.quantity.trim(),
    pricing_basis: draft.pricing_basis,
  };
  for (const field of TEXT_FIELDS) {
    const value = orNull(draft[field]);
    if (value !== null) payload[field] = value;
  }
  for (const field of [...DECIMAL_FIELDS, "target_markup_pct" as const]) {
    if (field === "quantity") continue;
    if ((INTERNAL_DECIMAL_FIELDS as readonly string[]).includes(field) && !includeCost) continue;
    const value = orNull(draft[field]);
    if (value !== null) payload[field] = value;
  }
  const override = draft.duration_months_override.trim();
  if (override !== "") {
    const months = Number.parseInt(override, 10);
    if (!Number.isNaN(months)) payload.duration_months_override = months;
  }
  return payload;
}

export function lineIsDirty(original: OfferLine, draft: LineDraft, includeCost: boolean): boolean {
  return Object.keys(diffLine(original, draft, includeCost)).length > 0;
}

/* ------------------------------------------------------------------------- */
/* Conflicte de revisió                                                       */
/* ------------------------------------------------------------------------- */

export const REVISION_CONFLICT_CODE = "economic_offer_revision_conflict";

export function isRevisionConflict(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const candidate = error as { status?: unknown; code?: unknown };
  return candidate.status === 409 && candidate.code === REVISION_CONFLICT_CODE;
}

/** Els camps que es comparen al diàleg de conflicte. Lectura, mai fusió. */
export const CONFLICT_FIELDS: ReadonlyArray<{ key: keyof LineDraft; label: string; money: boolean; internal?: boolean }> = [
  { key: "concept", label: "Concepte", money: false },
  { key: "quantity", label: "Quantitat", money: false },
  { key: "offered_unit_price_ex_vat", label: "Preu ofert", money: true },
  { key: "unit_cost_ex_vat", label: "Cost intern", money: true, internal: true },
  { key: "duration_months_override", label: "Durada pròpia", money: false },
  { key: "vat_rate", label: "IVA", money: false },
];

/** Com es llegeix cada camp sincronitzable a la pantalla. */
export const SYNC_FIELD_LABELS: Record<string, string> = {
  supplier_name: "Proveïdor",
  product_name: "Producte",
  quantity: "Quantitat",
  unit_cost_ex_vat: "Cost unitari",
  pricing_basis: "Facturació",
  duration_months_override: "Durada (mesos)",
  requirement_id: "Requisit",
  technical_solution_id: "Solució tècnica",
};

/** Els camps que són economia interna: no s'ensenyen sense permís. */
export const INTERNAL_SYNC_FIELDS = new Set(["unit_cost_ex_vat"]);

/**
 * Els camps divergents que aquest rol pot veure.
 *
 * **Defensa addicional, no la garantia.** Qui redacta és el servidor: per a un
 * rol sense economia interna, `diverged_fields` ja arriba sense els camps
 * interns. Filtrar aquí no protegiria res —la resposta ja hauria sortit—, i
 * només serveix perquè la pantalla no depengui d'haver-ho rebut bé.
 */
export function visibleDivergedFields(line: OfferLine, internal: boolean): string[] {
  return internal
    ? line.diverged_fields
    : line.diverged_fields.filter((field) => !INTERNAL_SYNC_FIELDS.has(field));
}
