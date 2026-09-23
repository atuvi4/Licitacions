import type { Level } from "@/components/ui";

/**
 * Opcions de solució: proveïdors, cotitzacions i tria humana.
 *
 * Dues distincions que tot aquest mòdul existeix per no perdre:
 *
 * * **una opció no és una línia d'oferta.** Aquí hi ha cost, mai preu de venda;
 * * **«sense decidir» no és «descartada».** Una alternativa que ningú no ha
 *   mirat i una que algú ha descartat amb motiu són coses diferents, i
 *   pintar-les igual esborraria feina que no s'ha fet.
 *
 * El cost viu dins d'`internal_economics`, que **existeix o no existeix**. Un
 * `null` al primer nivell voldria dir «encara no ho sabem», i qui no pot veure
 * el cost tampoc no ha de poder deduir això.
 */

export type QuoteStatus =
  | "draft"
  | "requested"
  | "pending"
  | "received"
  | "expired"
  | "declined";

export type OptionSelection = "undecided" | "selected" | "rejected";

export type PricingBasis = "one_off" | "monthly" | "annual" | "per_unit";

export type DimensionStatus = "undecided" | "decided" | "disputed";

/**
 * La magnitud de què depèn una opció, resumida.
 *
 * `working_value_num` és el valor d'ARA, llegit de la magnitud. No és cap còpia
 * desada a la relació: si ho fos, envelliria i un dia diria una cosa que ja no
 * és certa.
 */
export interface OptionDimension {
  id: string;
  key: string;
  label: string;
  unit: string | null;
  scope_label: string | null;
  status: DimensionStatus;
  working_value_text: string | null;
  working_value_num: string | null;
  archived_at: string | null;
  revision: number;
}

export interface OptionInternalEconomics {
  unit_cost_ex_vat: string | null;
  internal_note: string | null;
}

export interface SolutionOption {
  id: string;
  workspace_id: string;
  technical_solution_id: string | null;
  requirement_id: string | null;
  requirement_is_effective: boolean | null;
  supplier_name: string | null;
  product_name: string | null;
  description: string | null;
  quantity: string | null;
  unit: string | null;
  pricing_basis: PricingBasis | null;
  duration_months_override: number | null;
  quote_status: QuoteStatus;
  quote_date: string | null;
  quote_reference: string | null;
  quote_source: string | null;
  selection: OptionSelection;
  selection_reason: string | null;
  selected_by: string | null;
  selected_at: string | null;
  archived_at: string | null;
  /** El testimoni de CAS: el següent escrit l'ha de portar. */
  revision: number;
  /** De quines magnituds depèn. Ho declara una persona; res no ho dedueix. */
  dimensions: OptionDimension[];
  /** `null` quan aquest rol no pot veure el cost. No és «encara no escrit». */
  internal_economics: OptionInternalEconomics | null;
}

export interface SolutionOptionList {
  can_view_internal_economics: boolean;
  items: SolutionOption[];
}

/**
 * `received` vol dir **rebuda**, no aprovada.
 *
 * Dir-ne «cotització OK» faria passar per tancada una xifra que encara no ha
 * contrastat ningú.
 */
export const QUOTE_STATUS_LABELS: Record<QuoteStatus, string> = {
  draft: "Esborrany",
  requested: "Demanada",
  pending: "Pendent",
  received: "Rebuda",
  expired: "Caducada",
  declined: "Declinada",
};

export const QUOTE_STATUS_LEVEL: Record<QuoteStatus, Level> = {
  draft: "neutral",
  requested: "running",
  pending: "running",
  received: "ready",
  // Caducada avisa; no bloqueja res en aquesta fase.
  expired: "attention",
  declined: "neutral",
};

export const QUOTE_STATUSES: QuoteStatus[] = [
  "draft",
  "requested",
  "pending",
  "received",
  "expired",
  "declined",
];

export const SELECTION_LABELS: Record<OptionSelection, string> = {
  undecided: "Sense decidir",
  selected: "Seleccionada",
  rejected: "Rebutjada",
};

export const SELECTION_LEVEL: Record<OptionSelection, Level> = {
  undecided: "neutral",
  selected: "ready",
  // Rebutjada no és cap error: és una decisió presa, i s'ha de llegir com a tal.
  rejected: "neutral",
};

export const PRICING_BASIS_LABELS: Record<PricingBasis, string> = {
  one_off: "Un sol pagament",
  monthly: "Mensual",
  annual: "Anual",
  per_unit: "Per unitat",
};

/** Qui pot configurar opcions. Els mateixos que editen les solucions tècniques. */
export function canEditOptions(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "bid_manager" || role === "technical";
}

export function whoCanEditOptions(): string {
  return "els rols Propietari, Administrador, Responsable d'oferta i Tècnic";
}

/**
 * Una opció triada que encara espera la cotització.
 *
 * Només les triades: que una alternativa que ningú no farà servir no tingui
 * cotització no és cap feina pendent.
 */
export function awaitsQuote(option: SolutionOption): boolean {
  return (
    option.archived_at === null &&
    option.selection === "selected" &&
    (option.quote_status === "requested" || option.quote_status === "pending")
  );
}

/** Com es llegeix una opció en una línia: proveïdor i producte, o el que hi hagi. */
export function optionTitle(option: SolutionOption): string {
  const parts = [option.supplier_name, option.product_name].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "Opció sense nom";
}

export const DIMENSION_STATUS_LABELS: Record<DimensionStatus, string> = {
  undecided: "Sense decidir",
  decided: "Decidida",
  disputed: "Fonts que no coincideixen",
};

export const DIMENSION_STATUS_LEVEL: Record<DimensionStatus, Level> = {
  undecided: "neutral",
  decided: "ready",
  disputed: "attention",
};

/**
 * Com es llegeix el valor d'ara d'una magnitud.
 *
 * Deliberadament **no** cau cap a cap altra xifra quan no hi ha valor de
 * treball: si ningú no l'ha decidit, no n'hi ha, i ensenyar-ne una d'inventada
 * és el que fa que després algú la copiï a una oferta.
 */
export function dimensionValue(dimension: OptionDimension): string | null {
  if (dimension.working_value_text === null) return null;
  return dimension.unit
    ? `${dimension.working_value_text} ${dimension.unit}`
    : dimension.working_value_text;
}
