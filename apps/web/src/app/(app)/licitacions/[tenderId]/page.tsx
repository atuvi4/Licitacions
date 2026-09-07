"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  Checkbox,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Skeleton,
  Spinner,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { PreFitBadge, PreFitPanel } from "@/components/PreFit";
import { RequirementAnalysisSection } from "@/components/RequirementAnalysis";
import { useSession } from "@/components/SessionProvider";
import { api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import type { TenderMatch } from "@/lib/matching";
import type { RequirementAnalysis } from "@/lib/requirements";
import {
  CONTRACT_TYPE_LABELS,
  daysUntilDeadline,
  DOCUMENT_STATE_LABELS,
  DOCUMENT_STATE_TONES,
  documentState,
  formatBytes,
  formatDateTime,
  PROCEDURE_TYPE_LABELS,
  TENDER_DOCUMENT_LABELS,
  TENDER_STATUS_LABELS,
  TENDER_STATUS_TONE,
  type TenderDetail,
  type TenderRevision,
} from "@/lib/tenders";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[12px] text-muted">{label}</p>
      <div className="mt-0.5 text-sm text-ink">{children}</div>
    </div>
  );
}

export default function TenderDetailPage() {
  const params = useParams<{ tenderId: string }>();
  const tenderId = params.tenderId;

  const { companyId } = useSession();
  const { data, loading, error, reload } = useResource<TenderDetail>(`/tenders/${tenderId}`);
  const revisions = useResource<TenderRevision[]>(`/tenders/${tenderId}/revisions`);
  // Pot no existir (404) si encara no s'ha calculat: `PreFitPanel` ho explica.
  const match = useResource<TenderMatch>(
    companyId ? `/companies/${companyId}/matches/by-tender/${tenderId}` : null,
  );

  const analysis = useResource<RequirementAnalysis>(
    `/tenders/${tenderId}/analysis/requirements`,
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [processing, setProcessing] = useState<string | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);

  function toggleSelected(documentId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }

  async function processOne(documentId: string) {
    setProcessing(documentId);
    setProcessError(null);
    try {
      await api.post(`/tenders/${tenderId}/documents/${documentId}/process`, {});
      await reload();
    } catch (caught) {
      setProcessError(caught instanceof Error ? caught.message : "No s'ha pogut processar.");
    } finally {
      setProcessing(null);
    }
  }

  // Seqüencial i no en paral·lel: descarregar plecs de PLACSP en ràfega seria
  // poc educat amb un servei públic, i el backend és síncron.
  async function processSelected() {
    for (const documentId of selected) {
      await processOne(documentId);
    }
    setSelected(new Set());
  }

  if (error) {
    return (
      <>
        <PageHeader title="Licitació" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  if (loading || !data) {
    return (
      <>
        <PageHeader title="Licitació" />
        <Skeleton className="h-72 w-full" />
      </>
    );
  }

  const days = daysUntilDeadline(data.deadline_at);
  const mainCpvs = data.cpvs.filter((cpv) => cpv.is_main);
  const otherCpvs = data.cpvs.filter((cpv) => !cpv.is_main);

  return (
    <>
      <Link
        href="/licitacions"
        className="mb-3 inline-block text-[13px] text-muted hover:text-ink"
      >
        ← Torna a Oportunitats
      </Link>

      <PageHeader
        title={data.title}
        description={
          [data.expediente_number, data.buyer?.name].filter(Boolean).join(" · ") || undefined
        }
        actions={
          <div className="flex items-center gap-2">
            {data.revision_count > 1 && (
              <Badge tone="warning">Modificada ({data.revision_count} versions)</Badge>
            )}
            <Badge tone={TENDER_STATUS_TONE[data.status]}>
              {TENDER_STATUS_LABELS[data.status]}
            </Badge>
            {match.data && <PreFitBadge match={match.data} />}
          </div>
        }
      />

      <div className="space-y-4">
        {/* ------------------------------------------------------ Pre-fit --- */}
        {!match.loading && <PreFitPanel match={match.data} />}

        {/* ------------------------------------------------------- Resum --- */}
        <Card>
          <CardHeader title="Resum" />
          <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
            <Fact label="Número d'expedient">
              <span className="font-mono">{data.expediente_number ?? "—"}</span>
            </Fact>
            <Fact label="Tipus de contracte">{CONTRACT_TYPE_LABELS[data.contract_type]}</Fact>
            <Fact label="Procediment">{PROCEDURE_TYPE_LABELS[data.procedure_type]}</Fact>

            <Fact label="Valor estimat">
              <span className="tabular-nums">
                {formatMoney(data.estimated_value, data.currency)}
              </span>
            </Fact>
            <Fact label="Pressupost base">
              <span className="tabular-nums">{formatMoney(data.base_budget, data.currency)}</span>
            </Fact>
            <Fact label="Base sense impostos">
              <span className="tabular-nums">
                {formatMoney(data.base_budget_without_tax, data.currency)}
              </span>
            </Fact>

            <Fact label="Publicació">{formatDate(data.publication_date)}</Fact>
            <Fact label="Termini de presentació">
              {data.deadline_at ? (
                <span className={days !== null && days >= 0 && days <= 7 ? "text-danger" : ""}>
                  {formatDateTime(data.deadline_at)}
                  {days !== null && (
                    <span className="ml-2 text-[12px] text-faint">
                      {days < 0 ? "(tancat)" : days === 0 ? "(avui)" : `(${days} dies)`}
                    </span>
                  )}
                </span>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Lloc d'execució">
              {[data.place_of_execution, data.nuts_code].filter(Boolean).join(" · ") || "—"}
            </Fact>

            {data.description && (
              <div className="sm:col-span-3">
                <p className="text-[12px] text-muted">Objecte</p>
                <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-ink">
                  {data.description}
                </p>
              </div>
            )}

            {data.source_url && (
              <div className="sm:col-span-3">
                <a
                  href={data.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[13px] text-brand hover:underline"
                >
                  Obre l&apos;expedient a la plataforma oficial ↗
                </a>
              </div>
            )}
          </div>
        </Card>

        {/* ---------------------------------------------------- Organisme --- */}
        {data.buyer && (
          <Card>
            <CardHeader title="Organisme contractant" />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
              <Fact label="Nom">{data.buyer.name}</Fact>
              <Fact label="NIF">
                <span className="font-mono">{data.buyer.tax_identifier ?? "—"}</span>
              </Fact>
              <Fact label="Ubicació">
                {[data.buyer.city, data.buyer.postal_code].filter(Boolean).join(", ") || "—"}
              </Fact>
              {data.buyer.hierarchy && (
                <div className="sm:col-span-3">
                  <p className="text-[12px] text-muted">Dependència administrativa</p>
                  <p className="mt-0.5 text-[13px] text-muted">{data.buyer.hierarchy}</p>
                </div>
              )}
              {data.buyer.buyer_profile_url && (
                <div className="sm:col-span-3">
                  <a
                    href={data.buyer.buyer_profile_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[13px] text-brand hover:underline"
                  >
                    Perfil del contractant ↗
                  </a>
                </div>
              )}
            </div>
          </Card>
        )}

        {/* --------------------------------------------------------- CPV --- */}
        <Card>
          <CardHeader
            title="CPV"
            description="Classificació del contracte segons el vocabulari comú europeu."
          />
          <div className="px-5 py-5">
            {data.cpvs.length === 0 ? (
              <p className="text-[13px] text-muted">La font no publica cap CPV per a aquest expedient.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {[...mainCpvs, ...otherCpvs].map((cpv) => (
                  <span
                    key={cpv.cpv_code}
                    className={
                      cpv.is_main
                        ? "inline-flex items-center gap-2 rounded-full border border-brand bg-brand-soft px-3 py-1 text-[12px]"
                        : "inline-flex items-center gap-2 rounded-full border border-border-strong px-3 py-1 text-[12px]"
                    }
                  >
                    <span className="font-mono font-medium">{cpv.cpv_code}</span>
                    <span className="text-muted">{cpv.description}</span>
                    {cpv.is_main && <span className="text-[10px] text-brand-strong">PRINCIPAL</span>}
                  </span>
                ))}
              </div>
            )}
          </div>
        </Card>

        {/* -------------------------------------------------------- Lots --- */}
        <Card>
          <CardHeader
            title={`Lots (${data.lots.length})`}
            description="Un contracte es pot licitar per lots independents."
          />
          {data.lots.length === 0 ? (
            <EmptyState
              title="Sense lots"
              description="Aquest expedient no està dividit en lots."
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Lot</Th>
                  <Th>Títol</Th>
                  <Th>Import</Th>
                  <Th>CPV</Th>
                  <Th>Ubicació</Th>
                  <Th>Termini</Th>
                </tr>
              </thead>
              <tbody>
                {data.lots.map((lot) => (
                  <Tr key={lot.id}>
                    <Td className="tabular-nums font-medium">{lot.lot_number ?? lot.external_lot_id}</Td>
                    <Td className="max-w-sm">{lot.title ?? "—"}</Td>
                    <Td className="whitespace-nowrap tabular-nums">
                      {formatMoney(lot.base_budget ?? lot.estimated_value, lot.currency)}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {lot.cpvs.slice(0, 4).map((cpv) => (
                          <span
                            key={cpv.cpv_code}
                            title={cpv.description ?? undefined}
                            className="rounded bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-muted"
                          >
                            {cpv.cpv_code}
                          </span>
                        ))}
                        {lot.cpvs.length > 4 && (
                          <span className="text-[11px] text-faint">+{lot.cpvs.length - 4}</span>
                        )}
                      </div>
                    </Td>
                    <Td className="text-muted">
                      {[lot.place_of_execution, lot.nuts_code].filter(Boolean).join(" · ") || "—"}
                    </Td>
                    <Td className="whitespace-nowrap text-muted">
                      {lot.deadline_at ? formatDate(lot.deadline_at) : "—"}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        {/* --------------------------------------------------- Documents --- */}
        <Card>
          <CardHeader
            title={`Documents (${data.documents.length})`}
            description="Processa un plec per descarregar-ne l'original i extreure'n el text per pàgina."
            action={
              selected.size > 0 ? (
                <Button onClick={() => void processSelected()} disabled={processing !== null}>
                  Processa {selected.size} document{selected.size > 1 ? "s" : ""}
                </Button>
              ) : null
            }
          />
          {processError && <ErrorNotice message={processError} />}
          {data.documents.length === 0 ? (
            <EmptyState title="Sense documents publicats" />
          ) : (
            <ul className="divide-y divide-border">
              {data.documents.map((document) => {
                const file = document.latest_file;
                const state = documentState(file);
                const busy = processing === document.id;
                return (
                  <li key={document.id} className="flex items-center gap-4 px-5 py-3">
                    <Checkbox
                      label=""
                      checked={selected.has(document.id)}
                      onChange={() => toggleSelected(document.id)}
                      aria-label={`Selecciona ${document.name}`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{document.name}</p>
                      <p className="text-[12px] text-muted">
                        {TENDER_DOCUMENT_LABELS[document.document_type]}
                        {" · "}
                        {formatBytes(file?.file_size ?? null)}
                        {file?.page_count ? ` · ${file.page_count} pàgines` : ""}
                        {file?.processed_at ? ` · ${formatDate(file.processed_at)}` : ""}
                      </p>
                      {file?.error_code && (
                        <p className="text-[12px] text-danger">Error: {file.error_code}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <Badge tone={DOCUMENT_STATE_TONES[state]}>
                        {DOCUMENT_STATE_LABELS[state]}
                      </Badge>
                      {file?.extraction_id && (
                        <Link
                          href={`/licitacions/${data.id}/documents/${document.id}`}
                          className="text-[13px] text-brand hover:underline"
                        >
                          Text
                        </Link>
                      )}
                      <a
                        href={document.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[13px] text-brand hover:underline"
                      >
                        Origen ↗
                      </a>
                      <Button
                        variant="secondary"
                        onClick={() => void processOne(document.id)}
                        disabled={processing !== null}
                      >
                        {busy ? <Spinner /> : file ? "Reprocessa" : "Processa"}
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        {/* ---------------------------------------------- Anàlisi del plec --- */}
        <RequirementAnalysisSection
          tenderId={tenderId}
          analysis={analysis.data}
          loading={analysis.loading}
          error={analysis.error}
          onReload={analysis.reload}
        />

        {/* --------------------------------------------------- Historial --- */}
        <Card>
          <CardHeader
            title="Historial"
            description="Cada versió rebuda de la font es conserva, amb el seu hash i la seva data."
          />
          <div className="grid gap-4 border-b border-border px-5 py-4 sm:grid-cols-4">
            <Fact label="Vista per primer cop">{formatDateTime(data.first_seen_at)}</Fact>
            <Fact label="Vista per últim cop">{formatDateTime(data.last_seen_at)}</Fact>
            <Fact label="Últim canvi">{formatDateTime(data.last_changed_at)}</Fact>
            <Fact label="Versions">{data.revision_count}</Fact>
          </div>
          {revisions.loading ? (
            <Skeleton className="m-5 h-16" />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Versió</Th>
                  <Th>Rebuda</Th>
                  <Th>Publicada per la font</Th>
                  <Th>Hash del contingut</Th>
                  <Th>Mida</Th>
                  <Th>Estat</Th>
                </tr>
              </thead>
              <tbody>
                {(revisions.data ?? []).map((revision) => (
                  <Tr key={revision.id}>
                    <Td className="tabular-nums font-medium">{revision.revision_number}</Td>
                    <Td className="whitespace-nowrap text-muted">
                      {formatDateTime(revision.fetched_at)}
                    </Td>
                    <Td className="whitespace-nowrap text-muted">
                      {formatDateTime(revision.source_updated_at)}
                    </Td>
                    <Td>
                      <span
                        className="font-mono text-[11px] text-faint"
                        title={revision.raw_hash}
                      >
                        {revision.raw_hash.slice(0, 16)}…
                      </span>
                    </Td>
                    <Td className="tabular-nums text-muted">
                      {(revision.raw_size / 1024).toFixed(1)} KB
                    </Td>
                    <Td>
                      <Badge
                        tone={revision.processing_status === "processed" ? "success" : "danger"}
                      >
                        {revision.processing_status}
                      </Badge>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        {/* ------------------------------------------------- Traçabilitat --- */}
        {data.traceability && (
          <Card>
            <CardHeader
              title="Traçabilitat"
              description="D'on surt aquesta dada. Zona tècnica."
            />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
              <Fact label="Font">
                {data.traceability.source_name} ({data.traceability.source_code})
              </Fact>
              <Fact label="Identificador a la font">
                <span className="break-all font-mono text-[12px]">
                  {data.traceability.external_id}
                </span>
              </Fact>
              <Fact label="Descarregada el">
                {formatDateTime(data.traceability.fetched_at)}
              </Fact>
              <Fact label="Versió del parser">
                <span className="font-mono text-[12px]">{data.traceability.parser_version}</span>
              </Fact>
              <Fact label="SHA-256 del XML original">
                <span className="break-all font-mono text-[12px]">
                  {data.traceability.raw_hash}
                </span>
              </Fact>
              <Fact label="SourceRecord actual">
                <span className="break-all font-mono text-[12px]">
                  {data.traceability.latest_source_record_id}
                </span>
              </Fact>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
