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
import { CatalogueFilters } from "@/components/catalogue/CatalogueFilters";
import { CatalogueRow } from "@/components/catalogue/CatalogueRow";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, ErrorState, LoadingRows, Select } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { useResource } from "@/lib/hooks";
import type { TenderMatchListItem } from "@/lib/matching";
import type { CataloguePage, TenderFacets } from "@/lib/tenders";

const INGEST_COMMAND = "python -m app.ingestion.sync --source placsp_agregadas --max-pages 2";

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

  // La cerca global arriba per la URL; una segona cerca sense remuntar també s'aplica.
  useEffect(() => {
    if (!urlQ) return;
    setFilters((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setApplied((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setOffset(0);
  }, [urlQ]);

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

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const appliedCount = countCatalogueFilters(applied);
  const withoutAmount = list.data?.excluded_without_amount ?? 0;

  function apply() {
    setOffset(0);
    setApplied(filters);
  }

  function clear() {
    setFilters(EMPTY_CATALOGUE_FILTERS);
    setApplied(EMPTY_CATALOGUE_FILTERS);
    setOffset(0);
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
            <div role="list" aria-label="Licitacions del catàleg" aria-busy={list.loading}>
              {items.map(({ tender, match }) => (
                <div role="listitem" key={tender.id}>
                  <CatalogueRow tender={tender} match={match} sources={facets.data?.sources} />
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
