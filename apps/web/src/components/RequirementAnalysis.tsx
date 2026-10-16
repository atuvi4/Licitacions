"use client";

/**
 * Secció «Anàlisi del plec» de la fitxa de licitació (handoff L11).
 *
 * Dues regles de presentació que no són estètiques:
 *
 * 1. Cap requisit es mostra sense la seva evidència i el seu enllaç a la font.
 *    Si algun dia la cita desapareix de la targeta, l'usuari deixa de poder
 *    distingir el que diu el plec del que ha deduït un model.
 * 2. «Obre la pàgina» apunta a la revisió d'extracció que va originar la cita,
 *    no a la vigent. Si el document s'ha reextret, la cita continua sent la
 *    d'abans: és exactament el que la fa verificable.
 *
 * I una de flux: cap anàlisi es demana sense haver-ne vist el volum. Sempre
 * preview → confirmació humana; el botó «Analitza» només demana el preview.
 */

import Link from "next/link";
import { useCallback, useState } from "react";

import { ManualRequirementForm, canWriteRequirements } from "@/components/ManualRequirementForm";
import { useSession } from "@/components/SessionProvider";

import { AnalysisQuotePanel } from "@/components/AnalysisQuotePanel";
import { Attribution, Banner, Button, Card, CardHeader, ErrorNotice, ErrorState, EvidenceRef, LinkButton, StatusPill } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import {
  type AnalysisLimits,
  type AnalysisQuote,
  type JobRequest,
  type QuoteAcceptance,
  confirmAnalysis,
  jobErrorMessage,
  previewAnalysis,
  useJobRequest,
} from "@/lib/jobs";
import {
  ANALYSIS_BASIS_HINTS,
  ANALYSIS_BASIS_LABELS,
  ANALYSIS_BASIS_LEVEL,
  ANALYSIS_DISCLAIMER,
  ANALYSIS_STATE_LEVEL,
  ANALYSIS_STATUS_TEXT,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  EVALUATION_LABELS,
  EXTRACTION_STATUS_LABELS,
  OBLIGATION_LABELS,
  REVIEW_LEVEL,
  SKIP_REASON_LABELS,
  analysisState,
  isTrustworthyAnalysis,
  jobErrorAction,
} from "@/lib/labels";
import {
  COMPLETED_EMPTY_TEXT,
  RUN_HISTORY_LIMIT,
  RUN_STATUS_LABELS,
  SIMULATION_NOT_CURRENT_TEXT,
  canAdoptAnalysis,
  historicRuns,
  notCurrentReason,
  requirementAnalysisState,
  type AwardCriterion,
  type ExtractionRun,
  type Requirement,
  type RequirementAnalysis,
  type RequirementEvidence,
  type RequirementRunAdoption,
  type ReviewStatus,
} from "@/lib/requirements";
import type { TenderDocumentMeta, TenderPackage } from "@/lib/tenders";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Data i hora d'una passada: el mateix dia en pot haver-hi més d'una. */
const RUN_DATE = new Intl.DateTimeFormat("ca-ES", { dateStyle: "medium", timeStyle: "short" });
function runDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : RUN_DATE.format(date);
}

/**
 * L'historial d'anàlisis no vigents (P2-01): quantes n'hi ha i per què cap no
 * compta. Només lectura: no ensenya els seus requisits ni permet promoure'n cap.
 */
function RunHistoryNotice({ runs, hasEffective }: { runs: ExtractionRun[]; hasEffective: boolean }) {
  if (runs.length === 0) return null;
  const title =
    runs.length === 1 ? "1 anàlisi anterior d'aquests plecs no és vigent" : `${runs.length} anàlisis anteriors d'aquests plecs no són vigents`;
  return (
    <div
      className={
        hasEffective ? "mx-4 my-3 border border-border px-3.5 py-2.5" : "m-4 border border-border-strong bg-subtle px-3.5 py-2.5"
      }
    >
      <p className="font-heading text-[14px] font-semibold text-ink">{title}</p>
      <p className="mt-0.5 text-body text-muted">
        Es conserven com a historial i no compten: cap dels seus requisits ni criteris es mostra aquí ni entra en cap
        recompte.
        {!hasEffective && " Per això aquesta licitació continua sense anàlisi vigent."}
      </p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-caption text-muted">Historial (només lectura)</summary>
        <ul className="mt-1.5 flex flex-col gap-2">
          {runs.map((item) => (
            <li key={item.id} className="text-[13px]">
              <p className="text-ink">
                <span className="tabular-nums">{runDate(item.started_at)}</span> · {RUN_STATUS_LABELS[item.status]} ·{" "}
                <span className="tabular-nums">{item.requirements_extracted}</span> requisits ·{" "}
                <span className="tabular-nums">{item.award_criteria_extracted}</span> criteris · fragments{" "}
                <span className="tabular-nums">
                  {item.chunks_completed} de {item.chunks_total}
                </span>
              </p>
              <p className="text-muted">{notCurrentReason(item)}</p>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function evidenceHref(tenderId: string, evidence: RequirementEvidence): string | null {
  return evidence.tender_document_id
    ? `/licitacions/${tenderId}/documents/${evidence.tender_document_id}?page=${evidence.page_number}&extraction=${evidence.extraction_id}`
    : null;
}

/** La cita: línia compacta sempre visible; el text literal, plegat. */
function Evidence({ tenderId, evidence }: { tenderId: string; evidence: RequirementEvidence[] }) {
  if (evidence.length === 0) {
    return <EvidenceRef documentName={null} pageNumber={null} href={null} />;
  }
  return (
    <div className="space-y-1.5">
      {evidence.map((item) => (
        <details key={item.id} className="group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
            <EvidenceRef
              compact
              documentName={item.document_name}
              pageNumber={item.page_number}
              extractionRevision={item.extraction_revision}
              href={evidenceHref(tenderId, item)}
            />
            <span className="ml-2 text-caption text-muted group-open:hidden">· mostra la cita</span>
          </summary>
          <blockquote className="mt-1.5 border-l-2 border-accent-500 pl-2.5 text-[13px] leading-relaxed text-ink italic">
            «{item.quote}»
          </blockquote>
        </details>
      ))}
    </div>
  );
}

/**
 * Nomes lectura. Aquesta vista ja no revisa: els botons antics escrivien
 * l'estat directament al requisit extret, compartit per totes les empreses,
 * saltant-se la revisio de l'empresa. La revisio es fa a `/revisio`.
 * Es mostra com el que és, un senyal de l'extracció: text «Extracció …» i
 * pastilla de contorn, perquè no es confongui amb l'estat de la revisió.
 */
function ExtractionStatusPill({ current }: { current: ReviewStatus }) {
  return (
    <StatusPill level={REVIEW_LEVEL[current]} size="sm" outline>
      {EXTRACTION_STATUS_LABELS[current]}
    </StatusPill>
  );
}

function RequirementRow({
  tenderId,
  requirement,
  onEdit,
  onArchive,
}: {
  tenderId: string;
  requirement: Requirement;
  /** Només als escrits a mà. Un d'extret no s'edita: seria reescriure la
   * sortida d'una execució concreta, i llavors la run ja no diria què va
   * produir. Per als extrets hi ha la revisió, que ja hi era. */
  onEdit?: (requirement: Requirement) => void;
  onArchive?: (requirement: Requirement) => void;
}) {
  return (
    <li className="px-4 py-3.5 sm:px-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-ink">{requirement.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
            <span className={KICKER}>{OBLIGATION_LABELS[requirement.obligation]}</span>
            {/*
              Atribució segons QUI l'ha escrit. Un requisit manual no porta
              confiança d'IA: dir "confiança alta del model" d'una cosa que ha
              escrit una persona seria atribuir-li una autoria falsa.
            */}
            {requirement.origin === "manual" ? (
              <Attribution kind="human" label="Escrit a mà" />
            ) : (
              <Attribution kind="ai" confidence={requirement.confidence ?? undefined} />
            )}
            {requirement.has_conflict && (
              <StatusPill level="attention" size="sm">
                Possible conflicte entre documents
              </StatusPill>
            )}
          </p>
          {requirement.description && <p className="mt-1 text-[13px] text-muted">{requirement.description}</p>}
          <div className="mt-2">
            <Evidence tenderId={tenderId} evidence={requirement.evidence} />
          </div>
          {requirement.citations.length > 0 && (
            // La provinença d'un requisit escrit a mà: cites a una versió i
            // una pàgina concretes, que no es mouen quan arriba un document nou.
            <ul className="mt-2 grid gap-1">
              {requirement.citations.map((citation) => (
                <li key={citation.id} className="text-caption text-muted">
                  {citation.document_name ?? "Document"}
                  {citation.page_number ? ` · p. ${citation.page_number}` : ""}
                  {citation.location_label ? ` · ${citation.location_label}` : ""}
                  {citation.quote ? ` · «${citation.quote}»` : ""}
                </li>
              ))}
            </ul>
          )}
          {requirement.origin === "manual" && (onEdit || onArchive) && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {onEdit && (
                <Button variant="ghost" size="sm" onClick={() => onEdit(requirement)}>
                  Edita
                </Button>
              )}
              {onArchive && (
                <Button variant="ghost" size="sm" onClick={() => void onArchive(requirement)}>
                  Arxiva
                </Button>
              )}
            </div>
          )}
        </div>
        <div className="shrink-0 md:max-w-[40%]">
          <ExtractionStatusPill current={requirement.review_status} />
        </div>
      </div>
    </li>
  );
}

type Filter = "all" | "mandatory" | "review" | "scored";

function needsReview(requirement: Requirement): boolean {
  return requirement.review_status === "needs_review" || requirement.has_conflict || requirement.confidence === "low";
}

export function RequirementAnalysisSection({
  tenderId,
  companyId,
  analysis,
  documents,
  pkg,
  loading,
  error,
  onReload,
  editable = false,
}: {
  tenderId: string;
  companyId: string | null;
  analysis: RequirementAnalysis | null;
  /** Els plecs vigents de la fitxa: l'estat buit diu si ja tenen text (A5). */
  documents?: TenderDocumentMeta[];
  /** El paquet de la fitxa: la quota diu què falta sota la casella d'acceptar-lo (M4). */
  pkg?: TenderPackage | null;
  loading: boolean;
  error: string | null;
  onReload: () => Promise<void>;
  /**
   * Si en aquesta licitació s'hi poden escriure requisits a mà. Només a una
   * d'oberta a mà i pròpia: `Requirement` no té propietari, així que escriure'n
   * sobre una de pública els faria aparèixer a totes les empreses.
   */
  editable?: boolean;
}) {
  const { membership } = useSession();
  const canWrite = editable && canWriteRequirements(membership?.role);
  const [editing, setEditing] = useState<Requirement | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const archive = useCallback(
    async (requirement: Requirement) => {
      if (!companyId) return;
      await api.post(
        `/companies/${companyId}/tenders/${tenderId}/requirements/${requirement.id}/archive`,
        {},
      );
      await onReload();
    },
    [companyId, onReload, tenderId],
  );
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobFailure, setJobFailure] = useState<JobRequest | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  // Cap anàlisi es demana sense haver-ne vist el volum: primer el preview.
  const [quote, setQuote] = useState<AnalysisQuote | null>(null);
  const [quoteNotice, setQuoteNotice] = useState<string | null>(null);
  const limits = useResource<AnalysisLimits>(companyId ? `/companies/${companyId}/analysis-limits` : null);
  const canConfirm = limits.data?.can_confirm ?? false;

  const state = analysisState(analysis);
  const run = analysis?.run ?? null;
  // Les runs d'aquests plecs, siguin o no la vigent: una simulació o una lectura
  // acabada sense resultats mai són vigents i no arriben a `analysis.run`, però
  // s'han d'explicar (3B-2 §6.3); i les altres no vigents, com a historial (P2-01).
  const latestRuns = useResource<ExtractionRun[]>(`/tenders/${tenderId}/analysis/runs?limit=${RUN_HISTORY_LIMIT}`);
  const latest = Array.isArray(latestRuns.data) ? (latestRuns.data[0] ?? null) : null;
  const latestNotCurrent = latest && latest.id !== run?.id ? latest : null;
  const history = historicRuns(latestRuns.data, run?.id ?? null);
  const { reload: reloadLatest } = latestRuns;
  const [acceptance, setAcceptance] = useState<QuoteAcceptance>({});
  const [quoteForce, setQuoteForce] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [adoptionNotice, setAdoptionNotice] = useState<string | null>(null);
  const canAdopt = canAdoptAnalysis(membership?.role);

  // L'anàlisi s'executa en segon pla: aquí només es demana i se'n segueix
  // l'estat. Quan acaba, es recarrega el que se n'ha obtingut.
  const job = useJobRequest(companyId, tenderId, "analyze_tender_requirements", async (done) => {
    if (done.status === "failed") {
      setJobFailure(done);
      setActionError(jobErrorMessage(done.error_code));
    }
    await Promise.all([onReload(), reloadLatest()]);
  });
  const running = submitting || job.active;

  async function analyse(force: boolean) {
    if (!companyId) {
      setActionError("Cal una empresa activa per demanar l'anàlisi.");
      return;
    }
    // En una licitació pública la selecció és la canònica del servidor: el tenant
    // no força res (422 `public_tender_canonical_only`). Només en una de manual.
    const effectiveForce = editable && force;
    setSubmitting(true);
    setActionError(null);
    setJobFailure(null);
    setQuoteNotice(null);
    setAcceptance({});
    setQuoteForce(effectiveForce);
    try {
      setQuote(await previewAnalysis(companyId, tenderId, effectiveForce));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut calcular el volum.");
    } finally {
      setSubmitting(false);
    }
  }

  /** Una acceptació explícita canvia la quota: es torna a demanar el volum amb ella. */
  async function accept(change: QuoteAcceptance) {
    if (!companyId) return;
    const next = { ...acceptance, ...change };
    setAccepting(true);
    setActionError(null);
    try {
      setQuote(await previewAnalysis(companyId, tenderId, quoteForce, next));
      setAcceptance(next);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut recalcular el volum.");
    } finally {
      setAccepting(false);
    }
  }

  /** L'empresa adopta l'anàlisi canònica nova com la seva base (3B-2 §6.5). */
  async function adopt() {
    if (!companyId || !analysis?.canonical_run_id) return;
    setAdopting(true);
    setActionError(null);
    setAdoptionNotice(null);
    try {
      const adopted = await api.post<RequirementRunAdoption>(`/tenders/${tenderId}/analysis/requirements/adopt`, {
        run_id: analysis.canonical_run_id,
        company_id: companyId,
      });
      setAdoptionNotice(
        adopted.reconciliation_required
          ? "Feta. La revisió de l'empresa parlava d'una altra anàlisi: cal reconciliar la revisió amb la nova."
          : "Feta: aquesta és ara l'anàlisi de l'empresa.",
      );
      await onReload();
    } catch (caught) {
      setActionError(caught instanceof Error && caught.message ? caught.message : "No s'ha pogut adoptar l'anàlisi.");
    } finally {
      setAdopting(false);
    }
  }

  async function confirm() {
    if (!companyId || !quote) return;
    setSubmitting(true);
    setActionError(null);
    try {
      job.track(await confirmAnalysis(companyId, quote.id));
      setQuote(null);
      setQuoteNotice(null);
      void limits.reload();
    } catch (caught) {
      const replaced = caught instanceof ApiError ? caught.details.quote : undefined;
      if (caught instanceof ApiError && replaced) {
        // El preview ja no valia (plecs canviats o caducat): s'ensenya el nou.
        setQuote(replaced as AnalysisQuote);
        setQuoteNotice(
          caught.code === "quote_expired"
            ? "El preview havia caducat: revisa el volum actualitzat."
            : "El volum ha canviat des del preview: revisa'l abans de confirmar.",
        );
      } else if (caught instanceof ApiError) {
        setActionError(jobErrorMessage(caught.code));
      } else {
        setActionError("No s'ha pogut demanar l'anàlisi.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const requirements = analysis?.requirements ?? [];
  const criteria = analysis?.award_criteria ?? [];
  const counts = {
    all: requirements.length,
    mandatory: requirements.filter((item) => item.obligation === "mandatory").length,
    review: requirements.filter(needsReview).length,
    scored: requirements.filter((item) => item.obligation === "scored").length,
  };
  const visible = requirements.filter((item) => {
    if (filter === "mandatory") return item.obligation === "mandatory";
    if (filter === "review") return needsReview(item);
    if (filter === "scored") return item.obligation === "scored";
    return true;
  });
  const grouped = CATEGORY_ORDER.map((category) => ({
    category,
    items: visible.filter((requirement) => requirement.category === category),
  })).filter((group) => group.items.length > 0);

  const trustworthy = !run || isTrustworthyAnalysis(run.analysis_basis);
  const failure = jobFailure ?? (job.request?.status === "failed" ? job.request : null);
  const failureAction = failure ? jobErrorAction(failure.error_code) : null;
  const mainButtonLabel = run ? "Torna a analitzar" : "Analitza el plec";
  // Sense run però amb `analysis_state=current` (N3): la licitació té requisits
  // escrits a mà i no hi ha cap anàlisi automàtica. No és cap buit: el cos mostra
  // els requisits, i l'anàlisi automàtica queda com a acció secundària.
  const manualOnly = run === null && requirementAnalysisState(analysis) === "current";
  // Una sola CTA «Analitza» (A8): sense anàlisi la porta l'estat buit; la
  // capçalera només té botó quan hi ha una anàlisi (tornar-hi), feina en curs o
  // requisits manuals sense run.
  const headerButton = run !== null || job.active || quote !== null || manualOnly;
  // L'estat del plec segueix els documents (A5): sense text no hi ha res a llegir;
  // amb text, els requisits encara no s'han analitzat.
  const textExtracted = documents?.some((document) => document.analyzable && document.latest_file?.analysis_status === "extracted");
  const emptyTitle =
    documents === undefined
      ? "Requisits encara no analitzats"
      : textExtracted
        ? "Text extret · requisits encara no analitzats"
        : "Sense text extret";
  const emptyDescription =
    documents !== undefined && !textExtracted
      ? "Processa els plecs a la pestanya Plecs per extreure'n el text: fins llavors no hi ha res a analitzar. «0 bloquejos» aquí no vol dir que no n'hi hagi: vol dir que ningú ho ha mirat."
      : "«0 bloquejos» aquí no vol dir que no n'hi hagi: vol dir que ningú ho ha mirat. Els comptadors es queden a zero fins que hi hagi una anàlisi.";

  const FILTERS: Array<{ key: Filter; label: string; level: "blocking" | "attention" | "neutral" }> = [
    { key: "all", label: "Tots", level: "neutral" },
    { key: "mandatory", label: "Obligatoris", level: "neutral" },
    // Incidències = marcats per l'anàlisi, en conflicte o de confiança baixa: no
    // és el «Cal revisar» de la revisió. Puntuables = requisits que l'extracció
    // ha classificat amb obligació «puntuable»; no són els criteris que puntuen.
    { key: "review", label: "Incidències de l'extracció", level: "attention" },
    { key: "scored", label: "Puntuables segons l'extracció", level: "neutral" },
  ];

  return (
    <>
      <ManualRequirementForm
        open={formOpen}
        tenderId={tenderId}
        requirement={editing}
        onClose={() => setFormOpen(false)}
        onSaved={onReload}
      />
      <Card>
        <CardHeader
          title="Anàlisi automàtica"
          description="Extreu els requisits i els criteris d'adjudicació dels documents processats."
          action={
            <div className="flex flex-wrap items-center gap-2">
              {canWrite && (
                // Escriure un requisit no analitza res: és una afirmació
                // humana, i neix sense revisar.
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setEditing(null);
                    setFormOpen(true);
                  }}
                >
                  Afegeix un requisit
                </Button>
              )}
              {/* Amb un motor simulat o sense motor, «Completa» seria una
                  afirmació falsa sobre el plec: el procés ha acabat, però no
                  ha llegit res. Es diu el que és, i la pastilla no pot ser
                  «a punt». */}
              {run && !trustworthy ? (
                <StatusPill level={ANALYSIS_BASIS_LEVEL[run.analysis_basis]}>{ANALYSIS_BASIS_LABELS[run.analysis_basis]}</StatusPill>
              ) : (
                <StatusPill level={job.active ? "running" : ANALYSIS_STATE_LEVEL[state]}>
                  {ANALYSIS_STATUS_TEXT(analysis, job.request)}
                </StatusPill>
              )}
              {headerButton && (
                <Button
                  variant={run || manualOnly ? "secondary" : "primary"}
                  onClick={() => void analyse(state === "stale")}
                  disabled={running || loading || quote !== null}
                  loading={submitting}
                >
                  {mainButtonLabel}
                </Button>
              )}
            </div>
          }
        />

        {job.active && (
          <div className="border-b border-border px-5 py-4">
            <p className="flex flex-wrap items-center gap-2">
              <StatusPill level="running">En curs</StatusPill>
              <span className="text-[14px] text-ink">
                Llegint el plec i extraient requisits
                {job.request?.progress
                  ? ` · ${job.request.progress.done} de ${job.request.progress.total} fragments`
                  : " · la font no informa del progrés"}
              </span>
            </p>
            <p className="mt-1.5 text-[13px] text-muted">
              Pots tancar la pàgina: el seguiment es reprèn en tornar i el resultat quedarà aquí.
            </p>
          </div>
        )}

        {failure && failureAction && !job.active && (
          <div className="border-b border-border">
            <ErrorState
              title={failureAction.title}
              description={
                run
                  ? `L'anàlisi anterior es conserva i és la que veus a sota. ${jobErrorMessage(failure.error_code)}`
                  : jobErrorMessage(failure.error_code)
              }
              action={
                failureAction.action.kind === "process_first" || failureAction.action.kind === "choose_documents" ? (
                  <LinkButton href={`/licitacions/${tenderId}?tab=plecs`} variant="primary" size="md">
                    {failureAction.action.kind === "process_first" ? "Processa els plecs primer" : "Veure els plecs"}
                  </LinkButton>
                ) : failureAction.action.kind === "retry" ? (
                  <Button variant="primary" onClick={() => void analyse(true)} disabled={running || quote !== null}>
                    Torna-ho a demanar
                  </Button>
                ) : (
                  <Button variant="secondary" onClick={() => setJobFailure(null)}>
                    D&apos;acord
                  </Button>
                )
              }
              technical={{
                code: failure.error_code,
                requestId: failure.id,
                at: failure.finished_at ?? undefined,
                method: "analyze_tender_requirements",
              }}
            />
          </div>
        )}

        {actionError && !failure && <ErrorNotice message={actionError} />}
        {error && !actionError && <ErrorNotice message={error} />}

        {quote && (
          <AnalysisQuotePanel
            quote={quote}
            pkg={pkg ?? null}
            documents={documents}
            notice={quoteNotice}
            canConfirm={canConfirm}
            rerun={run !== null}
            confirming={submitting}
            onConfirm={() => void confirm()}
            onCancel={() => {
              setQuote(null);
              setQuoteNotice(null);
              setAcceptance({});
            }}
            onAccept={(change) => void accept(change)}
            accepting={accepting}
          />
        )}

        {analysis?.adoption_available && (
          // 3B-2 §6.5: una anàlisi canònica nova no substitueix la que l'empresa té
          // acceptada; l'empresa l'adopta, i només qui decideix la licitació.
          <div className="m-4 flex flex-col gap-2 border border-border-strong bg-subtle px-3.5 py-2.5 sm:flex-row sm:items-center sm:gap-3.5">
            <div className="min-w-0 flex-1">
              <p className="font-heading text-[14px] font-semibold text-ink">Hi ha una anàlisi nova del plec</p>
              <p className="text-body text-muted">
                {canAdopt
                  ? "L'empresa continua llegint l'anterior fins que l'adopteu. Adoptar-la no esborra la revisió feta."
                  : "L'empresa continua llegint l'anterior. Només qui decideix la licitació la pot adoptar."}
              </p>
            </div>
            {canAdopt && (
              <Button variant="primary" size="sm" onClick={() => void adopt()} loading={adopting} disabled={adopting}>
                Adopta-la
              </Button>
            )}
          </div>
        )}
        {adoptionNotice && <p className="mx-4 mb-2 text-[13px] text-ink">{adoptionNotice}</p>}

        {latestNotCurrent && latestNotCurrent.status === "completed_empty" && (
          <div className="m-4 border border-warning bg-warning-soft px-3.5 py-2.5">
            <p className="font-heading text-[14px] font-semibold text-warning">{COMPLETED_EMPTY_TEXT}</p>
            <p className="mt-0.5 text-body text-ink">
              L&apos;última lectura dels plecs ha acabat sense cap requisit ni criteri. O el plec no en diu res, o
              l&apos;extracció ha fallat sense dir-ho: ho ha de mirar una persona. No és la vigent.
            </p>
          </div>
        )}
        {latestNotCurrent &&
          latestNotCurrent.status !== "completed_empty" &&
          latestNotCurrent.run_basis !== "real" &&
          latestNotCurrent.run_basis !== "gold" && (
            <div className="m-4 border border-warning bg-warning-soft px-3.5 py-2.5">
              <p className="font-heading text-[14px] font-semibold text-warning">{SIMULATION_NOT_CURRENT_TEXT}</p>
              <p className="mt-0.5 text-body text-ink">
                La darrera anàlisi d&apos;aquests plecs és una simulació: no ha llegit el plec i mai no compta com a
                vigent. Els requisits d&apos;aquí només surten d&apos;una lectura real.
              </p>
            </div>
          )}

        <RunHistoryNotice runs={history} hasEffective={run !== null} />

        {run && !trustworthy && (
          <div className="m-4 flex flex-col gap-1 border border-warning bg-warning-soft px-3.5 py-2.5 sm:flex-row sm:items-center sm:gap-3.5">
            <span className="shrink-0 font-heading text-[13px] font-semibold tracking-[0.06em] text-warning uppercase">
              {ANALYSIS_BASIS_LABELS[run.analysis_basis]}
            </span>
            <p className="text-body text-ink">{ANALYSIS_BASIS_HINTS[run.analysis_basis]}</p>
          </div>
        )}

        {analysis?.is_stale && (
          <Banner
            level="attention"
            kicker="Obsoleta"
            className="m-4"
            action={
              <Button size="sm" variant="secondary" onClick={() => void analyse(true)} disabled={running || quote !== null}>
                Torna a analitzar
              </Button>
            }
          >
            Els documents han canviat des d&apos;aquesta anàlisi.
          </Banner>
        )}

        {!run && !job.active && !quote && !manualOnly && (
          <EmptyState
            title={emptyTitle}
            description={emptyDescription}
            action={
              <Button variant="primary" onClick={() => void analyse(false)} disabled={running || loading}>
                Analitza el plec
              </Button>
            }
          />
        )}

        {manualOnly && !job.active && !quote && (
          <p className="px-5 py-4 text-[13px] text-muted">
            Els requisits d&apos;aquesta licitació són escrits a mà. No s&apos;ha fet cap anàlisi automàtica del plec:
            és opcional i es pot demanar des de la capçalera d&apos;aquesta targeta.
          </p>
        )}

        {run && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-4 text-[13px] md:grid-cols-4">
            <div>
              <dt className={KICKER}>Requisits extrets</dt>
              <dd className="mt-0.5 tabular-nums">{run.requirements_extracted}</dd>
            </div>
            <div>
              <dt className={KICKER}>Criteris detectats</dt>
              <dd className="mt-0.5 tabular-nums">{run.award_criteria_extracted}</dd>
            </div>
            <div>
              <dt className={KICKER}>Documents · pàgines</dt>
              <dd className="mt-0.5 tabular-nums">
                {run.documents_analyzed} · {run.pages_analyzed}
              </dd>
            </div>
            <div>
              <dt className={KICKER}>Fragments llegits</dt>
              <dd className="mt-0.5 tabular-nums">
                {run.chunks_completed} de {run.chunks_total}
                {run.chunks_failed > 0 && (
                  <span className="text-danger">
                    {" "}
                    · {run.chunks_failed} {run.chunks_failed === 1 ? "fallit" : "fallits"}
                  </span>
                )}
              </dd>
            </div>
          </dl>
        )}
        {run && (
          <div className="px-5 pb-4">
              <details>
                <summary className="cursor-pointer text-caption text-muted">Detall tècnic de l&apos;anàlisi</summary>
                <p className="mt-1 font-mono text-[11.5px] text-muted">
                  {ANALYSIS_BASIS_LABELS[run.analysis_basis]} · {run.provider} · {run.model} · {run.extractor_version} ·{" "}
                  {run.prompt_version} · candidats rebutjats {run.requirements_rejected} · cites rebutjades{" "}
                  {run.evidence_rejected}
                  {run.input_tokens !== null && ` · tokens ${run.input_tokens} in / ${run.output_tokens ?? 0} out`}
                  {run.duration_ms !== null && ` · ${(run.duration_ms / 1000).toFixed(1)} s`}
                </p>
              </details>
          </div>
        )}

        {run?.error_message && (
          // Frase de producte al davant; el diagnòstic cru (fragments, esquema,
          // contracte) només al detall tècnic, sense perdre'l.
          <div className="border-t border-border px-5 py-3 text-[13px]">
            <p className="text-ink">
              {run.status === "partial"
                ? "Anàlisi incompleta: una part del plec no s'ha pogut processar automàticament."
                : jobErrorAction(run.error_code).title}
            </p>
            {run.status === "partial" && (
              <p className="mt-0.5 text-muted">La revisió pot incorporar contingut revisat manualment.</p>
            )}
            <details className="mt-1">
              <summary className="cursor-pointer text-caption text-muted">Detall tècnic</summary>
              <p className="mt-1 font-mono text-[11.5px] text-muted">
                {run.error_code ? `${run.error_code} · ` : ""}
                {run.error_message}
              </p>
            </details>
          </div>
        )}

        {run?.skipped_documents && run.skipped_documents.length > 0 && (
          <div className="border-t border-border px-5 py-3">
            <p className={KICKER}>Documents no analitzats</p>
            <ul className="mt-1 space-y-0.5">
              {run.skipped_documents.map((skipped) => (
                <li key={skipped.tender_document_id} className="text-[13px] text-muted">
                  <span className="text-ink">{skipped.document_name}</span> · {SKIP_REASON_LABELS[skipped.reason] ?? "no analitzable"}
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="border-t border-border px-5 py-3 text-caption text-muted">{ANALYSIS_DISCLAIMER}</p>
      </Card>

      {requirements.length > 0 && (
        <Card>
          <CardHeader
            // Sense run, tots són escrits a mà (N3): la targeta no pot dir «extrets» ni «proposats per l'anàlisi».
            title={manualOnly ? `Requisits escrits a mà (${requirements.length})` : `Requisits extrets (${requirements.length})`}
            description={
              manualOnly
                ? "Escrits per l'empresa; cap no ve de l'anàlisi automàtica. Escrit no vol dir acreditat: el compliment es compara a sota."
                : "Proposats per l'anàlisi automàtica amb cita i pàgina. Extret no vol dir acreditat: el compliment es compara a sota."
            }
          />
          {/* La crida principal a la revisió és al Resum: aquí, només text i un enllaç discret. */}
          <p className="border-b border-border px-4 py-2 text-caption text-muted sm:px-5">
            La revisió es gestiona des de la vista de revisió.{" "}
            <Link href={`/licitacions/${tenderId}/revisio`} className="text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800">
              Obre la revisió
            </Link>
          </p>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5" role="group" aria-label="Filtres de requisits">
            {FILTERS.map((item) => {
              const pressed = filter === item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={pressed}
                  onClick={() => setFilter(item.key)}
                  className="flex min-h-11 items-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:min-h-0"
                >
                  <StatusPill level={pressed ? "action" : item.level} outline>
                    {item.label}
                    <span className="tabular-nums">{counts[item.key]}</span>
                  </StatusPill>
                </button>
              );
            })}
          </div>
          {grouped.length === 0 ? (
            <p className="px-5 py-4 text-[13px] text-muted">Cap requisit en aquest filtre.</p>
          ) : (
            grouped.map((group) => (
              <div key={group.category}>
                <p className={`${KICKER} border-b border-border bg-subtle px-4 py-1.5 sm:px-5`}>
                  {CATEGORY_LABELS[group.category]} · {group.items.length}
                </p>
                <ul className="divide-y divide-border">
                  {group.items.map((requirement) => (
                    <RequirementRow
                      key={requirement.id}
                      tenderId={tenderId}
                      requirement={requirement}
                      onEdit={
                        canWrite
                          ? (item) => {
                              setEditing(item);
                              setFormOpen(true);
                            }
                          : undefined
                      }
                      onArchive={canWrite ? archive : undefined}
                    />
                  ))}
                </ul>
              </div>
            ))
          )}
        </Card>
      )}

      {criteria.length > 0 && (
        <Card>
          <CardHeader title={`Criteris d'adjudicació (${criteria.length})`} description="Com es puntuen les ofertes." />
          <ul className="divide-y divide-border">
            {criteria.map((criterion: AwardCriterion) => (
              <li key={criterion.id} className="px-4 py-3.5 sm:px-5">
                <p className="text-[14px] font-medium text-ink">{criterion.name}</p>
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
                  <span>
                    {criterion.points !== null ? `${criterion.points} punts` : "sense punts"} · {EVALUATION_LABELS[criterion.evaluation_type]}
                    {criterion.formula ? ` · ${criterion.formula}` : " · sense fórmula al plec"}
                  </span>
                  <Attribution kind="ai" confidence={criterion.confidence} />
                </p>
                <div className="mt-2">
                  <Evidence tenderId={tenderId} evidence={criterion.evidence} />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {run && requirements.length === 0 && criteria.length === 0 && !job.active && (
        <Card>
          <EmptyState
            title="L'anàlisi no ha trobat requisits amb evidència verificable"
            description="El motor només conserva els requisits que pot citar amb document i pàgina. Si el plec en té, cal reprocessar els documents o tornar a analitzar."
            action={
              <Button variant="secondary" onClick={() => void analyse(true)} disabled={running || quote !== null}>
                Torna a analitzar
              </Button>
            }
          />
        </Card>
      )}
    </>
  );
}
