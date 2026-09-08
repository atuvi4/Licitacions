"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { Badge, Button, Card, CardHeader, Field, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { formatDateTime } from "@/lib/tenders";
import { RECOMMENDATION_LABELS } from "@/lib/compliance";
import {
  DECISION_LABELS,
  DECISION_TONES,
  WARNING_LABELS,
  type WorkspaceDecisionType,
  type WorkspaceDetail,
} from "@/lib/workspace";

/**
 * El bloc on una persona decideix.
 *
 * Ensenya la recomanació automàtica i la decisió humana **una al costat de
 * l'altra i mai fusionades**: el sistema pot dir REVIEW i la decisió pot seguir
 * sent PENDENT durant setmanes. Això no és un estat inconsistent: és el cas
 * normal.
 *
 * Els avisos oberts es veuen abans de clicar, i tornen a sortir dins del
 * formulari: qui aprova ha de tenir el problema a la vista mentre escriu el
 * motiu, no a la pantalla anterior.
 */
export function DecisionPanel({
  workspace,
  onDecided,
}: {
  workspace: WorkspaceDetail;
  onDecided: () => Promise<void>;
}) {
  const [pending, setPending] = useState<WorkspaceDecisionType | null>(null);
  const [reason, setReason] = useState("");
  const mutation = useMutation();

  const warnings = workspace.open_warnings;
  const needsReason = pending === "go" && workspace.go_requires_reason;

  function open(decision: WorkspaceDecisionType) {
    mutation.reset();
    setReason("");
    setPending(decision);
  }

  async function submit() {
    if (!pending) return;
    const ok = await mutation.run(async () => {
      await api.post(
        `/companies/${workspace.company_id}/workspaces/${workspace.id}/decisions`,
        { decision: pending, reason: reason.trim() || null },
      );
    });
    if (ok) {
      setPending(null);
      await onDecided();
    }
  }

  const confirmQuestion: Record<WorkspaceDecisionType, string> = {
    go: "Aproves presentar candidatura a aquesta licitació?",
    no_go: "Confirmes descartar aquesta licitació?",
    defer: "Vols ajornar la decisió?",
  };

  return (
    <Card>
      <CardHeader
        title="Decisió"
        description="El sistema recomana. La decisió oficial la pren una persona."
      />
      <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
        <div>
          <p className="text-[12px] uppercase tracking-wide text-muted">Recomanació automàtica</p>
          <p className="mt-1 text-lg font-semibold text-ink">
            {workspace.recommendation
              ? RECOMMENDATION_LABELS[workspace.recommendation]
              : "Sense càlcul"}
          </p>
          <p className="mt-0.5 text-[12px] text-faint">
            Derivada de l&apos;elegibilitat i el pre-fit. No és una decisió.
          </p>
        </div>
        <div>
          <p className="text-[12px] uppercase tracking-wide text-muted">Decisió humana</p>
          <p className="mt-1 flex items-center gap-2 text-lg font-semibold text-ink">
            {workspace.human_decision ? (
              <Badge tone={DECISION_TONES[workspace.human_decision]}>
                {DECISION_LABELS[workspace.human_decision]}
              </Badge>
            ) : (
              <span className="text-warning">PENDENT</span>
            )}
          </p>
          {workspace.latest_decision && (
            <p className="mt-0.5 text-[12px] text-faint">
              {workspace.latest_decision.decided_by_name ?? "—"} ·{" "}
              {formatDateTime(workspace.latest_decision.decided_at)}
            </p>
          )}
        </div>
      </div>

      {warnings.length > 0 && (
        <div className="border-t border-border px-5 py-4">
          <p className="text-[13px] font-semibold text-warning">
            Avisos oberts sobre aquesta candidatura
          </p>
          <ul className="mt-2 space-y-1">
            {warnings.map((code) => (
              <li key={code} className="text-[13px] text-muted">
                ⚠ {WARNING_LABELS[code] ?? code}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[12px] text-faint">
            Es pot aprovar igualment —hi pot haver una raó legítima— però caldrà escriure-la.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-4">
        {workspace.can_decide ? (
          <>
            <Button variant="primary" onClick={() => open("go")}>
              Aprova GO
            </Button>
            <Button variant="danger" onClick={() => open("no_go")}>
              NO-GO
            </Button>
            <Button variant="secondary" onClick={() => open("defer")}>
              Posposa
            </Button>
          </>
        ) : (
          <p className="text-[13px] text-muted">
            El teu rol no pot aprovar ni descartar candidatures. Pots consultar-ho tot.
          </p>
        )}
      </div>

      <Modal
        open={pending !== null}
        title={pending ? confirmQuestion[pending] : ""}
        description="Quedarà registrat qui decideix, quan i per què."
        onClose={() => setPending(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setPending(null)} disabled={mutation.saving}>
              Cancel·la
            </Button>
            <Button
              variant={pending === "no_go" ? "danger" : "primary"}
              onClick={() => void submit()}
              loading={mutation.saving}
              disabled={needsReason && reason.trim().length === 0}
            >
              {pending ? DECISION_LABELS[pending] : ""}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-3 rounded-md bg-subtle px-3 py-3 text-[13px]">
            <div>
              <dt className="text-muted">Expedient</dt>
              <dd className="font-mono text-ink">{workspace.expediente_number ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Pre-fit</dt>
              <dd className="text-ink">{workspace.prefit_score ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Recomanació</dt>
              <dd className="text-ink">
                {workspace.recommendation
                  ? RECOMMENDATION_LABELS[workspace.recommendation]
                  : "Sense càlcul"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Bloqueigs confirmats</dt>
              <dd className="text-ink">{workspace.counters.confirmed_blockers}</dd>
            </div>
          </dl>

          {warnings.length > 0 && (
            <div className="rounded-md border border-warning/40 bg-warning-soft px-3 py-3">
              <p className="text-[13px] font-semibold text-warning">
                Decidiràs amb {warnings.length} avís
                {warnings.length === 1 ? "" : "os"} obert{warnings.length === 1 ? "" : "s"}
              </p>
              <ul className="mt-1.5 space-y-1">
                {warnings.map((code) => (
                  <li key={code} className="text-[13px] text-ink">
                    ⚠ {WARNING_LABELS[code] ?? code}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Field
            label={needsReason ? "Motiu (obligatori)" : "Motiu"}
            error={mutation.error ?? undefined}
          >
            <Textarea
              rows={4}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={
                needsReason
                  ? "Per què s'aprova malgrat els avisos?"
                  : "Opcional: context per a qui ho llegeixi d'aquí a sis mesos."
              }
            />
          </Field>
        </div>
      </Modal>
    </Card>
  );
}

/**
 * Historial. Append-only: canviar de GO a NO-GO no esborra el GO, l'apila a
 * sota. Si algú pregunta d'aquí a un any qui va aprovar què, la resposta és
 * aquesta llista.
 */
export function DecisionHistory({ workspace }: { workspace: WorkspaceDetail }) {
  if (workspace.decisions.length === 0) {
    return (
      <Card>
        <CardHeader title="Historial de decisions" />
        <p className="px-5 py-5 text-[13px] text-muted">
          Encara no hi ha cap decisió humana. La recomanació automàtica no en compta com a una.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Historial de decisions"
        description="Cap entrada no s'esborra ni es reescriu."
      />
      <ol className="divide-y divide-border">
        {workspace.decisions.map((entry, index) => (
          <li key={entry.id} className="px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={DECISION_TONES[entry.decision]}>{DECISION_LABELS[entry.decision]}</Badge>
              <span className="text-[13px] text-ink">{entry.decided_by_name ?? "—"}</span>
              <span className="text-[13px] text-faint">{formatDateTime(entry.decided_at)}</span>
              {index === 0 && <Badge tone="brand">Vigent</Badge>}
            </div>
            {entry.reason && (
              <p className="mt-1.5 whitespace-pre-line text-[13px] leading-relaxed text-muted">
                “{entry.reason}”
              </p>
            )}
            <p className="mt-1.5 text-[12px] text-faint">
              Amb {entry.confirmed_blockers} bloqueig(s) confirmat(s), {entry.potential_blockers}{" "}
              possible(s) i {entry.mandatory_unknown + entry.mandatory_needs_review} obligatori(s)
              sense resoldre
              {entry.prefit_score !== null && ` · pre-fit ${entry.prefit_score}`}
              {entry.automated_recommendation && ` · el sistema deia ${entry.automated_recommendation}`}
              {entry.deadline_passed && " · amb el termini ja tancat"}
            </p>
          </li>
        ))}
      </ol>
    </Card>
  );
}
