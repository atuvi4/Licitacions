"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import { Button, ErrorNotice, Field, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { candidateTitle, isPromotable, proposalSummary, type Observation, type Vocabulary } from "@/lib/knowledge";

const ACTION = "min-h-11 md:min-h-0";

interface Props {
  observation: Observation;
  vocabulary: Vocabulary | null;
  canDecide: boolean;
  /** Ja hi ha una dada actual sobre la mateixa clau: mana la declarada. */
  superseded?: boolean;
  onChanged: () => void;
}

/**
 * El que es pot fer amb una dada detectada a l'històric: confirmar-la com a
 * actual o descartar-la. Confirmar-la NO l'acredita: en neix una dada actual
 * declarada, que continua pendent d'evidència. Res no es promou sol.
 */
export function CandidateActions({ observation, vocabulary, canDecide, superseded = false, onChanged }: Props) {
  const path = useCompanyPath();
  const { saving, error, run, reset } = useMutation();
  const [mode, setMode] = useState<"promote" | "dismiss" | null>(null);
  const [note, setNote] = useState("");

  const promotable = isPromotable(observation) && !superseded;
  const title = candidateTitle(observation.subject, observation.subject_key, vocabulary);
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

  const label = promotable ? "Confirma com a actual" : "Descarta";
  return (
    <>
      {promotable && (
        <Button
          size="sm"
          variant="secondary"
          className={ACTION}
          disabled={!canDecide}
          title={canDecide ? undefined : "El teu rol no pot confirmar l'històric"}
          aria-label={`${label}: ${title}`}
          onClick={() => open("promote")}
        >
          Confirma com a actual
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
        title={mode === "promote" ? `Confirma «${title}» com a actual` : `Descarta «${title}»`}
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
              {mode === "promote" ? "Confirma com a actual" : "Descarta"}
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
            <p className="text-body leading-6 text-ink">
              Es crearà com a dada actual <strong className="font-medium">declarada</strong>:{" "}
              <strong className="font-medium">{proposalSummary(observation, vocabulary)}</strong>. No quedarà verificada:
              confirmar el que deia l&apos;històric no és acreditar-ho, i continuarà pendent d&apos;evidència.
            </p>
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
