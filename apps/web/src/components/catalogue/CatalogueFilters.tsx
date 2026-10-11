"use client";

import { Search, SlidersHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import {
  countCatalogueFilters,
  tenderStatusFilterOptions,
  type CatalogueFilters as Filters,
} from "@/app/(app)/licitacions/query";
import { Button, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  buildQuery,
  labelOf,
  type TenderFacets,
} from "@/lib/tenders";

export const SEARCH_PLACEHOLDER = "Cerca per títol, expedient, organisme o descripció";

/** El camp NUTS, perquè «Filtra per NUTS» (C1) hi pugui posar el focus. */
export const NUTS_INPUT_ID = "catalogue-nuts";
/** Els codis NUTS presents al catàleg (C2): només codis, cap nom inventat. */
export const NUTS_LIST_ID = "catalogue-nuts-codes";

/** Una opció del cercador: el servei diu `code`; aquí es guarda com a `number` (és un número de CPV, no un codi intern). */
interface CpvOption {
  number: string;
  description: string;
}

/**
 * Codi o prefix CPV amb ajuda: escriure paraules consulta `/cpv-codes?q=` (codi
 * o paraula de la descripció del catàleg) i triar una opció posa el codi al
 * filtre; escriure dígits fixa directament un codi o un prefix (`4531` troba
 * tot el que hi comença). El filtre s'aplica SEMPRE pel codi (addendum 3B-2,
 * C3): el text sol no filtra res, i es diu. `onUnresolved` avisa el formulari
 * del text que no és cap codi ni cap opció triada, perquè en aplicar-lo ho
 * digui en lloc d'ignorar-lo en silenci (F16).
 */
export function CpvLookup({
  value,
  onChange,
  onUnresolved,
}: {
  value: string;
  onChange: (value: string) => void;
  onUnresolved?: (text: string | null) => void;
}) {
  const listId = useId();
  const [text, setText] = useState(value);
  const [options, setOptions] = useState<CpvOption[]>([]);
  const emitted = useRef(value);

  // Si el filtre canvia des de fora (Neteja), el camp el segueix.
  useEffect(() => {
    if (value !== emitted.current) {
      emitted.current = value;
      setText(value);
      setOptions([]);
    }
  }, [value]);

  useEffect(() => {
    const term = text.trim();
    if (term.length < 2 || term === value) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .get<Array<{ code: string; description: string }>>(`/cpv-codes?${buildQuery({ q: term, limit: 8 })}`)
        .then((found) => {
          if (!cancelled) setOptions(found.map((cpv) => ({ number: cpv.code, description: cpv.description })));
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [text, value]);

  function emit(next: string) {
    emitted.current = next;
    onChange(next);
  }

  // Text que no és cap codi i encara no s'ha resolt en un codi: no filtra.
  const typedWord = text.trim() !== "" && !/^[\d\s-]+$/.test(text.trim()) && text.trim() !== value;
  const unresolved = typedWord ? text.trim() : null;
  useEffect(() => {
    onUnresolved?.(unresolved);
  }, [unresolved, onUnresolved]);

  return (
    <div className="relative">
      <Field label="CPV" hint="Codi o prefix (4531…). Per trobar el codi al catàleg, escriu una part del codi o de la descripció (cerca «conté»); el filtre s'aplica sempre pel codi triat.">
        <Input
          value={text}
          placeholder="45314320"
          role="combobox"
          aria-expanded={options.length > 0}
          aria-controls={listId}
          autoComplete="off"
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            emit(/^[\d-]+$/.test(next.trim()) ? next.trim() : "");
          }}
        />
      </Field>
      {typedWord && (
        <p role="note" className="mt-1 text-caption text-warning">
          El text no filtra: tria un codi de la llista.
        </p>
      )}
      {options.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-60 w-full overflow-auto border border-border-strong bg-surface text-[13px] shadow-card"
        >
          {options.map((option) => (
            <li key={option.number} role="option" aria-selected={option.number === value}>
              <button
                type="button"
                className="flex min-h-11 w-full gap-2 px-3 py-1.5 text-left hover:bg-subtle md:min-h-0"
                onClick={() => {
                  setText(option.number);
                  setOptions([]);
                  emit(option.number);
                }}
              >
                <span className="font-medium tabular-nums">{option.number}</span>
                <span className="min-w-0 text-muted">{option.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * F16: en aplicar els filtres amb una paraula al CPV que no s'ha resolt en cap
 * codi, es diu que el CPV no s'ha aplicat (i no surt cap xip). Mai en silenci.
 */
export function CpvNotApplied({ text, className }: { text: string | null; className?: string }) {
  if (!text) return null;
  return (
    <p role="alert" className={`border border-warning/25 bg-warning-soft px-3.5 py-2 text-[13px] text-ink ${className ?? ""}`}>
      <strong className="font-semibold">El filtre de CPV no s&apos;ha aplicat:</strong> «{text}» no és cap codi CPV. Tria&apos;n
      un de la llista de suggeriments o escriu un codi o prefix (4531…).
    </p>
  );
}

const optionsOf = (values: string[] | undefined, labels: Record<string, string>, kind: string) =>
  (values ?? []).map((value) => ({
    value,
    label: labelOf(labels, value, kind),
  }));

/**
 * Cerca i filtres del catàleg. Cap filtre està fixat per defecte: «Totes»
 * mostra el catàleg sencer fins que l'usuari en fixa algun.
 */
export function CatalogueFilters({
  filters,
  onChange,
  onApply,
  onClear,
  facets,
  open,
  onOpenChange,
}: {
  filters: Filters;
  onChange: (filters: Filters) => void;
  onApply: () => void;
  onClear: () => void;
  facets: TenderFacets | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  // El text viu a la caixa de cerca; els altres filtres, plegats.
  const advancedCount = countCatalogueFilters({ ...filters, q: "" });
  const sources = (facets?.sources ?? []).map((source) => ({ value: source.code, label: source.name }));

  // F16: el text del CPV que no s'ha resolt en cap codi, i el que s'ha intentat aplicar.
  const [unresolvedCpv, setUnresolvedCpv] = useState<string | null>(null);
  const [rejectedCpv, setRejectedCpv] = useState<string | null>(null);
  useEffect(() => {
    if (unresolvedCpv === null) setRejectedCpv(null);
  }, [unresolvedCpv]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setRejectedCpv(unresolvedCpv);
    onApply();
  }

  return (
    <form onSubmit={submit} className="mb-4" role="search" aria-label="Cerca i filtres del catàleg">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="flex h-11 min-w-0 flex-1 items-center gap-2 border border-border-strong bg-surface px-3 focus-within:border-brand md:h-10">
          <Search size={16} strokeWidth={1.5} aria-hidden className="shrink-0 text-muted" />
          <input
            type="search"
            aria-label="Cerca al catàleg"
            value={filters.q}
            placeholder={SEARCH_PLACEHOLDER}
            onChange={(event) => set({ q: event.target.value })}
            className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-muted"
          />
        </div>
        <Button type="submit" variant="primary" className="min-h-11 md:min-h-0">
          Cerca
        </Button>
      </div>

      <details
        open={open}
        onToggle={(event) => onOpenChange(event.currentTarget.open)}
        className="mt-3 border border-border bg-surface"
      >
        <summary className="cursor-pointer list-none px-4 py-2.5 font-heading text-[13px] font-semibold tracking-[0.02em] text-ink [&::-webkit-details-marker]:hidden">
          <span className="inline-flex items-center gap-2">
            <SlidersHorizontal size={16} strokeWidth={1.5} aria-hidden="true" />
            {advancedCount === 0 ? "Filtres" : `Filtres · ${advancedCount} ${advancedCount === 1 ? "actiu" : "actius"}`}
          </span>
        </summary>
        <div className="border-t border-border px-4 py-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <CpvLookup value={filters.cpv} onChange={(cpv) => set({ cpv })} onUnresolved={setUnresolvedCpv} />
            <Field label="Organisme" hint="Part del nom">
              <Input value={filters.buyer_q} onChange={(event) => set({ buyer_q: event.target.value })} />
            </Field>
            <Field label="Territori (NUTS)" hint="Prefix: ES51, ES5… Es mostra el codi tal com el publica la font.">
              <Input
                id={NUTS_INPUT_ID}
                list={NUTS_LIST_ID}
                value={filters.nuts}
                placeholder="ES51"
                autoComplete="off"
                onChange={(event) => set({ nuts: event.target.value })}
              />
              <datalist id={NUTS_LIST_ID}>
                {(facets?.nuts_codes ?? []).map((code) => (
                  <option key={code} value={code} />
                ))}
              </datalist>
            </Field>
            <Field
              label="Lloc d'execució (cobertura parcial)"
              hint="Municipi o comarca. Moltes licitacions de la font només indiquen el codi NUTS, no el lloc."
            >
              <Input value={filters.place_q} onChange={(event) => set({ place_q: event.target.value })} />
            </Field>
            <Field label="Import mínim">
              <Input type="number" min={0} value={filters.min_amount} onChange={(event) => set({ min_amount: event.target.value })} />
            </Field>
            <Field label="Import màxim">
              <Input type="number" min={0} value={filters.max_amount} onChange={(event) => set({ max_amount: event.target.value })} />
            </Field>
            <Field label="Publicades des de">
              <Input type="date" value={filters.published_from} onChange={(event) => set({ published_from: event.target.value })} />
            </Field>
            <Field label="Publicades fins a">
              <Input type="date" value={filters.published_to} onChange={(event) => set({ published_to: event.target.value })} />
            </Field>
            <Field label="Termini des de">
              <Input type="date" value={filters.deadline_from} onChange={(event) => set({ deadline_from: event.target.value })} />
            </Field>
            <Field label="Termini fins a">
              <Input type="date" value={filters.deadline_to} onChange={(event) => set({ deadline_to: event.target.value })} />
            </Field>
            <Field label="Tipus de contracte">
              <Select
                value={filters.contract_type}
                placeholder="Tots"
                options={optionsOf(facets?.contract_types, CONTRACT_TYPE_LABELS, "Tipus desconegut")}
                onChange={(event) => set({ contract_type: event.target.value })}
              />
            </Field>
            <Field label="Procediment">
              <Select
                value={filters.procedure_type}
                placeholder="Tots"
                options={optionsOf(facets?.procedure_types, PROCEDURE_TYPE_LABELS, "Tipus desconegut")}
                onChange={(event) => set({ procedure_type: event.target.value })}
              />
            </Field>
            <Field label="Estat de la licitació" hint="«En termini»: publicada i amb el termini encara obert">
              <Select
                value={filters.status}
                placeholder="Tots"
                options={tenderStatusFilterOptions(facets?.statuses)}
                onChange={(event) => set({ status: event.target.value })}
              />
            </Field>
            <Field label="Font">
              <Select
                value={filters.source}
                placeholder="Totes"
                options={sources}
                onChange={(event) => set({ source: event.target.value })}
              />
            </Field>
            <Field label="Disponibilitat a la font">
              <Select
                value={filters.source_removed}
                placeholder="Indiferent"
                options={[
                  { value: "true", label: "Ja no disponible a la font" },
                  { value: "false", label: "Encara disponible a la font" },
                ]}
                onChange={(event) => set({ source_removed: event.target.value })}
              />
            </Field>
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClear}>
              Neteja
            </Button>
            <Button type="submit" variant="secondary">
              Aplica els filtres
            </Button>
          </div>
        </div>
      </details>
      <CpvNotApplied text={rejectedCpv} className="mt-3" />
    </form>
  );
}
