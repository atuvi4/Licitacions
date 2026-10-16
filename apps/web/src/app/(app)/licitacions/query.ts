import { IN_TERM_LABEL } from "@/lib/labels";
import type { MatchStatus, MatchSummary } from "@/lib/matching";
import { TENDER_STATUS_LABELS, buildQuery, labelOf } from "@/lib/tenders";

/**
 * La consulta del feed d'Oportunitats, extreta de la pàgina (handoff L6) per
 * poder demostrar que el redisseny no en canvia ni un paràmetre: mateixos
 * modes, mateixos ordres, mateixos filtres, mateixa paginació.
 */

export const PAGE_SIZE = 25;

/**
 * Valor del filtre «Estat de la licitació» per a l'estat ACCIONABLE «En
 * termini» (F2): viatja com a `in_term=true` (publicada i termini conegut
 * posterior a ara, per instant, al backend), mai com a `status=open`, que és
 * l'estat cru de la font i inclou les vençudes.
 */
export const IN_TERM_STATUS = "in_term";

/**
 * `only_open`: termini posterior a ara, SIGUI QUIN SIGUI l'estat de la font (C3).
 * No és «En termini» (que també demana que la font la publiqui): el nom ho diu.
 */
export const ONLY_OPEN_LABEL = "Només amb termini obert (qualsevol estat de la font)";

/** Els paràmetres del filtre d'estat: `in_term` o l'estat cru de la font. */
export function statusParams(status: string): { status: string; in_term: true | undefined } {
  return status === IN_TERM_STATUS ? { status: "", in_term: true } : { status, in_term: undefined };
}

/** «En termini» (accionable) primer; després els estats crus, dits com a estat de la font. */
export function tenderStatusFilterOptions(statuses: string[] | undefined): Array<{ value: string; label: string }> {
  return [
    { value: IN_TERM_STATUS, label: IN_TERM_LABEL },
    ...(statuses ?? []).map((value) => ({
      value,
      label: `${labelOf(TENDER_STATUS_LABELS, value, "Estat desconegut")} (estat de la font)`,
    })),
  ];
}

/**
 * Modes del feed.
 *
 * El mateix catàleg vist des de sis angles. «Totes» no filtra res: el pre-fit
 * acompanya cada fila però no amaga cap licitació — decidir què no val la
 * pena mirar continua sent de l'usuari.
 */
export type Mode = {
  value: string;
  label: string;
  status?: MatchStatus;
  count: keyof MatchSummary;
};

export const MODES: Mode[] = [
  { value: "all", label: "Totes amb pre-fit", count: "total_tenders" },
  // «Prioritzades» (addendum 3B-2, D2): les candidates que el Radar prioritza; abans «Per revisar» (mockup 03).
  { value: "candidate", label: "Prioritzades", status: "candidate", count: "candidates" },
  { value: "low_fit", label: "Encaix baix", status: "low_fit", count: "low_fit" },
  { value: "excluded", label: "Descartades", status: "excluded", count: "excluded" },
  {
    value: "insufficient_data",
    label: "Sense dades suficients",
    status: "insufficient_data",
    count: "insufficient_data",
  },
  { value: "closed", label: "Fora de termini", status: "closed", count: "closed" },
];

export const SORT_OPTIONS = [
  { value: "score", label: "Millor pre-fit" },
  { value: "recent", label: "Vistes més recentment" },
  { value: "published", label: "Publicació més recent" },
  { value: "deadline", label: "Termini més proper" },
  { value: "amount", label: "Import més alt (VEC; si no n'hi ha, PBL)" },
];

export const EMPTY_FILTERS = {
  q: "",
  cpv: "",
  status: "",
  procedure_type: "",
  contract_type: "",
  nuts: "",
  min_amount: "",
  max_amount: "",
  published_from: "",
  deadline_to: "",
  has_lots: "",
  only_open: false,
};

export type Filters = typeof EMPTY_FILTERS;

export function activeMode(mode: string): Mode {
  return MODES.find((option) => option.value === mode) ?? MODES[0];
}

/** Exactament l'expressió que la pàgina feia servir abans de L6. */
export function feedQuery({
  applied,
  mode,
  sort,
  offset,
}: {
  applied: Filters;
  mode: string;
  sort: string;
  offset: number;
}): string {
  return buildQuery({
    ...applied,
    ...statusParams(applied.status),
    has_lots: applied.has_lots || undefined,
    only_open: applied.only_open || undefined,
    match_status: activeMode(mode).status,
    sort,
    limit: PAGE_SIZE,
    offset,
  });
}

/** `?match_status=` de la URL (cerca global, «Avui»): només un mode real; la resta, «Prioritzades». */
export function modeFromParam(value: string | null): string {
  if (!value) return "candidate";
  return MODES.some((option) => option.value === value && option.status) ? value : "candidate";
}

/** Quants filtres tenen valor: el rètol «Filtres · N actius». */
export function countApplied(filters: Filters): number {
  return Object.values(filters).filter((value) => value !== "" && value !== false).length;
}

/* -------------------------------------------------------------------------- */
/* Pestanyes: «Totes» (catàleg complet) i «Radar · prioritzades» (pre-fit)      */
/* -------------------------------------------------------------------------- */

export type CatalogueTab = "totes" | "revisar";

export const TAB_PARAM = "vista";

/**
 * Quina pestanya toca. `?vista=` mana; sense ell, `?match_status=` (que ve
 * d'«Avui») és la vista del pre-fit i la resta —inclosa la cerca `?q=`— és el
 * catàleg complet.
 */
export function tabFromParams(params: { get(name: string): string | null }): CatalogueTab {
  const explicit = params.get(TAB_PARAM);
  if (explicit === "revisar" || explicit === "totes") return explicit;
  return params.get("match_status") ? "revisar" : "totes";
}

/* -------------------------------------------------------------------------- */
/* «Totes»: contracte de consulta únic (spec 3B-1 §7)                           */
/* -------------------------------------------------------------------------- */

/**
 * «Totes» no porta mai `min_score` ni `match_status`: el pre-fit acompanya
 * cada fila però no decideix què hi surt ni en quin ordre.
 */
export const CATALOGUE_SORT_OPTIONS = [
  { value: "published", label: "Publicació més recent" },
  { value: "first_seen", label: "Vistes més recentment" },
  { value: "deadline", label: "Termini més proper" },
  { value: "amount", label: "Import més alt (VEC; si no n'hi ha, PBL)" },
  { value: "relevance", label: "Rellevància del text" },
];

export const DEFAULT_CATALOGUE_SORT = "published";

export const EMPTY_CATALOGUE_FILTERS = {
  q: "",
  cpv: "",
  buyer_q: "",
  nuts: "",
  place_q: "",
  min_amount: "",
  max_amount: "",
  published_from: "",
  published_to: "",
  deadline_from: "",
  deadline_to: "",
  contract_type: "",
  procedure_type: "",
  status: "",
  source: "",
  /** Tri-estat: "" (tot), "true" (només els que ja no hi són), "false" (només els vigents). */
  source_removed: "",
};

export type CatalogueFilters = typeof EMPTY_CATALOGUE_FILTERS;

/** Amb filtre d'import fixat, el servei diu quantes files queden fora per no tenir import. */
export function hasAmountFilter(filters: CatalogueFilters): boolean {
  return filters.min_amount !== "" || filters.max_amount !== "";
}

export function countCatalogueFilters(filters: CatalogueFilters): number {
  return Object.values(filters).filter((value) => value !== "").length;
}

/** `relevance` només té sentit amb text: sense `q` el servei ordena de manera neutral. */
export function catalogueSortOptions(filters: CatalogueFilters) {
  return filters.q.trim() ? CATALOGUE_SORT_OPTIONS : CATALOGUE_SORT_OPTIONS.filter((option) => option.value !== "relevance");
}

export function catalogueQuery({
  applied,
  sort = DEFAULT_CATALOGUE_SORT,
  offset,
}: {
  applied: CatalogueFilters;
  sort?: string;
  offset: number;
}): string {
  return buildQuery({
    mode: "all",
    q: applied.q.trim(),
    cpv: applied.cpv.trim(),
    buyer_q: applied.buyer_q.trim(),
    nuts: applied.nuts.trim(),
    place_q: applied.place_q.trim(),
    min_amount: applied.min_amount,
    max_amount: applied.max_amount,
    published_from: applied.published_from,
    published_to: applied.published_to,
    deadline_from: applied.deadline_from,
    // La data final és inclosa: el servei compara datetimes.
    deadline_to: applied.deadline_to ? `${applied.deadline_to}T23:59:59` : "",
    contract_type: applied.contract_type,
    procedure_type: applied.procedure_type,
    ...statusParams(applied.status),
    source: applied.source,
    source_removed: applied.source_removed,
    sort,
    limit: PAGE_SIZE,
    offset,
  });
}
