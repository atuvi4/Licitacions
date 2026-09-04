"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Select,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { PreFitBadge, PreFitDisclaimer, PreFitSummary } from "@/components/PreFit";
import { formatDate, formatMoney } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { api } from "@/lib/api";
import { useSession } from "@/components/SessionProvider";
import {
  MATCH_STATUS_HELP,
  type MatchStatus,
  type MatchSummary,
  type RecalculationResult,
  type TenderMatchListItem,
} from "@/lib/matching";
import {
  buildQuery,
  CONTRACT_TYPE_LABELS,
  daysUntilDeadline,
  PROCEDURE_TYPE_LABELS,
  TENDER_STATUS_LABELS,
  TENDER_STATUS_TONE,
  type Page,
  type TenderFacets,
} from "@/lib/tenders";

const PAGE_SIZE = 25;

/**
 * Modes del feed.
 *
 * El mateix cataleg vist des de quatre angles. "Totes" no filtra res: el
 * pre-fit acompanya cada fila però no amaga cap licitació — decidir què no
 * val la pena mirar continua sent de l'usuari.
 */
type Mode = {
  value: string;
  label: string;
  status?: MatchStatus;
  count: keyof MatchSummary;
};

const MODES: Mode[] = [
  { value: "all", label: "Totes", count: "total_tenders" },
  { value: "candidate", label: "Candidates", status: "candidate", count: "candidates" },
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

const SORT_OPTIONS = [
  { value: "score", label: "Millor pre-fit" },
  { value: "recent", label: "Vistes més recentment" },
  { value: "published", label: "Publicació més recent" },
  { value: "deadline", label: "Termini més proper" },
  { value: "amount", label: "Import més alt" },
];

const EMPTY_FILTERS = {
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

type Filters = typeof EMPTY_FILTERS;

function DeadlineCell({ value }: { value: string | null }) {
  const days = daysUntilDeadline(value);
  if (!value || days === null) return <span className="text-faint">—</span>;
  const tone =
    days < 0 ? "text-faint" : days <= 7 ? "text-danger font-medium" : days <= 21 ? "text-warning" : "text-ink";
  return (
    <div className="whitespace-nowrap">
      <span className={tone}>{formatDate(value)}</span>
      <span className="ml-2 text-[12px] text-faint">
        {days < 0 ? "tancat" : days === 0 ? "avui" : `${days} d`}
      </span>
    </div>
  );
}

export default function TendersFeedPage() {
  const { companyId } = useSession();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [mode, setMode] = useState("all");
  const [sort, setSort] = useState("score");
  const [offset, setOffset] = useState(0);
  const recalculation = useMutation();

  const activeMode = MODES.find((option) => option.value === mode) ?? MODES[0];

  const query = useMemo(
    () =>
      buildQuery({
        ...applied,
        has_lots: applied.has_lots || undefined,
        only_open: applied.only_open || undefined,
        match_status: activeMode.status,
        sort,
        limit: PAGE_SIZE,
        offset,
      }),
    [applied, activeMode.status, sort, offset],
  );

  // El feed sempre porta el pre-fit al costat: es el mateix cataleg public,
  // pero llegit des del perfil de l'empresa. Els criteris de cerca son
  // exactament els mateixos que a `/tenders`.
  const { data, loading, error, reload } = useResource<Page<TenderMatchListItem>>(
    companyId ? `/companies/${companyId}/matches?${query}` : null,
  );
  const facets = useResource<TenderFacets>("/tenders/facets");
  const summary = useResource<MatchSummary>(
    companyId ? `/companies/${companyId}/matches/summary` : null,
  );

  async function recalculate() {
    if (!companyId) return;
    const done = await recalculation.run(async () => {
      await api.post<RecalculationResult>(`/companies/${companyId}/matches/recalculate`, {});
    });
    if (done) {
      await reload();
      await summary.reload();
    }
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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

  const options = (values: string[] | undefined, labels: Record<string, string>) =>
    (values ?? []).map((value) => ({ value, label: labels[value] ?? value }));

  return (
    <>
      <PageHeader
        title="Oportunitats"
        description="Licitacions publicades a la Plataforma de Contratación del Sector Público, amb el pre-fit calculat sobre el vostre perfil."
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}
      {recalculation.error && <ErrorNotice message={recalculation.error} />}

      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                setMode(option.value);
                setOffset(0);
              }}
              className={
                option.value === mode
                  ? "rounded-full bg-brand px-3 py-1.5 text-[13px] font-medium text-white"
                  : "rounded-full border border-border px-3 py-1.5 text-[13px] text-muted hover:border-border-strong hover:text-ink"
              }
            >
              {option.label}
              {summary.data && (
                <span className="ml-1.5 tabular-nums opacity-70">
                  {String(summary.data[option.count] ?? 0)}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {summary.data?.stale ? (
            <span className="text-[12px] text-warning">
              {summary.data.stale} pre-fits desactualitzats
            </span>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            disabled={recalculation.saving}
            onClick={() => void recalculate()}
          >
            {recalculation.saving ? "Recalculant…" : "Recalcula el pre-fit"}
          </Button>
        </div>
      </div>

      <p className="mb-4 text-[12px] text-muted">
        {activeMode.status
          ? MATCH_STATUS_HELP[activeMode.status]
          : "Totes les licitacions ingerides, amb el pre-fit al costat. El pre-fit no n'amaga cap."}
      </p>

      <Card className="mb-4">
        <form onSubmit={apply} className="px-5 py-4">
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
                className="h-4 w-4 rounded border-border-strong accent-brand"
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
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
          <p className="text-[13px] text-muted">
            {loading ? "Carregant…" : `${total} licitacions`}
            {facets.data && total !== facets.data.total_tenders && (
              <span className="text-faint"> de {facets.data.total_tenders} en total</span>
            )}
          </p>
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-muted">Ordena per</span>
            <Select
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

        {loading ? (
          <LoadingRows rows={5} />
        ) : items.length === 0 ? (
          <EmptyState
            title={mode === "candidate" ? "Cap candidata ara mateix" : "Cap licitació"}
            description={
              total === 0 && facets.data?.total_tenders === 0
                ? "Encara no s'ha executat cap ingesta. Llança-la amb: python -m app.ingestion.placsp --limit 100"
                : mode === "candidate"
                  ? // Zero candidates es una resposta legitima, no un error: val mes
                    // dir-ho clar que rebaixar el llindar per omplir la pantalla.
                    "Cap de les licitacions ingerides encaixa prou amb el perfil declarat. Podeu ampliar els CPV o els territoris al perfil d'empresa, o revisar el mode «Encaix baix»."
                  : "Cap resultat amb aquests filtres."
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-[130px]">Pre-fit</Th>
                <Th>Licitació</Th>
                <Th>Organisme</Th>
                <Th>Import</Th>
                <Th>Publicació</Th>
                <Th>Termini</Th>
                <Th>Estat</Th>
              </tr>
            </thead>
            <tbody>
              {items.map(({ tender, match }) => (
                <Tr key={tender.id}>
                  <Td className="w-[130px] align-top">
                    <PreFitBadge match={match} />
                  </Td>
                  <Td className="min-w-[280px]">
                    <Link
                      href={`/licitacions/${tender.id}`}
                      className="font-medium text-ink hover:text-brand"
                    >
                      {tender.title}
                    </Link>
                    <p className="mt-0.5 text-[12px] text-muted">
                      {tender.expediente_number && (
                        <span className="font-mono">{tender.expediente_number}</span>
                      )}
                      {tender.expediente_number && " · "}
                      {PROCEDURE_TYPE_LABELS[tender.procedure_type]} ·{" "}
                      {CONTRACT_TYPE_LABELS[tender.contract_type]}
                      {tender.lot_count > 0 && ` · ${tender.lot_count} lots`}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {tender.cpvs.slice(0, 3).map((cpv) => (
                        <span
                          key={cpv.cpv_code}
                          title={cpv.description ?? undefined}
                          className={
                            cpv.is_main
                              ? "rounded bg-brand-soft px-1.5 py-0.5 font-mono text-[11px] text-brand-strong"
                              : "rounded bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-muted"
                          }
                        >
                          {cpv.cpv_code}
                        </span>
                      ))}
                      {tender.cpvs.length > 3 && (
                        <span className="text-[11px] text-faint">
                          +{tender.cpvs.length - 3}
                        </span>
                      )}
                      {tender.is_modified && (
                        <Badge tone="warning" className="ml-1">
                          Modificada ({tender.revision_count})
                        </Badge>
                      )}
                    </div>
                    <PreFitSummary match={match} />
                  </Td>
                  <Td>
                    <p className="max-w-[180px] truncate text-[13px] text-ink">
                      {tender.buyer?.name ?? "—"}
                    </p>
                    <p className="text-[12px] text-muted">
                      {[tender.place_of_execution, tender.nuts_code].filter(Boolean).join(" · ") ||
                        "—"}
                    </p>
                  </Td>
                  <Td className="whitespace-nowrap tabular-nums">
                    {formatMoney(tender.estimated_value ?? tender.base_budget, tender.currency)}
                  </Td>
                  <Td className="whitespace-nowrap text-muted">
                    {formatDate(tender.publication_date)}
                  </Td>
                  <Td>
                    <DeadlineCell value={tender.deadline_at} />
                  </Td>
                  <Td>
                    <Badge tone={TENDER_STATUS_TONE[tender.status]}>
                      {TENDER_STATUS_LABELS[tender.status]}
                    </Badge>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-border px-5 py-3">
            <p className="text-[13px] text-muted">
              Pàgina {page} de {pages}
            </p>
            <div className="flex gap-2">
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
            </div>
          </div>
        )}
      </Card>

      <PreFitDisclaimer className="mt-3" />
      <p className="mt-2 text-[12px] text-faint">
        El pre-fit compara metadades (CPV, territori, import i terminis) amb el vostre perfil
        d&apos;empresa. No amaga cap licitació: el mode «Totes» les mostra igualment.
        {summary.data && ` · Regles: ${summary.data.rules_version}`}
        {items.length > 0 && items[0].tender.source_code && ` · Font: ${items[0].tender.source_code}`}
      </p>
    </>
  );
}
