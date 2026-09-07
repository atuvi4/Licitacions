"use client";

/**
 * Secció "Compliment de l'empresa" de la fitxa de licitació.
 *
 * Les dues coses que la interfície NO pot fer, i que aquí es respecten:
 *
 * 1. **Barrejar pre-fit i elegibilitat.** Es mostren de costat, mai fusionats
 *    en cap puntuació. Un pre-fit alt amb un bloqueig continua sent un NO-GO.
 * 2. **Presentar un possible bloqueig com a definitiu.** Mentre el requisit no
 *    estigui verificat, el text diu «Possible bloqueig» i convida a revisar la
 *    font, no a descartar la licitació.
 */

import Link from "next/link";
import { useState } from "react";

import { Badge, Button, Card, CardHeader, EmptyState, ErrorNotice, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import {
  BLOCKER_LABELS,
  COMPLIANCE_DISCLAIMER,
  ELIGIBILITY_LABELS,
  ELIGIBILITY_TONES,
  RECOMMENDATION_LABELS,
  RESULT_ICONS,
  RESULT_LABELS,
  RESULT_TONES,
  STRENGTH_LABELS,
  explanationText,
  type Assessment,
  type AssessmentResult,
  type Compliance,
} from "@/lib/compliance";

function valueLine(value: Record<string, unknown> | null): string {
  if (!value) return "—";
  const parts: string[] = [];
  if (typeof value.amount === "number") {
    parts.push(`${value.amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  }
  if (typeof value.year === "number") parts.push(`(${value.year})`);
  if (typeof value.name === "string") parts.push(value.name);
  if (typeof value.coverage_amount === "number") {
    parts.push(`${value.coverage_amount.toLocaleString("ca-ES")} ${value.currency ?? ""}`.trim());
  }
  if (typeof value.accredited_projects === "number") {
    parts.push(`${value.accredited_projects} de ${value.required_projects} projectes`);
  }
  if (typeof value.minimum_projects === "number" && parts.length === 0) {
    parts.push(`${value.minimum_projects} projectes`);
  }
  if (typeof value.holders === "number") parts.push(`${value.holders} persona(es)`);
  return parts.length ? parts.join(" ") : JSON.stringify(value);
}

function AssessmentRow({
  tenderId,
  companyId,
  assessment,
  onReview,
  busy,
}: {
  tenderId: string;
  companyId: string;
  assessment: Assessment;
  onReview: (id: string, result: AssessmentResult | null) => void;
  busy: boolean;
}) {
  const source = assessment.requirement;
  const result = assessment.effective_result;
  const blocked = assessment.blocker_status !== "none";

  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {assessment.obligation === "mandatory" && <Badge tone="brand">Obligatori</Badge>}
            {blocked && (
              <Badge tone={assessment.blocker_status === "confirmed" ? "danger" : "warning"}>
                {BLOCKER_LABELS[assessment.blocker_status]}
              </Badge>
            )}
            {assessment.reviewed_result && <Badge tone="neutral">Revisat</Badge>}
          </div>
          <p className="mt-1.5 text-[13px] font-medium text-ink">
            {source?.title ?? "Requisit"}
          </p>
          <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-0.5 text-[12px] sm:grid-cols-2">
            <div className="flex gap-1">
              <dt className="text-muted">Plec:</dt>
              <dd className="text-ink">{valueLine(assessment.requirement_value_json)}</dd>
            </div>
            <div className="flex gap-1">
              <dt className="text-muted">Empresa:</dt>
              <dd className="text-ink">{valueLine(assessment.company_value_json)}</dd>
            </div>
          </dl>
          <p className="mt-1 text-[12px] text-muted">
            {explanationText(assessment.explanation_code)}
          </p>
          <p className="mt-0.5 font-mono text-[11px] text-muted">{assessment.rule_code}</p>
        </div>

        <Badge tone={RESULT_TONES[result]}>
          {RESULT_ICONS[result]} {RESULT_LABELS[result]}
        </Badge>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px]">
        {source?.tender_document_id && source.page_number && (
          <Link
            href={`/licitacions/${tenderId}/documents/${source.tender_document_id}?page=${source.page_number}&extraction=${source.extraction_id}`}
            className="text-brand hover:underline"
          >
            Veure font del plec ↗
          </Link>
        )}
        {assessment.evidence.map((evidence) => (
          <span key={evidence.id} className="text-muted">
            {evidence.label} · {STRENGTH_LABELS[evidence.strength]}
            {evidence.document_id && (
              <Link href="/documents" className="ml-1 text-brand hover:underline">
                Veure document ↗
              </Link>
            )}
          </span>
        ))}
        {assessment.evidence.length === 0 && result === "unknown" && (
          <Link href="/empresa" className="text-brand hover:underline">
            Completa el perfil ↗
          </Link>
        )}
      </div>

      <div className="mt-2 flex items-center gap-3 text-[12px]">
        <span className="text-muted">Revisió:</span>
        <button
          type="button"
          disabled={busy}
          onClick={() => onReview(assessment.id, null)}
          className="text-brand hover:underline disabled:text-muted disabled:no-underline"
        >
          Confirma
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onReview(assessment.id, "satisfies")}
          className="text-brand hover:underline disabled:text-muted disabled:no-underline"
        >
          Marca compleix
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onReview(assessment.id, "does_not_satisfy")}
          className="text-brand hover:underline disabled:text-muted disabled:no-underline"
        >
          Marca no compleix
        </button>
        {companyId && null}
      </div>
    </li>
  );
}

export function ComplianceSection({
  tenderId,
  companyId,
  compliance,
  loading,
  error,
  prefitScore,
  onReload,
}: {
  tenderId: string;
  companyId: string | null;
  compliance: Compliance | null;
  loading: boolean;
  error: string | null;
  prefitScore: number | null;
  onReload: () => Promise<void>;
}) {
  const [running, setRunning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);

  const run = compliance?.run ?? null;

  async function analyse(force: boolean) {
    if (!companyId) return;
    setRunning(true);
    setActionError(null);
    try {
      await api.post(`/companies/${companyId}/tenders/${tenderId}/compliance`, { force });
      await onReload();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut calcular.");
    } finally {
      setRunning(false);
    }
  }

  async function review(id: string, result: AssessmentResult | null) {
    if (!companyId) return;
    setReviewing(id);
    setActionError(null);
    try {
      await api.post(`/companies/${companyId}/assessments/${id}/review`, {
        review_status: "verified",
        reviewed_result: result,
      });
      await onReload();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut desar la revisió.");
    } finally {
      setReviewing(null);
    }
  }

  if (!companyId) return null;

  const assessments = compliance?.assessments ?? [];

  return (
    <>
      <Card>
        <CardHeader
          title="Compliment de l'empresa"
          description="Compara els requisits del plec amb el que pot acreditar l'empresa."
          action={
            <Button onClick={() => void analyse(Boolean(run))} disabled={running || loading}>
              {running ? <Spinner /> : run ? "Recalcula" : "Analitza compliment"}
            </Button>
          }
        />

        {(error || actionError) && <ErrorNotice message={actionError ?? error ?? ""} />}

        {run && (
          <>
            {/* Les dues dimensions, de costat i mai fusionades. */}
            <div className="grid grid-cols-1 gap-3 border-b border-border px-5 py-4 sm:grid-cols-3">
              <div>
                <p className="text-[12px] text-muted">Elegibilitat</p>
                <p className="mt-0.5">
                  <Badge tone={ELIGIBILITY_TONES[run.overall_result]}>
                    {ELIGIBILITY_LABELS[run.overall_result]}
                  </Badge>
                </p>
              </div>
              <div>
                <p className="text-[12px] text-muted">Pre-fit</p>
                <p className="mt-0.5 text-sm text-ink">
                  {run.prefit_score ?? prefitScore ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-[12px] text-muted">Recomanació</p>
                <p className="mt-0.5 text-sm text-ink">
                  {RECOMMENDATION_LABELS[run.recommendation]}
                </p>
              </div>
            </div>

            <dl className="grid grid-cols-2 gap-3 px-5 py-4 text-[13px] md:grid-cols-4">
              <div>
                <dt className="text-muted">Requisits obligatoris</dt>
                <dd>{run.mandatory_total}</dd>
              </div>
              <div>
                <dt className="text-muted">✅ Compleix</dt>
                <dd>{run.satisfies_count}</dd>
              </div>
              <div>
                <dt className="text-muted">❌ No compleix</dt>
                <dd>{run.does_not_satisfy_count}</dd>
              </div>
              <div>
                <dt className="text-muted">❓ Sense dades</dt>
                <dd>{run.unknown_count}</dd>
              </div>
              <div>
                <dt className="text-muted">⚠️ Cal revisar</dt>
                <dd>{run.needs_review_count}</dd>
              </div>
              <div>
                <dt className="text-muted">Possibles bloquejos</dt>
                <dd>{run.potential_blockers_count}</dd>
              </div>
              <div>
                <dt className="text-muted">Bloquejos confirmats</dt>
                <dd>{run.confirmed_blockers_count}</dd>
              </div>
              <div>
                <dt className="text-muted">Regles</dt>
                <dd className="truncate">{run.rules_version}</dd>
              </div>
            </dl>
          </>
        )}

        <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
          {COMPLIANCE_DISCLAIMER}
        </p>

        {compliance?.is_stale && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            El perfil de l&apos;empresa o els requisits han canviat des d&apos;aquest càlcul.
            Recalcula&apos;l per actualitzar-lo.
          </p>
        )}
      </Card>

      {assessments.length > 0 && (
        <Card>
          <CardHeader title={`Requisits avaluats (${assessments.length})`} />
          <ul className="divide-y divide-border">
            {assessments.map((assessment) => (
              <AssessmentRow
                key={assessment.id}
                tenderId={tenderId}
                companyId={companyId}
                assessment={assessment}
                busy={reviewing === assessment.id}
                onReview={review}
              />
            ))}
          </ul>
        </Card>
      )}

      {run && assessments.length === 0 && (
        <Card>
          <EmptyState title="Cap requisit avaluat" />
        </Card>
      )}
    </>
  );
}
