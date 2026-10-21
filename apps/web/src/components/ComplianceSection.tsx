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
import { comparedLine, consolidateAssessments, evidenceLabel, provenanceLine, valueLine } from "@/lib/compliance";
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
import type { AssessmentDisplay, AssessmentResult, Compliance } from "@/lib/compliance";
import type { RequirementAnalysisState } from "@/lib/requirements";

const EVIDENCE_DICTIONARIES = { types: INSURANCE_TYPE_LABELS as Record<string, string>, kinds: INSURANCE_LIMIT_KIND_LABELS };

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const LINK = "text-[13px] text-brand-strong hover:text-accent-800 disabled:cursor-default disabled:text-muted";


function explanation(code: string): { text: string; known: boolean } {
  const text = EXPLANATION_LABELS[code];
  return text ? { text, known: true } : { text: "Explicació no disponible per a aquesta regla.", known: false };
}

type GroupKey = "blockers" | "potential" | "not_met" | "review" | "unknown" | "satisfies" | "not_applicable";

/**
 * Confirmats i possibles, separats: la capçalera de la fitxa els compta igual
 * (`requirementsLine`). Un «no compleix» que no bloqueja (no obligatori, o una via
 * alternativa) té el seu propi grup: dir-ne «bloqueja» seria fals.
 */
const GROUPS: Array<{ key: GroupKey; label: string; hint?: string; collapsible?: boolean }> = [
  { key: "blockers", label: "Bloquejos confirmats", hint: "un requisit obligatori no es compleix, i una persona n'ha verificat el requisit o el veredicte" },
  { key: "potential", label: "Possibles bloquejos", hint: "el sistema diu que un requisit obligatori no es compleix; falta que una persona ho confirmi" },
  { key: "not_met", label: "No es compleix (no bloqueja)", hint: "requisit no obligatori, condicional o via alternativa" },
  { key: "review", label: "Per revisar", hint: "ho ha de mirar una persona" },
  { key: "unknown", label: "Sense dades", hint: "falta informació al perfil: ni sí ni no" },
  { key: "satisfies", label: "Acreditats", collapsible: true },
  { key: "not_applicable", label: "No aplica", collapsible: true },
];

function groupOf(row: AssessmentDisplay): GroupKey {
  if (row.blockerStatus === "confirmed") return "blockers";
  if (row.blockerStatus === "potential") return "potential";
  if (row.effectiveResult === "does_not_satisfy") return "not_met";
  if (row.effectiveResult === "needs_review") return "review";
  if (row.effectiveResult === "unknown") return "unknown";
  if (row.effectiveResult === "not_applicable") return "not_applicable";
  return "satisfies";
}

function citationHref(tenderId: string, citation: { tender_document_id: string | null; page_number: number | null; extraction_id: string | null }) {
  return citation.tender_document_id && citation.page_number
    ? `/licitacions/${tenderId}/documents/${citation.tender_document_id}?page=${citation.page_number}&extraction=${citation.extraction_id}`
    : null;
}

/**
 * Els duplicats d'una exigència (el plec la repeteix a diversos llocs): «també a»
 * cada requisit, amb TOTES les seves cites, i les cites addicionals del canònic.
 * Consolidar no amaga res: cap cita no es perd.
 */
function citationsLabel(count: number): string {
  return count === 1 ? "1 cita addicional" : `${count} cites addicionals`;
}

function resultLabel(value: string | undefined): string | undefined {
  return RESULT_LABELS[value as AssessmentResult] ?? value;
}

type ReviewTrace = { kind: "changed" | "dropped"; reason: string; text: string };

/**
 * Què ha passat amb la revisió d'un càlcul anterior (el «Recalcula» no l'esborra).
 * Conservada, però el resultat automàtic ha canviat des que es va fer: la revisió
 * mana, i algú l'ha de tornar a mirar. No conservada, perquè ja no parlava del
 * mateix (una confirmació d'un resultat que ha canviat, un «compleix» que el motor
 * ara contradiu, o un requisit editat): la fila torna a estar per revisar, i es diu
 * per què. La revisió es fa per a tota la fila, així que en un grup de duplicats
 * compta qualsevol membre.
 */
function reviewTrace(row: AssessmentDisplay): ReviewTrace | null {
  for (const item of [row.assessment, ...row.members]) {
    const carried = item.explanation_params?.review_carried_over as
      | { automatic_changed?: boolean; reviewed_against?: string }
      | undefined;
    if (carried?.automatic_changed) {
      return {
        kind: "changed",
        reason: "automatic_changed",
        text: `Revisió conservada d'un càlcul anterior. Des que es va fer, el resultat automàtic ha canviat (llavors: ${resultLabel(carried.reviewed_against)}): torna-la a mirar.`,
      };
    }
    const dropped = item.explanation_params?.review_not_carried as
      | { reason?: string; reviewed_against?: string; reviewed_result?: string | null; review_note?: string | null }
      | undefined;
    if (dropped) {
      const reason = dropped.reason ?? "";
      const note = dropped.review_note?.trim() ? ` («${dropped.review_note.trim()}»)` : "";
      const text =
        reason === "requirement_changed"
          ? "El requisit ha canviat des de l'última revisió, i aquella revisió no s'ha conservat: torna a revisar el requisit."
          : reason === "automatic_now_fails"
            ? `Una revisió d'un càlcul anterior deia «${resultLabel(dropped.reviewed_result ?? undefined)}»${note}, quan el resultat automàtic era «${resultLabel(dropped.reviewed_against)}». Ara el sistema diu «${RESULT_LABELS.does_not_satisfy}» i aquella revisió no s'ha conservat: torna a revisar el requisit.`
            : `Una confirmació d'un càlcul anterior, feta quan el resultat era «${resultLabel(dropped.reviewed_against)}», no s'ha conservat perquè el resultat automàtic ha canviat: torna a revisar el requisit.`;
      return { kind: "dropped", reason, text };
    }
  }
  return null;
}

function ReviewTraceNotice({ row }: { row: AssessmentDisplay }) {
  const trace = reviewTrace(row);
  if (!trace) return null;
  return trace.kind === "changed" ? (
    <p className="mt-1.5 text-caption text-warning" data-carried-review-changed>
      {trace.text}
    </p>
  ) : (
    <p className="mt-1.5 text-caption text-warning" data-review-not-carried={trace.reason}>
      {trace.text}
    </p>
  );
}

/** Resultats que la regla ha decidit sola: la fila no pot dir «no comparable». */
const DECIDED: AssessmentResult[] = ["satisfies", "does_not_satisfy"];
const COMPARED_BY_RULE = "Comparat per la regla: vegeu l'explicació";
const REVIEWED_BY_PERSON = "Revisat per una persona: vegeu el motiu de la revisió";

function GroupCitations({ tenderId, row }: { tenderId: string; row: AssessmentDisplay }) {
  const extra = (row.assessment.requirement?.citations ?? []).slice(1);
  if (row.members.length === 0 && extra.length === 0) return null;
  const memberCitations = row.members.reduce((total, member) => total + (member.requirement?.citations?.length ?? 0), 0);
  return (
    <details className="mt-1.5 text-caption text-muted" data-group-citations>
      <summary className="cursor-pointer">
        {row.members.length > 0
          ? `També a ${row.members.length} requisit${row.members.length === 1 ? "" : "s"} més · ${citationsLabel(memberCitations + extra.length)}`
          : citationsLabel(extra.length)}
      </summary>
      <ul className="mt-1 space-y-1.5">
        {extra.map((citation) => (
          <li key={citation.evidence_id}>
            <EvidenceRef compact documentName={citation.document_name} pageNumber={citation.page_number} extractionRevision={citation.extraction_revision} href={citationHref(tenderId, citation)} />
            <blockquote className="mt-0.5 border-l-2 border-accent-500 pl-2.5 text-[13px] leading-relaxed text-ink italic">«{citation.quote}»</blockquote>
          </li>
        ))}
        {row.members.map((member) => (
          <li key={member.id} data-group-member={member.requirement_id}>
            <p className="text-ink">També a: {member.requirement?.title ?? "Requisit"}</p>
            {(member.requirement?.citations ?? []).map((citation) => (
              <div key={citation.evidence_id} className="mt-0.5">
                <EvidenceRef compact documentName={citation.document_name} pageNumber={citation.page_number} extractionRevision={citation.extraction_revision} href={citationHref(tenderId, citation)} />
                <blockquote className="mt-0.5 border-l-2 border-accent-500 pl-2.5 text-[13px] leading-relaxed text-ink italic">«{citation.quote}»</blockquote>
              </div>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}

function AssessmentRow({
  tenderId,
  row,
  onReview,
  busy,
  failure,
}: {
  tenderId: string;
  row: AssessmentDisplay;
  onReview: (ids: string[], result: AssessmentResult | null, note: string | null) => Promise<boolean>;
  busy: boolean;
  failure: string | null;
}) {
  const assessment = row.assessment;
  const source = assessment.requirement;
  // El resultat de la FILA és el del grup (el més prudent dels duplicats).
  const result = row.effectiveResult;
  const blocked = row.blockerStatus !== "none";
  const blockerLevel = BLOCKER_LEVEL[row.blockerStatus];
  const reviewIds = [assessment.id, ...row.members.map((member) => member.id)];
  //: `undefined` = cap revisió a mig escriure; `null` = confirmar el que ja deia
  //: el sistema; un resultat = contradir-lo, i llavors cal motiu.
  const [pending, setPending] = useState<AssessmentResult | null | undefined>(undefined);
  const [note, setNote] = useState("");
  // L'avaluació que dona el resultat de la fila: la seva explicació i el seu valor
  // de l'empresa quadren amb la pastilla (en un grup divergent pot no ser el canònic).
  const deciding = row.deciding;
  // Una fila amb veredicte (de la regla o d'una revisió humana) mai no diu «no comparable».
  const decided = DECIDED.includes(row.effectiveResult);
  // «Demana el plec»: el valor que la regla ha COMPARAT (amb la seva procedència) si
  // n'hi ha; si no, el cru extret.
  const compared = comparedLine(deciding.compared_value);
  const comparedFrom = compared ? provenanceLine(deciding.compared_provenance, deciding.requirement?.citations) : null;
  // Qui ha donat el veredicte: si l'automàtic no era decisiu i el veredicte és d'una
  // revisió humana, la regla NO ha comparat res i no es pot dir que sí.
  const decidedBy =
    deciding.assessment_mode === "automatic" && DECIDED.includes(deciding.automatic_result)
      ? COMPARED_BY_RULE
      : REVIEWED_BY_PERSON;
  const rawAsks = valueLine(assessment.requirement_value_json);
  const asks = compared
    ? { text: comparedFrom ? `${compared} · ${comparedFrom}` : compared, raw: JSON.stringify(assessment.requirement_value_json) }
    : rawAsks.raw && decided
      ? { text: decidedBy, raw: rawAsks.raw }
      : rawAsks;
  const rawHave = valueLine(deciding.company_value_json);
  const have = rawHave.raw && decided ? { text: decidedBy, raw: rawHave.raw } : rawHave;
  const automatic = explanation(deciding.explanation_code);
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
          <GroupCitations tenderId={tenderId} row={row} />
          {row.divergent && (
            <p className="mt-1 text-caption text-muted" data-group-divergent>
              Els requisits duplicats no diuen el mateix: mana el resultat més prudent.
            </p>
          )}
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
          {deciding.evidence.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {deciding.evidence.map((evidence) => (
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
          {deciding.evidence.length === 0 && result === "unknown" && (
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
                {BLOCKER_LABELS[row.blockerStatus]}
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
            <p className="mt-1 text-caption text-muted" title={automatic.known ? undefined : deciding.explanation_code}>
              {automatic.text}
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-[13px] text-muted" title={automatic.known ? deciding.rule_code : `${deciding.rule_code} · ${deciding.explanation_code}`}>
          {automatic.text}
        </p>
      )}
      <ReviewTraceNotice row={row} />

      {/* Contradir el motor és una afirmació nova i el backend n'exigeix el
          motiu. Sense un lloc on escriure'l, prémer «Marca compleix» tornava un
          422 i no passava res: la regla existia i el producte no la deixava
          complir. El motiu es demana aquí, abans d'enviar res. */}
      {pending === undefined ? (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
          <span className={KICKER}>La teva revisió</span>
          {/* En un grup divergent, «confirmar» no diria quin dels resultats: cal triar-ne un. */}
          {!row.divergent && (
            <button type="button" disabled={busy} onClick={() => openReview(null)} className={`${LINK} min-h-11 md:min-h-0`}>
              Confirma
            </button>
          )}
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
                void onReview(reviewIds, pending, note.trim() || null).then((saved) => {
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

  // Una revisió s'aplica a TOTS els requisits de l'exigència (el canònic i els
  // duplicats): si no, el grup quedaria divergent i el recompte no canviaria.
  async function review(ids: string[], result: AssessmentResult | null, note: string | null): Promise<boolean> {
    if (!companyId) return false;
    const [id] = ids;
    setReviewing(id);
    setActionError(null);
    setFailedReview(null);
    let applied = 0;
    try {
      for (const target of ids) {
        await api.post(`/companies/${companyId}/assessments/${target}/review`, {
          review_status: "verified",
          reviewed_result: result,
          note,
        });
        applied += 1;
      }
      await onReload();
      return true;
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "No s'ha pogut desar la revisió.");
      setFailedReview(id);
      // Si una part del grup ja s'ha desat, la pantalla ha d'ensenyar-ho.
      if (applied > 0) await onReload();
      return false;
    } finally {
      setReviewing(null);
    }
  }

  if (!companyId) return null;

  const assessments = compliance?.assessments ?? [];
  // Una fila per exigència: els duplicats del plec van dins de la fila del canònic.
  const rows = consolidateAssessments(assessments, compliance?.groups ?? []);
  const undetermined = run?.overall_result === "undetermined";
  const groups = GROUPS.map((group) => ({
    ...group,
    items: rows.filter((row) => groupOf(row) === group.key),
  })).filter((group) => group.items.length > 0);
  const hasAttention = groups.some((group) => !group.collapsible);
  // Revisions d'un càlcul anterior que cal tornar a mirar: es compten a dalt i no queden mai plegades.
  const tracedCount = rows.filter((row) => reviewTrace(row) !== null).length;
  const analyseLabel = run ? "Recalcula" : "Analitza compliment";
  const analysisCurrent = analysisState === "current";
  const analysisGate =
    analysisState === "stale"
      ? "Cal una anàlisi del plec vigent per recalcular el compliment: l'anàlisi actual és obsoleta. Torna a analitzar el plec a l'apartat de dalt."
      : "Cal una anàlisi del plec vigent per calcular el compliment: primer analitza el plec a l'apartat de dalt.";

  const counters = run
    ? [
        { label: "Tots", value: run.requirements_total, level: "neutral" as const },
        { label: "Bloquejos confirmats", value: run.confirmed_blockers_count, level: "blocking" as const },
        { label: "Possibles bloquejos", value: run.potential_blockers_count, level: "attention" as const },
        { label: "Per revisar", value: run.needs_review_count, level: "attention" as const },
        { label: "Sense dades", value: run.unknown_count, level: "attention" as const },
        { label: "Acreditats", value: run.satisfies_count, level: "ready" as const },
        { label: "Obligatoris", value: run.mandatory_total, level: "neutral" as const },
        ...(tracedCount > 0
          ? [{ label: "Revisions a tornar a mirar", value: tracedCount, level: "attention" as const }]
          : []),
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
            title={`Requisits avaluats (${rows.length})`}
            description="Primer el que bloqueja o demana una persona; els acreditats, plegats a sota."
          />
          {groups.map((group) => {
            const collapsed = Boolean(group.collapsible && hasAttention && !open[group.key]);
            // Plegat no vol dir amagat: una fila amb un avís de revisió es veu igualment.
            const shown = collapsed ? group.items.filter((row) => reviewTrace(row) !== null) : group.items;
            return (
              <div key={group.key} data-assessment-group={group.key}>
                <div className="flex flex-wrap items-center gap-2 border-b border-border bg-subtle px-4 py-1.5 sm:px-5">
                  <span className={KICKER}>
                    {group.label} · {group.items.length}
                    {collapsed && shown.length > 0 ? ` · ${shown.length} amb avís` : ""}
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
                {shown.length > 0 && (
                  <ul className="divide-y divide-border">
                    {shown.map((row) => (
                      <AssessmentRow
                        key={row.assessment.id}
                        tenderId={tenderId}
                        row={row}
                        busy={reviewing === row.assessment.id}
                        failure={failedReview === row.assessment.id ? actionError : null}
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
