"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import {
  Attribution,
  Button,
  Card,
  CardHeader,
  Field,
  PermissionNote,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import {
  ANALYSIS_BASIS_HINTS,
  ANALYSIS_BASIS_LABELS,
  RECOMMENDATION_LABELS,
  isTrustworthyAnalysis,
} from "@/lib/compliance";
import { useMutation } from "@/lib/hooks";
import {
  DECISION_LEVEL,
  DECISION_PENDING_LEVEL,
  RECOMMENDATION_LEVEL,
  ROLE_LABELS,
} from "@/lib/labels";
import { formatDateTime } from "@/lib/tenders";
import {
  DECISION_LABELS,
  WARNING_LABELS,
  deadlineLabel,
  type WorkspaceDecisionType,
  type WorkspaceDetail,
} from "@/lib/workspace";

/** Els rols que el backend deixa signar (`DECISION_ROLES`). Diccionari, no nom. */
const DECIDING_ROLES = ["owner", "admin", "bid_manager"] as const;
const WHO_CAN_DECIDE = `qui té el rol de ${DECIDING_ROLES.map((role) => ROLE_LABELS[role]).join(", ").replace(/, ([^,]*)$/, " o $1")}`;

/** Longitud màxima del motiu al backend (`DecisionRequest.reason`). */
const REASON_MAX_LENGTH = 4000;

const ACTION_LABELS: Record<WorkspaceDecisionType, string> = {
  go: "Presentar-se · GO",
  no_go: "No presentar-se · NO-GO",
  defer: "Ajornar",
};

const MODAL_TITLES: Record<WorkspaceDecisionType, string> = {
  go: "Presentar-se · GO",
  no_go: "No presentar-se · NO-GO",
  defer: "Ajornar la decisió",
};

const SUBMIT_LABELS: Record<WorkspaceDecisionType, string> = {
  go: "Signa la decisió: GO",
  no_go: "Signa la decisió: NO-GO",
  defer: "Registra l'ajornament",
};

const RECOMMENDATION_HEADLINES: Record<string, string> = {
  go_candidate: "El sistema suggereix presentar-se.",
  eligible_low_fit: "El sistema veu la candidatura elegible, però amb poc encaix.",
  review: "El sistema suggereix revisar abans de decidir.",
  no_go: "El sistema suggereix no presentar-se.",
};

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * L'acte de decidir (handoff L12, mockup 06 i cas 2).
 *
 * Quatre coses que mai es fusionen: el pre-fit, l'elegibilitat, la
 * recomanació automàtica i la decisió humana. Aquí només hi ha les dues
 * últimes, i la recomanació és text signat pel càlcul, mai un botó: cap
 * «accepta la recomanació», cap GO automàtic. Només un GO HUMÀ registrat fa
 * avançar el flux, i això ho decideix la pàgina a partir de la decisió desada.
 *
 * Contracte real (`POST …/decisions {decision, reason}`): el motiu només és
 * obligatori per a un GO amb avisos oberts (`go_requires_reason`); NO-GO i
 * ajornar no en demanen mai; màxim 4000 caràcters; el backend fa la
 * fotografia dels avisos en registrar-la; la fila no s'edita ni s'esborra.
 */
export function DecisionPanel({
  workspace,
  onDecided,
  signerName,
}: {
  workspace: WorkspaceDetail;
  /** Es crida només després d'un POST correcte, amb la decisió registrada. */
  onDecided: (decision: WorkspaceDecisionType) => Promise<void>;
  /** Qui signarà: el nom real de la sessió. */
  signerName: string;
}) {
  const [pending, setPending] = useState<WorkspaceDecisionType | null>(null);
  const [reason, setReason] = useState("");
  const mutation = useMutation();

  const warnings = workspace.open_warnings;
  const needsReason = pending === "go" && workspace.go_requires_reason;
  const undetermined = workspace.eligibility === "undetermined" || workspace.eligibility === null;
  const trustworthy = isTrustworthyAnalysis(workspace.analysis_basis);
  const latest = workspace.latest_decision;
  const decided = workspace.human_decision;
  const counters = workspace.counters;

  function open(decision: WorkspaceDecisionType) {
    mutation.reset();
    setReason("");
    setPending(decision);
  }

  function close() {
    if (mutation.saving) return;
    setPending(null);
  }

  async function submit() {
    if (!pending) return;
    const ok = await mutation.run(async () => {
      await api.post(`/companies/${workspace.company_id}/workspaces/${workspace.id}/decisions`, {
        decision: pending,
        reason: reason.trim() || null,
      });
    });
    if (ok) {
      setPending(null);
      await onDecided(pending);
    }
  }

  const recommendationHeadline =
    workspace.recommendation && RECOMMENDATION_HEADLINES[workspace.recommendation]
      ? RECOMMENDATION_HEADLINES[workspace.recommendation]
      : "El sistema no pot suggerir res: no s'ha avaluat cap requisit del plec.";
  const unresolved = counters.mandatory_unknown + counters.mandatory_needs_review;
  const recommendationBasis = undetermined
    ? "«Cap bloqueig» aquí no vol dir que no n'hi hagi: vol dir que no s'ha mirat."
    : `Calculat amb ${plural(counters.confirmed_blockers, "bloqueig confirmat", "bloqueigs confirmats")}, ${plural(counters.potential_blockers, "possible", "possibles")} i ${plural(unresolved, "obligatori sense resoldre", "obligatoris sense resoldre")}; canviarà quan es resolguin.`;

  const apiError = mutation.apiError;
  const reasonRequiredByBackend = apiError?.code === "decision_reason_required";
  const backendWarnings = reasonRequiredByBackend
    ? ((apiError?.details.warnings as string[] | undefined) ?? []).map((code) => WARNING_LABELS[code] ?? code)
    : [];
  const forbidden = apiError?.status === 403;
  const otherError = mutation.error && !reasonRequiredByBackend && !forbidden ? mutation.error : null;

  const buttons = (
    <div className="flex flex-col gap-3 sm:flex-row">
      <Button
        variant="primary"
        size="md"
        className="min-h-12 flex-1 justify-center text-[16px] sm:min-h-0"
        disabled={!workspace.can_decide}
        onClick={() => open("go")}
      >
        {ACTION_LABELS.go}
      </Button>
      <Button
        variant="secondary"
        size="md"
        className="min-h-12 flex-1 justify-center text-[16px] sm:min-h-0"
        disabled={!workspace.can_decide}
        onClick={() => open("no_go")}
      >
        {ACTION_LABELS.no_go}
      </Button>
      <Button
        variant="ghost"
        size="md"
        className="min-h-12 justify-center sm:min-h-0"
        disabled={!workspace.can_decide}
        onClick={() => open("defer")}
      >
        {ACTION_LABELS.defer}
      </Button>
    </div>
  );

  return (
    <Card frame className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-5 px-5 py-5 sm:px-6 sm:py-6">
        <div>
          <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-brand-strong uppercase">
            Decisió GO / NO-GO
          </p>
          <h2 className="mt-1.5 font-heading text-[24px] leading-tight font-semibold text-ink">
            Ens presentem a aquesta licitació?
          </h2>
          <p className="mt-2 text-[14px] leading-6 text-muted">
            Només la decisió d&apos;una persona és oficial. Quedarà signada per{" "}
            <strong className="text-ink">{signerName}</strong>, datada, visible a l&apos;historial i no es
            podrà editar; si canvia el context, se&apos;n pot prendre una decisió nova a sobre.
          </p>
        </div>

        {latest && decided ? (
          <div className="border border-border bg-subtle px-4 py-3">
            <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">
              Decisió vigent
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <StatusPill level={DECISION_LEVEL[decided]}>{DECISION_LABELS[decided]}</StatusPill>
              <Attribution
                kind="human"
                person={{ name: latest.decided_by_name ?? "Una persona" }}
                at={latest.decided_at}
              />
              {workspace.decision_outdated && (
                <StatusPill level="attention" size="sm">
                  {DECISION_LABELS[decided]} del {formatDateTime(latest.decided_at)} · el plec ha canviat
                </StatusPill>
              )}
            </div>
            {latest.reason && (
              <p className="mt-2 text-[13px] leading-5 whitespace-pre-line text-ink">“{latest.reason}”</p>
            )}
            <p className="mt-1.5 text-[12.5px] text-muted">
              No s&apos;esborra ni es reescriu: si cal canviar-la, es pren una decisió nova a sobre.
            </p>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <StatusPill level={DECISION_PENDING_LEVEL}>Pendent</StatusPill>
            <span>Encara no s&apos;ha pres cap decisió. La recomanació automàtica no en compta com a una.</span>
          </div>
        )}

        {workspace.can_decide ? (
          buttons
        ) : (
          <PermissionNote who={WHO_CAN_DECIDE} what="signar la decisió" className="w-full [&>div]:w-full">
            {buttons}
          </PermissionNote>
        )}

        {workspace.go_requires_reason ? (
          <p className="text-[13px] leading-5 text-muted">
            Motiu · obligatori si aproves:{" "}
            <span className="text-ink">
              hi ha {plural(warnings.length, "avís obert", "avisos oberts")} i{" "}
              {warnings.length === 1 ? "quedarà registrat" : "quedaran registrats"} amb la decisió.
            </span>{" "}
            NO-GO i ajornar no en demanen.
          </p>
        ) : (
          <p className="text-[13px] leading-5 text-muted">
            Motiu · opcional. Quedarà a l&apos;historial amb la decisió, per a qui ho llegeixi d&apos;aquí
            a sis mesos.
          </p>
        )}

        <div className="mt-auto flex items-start gap-3 border-t border-border pt-4">
          <div className="min-w-0 flex-1">
            <Attribution kind="rule" label="Recomanació del sistema" />
            <p className="mt-1.5 text-[13px] leading-5 text-muted">
              <strong className="text-ink">{recommendationHeadline}</strong>{" "}
              {workspace.recommendation && (
                <StatusPill level={RECOMMENDATION_LEVEL[workspace.recommendation]} size="sm" className="mr-1 align-middle">
                  {RECOMMENDATION_LABELS[workspace.recommendation]}
                </StatusPill>
              )}
              {recommendationBasis} És un suggeriment, no una decisió: la responsabilitat i la signatura
              són d&apos;una persona.
            </p>
          </div>
        </div>
      </div>

      <Modal
        open={pending !== null}
        title={pending ? MODAL_TITLES[pending] : ""}
        description="Això és exactament el que quedarà registrat, i no es podrà editar; si canvia el context, se'n podrà prendre una de nova a sobre."
        onClose={close}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              onClick={() => void submit()}
              loading={mutation.saving}
              disabled={needsReason && reason.trim().length === 0}
            >
              {pending ? SUBMIT_LABELS[pending] : ""}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border border-border bg-subtle px-4 py-3 text-[13px]">
            <div>
              <dt className="text-muted">Decisió</dt>
              <dd className="mt-0.5">
                {pending && <StatusPill level={DECISION_LEVEL[pending]}>{DECISION_LABELS[pending]}</StatusPill>}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Signada per</dt>
              <dd className="mt-0.5 text-ink">{signerName} · ara, en registrar-la</dd>
            </div>
            <div>
              <dt className="text-muted">Expedient</dt>
              <dd className="mt-0.5 font-mono text-ink">{workspace.expediente_number ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Termini</dt>
              <dd className="mt-0.5 text-ink">
                {workspace.deadline_passed
                  ? "Ja ha passat"
                  : workspace.deadline_at
                    ? `${deadlineLabel(workspace.days_remaining) ?? ""} · ${formatDateTime(workspace.deadline_at)}`
                    : "No consta"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Pre-fit</dt>
              <dd className="mt-0.5 text-ink">{workspace.prefit_score ?? "Sense càlcul"}</dd>
            </div>
            <div>
              <dt className="text-muted">Recomanació automàtica</dt>
              <dd className="mt-0.5 text-ink">
                {workspace.recommendation ? RECOMMENDATION_LABELS[workspace.recommendation] : "Sense càlcul"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Bloqueigs confirmats</dt>
              {/* Un "0" surt igual tant si s'han avaluat vint requisits i cap
                  bloqueja com si no se n'ha avaluat cap. Són coses oposades i
                  aquesta és l'última pantalla abans d'un compromís. */}
              <dd className="mt-0.5 text-ink">
                {undetermined ? <span className="text-warning">Cap requisit avaluat</span> : counters.confirmed_blockers}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Anàlisi</dt>
              <dd className={`mt-0.5 ${trustworthy ? "text-ink" : "text-warning"}`}>
                {ANALYSIS_BASIS_LABELS[workspace.analysis_basis]}
                {workspace.analysis_outdated && " · endarrerida"}
              </dd>
            </div>
          </dl>

          {undetermined && (
            <div className="border border-warning bg-warning-soft px-4 py-3">
              <p className="text-[13px] font-semibold text-warning">
                Decidiràs sense saber si l&apos;empresa compleix
              </p>
              <p className="mt-1 text-[13px] leading-5 text-ink">
                No s&apos;ha avaluat cap requisit d&apos;aquest plec. La decisió és teva i quedarà
                registrada tal com és: presa sobre una anàlisi que encara no diu res.
              </p>
            </div>
          )}

          {!trustworthy && (
            <div className="border border-warning bg-warning-soft px-4 py-3">
              <p className="text-[13px] font-semibold text-warning">
                {ANALYSIS_BASIS_LABELS[workspace.analysis_basis]}
              </p>
              <p className="mt-1 text-[13px] leading-5 text-ink">{ANALYSIS_BASIS_HINTS[workspace.analysis_basis]}</p>
            </div>
          )}

          <div className="border border-border px-4 py-3">
            <p className="text-[13px] font-semibold text-ink">
              {warnings.length > 0
                ? `Quedaran registrats ${plural(warnings.length, "avís obert", "avisos oberts")}`
                : "Cap avís obert"}
            </p>
            {warnings.length > 0 ? (
              <ul className="mt-1.5 space-y-1">
                {warnings.map((code) => (
                  <li key={code} className="text-[13px] leading-5 text-ink">
                    {WARNING_LABELS[code] ?? code}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-[13px] leading-5 text-muted">
                El backend fa la fotografia dels avisos en el moment de signar; ara mateix no n&apos;hi ha cap.
              </p>
            )}
          </div>

          <Field
            label={needsReason ? "Motiu (obligatori: hi ha avisos oberts)" : "Motiu (opcional)"}
            hint={`${reason.length} de ${REASON_MAX_LENGTH} caràcters`}
            error={
              reasonRequiredByBackend
                ? `${mutation.error ?? "Cal escriure el motiu."}${backendWarnings.length > 0 ? ` ${backendWarnings.join(" ")}` : ""}`
                : undefined
            }
            required={needsReason}
          >
            <Textarea
              rows={4}
              maxLength={REASON_MAX_LENGTH}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={
                needsReason
                  ? "Per què s'aprova malgrat els avisos? Per exemple: «Anem en UTE amb qui aporta la classificació.»"
                  : "Opcional: context per a qui ho llegeixi d'aquí a sis mesos."
              }
            />
          </Field>

          {forbidden && (
            <div className="border border-warning bg-warning-soft px-4 py-3 text-[13px] leading-5">
              <p className="font-semibold text-warning">{mutation.error}</p>
              <p className="mt-1 text-ink">Només {WHO_CAN_DECIDE} pot signar la decisió.</p>
            </div>
          )}
          {otherError && (
            <div className="border border-danger bg-danger-soft px-4 py-3 text-[13px] leading-5">
              <p className="font-semibold text-danger">No s&apos;ha pogut registrar la decisió</p>
              <p className="mt-1 text-ink">{otherError} Res no ha quedat desat: torna-ho a provar.</p>
            </div>
          )}
        </div>
      </Modal>
    </Card>
  );
}

/**
 * Historial. Append-only: canviar de GO a NO-GO no esborra el GO, l'apila a
 * sota. Si algú pregunta d'aquí a un any qui va aprovar què, la resposta és
 * aquesta llista, en l'ordre que la retorna el backend.
 */
export function DecisionHistory({ workspace }: { workspace: WorkspaceDetail }) {
  return (
    <Card>
      <CardHeader
        title="Historial de decisions"
        description={workspace.decisions.length > 0 ? "Cap entrada no s'esborra ni es reescriu." : undefined}
      />
      {workspace.decisions.length === 0 ? (
        <p className="px-3.5 py-4 text-[13px] leading-5 text-muted">
          Encara no s&apos;ha pres cap decisió. La recomanació automàtica no en compta com a una.
        </p>
      ) : (
        <ol className="divide-y divide-border">
          {workspace.decisions.map((entry, index) => (
            <li key={entry.id} className="px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill level={DECISION_LEVEL[entry.decision]}>{DECISION_LABELS[entry.decision]}</StatusPill>
                <Attribution kind="human" person={{ name: entry.decided_by_name ?? "Una persona" }} at={entry.decided_at} />
                {index === 0 && (
                  <StatusPill level="action" size="sm" outline>
                    Vigent
                  </StatusPill>
                )}
              </div>
              {entry.reason && (
                <p className="mt-1.5 text-[13px] leading-5 whitespace-pre-line text-ink">“{entry.reason}”</p>
              )}
              <p className="mt-1.5 text-[12.5px] leading-5 text-muted">
                Amb {plural(entry.confirmed_blockers, "bloqueig confirmat", "bloqueigs confirmats")},{" "}
                {plural(entry.potential_blockers, "possible", "possibles")} i{" "}
                {plural(entry.mandatory_unknown + entry.mandatory_needs_review, "obligatori sense resoldre", "obligatoris sense resoldre")}
                {entry.prefit_score !== null && ` · pre-fit ${entry.prefit_score}`}
                {entry.automated_recommendation &&
                  ` · el sistema deia ${RECOMMENDATION_LABELS[entry.automated_recommendation as keyof typeof RECOMMENDATION_LABELS] ?? entry.automated_recommendation}`}
                {entry.deadline_passed && " · amb el termini ja tancat"}
              </p>
              {/* Els comptadors diuen quants bloquejos hi havia. Això diu la
                  resta: que l'anàlisi era una simulació, que no hi havia motor,
                  que l'elegibilitat estava indeterminada. */}
              {entry.open_warnings === null ? (
                <p className="mt-1 text-[12.5px] italic text-muted">Els avisos d&apos;aquell moment no es van registrar.</p>
              ) : entry.open_warnings.length > 0 ? (
                <div className="mt-1 space-y-0.5">
                  {entry.open_warnings.map((code) => (
                    <p key={code} className="text-[12.5px] leading-5 text-warning">
                      Es va decidir sabent-ho: {WARNING_LABELS[code] ?? code}
                    </p>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-[12.5px] text-muted">Sense cap avís obert.</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
