"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { CorrectionEditor } from "@/components/review/CorrectionEditor";
import { CitationPreview, EvidencePicker } from "@/components/review/EvidencePicker";
import { Button, ResultNotice, StatusPill, Textarea } from "@/components/ui";
import {
  type ActionFailure,
  type CanonicalRequirement,
  type ReviewActionName,
  actionFailure,
  submitLinkSource,
  submitReviewAction,
  verificationLevel,
  whoCanReview,
} from "@/lib/review";
import { type CitationDraft, citationPayload } from "@/lib/evidence";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Les accions que demanen text, amb la seva forma. Confirmar no en demana. */
const FORMS: Record<Exclude<ReviewActionName, "confirm">, {
  opener: string;
  title: string;
  explanation: string;
  label: string;
  submit: string;
  empty: string;
}> = {
  mark_partial: {
    opener: "Marcar parcial",
    title: "Marcar com a parcial",
    explanation: "Una part del requisit és correcta o està coberta i una altra no. La nota queda a l'historial de revisió; el text del plec no canvia.",
    label: "Què és parcial",
    submit: "Marca parcial",
    empty: "Cal explicar què és parcial.",
  },
  reject: {
    opener: "Descartar",
    title: "Descartar per a la vostra empresa",
    explanation: "El requisit queda descartat NOMÉS a la revisió de la vostra empresa. No s'esborra res: el text del plec i el que se n'ha extret es conserven, i es pot tornar a revisar.",
    label: "Motiu",
    submit: "Descarta de la revisió",
    empty: "Cal indicar el motiu.",
  },
  note: {
    opener: "Afegir nota",
    title: "Afegir una nota",
    explanation: "La nota queda a l'historial. No canvia l'estat del requisit ni el dona per verificat.",
    label: "Nota",
    submit: "Desa la nota",
    empty: "La nota no pot quedar buida.",
  },
};

const DONE: Record<ReviewActionName, string> = {
  confirm: "Requisit confirmat.",
  mark_partial: "Requisit marcat com a parcial.",
  reject: "Requisit descartat de la revisió. L'extracció es conserva.",
  note: "Nota afegida a l'historial.",
};

export interface ReviewActionsProps {
  item: CanonicalRequirement;
  companyId: string;
  tenderId: string;
  canWrite: boolean;
  /** L'element tal com l'ha tornat el servidor; `statusMayChange` diu si cal refer el resum. */
  onUpdated: (item: CanonicalRequirement, statusMayChange: boolean) => void;
  /** Després d'un 409: torna a llegir l'element canònic i el torna. */
  onStale: () => Promise<CanonicalRequirement | null>;
  /** Després d'un 404: l'element ja no hi és. */
  onGone: () => Promise<void>;
  /** L'editor de correccions pot aturar el tancament del calaix si hi ha canvis. */
  registerCloseGuard?: (guard: (() => boolean) | null) => void;
}

/**
 * La secció «Revisió» del detall d'un requisit: l'estat actual i les accions.
 *
 * Cada acció envia la revisió que es veu (`expected_revision`). No es marca res
 * abans que respongui el servidor, i un 409 no es reintenta: es mostra la
 * versió actual i l'usuari decideix.
 */
export function ReviewActions({
  item,
  companyId,
  tenderId,
  canWrite,
  onUpdated,
  onStale,
  onGone,
  registerCloseGuard,
}: ReviewActionsProps) {
  const [form, setForm] = useState<keyof typeof FORMS | null>(null);
  const [editing, setEditing] = useState(false);
  const [linking, setLinking] = useState(false);
  const [chosen, setChosen] = useState<CitationDraft | null>(null);
  const [linkPending, setLinkPending] = useState(false);
  const [linkProblem, setLinkProblem] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [pending, setPending] = useState<ReviewActionName | null>(null);
  const [failure, setFailure] = useState<ActionFailure | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [returnTo, setReturnTo] = useState<string | null>(null);
  const section = useRef<HTMLElement>(null);
  const focusField = () => section.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();

  // Un altre requisit al calaix: res del formulari anterior hi pot quedar.
  useEffect(() => {
    setForm(null);
    setEditing(false);
    setLinking(false);
    setChosen(null);
    setLinkProblem(null);
    setText("");
    setFieldError(null);
    setFailure(null);
    setDone(null);
  }, [item.canonical_id]);

  useEffect(() => {
    if (form) focusField();
  }, [form]);

  // El focus torna al botó que ha obert el formulari, un cop tornat a pintar.
  useEffect(() => {
    if (!form && returnTo) {
      section.current?.querySelector<HTMLButtonElement>(`[data-opener="${returnTo}"]`)?.focus();
      setReturnTo(null);
    }
  }, [form, returnTo]);

  function openForm(name: keyof typeof FORMS) {
    setForm(name);
    setText("");
    setFieldError(null);
    setFailure(null);
    setDone(null);
  }

  function closeForm() {
    setReturnTo(form);
    setForm(null);
    setFieldError(null);
  }

  function onFormKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // Escape tanca el formulari, no el calaix sencer: l'esborrany no es perd per sorpresa.
    if (event.key === "Escape") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      closeForm();
    }
  }

  async function run(action: ReviewActionName) {
    if (pending) return;
    if (action !== "confirm" && !text.trim()) {
      setFieldError(FORMS[action].empty);
      focusField();
      return;
    }
    setPending(action);
    setFailure(null);
    setDone(null);
    try {
      const updated = await submitReviewAction(companyId, tenderId, item, action, text);
      setForm(null);
      setText("");
      setDone(DONE[action]);
      onUpdated(updated, action !== "note");
    } catch (caught) {
      const problem = actionFailure(caught);
      if (problem.kind === "invalid") {
        setFieldError(problem.message);
      } else {
        setFailure(problem);
      }
      if (problem.kind === "conflict") {
        setForm(null);
        await onStale();
      } else if (problem.kind === "gone") {
        setForm(null);
        await onGone();
      }
    } finally {
      setPending(null);
    }
  }

  function closeLinking() {
    setLinking(false);
    setChosen(null);
    setLinkProblem(null);
    setReturnTo("link");
  }

  async function attach() {
    if (!chosen || linkPending) return;
    setLinkPending(true);
    setLinkProblem(null);
    setDone(null);
    try {
      // Sempre amb la revisió que es veu ara: després d'un 409 cal tornar-ho a prémer.
      const updated = await submitLinkSource(companyId, tenderId, item, citationPayload(chosen));
      setLinking(false);
      setChosen(null);
      setDone("Evidència afegida.");
      onUpdated(updated, false);
    } catch (caught) {
      const problem = actionFailure(caught);
      if (problem.kind === "conflict") {
        await onStale();
        setLinkProblem(
          "Aquest requisit ha canviat des que l'has obert. La cita triada es conserva: revisa la versió actual i torna-la a afegir si encara cal.",
        );
      } else if (problem.kind === "gone") {
        setLinkProblem(problem.message);
        await onGone();
      } else {
        setLinkProblem(problem.message);
      }
    } finally {
      setLinkPending(false);
    }
  }

  function onLinkKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      closeLinking();
    }
  }

  const busy = pending !== null;
  const active = form ? FORMS[form] : null;

  return (
    <section ref={section} aria-labelledby="detall-revisio" className="border border-border px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="detall-revisio" className={KICKER}>
          Revisió
        </h3>
        <StatusPill level={verificationLevel(item.verification_status)} size="sm">
          {item.verification_label}
        </StatusPill>
      </div>

      {done && <ResultNotice message={done} tone="success" onDismiss={() => setDone(null)} />}
      {failure && (
        <div role="alert" className="mt-2 border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
          {failure.message}
        </div>
      )}

      {!canWrite ? (
        <p className="mt-2 text-[13px] leading-5 text-muted">
          Només {whoCanReview()} pot revisar requisits. Tu el pots consultar.
        </p>
      ) : (
        <>
          {editing && (
            <CorrectionEditor
              item={item}
              companyId={companyId}
              tenderId={tenderId}
              registerCloseGuard={registerCloseGuard}
              fetchCurrent={onStale}
              onSaved={(updated) => {
                setEditing(false);
                setReturnTo("correct");
                setDone("Correcció desada.");
                onUpdated(updated, true);
              }}
              onClose={() => {
                setEditing(false);
                setReturnTo("correct");
              }}
            />
          )}

          {linking && (
            <div role="group" aria-labelledby="revisio-evidencia-titol" className="mt-2.5 space-y-2" onKeyDown={onLinkKeyDown}>
              <p id="revisio-evidencia-titol" className="text-body font-semibold text-ink">
                Afegir evidència
              </p>
              <p className="text-[13px] leading-5 text-muted">
                Una cita més del plec per a aquest requisit. No el verifica ni en canvia el contingut.
              </p>
              {!chosen ? (
                <EvidencePicker companyId={companyId} tenderId={tenderId} onChoose={setChosen} onCancel={closeLinking} />
              ) : (
                <>
                  <CitationPreview draft={chosen} />
                  {linkProblem && (
                    <p role="alert" className="border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
                      {linkProblem}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="primary" loading={linkPending} disabled={linkPending} onClick={() => void attach()}>
                      Afegeix l&apos;evidència
                    </Button>
                    <Button size="sm" variant="ghost" disabled={linkPending} onClick={() => setChosen(null)}>
                      Canvia la cita
                    </Button>
                    <Button size="sm" variant="ghost" disabled={linkPending} onClick={closeLinking}>
                      Cancel·la
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {!active && !editing && !linking && (
            <div className="mt-2.5 flex flex-wrap gap-2" role="group" aria-label="Accions de revisió">
              <Button size="sm" variant="primary" loading={pending === "confirm"} disabled={busy} onClick={() => void run("confirm")}>
                Confirmar
              </Button>
              {(["mark_partial", "reject"] as const).map((name) => (
                <Button
                  key={name}
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  data-opener={name}
                  onClick={() => openForm(name)}
                >
                  {FORMS[name].opener}
                </Button>
              ))}
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                data-opener="correct"
                onClick={() => {
                  setDone(null);
                  setFailure(null);
                  setEditing(true);
                }}
              >
                Corregir
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                data-opener="link"
                onClick={() => {
                  setDone(null);
                  setFailure(null);
                  setLinking(true);
                }}
              >
                Afegir evidència
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                data-opener="note"
                onClick={() => openForm("note")}
              >
                {FORMS.note.opener}
              </Button>
            </div>
          )}

          {active && form && (
            <div role="group" aria-labelledby="revisio-form-titol" className="mt-2.5 space-y-2" onKeyDown={onFormKeyDown}>
              <p id="revisio-form-titol" className="text-body font-semibold text-ink">
                {active.title}
              </p>
              <p className="text-[13px] leading-5 text-muted">{active.explanation}</p>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] leading-5 font-medium text-ink">{active.label}</span>
                <Textarea
                  rows={3}
                  value={text}
                  maxLength={2000}
                  aria-invalid={fieldError ? true : undefined}
                  aria-describedby={fieldError ? "revisio-form-error" : undefined}
                  onChange={(event) => {
                    setText(event.target.value);
                    if (fieldError) setFieldError(null);
                  }}
                />
              </label>
              {fieldError && (
                <p id="revisio-form-error" className="text-[13px] leading-5 text-danger">
                  {fieldError}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="primary" loading={pending === form} disabled={busy} onClick={() => void run(form)}>
                  {active.submit}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={closeForm}>
                  Cancel·la
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
