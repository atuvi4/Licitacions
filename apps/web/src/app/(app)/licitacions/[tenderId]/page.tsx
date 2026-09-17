"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { ComplianceSection } from "@/components/ComplianceSection";
import { PreFitPanel, ScoreValue } from "@/components/PreFit";
import { RequirementAnalysisSection } from "@/components/RequirementAnalysis";
import { useSession } from "@/components/SessionProvider";
import { TenderChangeHistory } from "@/components/TenderChangeHistory";
import { WorkspaceLauncher } from "@/components/WorkspaceLauncher";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  Checkbox,
  ErrorNotice,
  ErrorState,
  LinkButton,
  PageSkeleton,
  Skeleton,
  Spinner,
  StatusPill,
  Table,
  Tabs,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, api } from "@/lib/api";
import type { Compliance } from "@/lib/compliance";
import { formatDate, formatMoney } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import { type AnalysisLimits, jobErrorMessage, requestProcessing, useJobRequest } from "@/lib/jobs";
import { DOCUMENT_STATE_LEVEL, TENDER_STATUS_LEVEL, shortSummary } from "@/lib/labels";
import type { TenderMatch } from "@/lib/matching";
import type { RequirementAnalysis } from "@/lib/requirements";
import type { TenderHistoryRevision } from "@/lib/tender-changes";
import {
  CONTRACT_TYPE_LABELS,
  DOCUMENT_STATE_LABELS,
  PROCEDURE_TYPE_LABELS,
  TENDER_DOCUMENT_LABELS,
  TENDER_STATUS_LABELS,
  documentState,
  formatBytes,
  formatDateTime,
  type TenderDetail,
  type TenderRevision,
} from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

import {
  amountMeta,
  candidatureMeta,
  deadlineMeta,
  requirementsLine,
  tabFromParam,
  tabItems,
  type TabKey,
} from "./header";

/**
 * Fitxa de licitació (handoff L9, mockups 04/05): una capçalera de decisió
 * que respon «termini · encaix · requisits · què fer ara» i quatre pestanyes
 * que reordenen les targetes existents sense tocar-ne cap lògica. Plecs i
 * Requisits conserven els seus components actuals (L10 i L11 els redissenyen).
 */

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-caption text-muted">{label}</p>
      <div className="mt-0.5 text-sm text-ink">{children}</div>
    </div>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className={KICKER}>{label}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] font-semibold text-ink">
        {children}
      </div>
    </div>
  );
}

/** La candidatura d'aquesta licitació, si existeix. Un 404 vol dir «cap». */
function useTenderWorkspace(companyId: string | null, tenderId: string) {
  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    setChecked(false);
    async function look() {
      try {
        const found = await api.get<WorkspaceDetail>(`/companies/${companyId}/tenders/${tenderId}/workspace`);
        if (!cancelled) setWorkspace(found);
      } catch (caught) {
        // Un 404 vol dir que encara no hi ha candidatura; qualsevol altra
        // fallada tampoc en pot inventar cap.
        if (!cancelled) setWorkspace(null);
        if (!(caught instanceof ApiError && caught.status === 404)) console.warn("No s'ha pogut consultar la candidatura", caught);
      } finally {
        if (!cancelled) setChecked(true);
      }
    }
    void look();
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId]);
  return { workspace, checked };
}

function TenderDetail() {
  const params = useParams<{ tenderId: string }>();
  const tenderId = params.tenderId;
  const searchParams = useSearchParams();
  const urlTab = tabFromParam(searchParams.get("tab"));
  const [tab, setTab] = useState<TabKey>(urlTab);
  useEffect(() => {
    setTab(urlTab);
  }, [urlTab]);

  const { companyId } = useSession();
  const { data, loading, error, status, reload } = useResource<TenderDetail>(`/tenders/${tenderId}`);
  const revisions = useResource<TenderRevision[]>(`/tenders/${tenderId}/revisions`);
  const history = useResource<TenderHistoryRevision[]>(`/tenders/${tenderId}/history`);
  // Pot no existir (404) si encara no s'ha calculat: el Resum ho explica.
  const match = useResource<TenderMatch>(
    companyId ? `/companies/${companyId}/matches/by-tender/${tenderId}` : null,
  );

  const analysis = useResource<RequirementAnalysis>(`/tenders/${tenderId}/analysis/requirements`);
  const compliance = useResource<Compliance>(
    companyId ? `/companies/${companyId}/tenders/${tenderId}/compliance` : null,
  );
  const candidature = useTenderWorkspace(companyId, tenderId);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Documents demanats des d'aquesta pàgina i encara en curs. Si la pàgina es
  // recarrega amb feina en marxa no se sap quins són, però els botons queden
  // igualment desactivats fins que acaba.
  const [processingIds, setProcessingIds] = useState<Set<string>>(new Set());
  const [submittingProcess, setSubmittingProcess] = useState(false);
  const [processError, setProcessError] = useState<string | null>(null);

  function toggleSelected(documentId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }

  /**
   * Baixar un plec canvia la HISTÒRIA de la licitació, no només la seva fitxa:
   * en saber el SHA d'un document que abans no teníem es registra una revisió
   * nova i, si escau, els seus events. Refrescar només el detall deixava
   * «Historial de canvis» dient «Cap canvi detectat» just després de provocar-ne
   * un: calia recarregar la pàgina a mà per veure la conseqüència del que
   * s'acabava de fer.
   */
  const { reload: reloadHistory } = history;
  const { reload: reloadRevisions } = revisions;
  const reloadLifecycle = useCallback(async () => {
    await Promise.all([reload(), reloadHistory(), reloadRevisions()]);
  }, [reload, reloadHistory, reloadRevisions]);

  const { reload: reloadRequirements } = analysis;
  const { reload: reloadCompliance } = compliance;
  const reloadAnalysis = useCallback(async () => {
    await Promise.all([reloadRequirements(), reloadCompliance()]);
  }, [reloadRequirements, reloadCompliance]);

  const processJob = useJobRequest(companyId, tenderId, "process_tender_documents", async (done) => {
    setProcessingIds(new Set());
    if (done.status === "failed") setProcessError(jobErrorMessage(done.error_code));
    else if (done.outcome === "partial") {
      setProcessError("Algun document no s'ha pogut processar: en veuràs l'error a la seva fila.");
    }
    await reloadLifecycle();
  });
  const limits = useResource<AnalysisLimits>(companyId ? `/companies/${companyId}/analysis-limits` : null);
  const canProcess = limits.data?.can_process_documents ?? false;
  const processing = submittingProcess || processJob.active || !canProcess;

  // Una sola feina per a tots els documents: el worker els baixa un rere
  // l'altre, sense ràfegues contra un servei públic.
  async function processDocuments(documentIds: string[]) {
    if (!companyId || documentIds.length === 0) return;
    setSubmittingProcess(true);
    setProcessError(null);
    setProcessingIds(new Set(documentIds));
    try {
      processJob.track(await requestProcessing(companyId, tenderId, documentIds));
    } catch (caught) {
      setProcessingIds(new Set());
      setProcessError(caught instanceof Error ? caught.message : "No s'ha pogut demanar el processat.");
    } finally {
      setSubmittingProcess(false);
    }
  }

  async function processOne(documentId: string) {
    await processDocuments([documentId]);
  }

  async function processSelected() {
    await processDocuments([...selected]);
    setSelected(new Set());
  }

  if (error && !data) {
    if (status === 404) {
      return (
        <ErrorState
          title="Aquesta licitació ja no hi és"
          description="L'expedient ja no és al catàleg: la font l'ha retirat o l'enllaç és antic. Les candidatures que en depenguin continuen a Candidatures."
          action={
            <LinkButton href="/licitacions" variant="primary" size="md">
              Ves a Oportunitats
            </LinkButton>
          }
        />
      );
    }
    return (
      <ErrorState
        title="No hem pogut carregar la licitació"
        description="La connexió amb el servei ha fallat. No s'ha perdut res: torna-ho a provar."
        action={
          <Button variant="primary" onClick={() => void reload()}>
            Torna-ho a provar
          </Button>
        }
        technical={{ method: "GET /tenders", code: error }}
      />
    );
  }

  if (loading || !data) {
    return <PageSkeleton variant="detail" context="carregant la licitació…" />;
  }

  const deadline = deadlineMeta(data.deadline_at);
  const amount = amountMeta(data);
  const requirements = requirementsLine(analysis.data, compliance.data);
  const candidatureInfo = candidatureMeta(candidature.workspace);
  const cancelled = data.status === "cancelled";
  const mainCpvs = data.cpvs.filter((cpv) => cpv.is_main);
  const otherCpvs = data.cpvs.filter((cpv) => !cpv.is_main);
  const items = tabItems({ documents: data.documents.length, analysis: analysis.data, compliance: compliance.data });

  return (
    <>
      <p className="mb-3 text-[13px] text-muted">
        <Link href="/licitacions" className="text-brand-strong hover:text-accent-800">
          Oportunitats
        </Link>
        {" › "}
        {[data.buyer?.name, data.expediente_number].filter(Boolean).join(" · ") || data.external_id}
      </p>

      {/* ------------------------------------------- Capçalera de decisió --- */}
      <Card frame className="mb-4 bg-surface">
        <div data-tender-header className="px-5 py-4 sm:px-6">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 flex-1">
              <h1 className="font-heading text-[22px] leading-tight font-semibold text-ink">{data.title}</h1>
              <p className="mt-1.5 text-[13.5px] text-muted">
                {PROCEDURE_TYPE_LABELS[data.procedure_type]} · {CONTRACT_TYPE_LABELS[data.contract_type]}
                {data.lots.length > 0 && ` · ${data.lots.length} ${data.lots.length === 1 ? "lot" : "lots"}`}
                {data.publication_date && ` · publicada el ${formatDate(data.publication_date)}`}
                {data.source_url && (
                  <>
                    {" · "}
                    <a
                      href={data.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-brand-strong hover:text-accent-800"
                    >
                      obre la font ↗
                    </a>
                  </>
                )}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
              <StatusPill level={TENDER_STATUS_LEVEL[data.status]}>{TENDER_STATUS_LABELS[data.status]}</StatusPill>
              {data.source_publication_count > 1 && (
                <StatusPill level="attention">Publicada {data.source_publication_count} cops</StatusPill>
              )}
              {companyId && (!cancelled || candidature.workspace) && (
                <WorkspaceLauncher
                  companyId={companyId}
                  tenderId={tenderId}
                  existing={candidature.workspace?.id ?? null}
                  checked={candidature.checked}
                  className="min-h-11 w-full sm:min-h-0 sm:w-auto"
                />
              )}
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3 border-t border-border pt-4">
            <Meta label="Termini">
              <StatusPill level={deadline.level}>{deadline.text}</StatusPill>
              {deadline.date && <span className="text-[13px] font-normal text-muted">{deadline.date}</span>}
            </Meta>
            <Meta label="Encaix">
              {match.data ? (
                <>
                  <ScoreValue score={match.data.score} size="sm" />
                  <span className="text-[13px] font-normal text-muted">{shortSummary(match.data)}</span>
                </>
              ) : (
                <span className="font-normal text-muted">{match.loading ? "…" : "— · sense calcular"}</span>
              )}
            </Meta>
            <Meta label="Requisits">
              <span className="font-normal text-ink">{requirements.text}</span>
              {requirements.detail && (
                <span className={requirements.level === "blocking" ? "text-danger" : "font-normal text-muted"}>
                  · {requirements.detail}
                </span>
              )}
            </Meta>
            <Meta label="Import">
              <span className="tabular-nums">{amount.text}</span>
              {amount.note && <span className="text-[13px] font-normal text-muted">· {amount.note}</span>}
            </Meta>
            <Meta label="Candidatura">
              {candidatureInfo ? (
                <>
                  <StatusPill level={candidatureInfo.status.level}>{candidatureInfo.status.label}</StatusPill>
                  {candidatureInfo.decision ? (
                    <StatusPill level={candidatureInfo.decision.level}>{candidatureInfo.decision.label}</StatusPill>
                  ) : (
                    <span className="text-[13px] font-normal text-muted">sense decisió humana</span>
                  )}
                </>
              ) : (
                <span className="font-normal text-muted">{candidature.checked ? "Sense candidatura" : "…"}</span>
              )}
            </Meta>
          </div>
        </div>
      </Card>

      {cancelled && (
        <Banner
          level="blocking"
          kicker="Anul·lada"
          className="mb-4"
          action={
            <LinkButton href="/licitacions" variant="secondary" size="sm">
              Torna a Oportunitats
            </LinkButton>
          }
        >
          L&apos;òrgan ha anul·lat aquesta licitació: ja no admet cap oferta.
        </Banner>
      )}

      <Tabs items={items} value={tab} onChange={(key) => setTab(tabFromParam(key))} className="mb-5" />

      {/* ============================================================ Resum === */}
      {tab === "resum" && (
        <div className="space-y-4">
          {!match.loading &&
            (match.data ? (
              <PreFitPanel match={match.data} />
            ) : (
              <Card>
                <EmptyState
                  title="Aquesta licitació encara no té pre-fit calculat."
                  description="Es calcula per a totes les licitacions ingerides; recalcula'l des d'Oportunitats i torna aquí."
                  action={
                    <LinkButton href="/licitacions" variant="primary" size="md">
                      Recalcula el pre-fit a Oportunitats
                    </LinkButton>
                  }
                />
              </Card>
            ))}

          <Card>
            <CardHeader title="Resum" />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
              <Fact label="Número d'expedient">
                <span className="font-mono">{data.expediente_number ?? "—"}</span>
              </Fact>
              <Fact label="Tipus de contracte">{CONTRACT_TYPE_LABELS[data.contract_type]}</Fact>
              <Fact label="Procediment">{PROCEDURE_TYPE_LABELS[data.procedure_type]}</Fact>

              <Fact label="Valor estimat">
                <span className="tabular-nums">{formatMoney(data.estimated_value, data.currency)}</span>
              </Fact>
              <Fact label="Pressupost base">
                <span className="tabular-nums">{formatMoney(data.base_budget, data.currency)}</span>
              </Fact>
              <Fact label="Base sense impostos">
                <span className="tabular-nums">{formatMoney(data.base_budget_without_tax, data.currency)}</span>
              </Fact>

              <Fact label="Publicació">{formatDate(data.publication_date)}</Fact>
              <Fact label="Termini de presentació">
                {deadline.date ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span>{deadline.date}</span>
                    <StatusPill level={deadline.level} size="sm">
                      {deadline.text}
                    </StatusPill>
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
                  <p className="text-caption text-muted">Objecte</p>
                  <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-ink">{data.description}</p>
                </div>
              )}

              {data.source_url && (
                <div className="sm:col-span-3">
                  <a
                    href={data.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[13px] text-brand-strong hover:text-accent-800"
                  >
                    Obre l&apos;expedient a la plataforma oficial ↗
                  </a>
                </div>
              )}
            </div>
          </Card>

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
                    <p className="text-caption text-muted">Dependència administrativa</p>
                    <p className="mt-0.5 text-[13px] text-muted">{data.buyer.hierarchy}</p>
                  </div>
                )}
                {data.buyer.buyer_profile_url && (
                  <div className="sm:col-span-3">
                    <a
                      href={data.buyer.buyer_profile_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[13px] text-brand-strong hover:text-accent-800"
                    >
                      Perfil del contractant ↗
                    </a>
                  </div>
                )}
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title="CPV" description="Classificació del contracte segons el vocabulari comú europeu." />
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
                          ? "inline-flex items-center gap-2 border border-brand bg-brand-soft px-3 py-1 text-[12px]"
                          : "inline-flex items-center gap-2 border border-border-strong px-3 py-1 text-[12px]"
                      }
                    >
                      <span className="font-mono font-medium">{cpv.cpv_code}</span>
                      <span className="text-muted">{cpv.description}</span>
                      {cpv.is_main && <span className={KICKER}>principal</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader
              title={`Lots (${data.lots.length})`}
              description="Un contracte es pot licitar per lots independents."
            />
            {data.lots.length === 0 ? (
              <p className="px-5 py-4 text-[13px] text-muted">Aquest expedient no està dividit en lots.</p>
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
                              className="bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-muted"
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
        </div>
      )}

      {/* ============================================================ Plecs === */}
      {tab === "plecs" && (
        <Card>
          <CardHeader
            title={`Documents (${data.documents.length})`}
            description="Processa un plec per descarregar-ne l'original i extreure'n el text per pàgina."
            action={
              selected.size > 0 ? (
                <Button onClick={() => void processSelected()} disabled={processing}>
                  Processa {selected.size} document{selected.size > 1 ? "s" : ""}
                </Button>
              ) : null
            }
          />
          {processError && <ErrorNotice message={processError} />}
          {limits.data && !canProcess && (
            <p className="px-5 py-2 text-caption text-muted">El teu rol no pot demanar el processat dels plecs.</p>
          )}
          {data.documents.length === 0 ? (
            <p className="px-5 py-4 text-[13px] text-muted">La font no ha publicat cap document per a aquest expedient.</p>
          ) : (
            <ul className="divide-y divide-border">
              {data.documents.map((document) => {
                const file = document.latest_file;
                const state = documentState(file);
                const busy = processingIds.has(document.id);
                return (
                  <li key={document.id} className="flex flex-wrap items-center gap-4 px-5 py-3">
                    <Checkbox
                      label=""
                      checked={selected.has(document.id)}
                      onChange={() => toggleSelected(document.id)}
                      aria-label={`Selecciona ${document.name}`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{document.name}</p>
                      <p className="text-caption text-muted">
                        {TENDER_DOCUMENT_LABELS[document.document_type]}
                        {" · "}
                        {formatBytes(file?.file_size ?? null)}
                        {file?.page_count ? ` · ${file.page_count} pàgines` : ""}
                        {file?.processed_at ? ` · ${formatDate(file.processed_at)}` : ""}
                      </p>
                      {file?.error_code && (
                        <p className="text-caption text-danger" title={file.error_code}>
                          No s&apos;ha pogut processar aquest document.
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-3">
                      <StatusPill level={DOCUMENT_STATE_LEVEL[state]} size="sm">
                        {DOCUMENT_STATE_LABELS[state]}
                      </StatusPill>
                      {file?.extraction_id && (
                        <Link
                          href={`/licitacions/${data.id}/documents/${document.id}`}
                          className="text-[13px] text-brand-strong hover:text-accent-800"
                        >
                          Text
                        </Link>
                      )}
                      <a
                        href={document.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[13px] text-brand-strong hover:text-accent-800"
                      >
                        Origen ↗
                      </a>
                      <Button variant="secondary" size="sm" onClick={() => void processOne(document.id)} disabled={processing}>
                        {busy ? <Spinner /> : file ? "Reprocessa" : "Processa"}
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}

      {/* ======================================================== Requisits === */}
      {tab === "requisits" && (
        // Les seccions d'anàlisi i compliment són les actuals (L11 les
        // redissenya): si es fan més amples que un telèfon, es desplacen aquí
        // dins i no eixamplen la pàgina.
        <div className="min-w-0 space-y-4 overflow-x-auto">
          <RequirementAnalysisSection
            tenderId={tenderId}
            companyId={companyId}
            analysis={analysis.data}
            loading={analysis.loading}
            error={analysis.error}
            // Una anàlisi nova deixa el compliment endarrerit: el seu avís s'ha de
            // veure ara, no després d'un F5.
            onReload={reloadAnalysis}
          />
          <ComplianceSection
            tenderId={tenderId}
            companyId={companyId}
            compliance={compliance.data}
            loading={compliance.loading}
            error={compliance.error}
            prefitScore={match.data?.score ?? null}
            onReload={compliance.reload}
          />
        </div>
      )}

      {/* ======================================================== Historial === */}
      {tab === "historial" && (
        <div className="space-y-4">
          <TenderChangeHistory
            revisions={history.data}
            loading={history.loading}
            error={history.error}
            onReload={history.reload}
          />

          <Card>
            <CardHeader
              title="Publicacions de la font"
              description="Cada vegada que la font publica aquest expedient en queda una còpia, amb el seu hash i la seva data. Que hi hagi una publicació nova no vol dir que hagi canviat res: què ha canviat de veritat es veu a «Historial de canvis»."
            />
            <div className="grid gap-4 border-b border-border px-5 py-4 sm:grid-cols-4">
              <Fact label="Vista per primer cop">{formatDateTime(data.first_seen_at)}</Fact>
              <Fact label="Vista per últim cop">{formatDateTime(data.last_seen_at)}</Fact>
              <Fact label="Últim canvi">{formatDateTime(data.last_changed_at)}</Fact>
              <Fact label="Publicacions rebudes">{data.source_publication_count}</Fact>
            </div>
            {revisions.loading ? (
              <Skeleton className="m-5 h-16" />
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Publicació</Th>
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
                      <Td className="whitespace-nowrap text-muted">{formatDateTime(revision.fetched_at)}</Td>
                      <Td className="whitespace-nowrap text-muted">{formatDateTime(revision.source_updated_at)}</Td>
                      <Td>
                        <span className="font-mono text-[11px] text-faint" title={revision.raw_hash}>
                          {revision.raw_hash.slice(0, 16)}…
                        </span>
                      </Td>
                      <Td className="tabular-nums text-muted">{(revision.raw_size / 1024).toFixed(1)} KB</Td>
                      <Td>
                        <StatusPill level={revision.processing_status === "processed" ? "ready" : "blocking"} size="sm">
                          {revision.processing_status === "processed" ? "Processada" : "Amb error"}
                        </StatusPill>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          {data.traceability && (
            <Card>
              <CardHeader title="Traçabilitat" description="D'on surt aquesta dada. Zona tècnica." />
              <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
                <Fact label="Font">
                  {data.traceability.source_name} ({data.traceability.source_code})
                </Fact>
                <Fact label="Identificador a la font">
                  <span className="break-all font-mono text-[12px]">{data.traceability.external_id}</span>
                </Fact>
                <Fact label="Descarregada el">{formatDateTime(data.traceability.fetched_at)}</Fact>
                <Fact label="Versió del parser">
                  <span className="font-mono text-[12px]">{data.traceability.parser_version}</span>
                </Fact>
                <Fact label="SHA-256 del XML original">
                  <span className="break-all font-mono text-[12px]">{data.traceability.raw_hash}</span>
                </Fact>
                <Fact label="SourceRecord actual">
                  <span className="break-all font-mono text-[12px]">{data.traceability.latest_source_record_id}</span>
                </Fact>
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}

export default function TenderDetailPage() {
  return (
    <Suspense fallback={<PageSkeleton variant="detail" context="carregant la licitació…" />}>
      <TenderDetail />
    </Suspense>
  );
}
