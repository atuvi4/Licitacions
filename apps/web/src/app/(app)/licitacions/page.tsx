"use client";

import { SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { CatalogueTabs } from "@/components/catalogue/CatalogueTabs";
import { CatalogueView } from "@/components/catalogue/CatalogueView";
import { DeadlineCell } from "@/components/catalogue/CatalogueRow";
import { TendersHeader } from "@/components/catalogue/TendersHeader";
import { PreFitDisclaimer, ScoreValue } from "@/components/PreFit";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Card,
  ErrorState,
  Field,
  Input,
  LinkButton,
  LoadingRows,
  PageSkeleton,
  Select,
  StatusPill,
} from "@/components/ui";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { api } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { TENDER_STATUS_LEVEL } from "@/lib/labels";
import {
  MATCH_STATUS_HELP,
  MATCH_STATUS_LABELS,
  shortSummary,
  type MatchSummary,
  type RecalculationResult,
  type TenderMatchListItem,
} from "@/lib/matching";
import {
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  TENDER_STATUS_LABELS,
  labelOf,
  sourceLabel,
  type CataloguePage,
  type TenderFacets,
} from "@/lib/tenders";
import type { DashboardSummary } from "@/lib/types";

import {
  EMPTY_FILTERS,
  MODES,
  PAGE_SIZE,
  SORT_OPTIONS,
  activeMode,
  countApplied,
  feedQuery,
  modeFromParam,
  tabFromParams,
  type Filters,
} from "./query";

/**
 * Oportunitats (handoff L6, mockup 03): mateixes dades i mateixa consulta que
 * abans (`query.ts`), llegibles. Els modes són vistes, els filtres viuen
 * plegats, i cinc columnes responen «val la pena obrir-la?». El pre-fit no
 * amaga cap licitació ni és cap probabilitat de guanyar.
 */

const COLUMN_HEADERS = ["Encaix", "Licitació", "Termini", "Import", "Estat"];
const INGEST_COMMAND = "python -m app.ingestion.sync --source placsp_agregadas --max-pages 2";

function FitCell({ match }: { match: TenderMatchListItem["match"] }) {
  if (!match) return <span className="text-caption text-faint">Sense calcular</span>;
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <ScoreValue score={match.score} />
        {match.is_stale && (
          <StatusPill level="attention" size="sm">
            Desactualitzat
          </StatusPill>
        )}
      </div>
      <p className="text-caption text-muted">{shortSummary(match)}</p>
      {match.status !== "candidate" && (
        <p className="text-caption text-faint">{MATCH_STATUS_LABELS[match.status]}</p>
      )}
    </div>
  );
}

function ReviewFeed() {
  const { companyId } = useSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlQ = searchParams.get("q") ?? "";
  const urlMode = modeFromParam(searchParams.get("match_status"));

  const [filters, setFilters] = useState<Filters>(() => ({ ...EMPTY_FILTERS, q: urlQ }));
  const [applied, setApplied] = useState<Filters>(() => ({ ...EMPTY_FILTERS, q: urlQ }));
  const [mode, setMode] = useState(urlMode);
  const [sort, setSort] = useState("score");
  const [offset, setOffset] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(urlQ !== "");
  const recalculation = useMutation();

  // La cerca global i «Avui» arriben per la URL. Si la URL canvia sense
  // remuntar la pàgina (una segona cerca), el filtre s'aplica igualment.
  useEffect(() => {
    if (!urlQ) return;
    setFilters((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setApplied((current) => (current.q === urlQ ? current : { ...current, q: urlQ }));
    setOffset(0);
    setFiltersOpen(true);
  }, [urlQ]);
  useEffect(() => {
    setMode(urlMode);
    setOffset(0);
  }, [urlMode]);

  const active = activeMode(mode);
  const query = useMemo(() => feedQuery({ applied, mode, sort, offset }), [applied, mode, sort, offset]);

  // El feed sempre porta el pre-fit al costat: és el mateix catàleg públic,
  // però llegit des del perfil de l'empresa. Els criteris de cerca són
  // exactament els mateixos que a `/tenders`.
  const list = useResource<CataloguePage<TenderMatchListItem>>(
    companyId ? `/companies/${companyId}/matches?${query}` : null,
  );
  const facets = useResource<TenderFacets>(
    companyId ? `/companies/${companyId}/tenders/facets` : null,
  );
  const summary = useResource<MatchSummary>(
    companyId ? `/companies/${companyId}/matches/summary` : null,
  );
  // Només per distingir el buit B1 (perfil sense CPV) del «cap candidata».
  const profile = useResource<DashboardSummary>(companyId ? `/companies/${companyId}/summary` : null);

  async function recalculate() {
    if (!companyId) return;
    const done = await recalculation.run(async () => {
      await api.post<RecalculationResult>(`/companies/${companyId}/matches/recalculate`, {});
    });
    if (done) {
      await list.reload();
      await summary.reload();
    }
  }

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const appliedCount = countApplied(applied);
  const openCount = summary.data?.actionable ?? null;

  function apply(event: React.FormEvent) {
    event.preventDefault();
    setOffset(0);
    setApplied(filters);
  }

  function clear() {
    setFilters(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setOffset(0);
  }

  function selectMode(value: string) {
    setMode(value);
    setOffset(0);
  }

  /** «Mira les N obertes»: la vista «Totes» amb el filtre de termini obert visible, no amagat. */
  function showOpen() {
    const next = { ...EMPTY_FILTERS, only_open: true };
    setFilters(next);
    setApplied(next);
    setOffset(0);
    setMode("all");
    setFiltersOpen(true);
  }

  const options = (values: string[] | undefined, labels: Record<string, string>) =>
    (values ?? []).map((value) => ({ value, label: labels[value] ?? value }));

  if (!list.data && list.loading && !list.error) {
    return (
      <PageSkeleton
        variant="table"
        title="Oportunitats"
        context="carregant les licitacions…"
        columns={COLUMN_HEADERS}
      />
    );
  }

  const columns: Array<DataTableColumn<TenderMatchListItem>> = [
    { key: "fit", header: "Encaix", width: "13%", cell: ({ match }) => <FitCell match={match} /> },
    {
      key: "tender",
      header: "Licitació",
      cell: ({ tender }) => (
        <div className="min-w-0">
          <Link
            href={`/licitacions/${tender.id}`}
            onClick={(event) => event.stopPropagation()}
            className="font-medium text-ink hover:text-brand-strong"
          >
            {tender.title}
          </Link>
          <p className="text-caption text-muted">
            {[tender.expediente_number, tender.buyer?.name].filter(Boolean).join(" · ") || "—"}
          </p>
          <p className="text-caption text-muted">
            {labelOf(PROCEDURE_TYPE_LABELS, tender.procedure_type, "Tipus desconegut")} ·{" "}
            {labelOf(CONTRACT_TYPE_LABELS, tender.contract_type, "Tipus desconegut")}
            {tender.lot_count > 0 && ` · ${tender.lot_count} ${tender.lot_count === 1 ? "lot" : "lots"}`}
          </p>
        </div>
      ),
    },
    { key: "deadline", header: "Termini", width: "15%", cell: ({ tender }) => <DeadlineCell value={tender.deadline_at} /> },
    {
      key: "amount",
      header: "Import",
      width: "12%",
      align: "right",
      cell: ({ tender }) => (
        <span className="whitespace-nowrap tabular-nums">
          {formatMoney(tender.estimated_value ?? tender.base_budget_without_tax ?? tender.base_budget, tender.currency)}
        </span>
      ),
    },
    {
      key: "status",
      header: "Estat",
      width: "14%",
      cell: ({ tender }) => (
        <div className="flex flex-col items-start gap-1">
          <StatusPill
            level={Object.hasOwn(TENDER_STATUS_LEVEL, tender.status) ? TENDER_STATUS_LEVEL[tender.status] : "neutral"}
            size="sm"
          >
            {labelOf(TENDER_STATUS_LABELS, tender.status, "Estat desconegut")}
          </StatusPill>
          {tender.is_modified && tender.source_publication_count > 1 && (
            <StatusPill level="attention" size="sm">
              Publicada {tender.source_publication_count} cops
            </StatusPill>
          )}
        </div>
      ),
    },
  ];

  function renderEmpty() {
    if (facets.data && facets.data.total_tenders === 0) {
      return (
        <EmptyState
          title="Encara no s'ha llegit cap licitació"
          description="En aquest entorn encara no s'ha ingerit la font pública. Quan hi hagi licitacions, apareixeran aquí amb el pre-fit calculat."
          action={
            <Button variant="primary" onClick={() => { void facets.reload(); void list.reload(); }}>
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
    if (appliedCount > 0) {
      return (
        <EmptyState
          title={`Cap licitació encaixa amb ${appliedCount === 1 ? "el filtre" : `els ${appliedCount} filtres`}`}
          description="Prova de treure'n algun: el text i l'import solen ser els més restrictius."
          action={
            <Button variant="primary" onClick={clear}>
              Esborra els filtres
            </Button>
          }
          secondary={
            <Button variant="secondary" onClick={() => setFiltersOpen(true)}>
              Revisa els filtres
            </Button>
          }
        />
      );
    }
    if (mode === "candidate") {
      const openLabel = openCount ? `Mira les ${openCount} obertes` : "Mira totes les licitacions";
      if (profile.data && profile.data.counts.cpv_preferences === 0) {
        return (
          <EmptyState
            title="Encara no sabem què busqueu"
            description={`L'encaix es calcula amb els codis CPV, els territoris i la franja d'import del perfil. Sense això no podem ordenar les ${openCount ? `${openCount} ` : ""}licitacions obertes: només llistar-les.`}
            action={
              <LinkButton href="/empresa" variant="primary" size="md">
                Completa el perfil · 3 passos
              </LinkButton>
            }
            secondary={
              <Button variant="secondary" onClick={showOpen}>
                {openLabel}
              </Button>
            }
          />
        );
      }
      return (
        <EmptyState
          title="Cap candidata ara mateix"
          description="Cap de les licitacions ingerides encaixa prou amb el perfil declarat. Zero candidates és una resposta legítima, no un error: podeu ampliar els CPV o els territoris al perfil, o revisar la vista «Encaix baix»."
          action={
            <Button variant="primary" onClick={showOpen}>
              {openLabel}
            </Button>
          }
          secondary={
            <Button variant="secondary" onClick={() => selectMode("low_fit")}>
              Veure l&apos;encaix baix
            </Button>
          }
        />
      );
    }
    return (
      <EmptyState
        title="Cap licitació en aquesta vista"
        description={active.status ? MATCH_STATUS_HELP[active.status] : "No hi ha cap licitació ingerida."}
        action={
          <Button variant="primary" onClick={() => selectMode("all")}>
            Veure totes les licitacions
          </Button>
        }
      />
    );
  }

  const footerText =
    total === 1 ? `1 licitació · pàgina ${page} de ${pages}` : `${total} licitacions · pàgina ${page} de ${pages}`;

  return (
    <>
      <TendersHeader
        onImported={() => {
          void list.reload();
          void summary.reload();
        }}
      />
      <CatalogueTabs value="revisar" className="mb-4" />

      {recalculation.error ? (
        <Banner
          level="blocking"
          kicker="Recàlcul"
          className="mb-4"
          action={
            <Button size="sm" variant="secondary" loading={recalculation.saving} onClick={() => void recalculate()}>
              Torna-ho a provar
            </Button>
          }
        >
          {recalculation.error}
        </Banner>
      ) : summary.data && summary.data.stale > 0 ? (
        <Banner
          level="attention"
          kicker="Pre-fit"
          className="mb-4"
          action={
            <Button size="sm" variant="primary" loading={recalculation.saving} onClick={() => void recalculate()}>
              Recalcula
            </Button>
          }
        >
          {summary.data.stale} pre-fits desactualitzats: el perfil o les licitacions han canviat des de
          l&apos;últim càlcul.
        </Banner>
      ) : null}

      <div className="mb-1 flex flex-wrap items-center gap-2" role="group" aria-label="Vistes">
        {MODES.filter((option) => option.value !== "all").map((option) => {
          const pressed = option.value === mode;
          const count = summary.data ? summary.data[option.count] : null;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={pressed}
              onClick={() => selectMode(option.value)}
              className="flex min-h-11 items-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:min-h-0"
            >
              <StatusPill level={pressed ? "action" : "neutral"} outline>
                {option.label}
                {count !== null && <span className="tabular-nums">{String(count)}</span>}
              </StatusPill>
            </button>
          );
        })}
        <Button
          size="sm"
          variant="secondary"
          loading={recalculation.saving}
          onClick={() => void recalculate()}
          className="ml-auto"
        >
          Recalcula el pre-fit
        </Button>
      </div>

      <p className="mb-4 text-caption text-muted">
        {active.status
          ? MATCH_STATUS_HELP[active.status]
          : "Totes les licitacions ingerides, amb el pre-fit al costat. El pre-fit no n'amaga cap."}
      </p>

      <details
        open={filtersOpen}
        onToggle={(event) => setFiltersOpen(event.currentTarget.open)}
        className="mb-4 border border-border bg-surface"
      >
        <summary className="cursor-pointer list-none px-4 py-2.5 font-heading text-[13px] font-semibold tracking-[0.02em] text-ink [&::-webkit-details-marker]:hidden">
          <span className="inline-flex items-center gap-2">
            <SlidersHorizontal size={16} strokeWidth={1.5} aria-hidden="true" />
            {appliedCount === 0
              ? "Filtres"
              : `Filtres · ${appliedCount} ${appliedCount === 1 ? "actiu" : "actius"}`}
          </span>
        </summary>
        <form onSubmit={apply} className="border-t border-border px-4 py-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Text" className="lg:col-span-2">
              <Input
                value={filters.q}
                placeholder="Títol o número d'expedient"
                onChange={(event) => setFilters({ ...filters, q: event.target.value })}
              />
            </Field>
            <Field label="CPV">
              <Input
                value={filters.cpv}
                placeholder="45314320"
                onChange={(event) => setFilters({ ...filters, cpv: event.target.value })}
              />
            </Field>
            <Field label="NUTS" hint="Prefix: ES51, ES5…">
              <Input
                value={filters.nuts}
                placeholder="ES51"
                onChange={(event) => setFilters({ ...filters, nuts: event.target.value })}
              />
            </Field>
            <Field label="Estat de la licitació" hint="Segons la font, no el pre-fit">
              <Select
                value={filters.status}
                placeholder="Tots"
                options={options(facets.data?.statuses, TENDER_STATUS_LABELS)}
                onChange={(event) => setFilters({ ...filters, status: event.target.value })}
              />
            </Field>
            <Field label="Procediment">
              <Select
                value={filters.procedure_type}
                placeholder="Tots"
                options={options(facets.data?.procedure_types, PROCEDURE_TYPE_LABELS)}
                onChange={(event) =>
                  setFilters({ ...filters, procedure_type: event.target.value })
                }
              />
            </Field>
            <Field label="Tipus de contracte">
              <Select
                value={filters.contract_type}
                placeholder="Tots"
                options={options(facets.data?.contract_types, CONTRACT_TYPE_LABELS)}
                onChange={(event) =>
                  setFilters({ ...filters, contract_type: event.target.value })
                }
              />
            </Field>
            <Field label="Lots">
              <Select
                value={filters.has_lots}
                placeholder="Indiferent"
                options={[
                  { value: "true", label: "Amb lots" },
                  { value: "false", label: "Sense lots" },
                ]}
                onChange={(event) => setFilters({ ...filters, has_lots: event.target.value })}
              />
            </Field>
            <Field label="Import mínim">
              <Input
                type="number"
                min={0}
                value={filters.min_amount}
                onChange={(event) => setFilters({ ...filters, min_amount: event.target.value })}
              />
            </Field>
            <Field label="Import màxim">
              <Input
                type="number"
                min={0}
                value={filters.max_amount}
                onChange={(event) => setFilters({ ...filters, max_amount: event.target.value })}
              />
            </Field>
            <Field label="Publicades des de">
              <Input
                type="date"
                value={filters.published_from}
                onChange={(event) =>
                  setFilters({ ...filters, published_from: event.target.value })
                }
              />
            </Field>
            <Field label="Termini fins a">
              <Input
                type="date"
                value={filters.deadline_to}
                onChange={(event) => setFilters({ ...filters, deadline_to: event.target.value })}
              />
            </Field>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={filters.only_open}
                onChange={(event) =>
                  setFilters({ ...filters, only_open: event.target.checked })
                }
                className="h-4 w-4 border-border-strong accent-brand"
              />
              Només amb termini obert
            </label>
            <div className="flex items-center gap-2">
              <Button type="button" variant="ghost" onClick={clear}>
                Neteja
              </Button>
              <Button type="submit" variant="primary">
                Aplica els filtres
              </Button>
            </div>
          </div>
        </form>
      </details>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-subtle px-3.5 py-2">
          <p className="text-[13px] text-muted" aria-live="polite">
            {list.loading ? "Carregant…" : null}
          </p>
          <div className="flex items-center gap-2">
            <label htmlFor="feed-sort" className="text-caption text-muted">
              Ordena per
            </label>
            <Select
              id="feed-sort"
              className="h-8 w-56"
              value={sort}
              options={SORT_OPTIONS}
              onChange={(event) => {
                setSort(event.target.value);
                setOffset(0);
              }}
            />
          </div>
        </div>

        {list.error ? (
          <ErrorState
            title="No hem pogut carregar les oportunitats"
            description="La connexió amb el servei ha fallat. No s'ha perdut res: cap filtre ni cap vista depèn d'aquesta càrrega."
            action={
              <Button variant="primary" onClick={() => void list.reload()}>
                Torna-ho a provar
              </Button>
            }
            technical={{ method: "GET /matches", code: list.error }}
          />
        ) : list.loading ? (
          // En recarregar (vista, ordre, pàgina) les columnes reals es queden; només la dada és fantasma.
          <DataTable<TenderMatchListItem>
            columns={columns}
            rows={[]}
            rowKey={(row) => row.tender.id}
            stackBelow="md"
            emptyState={<LoadingRows rows={5} />}
          />
        ) : items.length === 0 ? (
          renderEmpty()
        ) : (
          <DataTable<TenderMatchListItem>
            columns={columns}
            rows={items}
            rowKey={(row) => row.tender.id}
            onRowClick={(row) => router.push(`/licitacions/${row.tender.id}`)}
            stackBelow="md"
            footer={
              <>
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
              </>
            }
          />
        )}
      </Card>

      <PreFitDisclaimer className="mt-3" />
      <p className="mt-2 text-caption text-faint">
        El pre-fit compara metadades (CPV, territori, import i terminis) amb el vostre perfil
        d&apos;empresa. No amaga cap licitació: la vista «Totes» les mostra igualment.
        {summary.data && ` · Regles: ${summary.data.rules_version}`}
        {items.length > 0 && items[0].tender.source_code && ` · Font: ${sourceLabel(items[0].tender.source_code, facets.data?.sources)}`}
      </p>
    </>
  );
}

function TendersPage() {
  const searchParams = useSearchParams();
  const tab = tabFromParams(searchParams);
  const [reloadKey, setReloadKey] = useState(0);
  if (tab === "revisar") return <ReviewFeed />;
  return (
    <>
      <TendersHeader onImported={() => setReloadKey((key) => key + 1)} />
      <CatalogueTabs value="totes" className="mb-4" />
      <CatalogueView key={reloadKey} />
    </>
  );
}

export default function TendersFeedPage() {
  return (
    <Suspense fallback={<PageSkeleton variant="table" title="Oportunitats" columns={COLUMN_HEADERS} />}>
      <TendersPage />
    </Suspense>
  );
}
