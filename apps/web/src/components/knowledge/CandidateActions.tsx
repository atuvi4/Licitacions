"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import { Button, ErrorNotice, Field, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { candidateTitle, describeProposal, isPromotable, proposalHeadline, type Observation, type Vocabulary } from "@/lib/knowledge";

const ACTION = "min-h-11 md:min-h-0";

interface Props {
  observation: Observation;
  vocabulary: Vocabulary | null;
  canDecide: boolean;
  /** Ja hi ha una dada actual sobre la mateixa clau: mana la declarada. */
  superseded?: boolean;
  onChanged: () => void;
}

const SAVED = "Si ho confirmes, Tuvi guardarà com a dada actual";

/**
 * El que es pot fer amb una dada detectada a l'històric: confirmar-la com a
 * actual o descartar-la. Confirmar-la NO l'acredita: en neix una dada actual
 * declarada, que continua pendent d'evidència. Res no es promou sol.
 *
 * Abans de confirmar es veu la dada exacta que es desarà, llegida de la
 * proposta estructurada: una proposta en negatiu («no en té») es desa com un
 * NO, i no s'ha de poder prendre per un sí.
 */
export function CandidateActions({ observation, vocabulary, canDecide, superseded = false, onChanged }: Props) {
  const path = useCompanyPath();
  const { saving, error, run, reset } = useMutation();
  const [mode, setMode] = useState<"promote" | "dismiss" | null>(null);
  const [note, setNote] = useState("");

  const promotable = isPromotable(observation) && !superseded;
  const title = candidateTitle(observation.subject, observation.subject_key, vocabulary);
  const described = describeProposal(observation, vocabulary);
  const base = path(`/knowledge/observations/${observation.id}`);

  function open(next: "promote" | "dismiss") {
    reset();
    setNote("");
    setMode(next);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      if (mode === "promote") await api.post(`${base}/promote`, { note: note.trim() || null });
      else await api.post(`${base}/dismiss`, { note: note.trim() });
    });
    if (ok) {
      setMode(null);
      onChanged();
    }
  }

  return (
    <>
      {promotable && (
        <Button
          size="sm"
          variant="secondary"
          className={ACTION}
          disabled={!canDecide}
          title={canDecide ? undefined : "El teu rol no pot confirmar l'històric"}
          aria-label={`Revisa i confirma: ${title}`}
          onClick={() => open("promote")}
        >
          Revisa i confirma
        </Button>
      )}
      <Button
        size="sm"
        variant="ghost"
        className={ACTION}
        disabled={!canDecide}
        title={canDecide ? undefined : "El teu rol no pot descartar l'històric"}
        aria-label={`Descarta: ${title}`}
        onClick={() => open("dismiss")}
      >
        Descarta
      </Button>

      <Modal
        open={mode !== null}
        title={mode === "promote" ? `Revisa i confirma «${title}»` : `Descarta «${title}»`}
        onClose={() => setMode(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setMode(null)} disabled={saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="candidate-form"
              loading={saving}
              disabled={mode === "dismiss" && note.trim().length < 3}
            >
              {mode === "promote" ? "Confirma i desa" : "Descarta"}
            </Button>
          </>
        }
      >
        <form id="candidate-form" onSubmit={submit} className="space-y-4">
          {error && <ErrorNotice message={error} />}
          <div className="rounded-card border border-border bg-subtle px-4 py-3">
            <p className="text-caption font-medium text-muted">Què diu l&apos;històric</p>
            <p className="mt-0.5 text-body text-ink">{observation.statement}</p>
          </div>
          {mode === "promote" ? (
            <>
              <div role="group" aria-label={SAVED} className="rounded-card border border-border-strong bg-surface px-4 py-3">
                <p className="text-caption font-medium text-muted">{SAVED}</p>
                <p
                  className="mt-1 font-heading text-[18px] leading-tight font-semibold text-ink"
                  data-proposal={described.negative ? "negative" : "positive"}
                >
                  {described.label}: <span className={described.negative ? "text-danger" : undefined}>{described.value}</span>
                </p>
                {described.fields.length > 0 && (
                  <dl className="mt-2 space-y-1">
                    {described.fields.map((field, position) => (
                      <div key={position} className="flex flex-wrap gap-x-2 text-body">
                        <dt className="text-muted">{field.label}</dt>
                        <dd className="text-ink">{field.value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>
              {described.negative && (
                <p role="note" className="rounded-control border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
                  Es desarà en negatiu: quedarà escrit «{proposalHeadline(described)}». Si avui la resposta és que sí, no ho
                  confirmis: descarta el suggeriment i declara la dada des de «Coneixement actual».
                </p>
              )}
              <div>
                <p className="text-caption font-medium text-muted">Quedarà</p>
                <ul aria-label="Com quedarà" className="mt-1 list-disc space-y-0.5 pl-5 text-body text-ink">
                  <li>
                    <strong className="font-medium">Declarat</strong>, amb l&apos;origen anotat com a confirmat de l&apos;històric.
                  </li>
                  <li>
                    <strong className="font-medium">No verificat</strong>: confirmar el que deia l&apos;històric no és acreditar-ho.
                  </li>
                  <li>
                    <strong className="font-medium">Sense evidència</strong>. Per verificar aquesta dada caldrà aportar evidència i
                    verificar-la.
                  </li>
                </ul>
              </div>
            </>
          ) : (
            <p className="text-body leading-6 text-ink">
              Deixarà de sortir com a pendent. No s&apos;esborra: queda registrat qui l&apos;ha descartat i per què.
            </p>
          )}
          <Field
            label={mode === "promote" ? "Nota (opcional)" : "Motiu"}
            required={mode === "dismiss"}
            hint={mode === "dismiss" ? "Per exemple: «ja no treballem amb aquest proveïdor»." : undefined}
          >
            <Textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
          </Field>
        </form>
      </Modal>
    </>
  );
}
