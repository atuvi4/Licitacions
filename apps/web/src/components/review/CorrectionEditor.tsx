"use client";

import { Plus } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { ValueRow } from "@/components/review/ValueRow";
import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  CATEGORY_OPTIONS,
  type Draft,
  type DraftValue,
  LIMITS,
  MODALITY_OPTIONS,
  OBLIGATION_OPTIONS,
  type StatusChoice,
  correctionPayload,
  diff,
  draftFrom,
  hasErrors,
  isNoop,
  newValue,
  noteRequired,
  rebase,
  validate,
} from "@/lib/correction";
import { type CanonicalRequirement, actionFailure, submitCorrection } from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Una cita llarga, retallada per paraules: la sencera es pot consultar al detall. */
function snippet(quote: string, max = 280): string {
  const text = quote.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, text.lastIndexOf(" ", max) > 0 ? text.lastIndexOf(" ", max) : max)}…`;
}

/** D'on surt un valor, segons l'element: corregir un extret no és el mateix que afegir-ne un. */
function sourceLabel(source: string | undefined, provenance: string): string | null {
  if (source === "document") return "extret del document";
  if (source === "reviewer") return provenance === "manual" ? "afegit manualment" : "corregit pel revisor";
  return null;
}

export const CONFLICT_DRAFT_MESSAGE = "Aquest requisit ha canviat mentre l'editaves. No hem desat la teva correcció.";

/** Els camps del 422 del backend, dits amb el nom que veu la persona. */
const FIELD_NAMES: Record<string, string> = {
  title: "Títol",
  description: "Descripció",
  category: "Categoria",
  obligation: "Obligació",
  modality: "Aplicació",
  values: "Valors",
  remove_values: "Valors",
  note: "Nota de correcció",
  verification_status: "Estat",
};

function invalidFields(caught: unknown): string[] {
  if (!(caught instanceof ApiError)) return [];
  const names = caught.fields.map((field) => FIELD_NAMES[field.field.split(".")[0]]).filter(Boolean);
  return [...new Set(names)];
}

interface Conflict {
  current: CanonicalRequirement;
  /** La persona ha mirat la versió nova i l'editor ja en parteix. */
  reviewed: boolean;
  changedOnServer: string[];
  conflicts: string[];
}

export interface CorrectionEditorProps {
  item: CanonicalRequirement;
  companyId: string;
  tenderId: string;
  onSaved: (item: CanonicalRequirement) => void;
  onClose: () => void;
  /** Torna a llegir l'element canònic (després d'un 409). */
  fetchCurrent: () => Promise<CanonicalRequirement | null>;
  /** Registra qui pot aturar el tancament del calaix quan hi ha canvis sense desar. */
  registerCloseGuard?: (guard: (() => boolean) | null) => void;
}

/**
 * Corregeix la instantània acceptada d'un requisit. Mai no toca l'extracció.
 *
 * Tres versions conviuen: `base` (d'on parteix l'edició), el `draft` (el que
 * s'escriu) i, després d'un 409, `current` (la del servidor). Només s'envia la
 * diferència entre `base` i `draft`, amb la revisió de `base`.
 */
export function CorrectionEditor({
  item,
  companyId,
  tenderId,
  onSaved,
  onClose,
  fetchCurrent,
  registerCloseGuard,
}: CorrectionEditorProps) {
  const id = useId();
  const [base, setBase] = useState<CanonicalRequirement>(item);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(item.editable));
  const [statusChoice, setStatusChoice] = useState<StatusChoice>("keep");
  const [note, setNote] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [serverError, setServerError] = useState<{ message: string; fields: string[] } | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const content = useMemo(() => diff(base.editable, draft), [base, draft]);
  const noop = isNoop(content);
  const errors = useMemo(() => validate(draft), [draft]);
  const needsNote = noteRequired(base, statusChoice);
  const noteMissing = needsNote && !note.trim();
  const dirty = !noop || note.trim() !== "" || statusChoice !== "keep";
  const blockedByConflict = conflict !== null && !conflict.reviewed;
  const canSave = !pending && !noop && !blockedByConflict;

  // El calaix no es tanca en silenci amb canvis pendents.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    registerCloseGuard?.(() => {
      if (!dirtyRef.current) return false;
      setConfirmDiscard(true);
      return true;
    });
    return () => registerCloseGuard?.(null);
  }, [registerCloseGuard]);

  useEffect(() => {
    root.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, []);

  useEffect(() => {
    if (confirmDiscard) root.current?.querySelector<HTMLButtonElement>("[data-keep-editing]")?.focus();
  }, [confirmDiscard]);

  function update(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setServerError(null);
  }

  function updateValue(key: string, patch: Partial<DraftValue>) {
    setDraft((current) => ({
      ...current,
      values: current.values.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    }));
    setServerError(null);
  }

  function removeValue(row: DraftValue) {
    setDraft((current) => ({
      ...current,
      values: current.values.filter((value) => value.key !== row.key),
      removed: row.isNew ? current.removed : [...current.removed, row.key],
    }));
  }

  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") return;
    // L'Escape es queda a l'editor: no tanca el calaix amb un esborrany a dins.
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
    if (confirmDiscard) setConfirmDiscard(false);
    else requestClose();
  }

  async function save() {
    setSubmitted(true);
    if (!canSave || hasErrors(errors) || noteMissing) return;
    setPending(true);
    setServerError(null);
    try {
      const updated = await submitCorrection(
        companyId,
        tenderId,
        base.canonical_id,
        base.review_revision,
        correctionPayload(base, content, statusChoice, note),
      );
      onSaved(updated);
    } catch (caught) {
      const problem = actionFailure(caught);
      if (problem.kind === "conflict") {
        // L'esborrany es queda tal com és; només es llegeix la versió nova.
        const current = await fetchCurrent();
        if (current) {
          const preview = rebase(base.editable, current.editable, draft);
          setConflict({ current, reviewed: false, changedOnServer: preview.changedOnServer, conflicts: [] });
        } else {
          setServerError({ message: "L'element ja no està disponible.", fields: [] });
        }
      } else {
        setServerError({ message: problem.message, fields: problem.kind === "invalid" ? invalidFields(caught) : [] });
      }
    } finally {
      setPending(false);
    }
  }

  function reviewNewVersion() {
    if (!conflict) return;
    const result = rebase(base.editable, conflict.current.editable, draft);
    setDraft(result.draft);
    setBase(conflict.current);
    setStatusChoice("keep");
    setConflict({ ...conflict, reviewed: true, changedOnServer: result.changedOnServer, conflicts: result.conflicts });
    setSubmitted(false);
  }

  const show = submitted;

  return (
    <div ref={root} role="group" aria-labelledby={`${id}-title`} className="mt-2.5 space-y-4" onKeyDown={onKeyDown}>
      <div>
        <p id={`${id}-title`} className="text-body font-semibold text-ink">
          Corregir el requisit
        </p>
        <p className="text-[13px] leading-5 text-muted">
          Canvia el que la vostra empresa accepta d&apos;aquest requisit. El text del plec i el que se n&apos;ha extret no es
          modifiquen.
        </p>
      </div>

      {conflict && (
        <div role="alert" className="space-y-2 rounded-control border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
          <p className="font-semibold">{CONFLICT_DRAFT_MESSAGE}</p>
          <p>
            Hi ha una revisió nova (revisió {conflict.current.review_revision}, estat «{conflict.current.verification_label}»).
            {conflict.changedOnServer.length > 0 && <> Ha canviat: {conflict.changedOnServer.join(", ")}.</>}
          </p>
          {!conflict.reviewed ? (
            <>
              <p>El teu esborrany es conserva. Abans de tornar a desar, revisa la versió nova.</p>
              <Button size="sm" variant="secondary" onClick={reviewNewVersion}>
                Revisa la versió nova
              </Button>
            </>
          ) : (
            <p>
              L&apos;editor ja parteix de la revisió {conflict.current.review_revision}: el que no havies tocat és el de la versió
              nova i els teus canvis es mantenen.
              {conflict.conflicts.length > 0 && (
                <> Mira-ho amb cura: {conflict.conflicts.join(", ")} també ha canviat a la versió nova.</>
              )}
            </p>
          )}
        </div>
      )}

      <Field label="Títol" error={show ? errors.title : undefined} required>
        <Input value={draft.title} onChange={(event) => update({ title: event.target.value })} aria-invalid={show && errors.title ? true : undefined} />
      </Field>
      <Field label="Descripció" error={show ? errors.description : undefined}>
        <Textarea rows={3} value={draft.description} onChange={(event) => update({ description: event.target.value })} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Categoria" className="sm:col-span-2">
          <Select value={draft.category} options={CATEGORY_OPTIONS} onChange={(event) => update({ category: event.target.value })} />
        </Field>
        <Field label="Obligació" hint="Què en fa el plec.">
          <Select value={draft.obligation} options={OBLIGATION_OPTIONS} onChange={(event) => update({ obligation: event.target.value })} />
        </Field>
        <Field label="Aplicació" hint="Quan s'aplica el requisit.">
          <Select value={draft.modality} options={MODALITY_OPTIONS} onChange={(event) => update({ modality: event.target.value })} />
        </Field>
      </div>

      {base.evidence.length > 0 && (
        // La cita al costat dels valors: es poden corregir sense baixar pel calaix.
        <section aria-label="Evidència de referència" className="space-y-1.5">
          <p className={KICKER}>Evidència de referència</p>
          <ul className="space-y-2">
            {base.evidence.map((evidence, index) => (
              <li key={`${evidence.page}-${index}`} className="border-l-2 border-accent-500 pl-2.5">
                <p className="text-caption text-muted">
                  <span className="font-medium text-ink">{evidence.document ?? "Document"}</span>
                  {evidence.page !== null && <> · p. {evidence.page}</>}
                  {evidence.section && <> · § {evidence.section}</>}
                </p>
                {evidence.quote && (
                  <blockquote className="mt-0.5 text-[13px] leading-relaxed text-ink italic">
                    «{snippet(evidence.quote)}»
                  </blockquote>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <fieldset className="space-y-2">
        <legend className={KICKER}>Valors</legend>
        {draft.values.length === 0 && <p className="text-[13px] text-muted">Sense valors estructurats.</p>}
        {draft.values.map((row, index) => (
          <ValueRow
            key={row.key}
            index={index}
            row={row}
            sourceLabel={sourceLabel(base.editable.values.find((value) => value.key === row.key)?.source, base.provenance)}
            error={show ? errors.values[row.key] : undefined}
            onChange={(patch) => updateValue(row.key, patch)}
            onRemove={() => removeValue(row)}
          />
        ))}
        {show && errors.general && <p className="text-[13px] text-danger">{errors.general}</p>}
        <Button size="sm" variant="ghost" onClick={() => update({ values: [...draft.values, newValue(draft, base.editable)] })}>
          <Plus size={14} strokeWidth={1.5} aria-hidden />
          Afegeix un valor
        </Button>
      </fieldset>

      <fieldset className="space-y-1.5">
        <legend className={KICKER}>Estat després de desar</legend>
        <p className="text-[13px] leading-5 text-muted">Corregir el contingut no implica verificar-lo.</p>
        <label className="flex items-center gap-2 text-body text-ink">
          <input type="radio" name={`${id}-status`} checked={statusChoice === "keep"} onChange={() => setStatusChoice("keep")} />
          Mantenir l&apos;estat actual ({base.verification_label})
        </label>
        {base.verification_status !== "verified" && (
          <label className="flex items-center gap-2 text-body text-ink">
            <input type="radio" name={`${id}-status`} checked={statusChoice === "verified"} onChange={() => setStatusChoice("verified")} />
            Verificat: he comprovat el requisit corregit
          </label>
        )}
      </fieldset>

      <Field
        label={needsNote ? "Nota de correcció (obligatòria)" : "Nota de correcció (opcional)"}
        hint={needsNote ? `Per mantenir l'estat «${base.verification_label}» cal explicar-ho.` : "Queda a l'historial de revisió."}
        error={show && noteMissing ? "Cal una nota per mantenir aquest estat." : undefined}
      >
        <Textarea rows={2} value={note} maxLength={LIMITS.note} onChange={(event) => setNote(event.target.value)} />
      </Field>

      {serverError && (
        <div role="alert" className="rounded-control border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
          {serverError.message}
          {serverError.fields.length > 0 && <> ({serverError.fields.join(", ")})</>}
        </div>
      )}

      {confirmDiscard ? (
        <div role="alertdialog" aria-labelledby={`${id}-discard`} className="space-y-2 rounded-control border border-border-strong px-3 py-2">
          <p id={`${id}-discard`} className="text-body font-semibold text-ink">
            Descartar els canvis no desats?
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" data-keep-editing onClick={() => setConfirmDiscard(false)}>
              Continua editant
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              Descarta els canvis
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" loading={pending} disabled={!canSave} onClick={() => void save()}>
            Desa la correcció
          </Button>
          <Button size="sm" variant="ghost" disabled={pending} onClick={requestClose}>
            Cancel·la
          </Button>
          {noop && !pending && <span className="text-[13px] text-muted">No hi ha cap canvi per desar.</span>}
        </div>
      )}
    </div>
  );
}
