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
  { value: "all", label: "Totes", count: "total_tenders" },
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

/** `?match_status=` de la URL (cerca global, «Avui»): només un mode real; la resta és «Totes». */
export function modeFromParam(value: string | null): string {
  if (!value) return "all";
  return MODES.some((option) => option.value === value && option.status) ? value : "all";
}

/** Quants filtres tenen valor: el rètol «Filtres · N actius». */
export function countApplied(filters: Filters): number {
  return Object.values(filters).filter((value) => value !== "" && value !== false).length;
}
