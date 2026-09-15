"use client";

/**
 * Secció "Anàlisi del plec" de la fitxa de licitació.
 *
 * Dues regles de presentació que no són estètiques:
 *
 * 1. Cap requisit es mostra sense la seva evidència i el seu enllaç a la font.
 *    Si algun dia la cita desapareix de la targeta, l'usuari deixa de poder
 *    distingir el que diu el plec del que ha dedaït un model.
 * 2. "Veure font" apunta a la revisió d'extracció que va originar la cita, no a
 *    la vigent. Si el document s'ha reextret, la cita continua sent la d'abans:
 *    és exactament el que la fa verificable.
 */

import Link from "next/link";
import { useState } from "react";

import { Badge, Button, Card, CardHeader, EmptyState, ErrorNotice, Spinner } from "@/components/ui";
import {
  ANALYSIS_BASIS_HINTS,
  ANALYSIS_BASIS_LABELS,
  ANALYSIS_BASIS_TONES,
  isTrustworthyAnalysis,
} from "@/lib/compliance";
import { api } from "@/lib/api";
import { jobErrorMessage, requestAnalysis, useJobRequest } from "@/lib/jobs";
import {
  ANALYSIS_DISCLAIMER,
  ANALYSIS_STATE_LABELS,
  ANALYSIS_STATE_TONES,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  CONFIDENCE_LABELS,
  CONFIDENCE_TONES,
  EVALUATION_LABELS,
  OBLIGATION_LABELS,
  OBLIGATION_TONES,
  REVIEW_LABELS,
  SKIP_REASON_LABELS,
  analysisState,
  type AwardCriterion,
  type Requirement,
  type RequirementAnalysis,
  type RequirementEvidence,
  type ReviewStatus,
} from "@/lib/requirements";

function EvidenceLink({
  tenderId,
  evidence,
}: {
  tenderId: string;
  evidence: RequirementEvidence;
}) {
  const label = evidence.document_name ?? "Document";
  const href = evidence.tender_document_id
    ? `/licitacions/${tenderId}/documents/${evidence.tender_document_id}?page=${evidence.page_number}&extraction=${evidence.extraction_id}`
    : null;

  return (
    <div className="mt-2 rounded-md border border-border bg-canvas px-3 py-2">
      <p className="text-[12px] text-muted">
        {label} · p. {evidence.page_number}
        {evidence.extraction_revision ? ` · revisió ${evidence.extraction_revision}` : ""}
      </p>
      <blockquote className="mt-1 border-l-2 border-border-strong pl-2 text-[12px] italic text-ink">
        {evidence.quote}
      </blockquote>
      {href && (
        <Link href={href} className="mt-1 inline-block text-[12px] text-brand hover:underline">
          Veure font ↗
        </Link>
      )}
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
    <div className="flex items-center gap-2">
      <span className="text-[12px] text-muted">{REVIEW_LABELS[current]}</span>
      {options.map((status) => (
        <button
          key={status}
          type="button"
          disabled={busy || current === status}
          onClick={() => onReview(status)}
          className="text-[12px] text-brand hover:underline disabled:cursor-default disabled:text-muted disabled:no-underline"
        >
          {REVIEW_LABELS[status]}
        </button>
      ))}
    </div>
  );
}

function RequirementCard({
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
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={OBLIGATION_TONES[requirement.obligation]}>
              {OBLIGATION_LABELS[requirement.obligation]}
            </Badge>
            {requirement.has_conflict && (
              <Badge tone="warning">Possible conflicte entre documents</Badge>
            )}
          </div>
          <p className="mt-1.5 text-[13px] font-medium text-ink">{requirement.title}</p>
          {requirement.description && (
            <p className="mt-0.5 text-[12px] text-muted">{requirement.description}</p>
          )}
        </div>
        <Badge tone={CONFIDENCE_TONES[requirement.confidence]}>
          Confiança {CONFIDENCE_LABELS[requirement.confidence]}
        </Badge>
      </div>

      {requirement.evidence.map((evidence) => (
        <EvidenceLink key={evidence.id} tenderId={tenderId} evidence={evidence} />
      ))}

      <div className="mt-2">
        <ReviewControls
          current={requirement.review_status}
          busy={busy}
          onReview={(status) => onReview(requirement.id, status)}
        />
      </div>
    </li>
  );
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
  const [reviewing, setReviewing] = useState<string | null>(null);

  const state = analysisState(analysis);
  const run = analysis?.run ?? null;

  // L'anàlisi s'executa en segon pla: aquí només es demana i se'n segueix
  // l'estat. Quan acaba, es recarrega el que se n'ha obtingut.
  const job = useJobRequest(companyId, tenderId, "analyze_tender_requirements", async (done) => {
    if (done.status === "failed") setActionError(jobErrorMessage(done.error_code));
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
    try {
      job.track(await requestAnalysis(companyId, tenderId, force));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut demanar l'anàlisi.");
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
  const grouped = CATEGORY_ORDER.map((category) => ({
    category,
    items: requirements.filter((requirement) => requirement.category === category),
  })).filter((group) => group.items.length > 0);

  return (
    <>
      <Card>
        <CardHeader
          title="Anàlisi del plec"
          description="Extreu els requisits i els criteris d'adjudicació dels documents processats."
          action={
            <div className="flex items-center gap-2">
              {/* Amb un motor simulat o sense motor, «Complet» seria una
                  afirmació falsa sobre el plec: el procés ha acabat, però no
                  ha llegit res. Es diu el que és, i el distintiu no pot ser
                  verd. */}
              {run && !isTrustworthyAnalysis(run.analysis_basis) ? (
                <Badge tone={ANALYSIS_BASIS_TONES[run.analysis_basis]}>
                  {ANALYSIS_BASIS_LABELS[run.analysis_basis]}
                </Badge>
              ) : (
                <Badge tone={ANALYSIS_STATE_TONES[state]}>{ANALYSIS_STATE_LABELS[state]}</Badge>
              )}
              {job.active && (
                <span className="text-[12px] text-muted">
                  {job.request?.progress
                    ? `Analitzant ${job.request.progress.done}/${job.request.progress.total}`
                    : "Analitzant en segon pla…"}
                </span>
              )}
              <Button onClick={() => void analyse(state === "stale")} disabled={running || loading}>
                {running ? <Spinner /> : run ? "Torna a analitzar" : "Analitza requisits"}
              </Button>
            </div>
          }
        />

        {(error || actionError) && <ErrorNotice message={actionError ?? error ?? ""} />}

        {run && !isTrustworthyAnalysis(run.analysis_basis) && (
          <div className="border-b border-border bg-warning-soft px-5 py-3 text-[13px]">
            <p className="font-medium">{ANALYSIS_BASIS_LABELS[run.analysis_basis]}</p>
            <p className="text-muted">{ANALYSIS_BASIS_HINTS[run.analysis_basis]}</p>
          </div>
        )}

        <p className="border-b border-border px-5 py-3 text-[12px] text-muted">
          {ANALYSIS_DISCLAIMER}
        </p>

        {run && (
          <dl className="grid grid-cols-2 gap-3 px-5 py-4 text-[13px] md:grid-cols-4">
            <div>
              <dt className="text-muted">Requisits</dt>
              <dd>{run.requirements_extracted}</dd>
            </div>
            <div>
              <dt className="text-muted">Criteris</dt>
              <dd>{run.award_criteria_extracted}</dd>
            </div>
            <div>
              <dt className="text-muted">Documents · pàgines</dt>
              <dd>
                {run.documents_analyzed} · {run.pages_analyzed}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Chunks</dt>
              <dd>
                {run.chunks_completed}/{run.chunks_total}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Candidats rebutjats</dt>
              <dd>{run.requirements_rejected}</dd>
            </div>
            <div>
              <dt className="text-muted">Cites rebutjades</dt>
              <dd>{run.evidence_rejected}</dd>
            </div>
            <div>
              <dt className="text-muted">Motor</dt>
              <dd className="truncate">
                {ANALYSIS_BASIS_LABELS[run.analysis_basis]}
                <span className="text-faint"> · {run.provider} · {run.model}</span>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Extractor</dt>
              <dd className="truncate">
                {run.extractor_version} · {run.prompt_version}
              </dd>
            </div>
            {run.input_tokens !== null && (
              <div>
                <dt className="text-muted">Tokens</dt>
                <dd>
                  {run.input_tokens} in / {run.output_tokens ?? 0} out
                </dd>
              </div>
            )}
            {run.duration_ms !== null && (
              <div>
                <dt className="text-muted">Durada</dt>
                <dd>{(run.duration_ms / 1000).toFixed(1)} s</dd>
              </div>
            )}
          </dl>
        )}

        {run?.error_message && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-danger">
            {run.error_code}: {run.error_message}
          </p>
        )}

        {analysis?.is_stale && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            Els documents han canviat des d&apos;aquesta anàlisi. Torna-la a executar per
            actualitzar-la.
          </p>
        )}

        {run?.skipped_documents && run.skipped_documents.length > 0 && (
          <div className="border-t border-border px-5 py-3">
            <p className="text-[12px] font-medium text-ink">Documents no analitzats</p>
            <ul className="mt-1 space-y-0.5">
              {run.skipped_documents.map((skipped) => (
                <li key={skipped.tender_document_id} className="text-[12px] text-muted">
                  {skipped.document_name} —{" "}
                  {SKIP_REASON_LABELS[skipped.reason] ?? skipped.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      {grouped.length > 0 && (
        <Card>
          <CardHeader title={`Requisits (${requirements.length})`} />
          {grouped.map((group) => (
            <div key={group.category}>
              <p className="border-b border-border bg-canvas px-5 py-2 text-[12px] font-medium uppercase tracking-wide text-muted">
                {CATEGORY_LABELS[group.category]}
              </p>
              <ul className="divide-y divide-border">
                {group.items.map((requirement) => (
                  <RequirementCard
                    key={requirement.id}
                    tenderId={tenderId}
                    requirement={requirement}
                    busy={reviewing === requirement.id}
                    onReview={review}
                  />
                ))}
              </ul>
            </div>
          ))}
        </Card>
      )}

      {criteria.length > 0 && (
        <Card>
          <CardHeader
            title={`Criteris d'adjudicació (${criteria.length})`}
            description="Com es puntuen les ofertes."
          />
          <ul className="divide-y divide-border">
            {criteria.map((criterion: AwardCriterion) => (
              <li key={criterion.id} className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium text-ink">{criterion.name}</p>
                    <p className="mt-0.5 text-[12px] text-muted">
                      {criterion.points !== null ? `${criterion.points} punts` : "sense punts"}
                      {" · "}
                      {EVALUATION_LABELS[criterion.evaluation_type]}
                      {criterion.formula ? ` · ${criterion.formula}` : " · sense fórmula al plec"}
                    </p>
                  </div>
                  <Badge tone={CONFIDENCE_TONES[criterion.confidence]}>
                    Confiança {CONFIDENCE_LABELS[criterion.confidence]}
                  </Badge>
                </div>
                {criterion.evidence.map((evidence) => (
                  <EvidenceLink key={evidence.id} tenderId={tenderId} evidence={evidence} />
                ))}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {run && requirements.length === 0 && criteria.length === 0 && (
        <Card>
          <EmptyState title="L'anàlisi no ha trobat requisits amb evidència verificable" />
        </Card>
      )}
    </>
  );
}
