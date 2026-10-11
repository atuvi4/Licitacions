"use client";

import { X } from "lucide-react";

import { IN_TERM_STATUS, type CatalogueFilters, type Filters } from "@/app/(app)/licitacions/query";
import { formatDate, formatMoney } from "@/lib/format";
import { IN_TERM_LABEL } from "@/lib/labels";
import {
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  TENDER_STATUS_LABELS,
  labelOf,
  sourceLabel,
  type TenderFacets,
} from "@/lib/tenders";

/**
 * Xips dels filtres actius (addendum 3B-2, C5): un per filtre fixat, amb el
 * valor en paraules (mai un codi cru) i una «×» que treu només aquell filtre.
 * Els rètols són els mateixos que els camps del formulari.
 */

export interface FilterChip {
  key: string;
  label: string;
}

const quoted = (value: string) => `«${value.trim()}»`;

/** Els rètols comuns als dos feeds; el text del catàleg i el del feed es diuen igual. */
function chipLabels(facets: TenderFacets | null | undefined): Record<string, (value: string) => string> {
  return {
    q: (value) => `Text: ${quoted(value)}`,
    cpv: (value) => `CPV: ${value.trim()}`,
    buyer_q: (value) => `Organisme: ${quoted(value)}`,
    nuts: (value) => `NUTS: ${value.trim().toUpperCase()}`,
    place_q: (value) => `Lloc d'execució: ${quoted(value)}`,
    min_amount: (value) => `Import mínim: ${formatMoney(value)}`,
    max_amount: (value) => `Import màxim: ${formatMoney(value)}`,
    published_from: (value) => `Publicades des de: ${formatDate(value)}`,
    published_to: (value) => `Publicades fins a: ${formatDate(value)}`,
    deadline_from: (value) => `Termini des de: ${formatDate(value)}`,
    deadline_to: (value) => `Termini fins a: ${formatDate(value)}`,
    contract_type: (value) => `Tipus de contracte: ${labelOf(CONTRACT_TYPE_LABELS, value, "Tipus desconegut")}`,
    procedure_type: (value) => `Procediment: ${labelOf(PROCEDURE_TYPE_LABELS, value, "Tipus desconegut")}`,
    // F2: «En termini» és l'estat accionable; la resta són estats crus de la font.
    status: (value) =>
      value === IN_TERM_STATUS
        ? `Estat: ${IN_TERM_LABEL}`
        : `Estat a la font: ${labelOf(TENDER_STATUS_LABELS, value, "Estat desconegut")}`,
    source: (value) => `Font: ${sourceLabel(value, facets?.sources) ?? value}`,
    source_removed: (value) => (value === "true" ? "Ja no disponible a la font" : "Encara disponible a la font"),
    has_lots: (value) => (value === "true" ? "Amb lots" : "Sense lots"),
    // C3: no és l'estat «En termini»; només mira el termini.
    only_open: () => "Termini obert (qualsevol estat de la font)",
  };
}

function chipsOf(filters: Record<string, string | boolean>, facets?: TenderFacets | null): FilterChip[] {
  const labels = chipLabels(facets);
  const chips: FilterChip[] = [];
  for (const [key, value] of Object.entries(filters)) {
    if (value === "" || value === false) continue;
    const render = labels[key];
    chips.push({ key, label: render ? render(String(value)) : `${key}: ${String(value)}` });
  }
  return chips;
}

/** Els filtres fixats de «Totes», en l'ordre del formulari. */
export function catalogueFilterChips(filters: CatalogueFilters, facets?: TenderFacets | null): FilterChip[] {
  return chipsOf(filters, facets);
}

/** Els filtres fixats de la vista del Radar (el feed amb pre-fit). */
export function feedFilterChips(filters: Filters, facets?: TenderFacets | null): FilterChip[] {
  return chipsOf(filters, facets);
}

export function FilterChips({
  chips,
  onRemove,
  className,
}: {
  chips: FilterChip[];
  onRemove: (key: string) => void;
  className?: string;
}) {
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Filtres actius" className={["flex flex-wrap items-center gap-2", className].filter(Boolean).join(" ")}>
      {chips.map((chip) => (
        <li key={chip.key}>
          <button
            type="button"
            aria-label={`Treu el filtre ${chip.label}`}
            onClick={() => onRemove(chip.key)}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-pill border border-border-strong bg-surface px-2.5 py-1 text-[12.5px] text-ink hover:border-brand hover:text-brand-strong md:min-h-0"
          >
            <span>{chip.label}</span>
            <X size={13} strokeWidth={1.75} aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  );
}
