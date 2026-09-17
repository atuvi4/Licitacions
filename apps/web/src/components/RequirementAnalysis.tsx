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

import { useState } from "react";

import { AnalysisQuotePanel } from "@/components/AnalysisQuotePanel";
import { Attribution, Banner, Button, Card, CardHeader, ErrorNotice, ErrorState, EvidenceRef, LinkButton, StatusPill } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import {
  type AnalysisLimits,
  type AnalysisQuote,
  type JobRequest,
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
  OBLIGATION_LABELS,
  REVIEW_LABELS,
  REVIEW_LEVEL,
  SKIP_REASON_LABELS,
  analysisState,
  isTrustworthyAnalysis,
  jobErrorAction,
} from "@/lib/labels";
import type { AwardCriterion, Requirement, RequirementAnalysis, RequirementEvidence, ReviewStatus } from "@/lib/requirements";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const LINK = "text-[13px] text-brand-strong hover:text-accent-800 disabled:cursor-default disabled:text-muted";

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

function ReviewControls({
  current,
  onReview,
  busy,
}: {
  current: ReviewStatus;
  onReview: (status: ReviewStatus) => void;
  busy: boolean;
}) {
  const options: ReviewStatus[] = ["verified", "needs_review", "rejected"];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <StatusPill level={REVIEW_LEVEL[current]} size="sm">
        {REVIEW_LABELS[current]}
      </StatusPill>
      {options.map((status) => (
        <button
          key={status}
          type="button"
          disabled={busy || current === status}
          onClick={() => onReview(status)}
          className={`${LINK} min-h-11 md:min-h-0`}
        >
          {REVIEW_LABELS[status]}
        </button>
      ))}
    </div>
  );
}

function RequirementRow({
  tenderId,
  requirement,
  onReview,
  busy,
}: {
  tenderId: string;
  requirement: Requirement;
  onReview: (id: string, status: ReviewStatus) => void;
  busy: boolean;
}) {
  return (
    <li className="px-4 py-3.5 sm:px-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-ink">{requirement.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
            <span className={KICKER}>{OBLIGATION_LABELS[requirement.obligation]}</span>
            <Attribution kind="ai" confidence={requirement.confidence} />
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
        </div>
        <div className="shrink-0 md:max-w-[40%]">
          <ReviewControls current={requirement.review_status} busy={busy} onReview={(status) => onReview(requirement.id, status)} />
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
  loading,
  error,
  onReload,
}: {
  tenderId: string;
  companyId: string | null;
  analysis: RequirementAnalysis | null;
  loading: boolean;
  error: string | null;
  onReload: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobFailure, setJobFailure] = useState<JobRequest | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  // Cap anàlisi es demana sense haver-ne vist el volum: primer el preview.
  const [quote, setQuote] = useState<AnalysisQuote | null>(null);
  const [quoteNotice, setQuoteNotice] = useState<string | null>(null);
  const limits = useResource<AnalysisLimits>(companyId ? `/companies/${companyId}/analysis-limits` : null);
  const canConfirm = limits.data?.can_confirm ?? false;

  const state = analysisState(analysis);
  const run = analysis?.run ?? null;

  // L'anàlisi s'executa en segon pla: aquí només es demana i se'n segueix
  // l'estat. Quan acaba, es recarrega el que se n'ha obtingut.
  const job = useJobRequest(companyId, tenderId, "analyze_tender_requirements", async (done) => {
    if (done.status === "failed") {
      setJobFailure(done);
      setActionError(jobErrorMessage(done.error_code));
    }
    await onReload();
  });
  const running = submitting || job.active;

  async function analyse(force: boolean) {
    if (!companyId) {
      setActionError("Cal una empresa activa per demanar l'anàlisi.");
      return;
    }
    setSubmitting(true);
    setActionError(null);
    setJobFailure(null);
    setQuoteNotice(null);
    try {
      setQuote(await previewAnalysis(companyId, tenderId, force));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut calcular el volum.");
    } finally {
      setSubmitting(false);
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

  async function review(id: string, status: ReviewStatus) {
    setReviewing(id);
    setActionError(null);
    try {
      await api.post(`/requirements/${id}/review`, { review_status: status });
      await onReload();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut desar la revisió.");
    } finally {
      setReviewing(null);
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
  const mainButtonLabel = run ? "Torna a analitzar" : "Analitza requisits";

  const FILTERS: Array<{ key: Filter; label: string; level: "blocking" | "attention" | "neutral" }> = [
    { key: "all", label: "Tots", level: "neutral" },
    { key: "mandatory", label: "Obligatoris", level: "neutral" },
    { key: "review", label: "Per revisar", level: "attention" },
    { key: "scored", label: "Puntuables", level: "neutral" },
  ];

  return (
    <>
      <Card>
        <CardHeader
          title="Anàlisi del plec"
          description="Extreu els requisits i els criteris d'adjudicació dels documents processats."
          action={
            <div className="flex flex-wrap items-center gap-2">
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
              <Button
                variant={run ? "secondary" : "primary"}
                onClick={() => void analyse(state === "stale")}
                disabled={running || loading || quote !== null}
                loading={submitting}
              >
                {mainButtonLabel}
              </Button>
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
            notice={quoteNotice}
            canConfirm={canConfirm}
            confirmRoles={limits.data?.confirm_roles}
            confirming={submitting}
            onConfirm={() => void confirm()}
            onCancel={() => {
              setQuote(null);
              setQuoteNotice(null);
            }}
          />
        )}

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

        {!run && !job.active && !quote && (
          <EmptyState
            title="Aquest plec encara no s'ha llegit"
            description="«0 bloquejos» aquí no vol dir que no n'hi hagi: vol dir que ningú ho ha mirat. Els comptadors es queden a zero fins que hi hagi una anàlisi."
            action={
              <Button variant="primary" onClick={() => void analyse(false)} disabled={running || loading}>
                Analitza els plecs
              </Button>
            }
          />
        )}

        {run && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-4 text-[13px] md:grid-cols-4">
            <div>
              <dt className={KICKER}>Requisits</dt>
              <dd className="mt-0.5 tabular-nums">{run.requirements_extracted}</dd>
            </div>
            <div>
              <dt className={KICKER}>Criteris</dt>
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
                {run.chunks_failed > 0 && <span className="text-danger"> · {run.chunks_failed} fallits</span>}
              </dd>
            </div>
            <div className="col-span-2 md:col-span-4">
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
          </dl>
        )}

        {run?.error_message && (
          <p className="border-t border-border px-5 py-3 text-[13px] text-danger" title={run.error_code ?? undefined}>
            {jobErrorAction(run.error_code).title}: {run.error_message}
          </p>
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
            title={`Requisits (${requirements.length})`}
            description="Proposats per l'anàlisi amb cita i pàgina. Extret no vol dir acreditat: el compliment es compara a sota."
          />
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
                      busy={reviewing === requirement.id}
                      onReview={review}
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
