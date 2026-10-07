import type { MatchStatus, MatchSummary } from "@/lib/matching";
import { buildQuery } from "@/lib/tenders";

/**
 * La consulta del feed d'Oportunitats, extreta de la pàgina (handoff L6) per
 * poder demostrar que el redisseny no en canvia ni un paràmetre: mateixos
 * modes, mateixos ordres, mateixos filtres, mateixa paginació.
 */

export const PAGE_SIZE = 25;

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
  // «Per revisar» és l'etiqueta del mockup 03 per al mode `candidate` (B).
  { value: "candidate", label: "Per revisar", status: "candidate", count: "candidates" },
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
  { value: "amount", label: "Import més alt" },
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
    has_lots: applied.has_lots || undefined,
    only_open: applied.only_open || undefined,
    match_status: activeMode(mode).status,
    sort,
    limit: PAGE_SIZE,
    offset,
  });
}

/** `?match_status=` de la URL (cerca global, «Avui»): només un mode real; la resta, «Per revisar». */
export function modeFromParam(value: string | null): string {
  if (!value) return "candidate";
  return MODES.some((option) => option.value === value && option.status) ? value : "candidate";
}

/** Quants filtres tenen valor: el rètol «Filtres · N actius». */
export function countApplied(filters: Filters): number {
  return Object.values(filters).filter((value) => value !== "" && value !== false).length;
}

/* -------------------------------------------------------------------------- */
/* Pestanyes: «Totes» (catàleg complet) i «Per revisar» (vista del pre-fit)     */
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
  { value: "amount", label: "Import més alt" },
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
    status: applied.status,
    source: applied.source,
    source_removed: applied.source_removed,
    sort,
    limit: PAGE_SIZE,
    offset,
  });
}
