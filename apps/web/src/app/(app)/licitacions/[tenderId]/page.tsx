"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ComplianceSection } from "@/components/ComplianceSection";
import { PreFitMissing, PreFitPanel, ScoreValue } from "@/components/PreFit";
import { RequirementAnalysisSection } from "@/components/RequirementAnalysis";
import { AnalysisTab } from "@/components/tender-analysis/AnalysisTab";
import { ReviewSummaryCard } from "@/components/review/ReviewSummaryCard";
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
  PermissionNote,
  StatusPill,
  Tabs,
} from "@/components/ui";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, api } from "@/lib/api";
import type { Compliance } from "@/lib/compliance";
import { formatDate } from "@/lib/format";
import { LOT_AMOUNT_COLUMNS, budgetWithTaxView } from "@/lib/pscpAmounts";
import { AmountStack } from "@/components/AmountStack";
import { useMutation, useResource } from "@/lib/hooks";
import { type AnalysisLimits, jobErrorMessage, requestProcessing, useJobRequest } from "@/lib/jobs";
import { jobErrorAction, shortSummary, tenderStatusView } from "@/lib/labels";
import { prefitProfileReady, type TenderMatch } from "@/lib/matching";
import { requirementAnalysisState, type RequirementAnalysis } from "@/lib/requirements";
import type { DashboardSummary } from "@/lib/types";
import { publicationStatusLabel, type TenderHistoryRevision } from "@/lib/tender-changes";
import {
  AMOUNT_BASIS_LABEL,
  AMOUNT_BASIS_SHORT,
  AMOUNT_HELP,
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  SOURCE_FIRST_CPV_HELP,
  SOURCE_FIRST_CPV_LABEL,
  TENDER_DOCUMENT_LABELS,
  cpvDescription,
  labelOf,
  formatBytes,
  formatDateTime,
  type TenderDetail,
  type TenderDocumentMeta,
  type TenderLot,
  type TenderRevision,
} from "@/lib/tenders";
import { DocumentReviewPanel, NewVersionButton } from "@/components/DocumentReviewPanel";
import type { WorkspaceDetail } from "@/lib/workspace";

import { extractedText, fileErrorText, isQueued, jobPill, originHref, readingLabel, readingSubtext, selectionLabel, versionText } from "./documents";
import { topLevelDocuments } from "./package";
import { LotAmountValue, LotsPscpNote, PscpAttribution, PscpBudgetValue } from "./BudgetWithTax";
import { DocumentBadges, PackageNotice, PlecsReadingLine, RemovedDocuments } from "./PlecsOverview";
import { PortalSyncBar, usePortalSync } from "./PortalSync";
import {
  amountFact,
  amountMeta,
  candidatureMeta,
  deadlineMeta,
  requirementsLine,
  sameTender,
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

/**
 * Un import d'una base concreta (P2-06): el valor exacte, amb cèntims, o que no consta. Un 0,00 €
 * publicat no és cap import: es diu que s'ha publicat a zero i que no s'utilitza (`amountFact`).
 */
function AmountValue({ value, currency, manual = false }: { value: string | null; currency: string; manual?: boolean }) {
  const fact = amountFact(value, currency, { manual });
  if (fact.kind === "missing") return <span className="text-muted">{fact.text}</span>;
  if (fact.kind === "amount") return <span className="tabular-nums">{fact.text}</span>;
  return <span data-amount-zero={fact.kind === "zero" ? "" : undefined}>{fact.text}</span>;
}

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
  const { data, error, status, reload } = useResource<TenderDetail>(`/tenders/${tenderId}`);
  const revisions = useResource<TenderRevision[]>(`/tenders/${tenderId}/revisions`);
  const history = useResource<TenderHistoryRevision[]>(`/tenders/${tenderId}/history`);
  // Pot no existir (404) si encara no s'ha calculat: el Resum ho explica.
  const match = useResource<TenderMatch>(
    companyId ? `/companies/${companyId}/matches/by-tender/${tenderId}` : null,
  );
  // Només per saber si el perfil permet calcular el pre-fit (CPV, territoris o
  // preferències de contracte): sense, no s'ofereix cap càlcul.
  const profile = useResource<DashboardSummary>(companyId ? `/companies/${companyId}/summary` : null);
  const { run: runPrefit, saving: prefitSaving, error: prefitError } = useMutation();
  const { reload: reloadMatch } = match;
  const calculatePrefit = useCallback(async () => {
    if (!companyId) return;
    // Només aquesta licitació: el recàlcul global és d'Oportunitats.
    const done = await runPrefit(async () => {
      await api.post(`/companies/${companyId}/matches/recalculate?tender_id=${tenderId}`, {});
    });
    if (done) await reloadMatch();
  }, [companyId, runPrefit, reloadMatch, tenderId]);

  // La run de requisits que llegeix l'empresa activa (el seu punter o la canònica):
  // amb més d'una empresa, el servidor no la pot endevinar.
  const analysis = useResource<RequirementAnalysis>(
    companyId
      ? `/tenders/${tenderId}/analysis/requirements?company_id=${companyId}`
      : `/tenders/${tenderId}/analysis/requirements`,
  );
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
  // Els últims documents demanats: si la feina falla, es tornen a demanar els mateixos.
  const [lastRequested, setLastRequested] = useState<string[]>([]);
  const documentsTable = useRef<HTMLDivElement | null>(null);

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
  // Aquí i no a la pestanya: el resultat de la sincronització no es perd si es canvia de
  // pestanya. Uns documents nous poden deixar endarrerides l'anàlisi i el compliment.
  const reloadAfterPortalSync = useCallback(async () => {
    await Promise.all([reloadLifecycle(), reloadAnalysis()]);
  }, [reloadLifecycle, reloadAnalysis]);
  const portalSync = usePortalSync(companyId, tenderId, data?.portal_sync ?? null, reloadAfterPortalSync);
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
    setLastRequested(documentIds);
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

  // La fitxa que hi ha és d'una altra licitació (s'ha canviat d'enllaç i la lectura ha fallat):
  // no se'n pinta res, es diu l'error.
  if (error && (!data || !sameTender(data.id, tenderId))) {
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

  // L'esquelet només fins a tenir la fitxa d'AQUESTA licitació: tornar-la a llegir (després
  // de processar o de sincronitzar) no buida la pantalla ni fa perdre la posició ni els avisos.
  if (!data || !sameTender(data.id, tenderId)) {
    return <PageSkeleton variant="detail" context="carregant la licitació…" />;
  }

  const deadline = deadlineMeta(data.deadline_at);
  const statusView = tenderStatusView(data);
  const amount = amountMeta(data);
  // F-06a: el PBL amb IVA de la PSCP, només informatiu (la capçalera no canvia).
  const budgetWithTax = budgetWithTaxView(data);
  const requirements = requirementsLine(analysis.data, compliance.data);
  const candidatureInfo = candidatureMeta(candidature.workspace);
  const cancelled = data.status === "cancelled";
  const mainCpvs = data.cpvs.filter((cpv) => cpv.is_main);
  const otherCpvs = data.cpvs.filter((cpv) => !cpv.is_main);
  // Els àlies van plegats sota el canònic: no compten com a plecs.
  const topLevelCount = topLevelDocuments(data.documents).length;
  const items = tabItems({ documents: topLevelCount, analysis: analysis.data, compliance: compliance.data });
  // «Hi ha anàlisi?» amb una sola font de veritat (M1): `analysis_state` del servidor.
  const analysisState = requirementAnalysisState(analysis.data);

  const jobState = processJob.request;
  const jobStatus = jobPill(jobState);
  const jobFailed = jobState?.status === "failed";
  const jobPartial = jobState?.status === "succeeded" && jobState.outcome === "partial";
  const selection = selectionLabel(selected.size);
  const LINK = "text-[13px] text-brand-strong hover:text-accent-800";

  // Una licitacio oberta a ma es la unica que aquesta empresa pot editar: si la
  // veiem i ve de la font MANUAL, es seva.
  const isManual = data.traceability?.source_code === "manual";

  const documentColumns: Array<DataTableColumn<TenderDocumentMeta>> = [
    {
      key: "select",
      header: "",
      width: "4%",
      cell: (document) => (
        <Checkbox
          label=""
          className="min-h-11 md:min-h-0"
          checked={selected.has(document.id)}
          onChange={() => toggleSelected(document.id)}
          aria-label={`Selecciona ${document.name}`}
        />
      ),
    },
    {
      key: "document",
      header: "Document",
      cell: (document) => {
        const file = document.latest_file;
        const version = versionText(file);
        return (
          <div className="min-w-0">
            <p className="truncate font-medium text-ink" title={document.name}>
              {document.name}
            </p>
            <p className="text-caption text-muted">
              {TENDER_DOCUMENT_LABELS[document.document_type]}
              {document.published_at && ` · publicat el ${formatDate(document.published_at)}`}
              {version && ` · ${version}`}
            </p>
            {file?.error_code && (
              <p className="text-caption text-danger" title={file.error_code}>
                {fileErrorText(file.error_code)}
              </p>
            )}
            <DocumentBadges document={document} documents={data.documents} />
          </div>
        );
      },
    },
    ...(isManual
      ? [
          {
            key: "versions",
            header: "",
            width: "14%",
            // Nomes a una licitacio oberta a ma: els plecs d'una de publicada
            // els porta la font, i substituir-los des d'aqui seria escriure per
            // sobre del que ha publicat l'organ.
            cell: (document: TenderDocumentMeta) => (
              <NewVersionButton
                tenderId={data.id}
                documentId={document.id}
                onUploaded={(created) => {
                  if (created) void reload();
                }}
              />
            ),
          } satisfies DataTableColumn<TenderDocumentMeta>,
        ]
      : []),
    {
      key: "text",
      header: "Text extret",
      width: "18%",
      cell: (document) => (
        <div>
          <span className="tabular-nums">{extractedText(document.latest_file)}</span>
          {document.latest_file?.file_size !== null && document.latest_file?.file_size !== undefined && (
            <p className="text-caption text-muted">{formatBytes(document.latest_file.file_size)}</p>
          )}
        </div>
      ),
    },
    {
      key: "reading",
      header: "Lectura",
      width: "16%",
      cell: (document) => {
        const file = document.latest_file;
        // A5: «Sense text extret» → «Text extret · requisits encara no analitzats» → «Analitzat».
        const reading = readingLabel(document, analysis.data);
        const subtext = readingSubtext(file);
        return (
          <div className="flex flex-col items-start gap-1">
            {processingIds.has(document.id) ? (
              isQueued(jobState) ? (
                <StatusPill level="neutral" size="sm">
                  En cua
                </StatusPill>
              ) : (
                <StatusPill level="running" size="sm">
                  Processant
                </StatusPill>
              )
            ) : (
              <StatusPill level={reading.level} size="sm">
                {reading.label}
              </StatusPill>
            )}
            {subtext && <span className="text-caption text-muted">{subtext}</span>}
          </div>
        );
      },
    },
    {
      key: "action",
      header: "Acció",
      width: "24%",
      align: "right",
      cell: (document) => {
        const file = document.latest_file;
        const origin = originHref(document.url, data.source_url);
        return (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:justify-end">
            {file?.extraction_id && (
              <Link href={`/licitacions/${data.id}/documents/${document.id}`} className={LINK}>
                Obre el text
              </Link>
            )}
            {origin && (
              <a href={origin} target="_blank" rel="noopener noreferrer" className={LINK}>
                Origen ↗
              </a>
            )}
            <Button
              variant="secondary"
              size="sm"
              className="min-h-11 md:min-h-0"
              onClick={() => void processOne(document.id)}
              disabled={processing}
              loading={processingIds.has(document.id)}
            >
              {file ? "Reprocessa" : "Processa"}
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <>
      <p className="mb-3 text-[13px] text-muted">
        <Link href="/licitacions" className="text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800">
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
                {labelOf(PROCEDURE_TYPE_LABELS, data.procedure_type, "Tipus desconegut")} ·{" "}
                {labelOf(CONTRACT_TYPE_LABELS, data.contract_type, "Tipus desconegut")}
                {data.lots.length > 0 && ` · ${data.lots.length} ${data.lots.length === 1 ? "lot" : "lots"}`}
                {data.publication_date && ` · publicada el ${formatDate(data.publication_date)}`}
                {data.source_url && (
                  <>
                    {" · "}
                    <a
                      href={data.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800"
                    >
                      obre la font ↗
                    </a>
                  </>
                )}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
              {/* L'estat vigent (A12): «Fora de termini» si el termini ha passat,
                  passi el que passi a la font; l'estat de la font es diu a part. */}
              <StatusPill level={statusView.level}>{statusView.label}</StatusPill>
              {statusView.sourceStatus && (
                <span className="text-[13px] text-muted">Estat a la font: {statusView.sourceStatus}</span>
              )}
              {data.source_publication_count > 1 && (
                <StatusPill level="attention">Publicada {data.source_publication_count} cops</StatusPill>
              )}
              {data.source_removed_at && (
                <StatusPill level="neutral">
                  Ja no disponible a la font (des del {formatDate(data.source_removed_at)})
                </StatusPill>
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
              {!analysis.loading && !analysis.error && analysisState === "absent" && tab !== "requisits" && (
                // Sense anàlisi (segons `analysis_state`, M1), el que toca és fer-la
                // (3B-2 §7.1.3). A la pestanya Requisits la CTA ja hi és (A8).
                <Link href={`/licitacions/${tenderId}?tab=requisits`} className={LINK}>
                  Analitza el plec
                </Link>
              )}
              {requirements.detail && (
                <span
                  className={
                    requirements.level === "blocking"
                      ? "text-danger"
                      : requirements.level === "attention"
                        ? "text-warning"
                        : "font-normal text-muted"
                  }
                >
                  · {requirements.detail}
                </span>
              )}
            </Meta>
            <Meta label="Import">
              <span className="tabular-nums">{amount.text}</span>
              {amount.note && <span className="text-[13px] font-normal text-muted">· {amount.note}</span>}
              {amount.budget && (
                <span className="block text-[13px] font-normal text-muted" data-amount-budget>
                  <span className="tabular-nums">{amount.budget.text}</span> · {amount.budget.note}
                </span>
              )}
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
          {/* L'entrada principal a la revisió de l'empresa, a la pestanya on s'arriba. */}
          <ReviewSummaryCard companyId={companyId} tenderId={tenderId} />
          {!match.loading &&
            (match.data ? (
              <PreFitPanel match={match.data} />
            ) : (
              <PreFitMissing
                profileReady={prefitProfileReady(profile.data?.counts)}
                calculating={prefitSaving}
                error={prefitError}
                onCalculate={() => void calculatePrefit()}
              />
            ))}

          <Card>
            <CardHeader title="Resum" />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
              <Fact label="Número d'expedient">
                <span className="font-mono">{data.expediente_number ?? "—"}</span>
              </Fact>
              <Fact label="Tipus de contracte">{labelOf(CONTRACT_TYPE_LABELS, data.contract_type, "Tipus desconegut")}</Fact>
              <Fact label="Procediment">{labelOf(PROCEDURE_TYPE_LABELS, data.procedure_type, "Tipus desconegut")}</Fact>

              {/* P2-06: cada fet diu la seva base; si la font no la publica, es diu, no «—». */}
              <Fact label={AMOUNT_BASIS_LABEL.vec}>
                <AmountValue value={data.estimated_value} currency={data.currency} manual={isManual} />
              </Fact>
              <Fact label={AMOUNT_BASIS_LABEL.pbl_net}>
                <AmountValue value={data.base_budget_without_tax} currency={data.currency} manual={isManual} />
              </Fact>
              <Fact label={AMOUNT_BASIS_LABEL.pbl_gross}>
                {budgetWithTax.expedient ? (
                  <PscpBudgetValue observation={budgetWithTax.expedient} />
                ) : (
                  <AmountValue value={data.base_budget} currency={data.currency} manual={isManual} />
                )}
              </Fact>
              <p className="text-caption text-muted sm:col-span-3">{AMOUNT_HELP}</p>
              <PscpAttribution text={budgetWithTax.attribution} />

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

              {/* Sense descripció publicada es diu així: mai el títol com a objecte
                  inventat, ni la plantilla de metadades del feed (3B-2 §7.1.2). */}
              <div className="sm:col-span-3">
                <p className="text-caption text-muted">Objecte</p>
                {data.description ? (
                  <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-ink">{data.description}</p>
                ) : (
                  // Una licitació oberta a mà no ve de cap feed (M5).
                  <p className="mt-1 text-sm text-muted">{isManual ? "Sense descripció" : "Objecte no publicat al feed"}</p>
                )}
              </div>

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
                  {[...mainCpvs, ...otherCpvs].map((cpv) => {
                    // El nom oficial del catàleg, mai el codi repetit (D4).
                    const description = cpvDescription(cpv);
                    return (
                      <span
                        key={cpv.cpv_code}
                        className={
                          cpv.is_main
                            ? "inline-flex items-center gap-2 border border-brand bg-brand-soft px-3 py-1 text-[12px]"
                            : "inline-flex items-center gap-2 border border-border-strong px-3 py-1 text-[12px]"
                        }
                      >
                        <span className="font-mono font-medium">{cpv.cpv_code}</span>
                        {description && <span className="text-muted">{description}</span>}
                        {cpv.is_main && (
                          <span className={KICKER} title={SOURCE_FIRST_CPV_HELP}>
                            {SOURCE_FIRST_CPV_LABEL}
                          </span>
                        )}
                      </span>
                    );
                  })}
                </div>
              )}
              {mainCpvs.length > 0 && (
                <p className="mt-3 text-caption text-muted" data-cpv-provenance>
                  {SOURCE_FIRST_CPV_HELP}
                </p>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader
              title={`Lots (${data.lots.length})`}
              description="Un contracte es pot licitar per lots independents."
            />
            {data.lots.length === 0 ? (
              <p className="px-3.5 py-3 text-[13px] text-muted">Aquest expedient no està dividit en lots.</p>
            ) : (
              <>
                <LotsPscpNote text={budgetWithTax.lotsNote} sumConflicts={budgetWithTax.lotsSumConflicts} />
                <DataTable<TenderLot>
                  columns={[
                    { key: "lot", header: "Lot", width: "8%", cell: (lot) => <span className="font-medium tabular-nums">{lot.lot_number ?? lot.external_lot_id}</span> },
                    { key: "title", header: "Títol", cell: (lot) => <span>{lot.title ?? "—"}</span> },
                    // F-06e: VEC, PBL sense IVA i PBL amb IVA, cadascun a la seva columna: el de la PLACSP
                    // si n'hi ha; si no, el de la PSCP (accepted « · PSCP», conflict pendent, 0 publicat).
                    // Sense cap import ni cap observació a cap lot (fora de Catalunya), com abans: una
                    // sola columna «Import» (P2-06).
                    ...(budgetWithTax.lotsWithoutAmounts
                      ? [
                          {
                            key: "amount",
                            header: "Import",
                            align: "right" as const,
                            width: "14%",
                            cell: (lot: TenderLot) => <AmountStack item={lot} currency={lot.currency} />,
                          },
                        ]
                      : LOT_AMOUNT_COLUMNS.map((column) => ({
                          key: column.key,
                          header: AMOUNT_BASIS_SHORT[column.basis],
                          align: "right" as const,
                          width: "12%",
                          cell: (lot: TenderLot) => <LotAmountValue cell={budgetWithTax.lotCells[lot.id][column.key]} currency={lot.currency} />,
                        }))),
                    {
                      key: "cpv",
                      header: "CPV",
                      hideBelow: "lg",
                      cell: (lot) => (
                        <div className="flex flex-wrap gap-1">
                          {lot.cpvs.slice(0, 4).map((cpv) => (
                            <span key={cpv.cpv_code} title={cpvDescription(cpv) ?? undefined} className="bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-muted">
                              {cpv.cpv_code}
                            </span>
                          ))}
                          {lot.cpvs.length > 4 && <span className="text-[11px] text-faint">+{lot.cpvs.length - 4}</span>}
                        </div>
                      ),
                    },
                    {
                      key: "place",
                      header: "Ubicació",
                      hideBelow: "lg",
                      cell: (lot) => <span className="text-muted">{[lot.place_of_execution, lot.nuts_code].filter(Boolean).join(" · ") || "—"}</span>,
                    },
                    { key: "deadline", header: "Termini", width: "12%", cell: (lot) => <span className="whitespace-nowrap text-muted">{lot.deadline_at ? formatDate(lot.deadline_at) : "—"}</span> },
                  ]}
                  rows={data.lots}
                  rowKey={(lot) => lot.id}
                  stackBelow="md"
                />
              </>
            )}
          </Card>
        </div>
      )}

      {/* ============================================================ Plecs === */}
      {tab === "plecs" && (
        <div className="space-y-4">
          {jobFailed && jobState ? (
            <ErrorState
              title={jobErrorAction(jobState.error_code).title}
              description="Cap document s'ha perdut: els que ja estaven processats continuen igual. Torna a demanar el processat dels que faltin."
              action={
                <Button
                  variant="primary"
                  disabled={processing}
                  onClick={() =>
                    void processDocuments(lastRequested.length > 0 ? lastRequested : data.documents.map((document) => document.id))
                  }
                >
                  Torna-ho a demanar
                </Button>
              }
              technical={{
                code: jobState.error_code,
                requestId: jobState.id,
                at: jobState.finished_at ?? undefined,
                method: "process_tender_documents",
              }}
            />
          ) : jobPartial ? (
            <Banner
              level="attention"
              kicker="Processat"
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => documentsTable.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                >
                  Veure les files
                </Button>
              }
            >
              Algun document no s&apos;ha pogut processar: en veuràs l&apos;error a la seva fila.
            </Banner>
          ) : (
            processError && <ErrorNotice message={processError} />
          )}

          <DocumentReviewPanel
            workspaceId={candidature.workspace?.id ?? null}
            documents={data.documents.length}
            onReviewed={() => void reload()}
          />

          <Card>
            <CardHeader
              title={`Plecs (${topLevelCount})`}
              description="Processa un plec per descarregar-ne l'original i extreure'n el text per pàgina. La selecció només governa el processament; no limita quins documents utilitza l'anàlisi de requisits."
              action={jobStatus ? <StatusPill level={jobStatus.level}>{jobStatus.label}</StatusPill> : undefined}
            />
            {/* Bloc 2, etapa 3: només a una licitació de la PSCP (també amb 0 documents). */}
            {data.portal_sync && (
              <PortalSyncBar
                companyId={companyId}
                portalSync={data.portal_sync}
                state={portalSync}
                canRequest={canProcess}
                permissionKnown={Boolean(limits.data)}
              />
            )}
            {/* També amb 0 documents vigents (N4): «Cap document analitzable» i l'estat del paquet. */}
            <div className="space-y-2 border-b border-border px-5 py-3">
              <PlecsReadingLine documents={data.documents} />
              <PackageNotice pkg={data.package ?? null} documents={data.documents} />
            </div>
            {limits.data && !canProcess && (
              <div className="border-b border-border px-4 py-3">
                <PermissionNote
                  who="qui té permís per processar plecs"
                  what="demanar el processat dels plecs"
                  action={<span className="text-muted">Demana-ho a qui administra l&apos;empresa.</span>}
                >
                  <Button variant="primary" size="sm" disabled>
                    Processa els triats
                  </Button>
                </PermissionNote>
              </div>
            )}
            {data.documents.length === 0 ? (
              <EmptyState
                title="Sense documents publicats"
                description="La font no ha publicat cap document per a aquest expedient. Quan en publiqui, apareixeran aquí per processar-los."
                action={
                  data.source_url ? (
                    <a
                      href={data.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ui-action-md inline-flex items-center justify-center rounded-control border border-border-strong bg-surface px-3 font-heading text-[13.5px] font-semibold text-ink shadow-control hover:bg-subtle"
                    >
                      Obre l&apos;expedient a la font ↗
                    </a>
                  ) : (
                    <LinkButton href="/licitacions" variant="secondary" size="md">
                      Torna a Oportunitats
                    </LinkButton>
                  )
                }
              />
            ) : (
              <div ref={documentsTable}>
                <DataTable<TenderDocumentMeta>
                  columns={documentColumns}
                  // Els àlies van plegats sota el seu canònic, no en una fila pròpia.
                  rows={topLevelDocuments(data.documents)}
                  rowKey={(document) => document.id}
                  isSelected={(document) => selected.has(document.id)}
                  stackBelow="md"
                  footer={
                    selected.size > 0 ? (
                      <>
                        <span className="font-medium text-ink">{selection.count}</span>
                        <Button variant="primary" size="sm" onClick={() => void processSelected()} disabled={processing}>
                          {selection.action}
                        </Button>
                      </>
                    ) : (
                      <span>
                        {data.documents.length} {data.documents.length === 1 ? "document" : "documents"} · tria&apos;n
                        per processar-los junts
                      </span>
                    )
                  }
                />
              </div>
            )}
            <RemovedDocuments documents={data.removed_documents ?? []} />
          </Card>
        </div>
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
            // Només a una licitació oberta a mà i pròpia: `Requirement` no té
            // propietari, així que escriure'n sobre una de pública els faria
            // aparèixer a totes les empreses.
            editable={isManual}
            analysis={analysis.data}
            documents={data.documents}
            pkg={data.package ?? null}
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
            analysisState={analysisState}
            onReload={async () => {
              await compliance.reload();
            }}
          />
        </div>
      )}

      {/* ========================================================= Anàlisi === */}
      {tab === "analisi" && (
        <AnalysisTab
          companyId={companyId}
          tenderId={tenderId}
          requirements={analysis.data}
          requirementsFailed={Boolean(analysis.error)}
          workspaceId={candidature.workspace?.id ?? null}
          candidatureChecked={candidature.checked}
        />
      )}

      {/* ======================================================== Historial === */}
      {tab === "historial" && (
        <div className="space-y-4">
          <TenderChangeHistory
            revisions={history.data}
            // Vigents i retirats: un canvi d'un document retirat també es diu pel nom.
            documents={[...data.documents, ...(data.removed_documents ?? [])].map((document) => ({
              url: document.url,
              name: document.name,
            }))}
            loading={history.loading}
            error={history.error}
            onReload={history.reload}
          />

          <Card>
            <CardHeader
              title="Publicacions de la font"
              description="Cada vegada que la font publica aquest expedient en queda una còpia, amb el seu hash i la seva data. Que hi hagi una publicació nova no vol dir que hagi canviat res: què ha canviat de veritat es veu a «Com ha evolucionat l'expedient», a la part «Publicat per la font»."
            />
            <div className="grid gap-4 border-b border-border px-5 py-4 sm:grid-cols-4">
              <Fact label="Vista per primer cop">{formatDateTime(data.first_seen_at)}</Fact>
              <Fact label="Vista per últim cop">{formatDateTime(data.last_seen_at)}</Fact>
              <Fact label="Últim canvi">{formatDateTime(data.last_changed_at)}</Fact>
              <Fact label="Publicacions rebudes">{data.source_publication_count}</Fact>
            </div>
            {revisions.loading && !revisions.data ? (
              <PageSkeleton variant="table" columns={["Publicació", "Rebuda", "Publicada per la font", "Hash del contingut", "Mida", "Estat"]} />
            ) : (
              <DataTable<TenderRevision>
                columns={[
                  { key: "number", header: "Publicació", width: "10%", cell: (revision) => <span className="font-medium tabular-nums">{revision.revision_number}</span> },
                  { key: "fetched", header: "Rebuda", cell: (revision) => <span className="whitespace-nowrap text-muted">{formatDateTime(revision.fetched_at)}</span> },
                  { key: "source", header: "Publicada per la font", hideBelow: "lg", cell: (revision) => <span className="whitespace-nowrap text-muted">{formatDateTime(revision.source_updated_at)}</span> },
                  {
                    key: "hash",
                    header: "Hash del contingut",
                    hideBelow: "lg",
                    cell: (revision) => (
                      <code className="font-mono text-[11px] text-faint" title={revision.raw_hash}>
                        {revision.raw_hash.slice(0, 16)}…
                      </code>
                    ),
                  },
                  { key: "size", header: "Mida", align: "right", width: "10%", cell: (revision) => <span className="tabular-nums text-muted">{(revision.raw_size / 1024).toFixed(1)} KB</span> },
                  {
                    key: "state",
                    header: "Estat",
                    width: "12%",
                    cell: (revision) => (
                      <StatusPill level={revision.processing_status === "processed" ? "ready" : revision.processing_status === "failed" ? "blocking" : "attention"} size="sm">
                        {publicationStatusLabel(revision.processing_status)}
                      </StatusPill>
                    ),
                  },
                ]}
                rows={revisions.data ?? []}
                rowKey={(revision) => revision.id}
                stackBelow="md"
                emptyState={
                  <p className="px-3.5 py-4 text-[13px] text-muted">Encara no s&apos;ha rebut cap publicació d&apos;aquest expedient.</p>
                }
              />
            )}
          </Card>

          {data.traceability && (
            // Traçabilitat tècnica (hashes, SourceRecord) plegada per defecte (addendum 3B-2, D5).
            <details className="group">
              <summary className="cursor-pointer list-none text-[13px] font-medium text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
                Detalls tècnics
              </summary>
            <Card className="mt-2">
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
            </details>
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
