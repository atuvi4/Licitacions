"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  DEFAULT_CATALOGUE_SORT,
  EMPTY_CATALOGUE_FILTERS,
  PAGE_SIZE,
  catalogueQuery,
  catalogueSortOptions,
  countCatalogueFilters,
  type CatalogueFilters as Filters,
} from "@/app/(app)/licitacions/query";
import { CatalogueFilters, NUTS_INPUT_ID } from "@/components/catalogue/CatalogueFilters";
import { CatalogueRow } from "@/components/catalogue/CatalogueRow";
import { FilterChips, catalogueFilterChips } from "@/components/catalogue/FilterChips";
import { PreFitUncalculatedNotice } from "@/components/catalogue/PreFitSummary";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, ErrorState, LoadingRows, Select } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { RecalculationResult, TenderMatchListItem } from "@/lib/matching";
import type { CataloguePage, TenderFacets } from "@/lib/tenders";

const INGEST_COMMAND = "python -m app.ingestion.sync --source placsp_agregadas --max-pages 2";

/** La cerca s'aplica sola al cap d'aquest temps (addendum 3B-2, D1); Enter l'aplica a l'instant. */
export const SEARCH_DEBOUNCE_MS = 300;

/**
 * «Totes»: el catàleg públic complet, ordenat per publicació. El pre-fit
 * acompanya la fila si existeix, però no decideix què hi surt ni en quin
 * ordre; cap filtre està fixat fins que l'usuari el fixa.
 */
export function CatalogueView() {
  const { companyId } = useSession();
  const searchParams = useSearchParams();
  const urlQ = searchParams.get("q") ?? "";

  const [filters, setFilters] = useState<Filters>(() => ({ ...EMPTY_CATALOGUE_FILTERS, q: urlQ }));
  const [applied, setApplied] = useState<Filters>(() => ({ ...EMPTY_CATALOGUE_FILTERS, q: urlQ }));
  const [sort, setSort] = useState(DEFAULT_CATALOGUE_SORT);
  const [offset, setOffset] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  // «Filtra per NUTS» (C1): cada petició posa el focus al camp NUTS un cop obert el panell.
  const [nutsFocus, setNutsFocus] = useState(0);

  // La cerca global arriba per la URL; una segona cerca sense remuntar també s'aplica.
  useEffect(() => {
    if (!urlQ) return;
    setFilters((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setApplied((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setOffset(0);
  }, [urlQ]);

  // D1: el text de la cerca s'aplica sol al cap de 300 ms; la resta de filtres
  // esperen «Aplica». Enter (el formulari) ho aplica tot a l'instant.
  useEffect(() => {
    if (filters.q === applied.q) return;
    const q = filters.q;
    const timer = setTimeout(() => {
      setApplied((current) => (current.q === q ? current : { ...current, q }));
      setOffset(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q, applied.q]);

  useEffect(() => {
    if (nutsFocus === 0) return;
    document.getElementById(NUTS_INPUT_ID)?.focus();
  }, [nutsFocus]);

  // `relevance` només existeix amb text: si desapareix, tornem a l'ordre neutral.
  const sortOptions = catalogueSortOptions(applied);
  const effectiveSort = sortOptions.some((option) => option.value === sort) ? sort : DEFAULT_CATALOGUE_SORT;
  const query = useMemo(
    () => catalogueQuery({ applied, sort: effectiveSort, offset }),
    [applied, effectiveSort, offset],
  );

  const list = useResource<CataloguePage<TenderMatchListItem>>(
    companyId ? `/companies/${companyId}/matches?${query}` : null,
  );
  const facets = useResource<TenderFacets>(companyId ? `/companies/${companyId}/tenders/facets` : null);
  // El recàlcul que ja existeix, des de la franja «no s'ha calculat» (F3).
  const recalculation = useMutation();

  async function recalculate() {
    if (!companyId) return;
    const done = await recalculation.run(async () => {
      await api.post<RecalculationResult>(`/companies/${companyId}/matches/recalculate`, {});
    });
    if (done) await list.reload();
  }

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const appliedCount = countCatalogueFilters(applied);
  const withoutAmount = list.data?.excluded_without_amount ?? 0;
  const hasText = applied.q.trim() !== "";
  const chips = catalogueFilterChips(applied, facets.data);

  function apply() {
    setOffset(0);
    setApplied(filters);
  }

  function clear() {
    setFilters(EMPTY_CATALOGUE_FILTERS);
    setApplied(EMPTY_CATALOGUE_FILTERS);
    setOffset(0);
  }

  /** Treu només aquell filtre (C5), del formulari i de la consulta. */
  function removeFilter(key: string) {
    const empty = EMPTY_CATALOGUE_FILTERS[key as keyof Filters];
    setFilters((current) => ({ ...current, [key]: empty }));
    setApplied((current) => ({ ...current, [key]: empty }));
    setOffset(0);
  }

  /** «Filtra per NUTS» (C1): treu el lloc d'execució, obre els filtres i posa el focus al NUTS. */
  function filterByNuts() {
    removeFilter("place_q");
    setFiltersOpen(true);
    setNutsFocus((count) => count + 1);
  }

  function renderEmpty() {
    if (facets.data && facets.data.total_tenders === 0) {
      return (
        <EmptyState
          title="Encara no s'ha llegit cap licitació"
          description="En aquest entorn encara no s'ha ingerit cap font pública. Quan hi hagi licitacions, apareixeran aquí."
          action={
            <Button
              variant="primary"
              onClick={() => {
                void facets.reload();
                void list.reload();
              }}
            >
              Torna-ho a provar
            </Button>
          }
          secondary={
            <details className="text-left">
              <summary className="cursor-pointer text-caption text-muted">Detall tècnic</summary>
              <code className="mt-1 block font-mono text-[11.5px] text-muted">{INGEST_COMMAND}</code>
            </details>
          }
        />
      );
    }
    if (applied.place_q.trim() !== "") {
      // C1: la font (1044) sovint només publica el codi NUTS, no el municipi.
      return (
        <EmptyState
          title="Cap licitació amb aquest lloc d'execució"
          description="La cobertura d'aquest filtre és parcial: moltes licitacions de la font només indiquen el territori amb el codi NUTS, no el municipi ni la comarca. Prova de filtrar pel codi NUTS de la província o la comunitat."
          action={
            <Button variant="primary" onClick={filterByNuts}>
              Filtra per NUTS
            </Button>
          }
          secondary={
            <Button variant="secondary" onClick={clear}>
              Esborra els filtres
            </Button>
          }
        />
      );
    }
    return (
      <EmptyState
        title={
          appliedCount > 0
            ? `Cap licitació encaixa amb ${appliedCount === 1 ? "la cerca" : `les ${appliedCount} condicions`}`
            : "Cap licitació al catàleg"
        }
        description="Prova de treure'n alguna condició: el text, l'import i el territori solen ser les més restrictives."
        action={
          appliedCount > 0 ? (
            <Button variant="primary" onClick={clear}>
              Esborra els filtres
            </Button>
          ) : undefined
        }
      />
    );
  }

  const footerText =
    total === 1 ? `1 licitació · pàgina ${page} de ${pages}` : `${total} licitacions · pàgina ${page} de ${pages}`;

  return (
    <>
      <CatalogueFilters
        filters={filters}
        onChange={setFilters}
        onApply={apply}
        onClear={clear}
        facets={facets.data ?? null}
        open={filtersOpen}
        onOpenChange={setFiltersOpen}
      />

      <FilterChips chips={chips} onRemove={removeFilter} className="mb-4" />

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-subtle px-3.5 py-2">
          <p className="text-[13px] text-muted" aria-live="polite">
            {list.loading
              ? "Carregant…"
              : list.data
                ? total === 1
                  ? "1 licitació"
                  : `${total} licitacions`
                : null}
            {!list.loading && withoutAmount > 0 && (
              <span data-testid="without-amount">
                {" · "}
                {withoutAmount} sense import conegut no es mostren
              </span>
            )}
            {/* C6: amb text, l'ordre per rellevància és una opció explícita que no amaga res. */}
            {hasText && effectiveSort !== "relevance" && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-2"
                onClick={() => {
                  setSort("relevance");
                  setOffset(0);
                }}
              >
                Ordena per rellevància
              </Button>
            )}
            {effectiveSort === "relevance" && (
              <span className="ml-2 text-caption">
                Ordre per rellevància del text: només canvia l&apos;ordre, no amaga cap licitació.
              </span>
            )}
          </p>
          <div className="flex items-center gap-2">
            <label htmlFor="catalogue-sort" className="text-caption text-muted">
              Ordena per
            </label>
            <Select
              id="catalogue-sort"
              className="h-8 w-56"
              value={effectiveSort}
              options={sortOptions}
              onChange={(event) => {
                setSort(event.target.value);
                setOffset(0);
              }}
            />
          </div>
        </div>

        {list.error ? (
          <ErrorState
            title="No hem pogut carregar el catàleg"
            description="La connexió amb el servei ha fallat. No s'ha perdut res: cap filtre depèn d'aquesta càrrega."
            action={
              <Button variant="primary" onClick={() => void list.reload()}>
                Torna-ho a provar
              </Button>
            }
            technical={{ method: "GET /matches", code: list.error }}
          />
        ) : list.loading && items.length === 0 ? (
          <LoadingRows rows={5} />
        ) : items.length === 0 ? (
          renderEmpty()
        ) : (
          <>
            <PreFitUncalculatedNotice
              count={items.filter(({ match }) => !match).length}
              onCalculate={() => void recalculate()}
              calculating={recalculation.saving}
              error={recalculation.error}
            />
            <div role="list" aria-label="Licitacions del catàleg" aria-busy={list.loading}>
              {items.map(({ tender, match }) => (
                <div role="listitem" key={tender.id}>
                  <CatalogueRow tender={tender} match={match} sources={facets.data?.sources} cpvFilter={applied.cpv} />
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-subtle px-3.5 py-2 text-[13px] text-muted">
              <span>{footerText}</span>
              {pages > 1 && (
                <span className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  >
                    Anterior
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                  >
                    Següent
                  </Button>
                </span>
              )}
            </div>
          </>
        )}
      </Card>

      <p className="mt-3 text-caption text-faint">
        El catàleg mostra totes les licitacions ingerides, ordenades per publicació. El pre-fit, si existeix, només
        acompanya cada fila: no en decideix la presència ni l&apos;ordre.
      </p>
    </>
  );
}
