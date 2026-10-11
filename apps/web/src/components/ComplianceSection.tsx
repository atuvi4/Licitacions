"use client";

/**
 * Secció «Compliment de l'empresa» de la fitxa de licitació (handoff L11).
 *
 * Les dues coses que la interfície NO pot fer, i que aquí es respecten:
 *
 * 1. **Barrejar pre-fit i elegibilitat.** Es mostren de costat, mai fusionats
 *    en cap puntuació. Un pre-fit alt amb un bloqueig continua sent un NO-GO.
 * 2. **Presentar un possible bloqueig com a definitiu.** Mentre el requisit no
 *    estigui verificat, el text diu «Possible bloqueig» i convida a revisar la
 *    font, no a descartar la licitació.
 *
 * I una tercera: «Sense dades» no és un no, «Sense determinar» no és un OK, i
 * la recomanació és del motor; la decisió GO / NO-GO és d'una persona i no
 * viu aquí.
 */

import Link from "next/link";
import { useState } from "react";

import { ScoreValue } from "@/components/PreFit";
import { Attribution, Banner, Button, Card, CardHeader, ErrorNotice, EvidenceRef, StatusPill } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { evidenceLabel, valueLine } from "@/lib/compliance";
import { INSURANCE_LIMIT_KIND_LABELS, INSURANCE_TYPE_LABELS } from "@/lib/format";
import { api } from "@/lib/api";
import {
  BLOCKER_LABELS,
  BLOCKER_LEVEL,
  COMPLIANCE_DISCLAIMER,
  ELIGIBILITY_HINTS,
  ELIGIBILITY_LABELS,
  ELIGIBILITY_LEVEL,
  EXPLANATION_LABELS,
  OBLIGATION_LABELS,
  RECOMMENDATION_LABELS,
  RESULT_LABELS,
  RESULT_LEVEL,
  STRENGTH_LABELS,
  STRENGTH_LEVEL,
} from "@/lib/labels";
import type { Assessment, AssessmentResult, Compliance } from "@/lib/compliance";
import type { RequirementAnalysisState } from "@/lib/requirements";

const EVIDENCE_DICTIONARIES = { types: INSURANCE_TYPE_LABELS as Record<string, string>, kinds: INSURANCE_LIMIT_KIND_LABELS };

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const LINK = "text-[13px] text-brand-strong hover:text-accent-800 disabled:cursor-default disabled:text-muted";


function explanation(code: string): { text: string; known: boolean } {
  const text = EXPLANATION_LABELS[code];
  return text ? { text, known: true } : { text: "Explicació no disponible per a aquesta regla.", known: false };
}

type GroupKey = "blockers" | "potential" | "review" | "unknown" | "satisfies" | "not_applicable";

const GROUPS: Array<{ key: GroupKey; label: string; hint?: string; collapsible?: boolean }> = [
  { key: "blockers", label: "Bloquegen", hint: "un requisit obligatori no es compleix" },
  { key: "potential", label: "Possibles bloquejos", hint: "l'extracció no està verificada" },
  { key: "review", label: "Per revisar", hint: "ho ha de mirar una persona" },
  { key: "unknown", label: "Sense dades", hint: "falta informació al perfil: ni sí ni no" },
  { key: "satisfies", label: "Acreditats", collapsible: true },
  { key: "not_applicable", label: "No aplica", collapsible: true },
];

function groupOf(assessment: Assessment): GroupKey {
  if (assessment.blocker_status === "confirmed" || assessment.effective_result === "does_not_satisfy") return "blockers";
  if (assessment.blocker_status === "potential") return "potential";
  if (assessment.effective_result === "needs_review") return "review";
  if (assessment.effective_result === "unknown") return "unknown";
  if (assessment.effective_result === "not_applicable") return "not_applicable";
  return "satisfies";
}

function AssessmentRow({
  tenderId,
  assessment,
  onReview,
  busy,
  failure,
}: {
  tenderId: string;
  assessment: Assessment;
  onReview: (id: string, result: AssessmentResult | null, note: string | null) => Promise<boolean>;
  busy: boolean;
  failure: string | null;
}) {
  const source = assessment.requirement;
  const result = assessment.effective_result;
  const blocked = assessment.blocker_status !== "none";
  const blockerLevel = BLOCKER_LEVEL[assessment.blocker_status];
  //: `undefined` = cap revisió a mig escriure; `null` = confirmar el que ja deia
  //: el sistema; un resultat = contradir-lo, i llavors cal motiu.
  const [pending, setPending] = useState<AssessmentResult | null | undefined>(undefined);
  const [note, setNote] = useState("");
  const asks = valueLine(assessment.requirement_value_json);
  const have = valueLine(assessment.company_value_json);
  const automatic = explanation(assessment.explanation_code);
  const sourceHref =
    source?.tender_document_id && source.page_number
      ? `/licitacions/${tenderId}/documents/${source.tender_document_id}?page=${source.page_number}&extraction=${source.extraction_id}`
      : null;

  function openReview(next: AssessmentResult | null) {
    setPending(next);
    setNote("");
  }

  return (
    <li className="px-4 py-3.5 sm:px-5">
      <div className="grid gap-3 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
        {/* Requisit: què demana el plec i d'on surt. */}
        <div className="min-w-0">
          <p className="text-[14px] font-medium text-ink">{source?.title ?? "Requisit"}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
            <span className={KICKER}>{OBLIGATION_LABELS[assessment.obligation]}</span>
            {source ? (
              <details className="group inline">
                <summary className="inline cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                  <EvidenceRef
                    compact
                    documentName={source.document_name}
                    pageNumber={source.page_number}
                    extractionRevision={source.extraction_revision}
                    href={sourceHref}
                  />
                </summary>
                {source.quote && (
                  <blockquote className="mt-1.5 border-l-2 border-accent-500 pl-2.5 text-[13px] leading-relaxed text-ink italic">
                    «{source.quote}»
                  </blockquote>
                )}
              </details>
            ) : (
              <EvidenceRef documentName={null} pageNumber={null} href={null} />
            )}
          </div>
        </div>

        {/* Demana el plec. */}
        <div className="min-w-0 md:border-l md:border-border md:pl-3">
          <p className={`${KICKER} md:hidden`}>Demana el plec</p>
          <p className="text-[13px] text-ink" title={asks.raw ?? undefined}>
            {asks.text}
          </p>
        </div>

        {/* Tenim: el valor del perfil i les evidències de l'empresa. */}
        <div className="min-w-0 md:border-l md:border-border md:pl-3">
          <p className={`${KICKER} md:hidden`}>Tenim</p>
          <p className={`text-[13px] ${result === "does_not_satisfy" ? "font-medium text-danger" : "text-ink"}`} title={have.raw ?? undefined}>
            {have.text}
          </p>
          {assessment.evidence.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {assessment.evidence.map((evidence) => (
                <li key={evidence.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-caption text-muted">
                  <span className="text-ink" title={evidenceLabel(evidence.label, EVIDENCE_DICTIONARIES).raw ?? undefined}>
                    {evidenceLabel(evidence.label, EVIDENCE_DICTIONARIES).text}
                  </span>
                  <StatusPill level={STRENGTH_LEVEL[evidence.strength]} size="sm">
                    {STRENGTH_LABELS[evidence.strength]}
                  </StatusPill>
                  {evidence.document_id && (
                    <Link href="/documents" className={LINK}>
                      Veure el document
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
          {assessment.evidence.length === 0 && result === "unknown" && (
            // Cap verb d'onboarding (addendum 3B-2, B1): la dada que falta es declara al coneixement.
            <p className="mt-1 text-caption">
              <Link href="/empresa/coneixement" className={LINK}>
                Afegeix la dada al perfil →
              </Link>
            </p>
          )}
        </div>

        {/* Estat: pastilla, bloqueig i qui ho afirma. */}
        <div className="flex flex-col items-start gap-1.5 md:items-end md:text-right">
          <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
            <StatusPill level={RESULT_LEVEL[result]}>{RESULT_LABELS[result]}</StatusPill>
            {blocked && blockerLevel && (
              <StatusPill level={blockerLevel} size="sm">
                {BLOCKER_LABELS[assessment.blocker_status]}
              </StatusPill>
            )}
          </div>
          {assessment.reviewed_result ? (
            <Attribution kind="human" person={assessment.reviewed_by_name ? { name: assessment.reviewed_by_name } : undefined} at={assessment.reviewed_at ?? undefined} />
          ) : (
            <Attribution kind="rule" />
          )}
        </div>
      </div>

      {/* Que el sistema no pugui decidir i una persona ho confirmi després és
          coherent. El que no ho és: ensenyar el veredicte humà amb l'explicació
          automàtica que diu el contrari, com si ho hagués decidit el motor.
          Primer qui ho ha dit; a sota, què deia el sistema. El resultat
          automàtic no s'esborra mai. */}
      {assessment.reviewed_result ? (
        <div className="mt-2 space-y-1.5">
          <p className="text-[13px] text-muted">
            {assessment.review_note ? <>«{assessment.review_note}»</> : <span className="italic">Motiu no registrat.</span>}
          </p>
          <div className="border border-border bg-subtle px-3 py-2">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
              <span className={KICKER}>Resultat automàtic</span>
              <StatusPill level={RESULT_LEVEL[assessment.automatic_result]} size="sm">
                {RESULT_LABELS[assessment.automatic_result]}
              </StatusPill>
              <Attribution kind="rule" />
            </p>
            <p className="mt-1 text-caption text-muted" title={automatic.known ? undefined : assessment.explanation_code}>
              {automatic.text}
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-[13px] text-muted" title={automatic.known ? assessment.rule_code : `${assessment.rule_code} · ${assessment.explanation_code}`}>
          {automatic.text}
        </p>
      )}

      {/* Contradir el motor és una afirmació nova i el backend n'exigeix el
          motiu. Sense un lloc on escriure'l, prémer «Marca compleix» tornava un
          422 i no passava res: la regla existia i el producte no la deixava
          complir. El motiu es demana aquí, abans d'enviar res. */}
      {pending === undefined ? (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
          <span className={KICKER}>La teva revisió</span>
          <button type="button" disabled={busy} onClick={() => openReview(null)} className={`${LINK} min-h-11 md:min-h-0`}>
            Confirma
          </button>
          <button type="button" disabled={busy} onClick={() => openReview("satisfies")} className={`${LINK} min-h-11 md:min-h-0`}>
            Marca compleix
          </button>
          <button type="button" disabled={busy} onClick={() => openReview("does_not_satisfy")} className={`${LINK} min-h-11 md:min-h-0`}>
            Marca no compleix
          </button>
        </div>
      ) : (
        <div className="mt-2 space-y-1.5 border border-border-strong bg-surface p-3 text-[13px]">
          <p className="text-muted">
            {pending === null
              ? "Confirmes el que diu el sistema. El motiu és opcional."
              : `Registres «${RESULT_LABELS[pending]}» per sobre del resultat automàtic. Escriu per què: quedarà signat amb el teu nom i data.`}
          </p>
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={2}
            placeholder="Per què aquest requisit queda així"
            className="w-full border border-border-strong bg-surface px-2 py-1.5 text-[13px] text-ink"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={busy || (pending !== null && !note.trim())}
              loading={busy}
              onClick={() => {
                void onReview(assessment.id, pending, note.trim() || null).then((saved) => {
                  if (!saved) return;
                  setPending(undefined);
                  setNote("");
                });
              }}
            >
              Desa la revisió
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setPending(undefined);
                setNote("");
              }}
            >
              Cancel·la
            </Button>
          </div>
        </div>
      )}
      {/* El bàner de dalt queda a desenes de requisits de distància. Si la
          revisió falla, ha de dir-ho aquí, on s'ha premut. */}
      {failure && <p className="mt-1.5 text-[13px] text-danger">{failure}</p>}
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
  analysisState,
  onReload,
}: {
  tenderId: string;
  companyId: string | null;
  compliance: Compliance | null;
  loading: boolean;
  error: string | null;
  prefitScore: number | null;
  /**
   * Si hi ha anàlisi del plec vigent (A7). Sense (`absent`) o obsoleta (`stale`)
   * no s'ofereix calcular ni recalcular el compliment: el servidor ho refusaria
   * (422 `no_requirements_to_assess`) o compararia contra uns requisits caducats.
   */
  analysisState: RequirementAnalysisState;
  onReload: () => Promise<void>;
}) {
  const [running, setRunning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [failedReview, setFailedReview] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

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

  async function review(id: string, result: AssessmentResult | null, note: string | null): Promise<boolean> {
    if (!companyId) return false;
    setReviewing(id);
    setActionError(null);
    setFailedReview(null);
    try {
      await api.post(`/companies/${companyId}/assessments/${id}/review`, {
        review_status: "verified",
        reviewed_result: result,
        note,
      });
      await onReload();
      return true;
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut desar la revisió.");
      setFailedReview(id);
      return false;
    } finally {
      setReviewing(null);
    }
  }

  if (!companyId) return null;

  const assessments = compliance?.assessments ?? [];
  const undetermined = run?.overall_result === "undetermined";
  const groups = GROUPS.map((group) => ({
    ...group,
    items: assessments.filter((assessment) => groupOf(assessment) === group.key),
  })).filter((group) => group.items.length > 0);
  const hasAttention = groups.some((group) => !group.collapsible);
  const analyseLabel = run ? "Recalcula" : "Analitza compliment";
  const analysisCurrent = analysisState === "current";
  const analysisGate =
    analysisState === "stale"
      ? "Cal una anàlisi del plec vigent per recalcular el compliment: l'anàlisi actual és obsoleta. Torna a analitzar el plec a l'apartat de dalt."
      : "Cal una anàlisi del plec vigent per calcular el compliment: primer analitza el plec a l'apartat de dalt.";

  const counters = run
    ? [
        { label: "Tots", value: run.requirements_total, level: "neutral" as const },
        { label: "Bloquegen", value: run.confirmed_blockers_count, level: "blocking" as const },
        { label: "Possibles bloquejos", value: run.potential_blockers_count, level: "attention" as const },
        { label: "Per revisar", value: run.needs_review_count, level: "attention" as const },
        { label: "Sense dades", value: run.unknown_count, level: "attention" as const },
        { label: "Acreditats", value: run.satisfies_count, level: "ready" as const },
        { label: "Obligatoris", value: run.mandatory_total, level: "neutral" as const },
      ]
    : [];

  return (
    <>
      <Card>
        <CardHeader
          title="Compliment de l'empresa"
          description="Compara els requisits del plec amb el que pot acreditar l'empresa. Comparació determinista amb el perfil."
          action={
            analysisCurrent ? (
              <Button variant={run ? "secondary" : "primary"} onClick={() => void analyse(Boolean(run))} disabled={running || loading} loading={running}>
                {analyseLabel}
              </Button>
            ) : undefined
          }
        />

        {!analysisCurrent && <p className="border-b border-border px-5 py-3 text-[13px] text-muted">{analysisGate}</p>}

        {(error || actionError) && !failedReview && <ErrorNotice message={actionError ?? error ?? ""} />}

        {run && undetermined && (
          <Banner
            level="attention"
            kicker="Sense avaluar"
            className="m-4"
            action={
              analysisCurrent ? (
                <Button size="sm" variant="primary" onClick={() => void analyse(Boolean(run))} disabled={running || loading}>
                  {analyseLabel}
                </Button>
              ) : undefined
            }
          >
            Encara no es pot dir si l&apos;empresa compleix: no s&apos;ha avaluat cap requisit del plec. Els comptadors
            són zero perquè no hi ha res avaluat, no perquè no hi hagi impediments.
          </Banner>
        )}

        {compliance?.is_stale && !undetermined && (
          <Banner
            level="attention"
            kicker="Obsolet"
            className="m-4"
            action={
              analysisCurrent ? (
                <Button size="sm" variant="secondary" onClick={() => void analyse(true)} disabled={running || loading}>
                  Recalcula ara
                </Button>
              ) : undefined
            }
          >
            El perfil de l&apos;empresa o els requisits han canviat des d&apos;aquest càlcul.
          </Banner>
        )}

        {run && (
          <>
            {/* Les dues dimensions, de costat i mai fusionades; la recomanació és del motor. */}
            <div className="grid grid-cols-1 gap-4 border-b border-border px-5 py-4 sm:grid-cols-3">
              <div>
                <p className={KICKER}>Elegibilitat</p>
                <p className="mt-1">
                  <StatusPill level={ELIGIBILITY_LEVEL[run.overall_result]}>{ELIGIBILITY_LABELS[run.overall_result]}</StatusPill>
                </p>
                <p className="mt-1 text-caption text-muted">{ELIGIBILITY_HINTS[run.overall_result]}</p>
              </div>
              <div>
                <p className={KICKER}>Pre-fit</p>
                <p className="mt-1">
                  <ScoreValue score={run.prefit_score ?? prefitScore} size="sm" />
                </p>
                <p className="mt-1 text-caption text-muted">Encaix de metadades; no és una probabilitat de guanyar.</p>
              </div>
              <div>
                {/* A14: el motor dona un resultat, no una recomanació; GO/NO-GO només ho diu una persona. */}
                <p className={KICKER}>Resultat de l&apos;anàlisi</p>
                <p className="mt-1 text-[14px] font-medium text-ink">{RECOMMENDATION_LABELS[run.recommendation]}</p>
                <p className="mt-1 text-caption text-muted">La presentació la decideix una persona a la candidatura.</p>
              </div>
            </div>

            <div
              data-compliance-counters
              className={`flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 ${undetermined ? "opacity-55" : ""}`}
              aria-label="Recomptes del compliment"
            >
              {counters.map((counter) => (
                <StatusPill key={counter.label} level={counter.value > 0 ? counter.level : "neutral"} outline>
                  {counter.label}
                  <span className="tabular-nums">{counter.value}</span>
                </StatusPill>
              ))}
              <span className="ml-auto text-caption text-muted">regles {run.rules_version}</span>
            </div>
          </>
        )}

        {!run && !loading && (
          <EmptyState
            title="Encara no s'ha comparat el plec amb el perfil"
            description={
              analysisCurrent
                ? "Sense aquesta comparació no hi ha cap veredicte: ni bloquejos ni acreditacions. Es calcula a partir dels requisits extrets i de les dades del perfil."
                : "Sense aquesta comparació no hi ha cap veredicte: ni bloquejos ni acreditacions. Es calcula a partir dels requisits del plec, i encara no n'hi ha cap d'analitzat."
            }
            action={
              analysisCurrent ? (
                <Button variant="primary" onClick={() => void analyse(false)} disabled={running || loading}>
                  Analitza compliment
                </Button>
              ) : undefined
            }
          />
        )}

        <p className="border-t border-border px-5 py-3 text-caption text-muted">{COMPLIANCE_DISCLAIMER}</p>
      </Card>

      {assessments.length > 0 && (
        <Card>
          <CardHeader
            title={`Requisits avaluats (${assessments.length})`}
            description="Primer el que bloqueja o demana una persona; els acreditats, plegats a sota."
          />
          {groups.map((group) => {
            const collapsed = Boolean(group.collapsible && hasAttention && !open[group.key]);
            return (
              <div key={group.key} data-assessment-group={group.key}>
                <div className="flex flex-wrap items-center gap-2 border-b border-border bg-subtle px-4 py-1.5 sm:px-5">
                  <span className={KICKER}>
                    {group.label} · {group.items.length}
                  </span>
                  {group.hint && <span className="text-caption text-muted">{group.hint}</span>}
                  {group.collapsible && hasAttention && (
                    <button
                      type="button"
                      aria-expanded={!collapsed}
                      onClick={() => setOpen((current) => ({ ...current, [group.key]: collapsed }))}
                      className="ml-auto text-caption text-brand-strong hover:text-accent-800"
                    >
                      {collapsed ? "plegats · desplega" : "plega"}
                    </button>
                  )}
                </div>
                {!collapsed && (
                  <ul className="divide-y divide-border">
                    {group.items.map((assessment) => (
                      <AssessmentRow
                        key={assessment.id}
                        tenderId={tenderId}
                        assessment={assessment}
                        busy={reviewing === assessment.id}
                        failure={failedReview === assessment.id ? actionError : null}
                        onReview={review}
                      />
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </Card>
      )}

      {run && assessments.length === 0 && !undetermined && (
        <Card>
          <EmptyState
            title="Cap requisit avaluat"
            description="El càlcul ha acabat sense cap requisit per comparar. Si el plec en té, cal analitzar-lo primer."
            action={
              analysisCurrent ? (
                <Button variant="secondary" onClick={() => void analyse(true)} disabled={running || loading}>
                  Recalcula
                </Button>
              ) : undefined
            }
          />
        </Card>
      )}
    </>
  );
}
