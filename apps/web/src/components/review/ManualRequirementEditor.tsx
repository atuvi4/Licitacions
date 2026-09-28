"use client";

import { Plus, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { CitationPreview, EvidencePicker } from "@/components/review/EvidencePicker";
import { ValueRow } from "@/components/review/ValueRow";
import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  CATEGORY_OPTIONS,
  type Draft,
  type DraftValue,
  EMPTY_REQUIREMENT,
  LIMITS,
  MODALITY_OPTIONS,
  OBLIGATION_OPTIONS,
  draftFrom,
  hasErrors,
  newValue,
  reviewerValue,
  validate,
} from "@/lib/correction";
import { type CitationDraft, citationPayload } from "@/lib/evidence";
import { type CanonicalRequirement, actionFailure, createManualRequirement } from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

const FIELD_NAMES: Record<string, string> = {
  title: "Títol",
  description: "Descripció",
  category: "Categoria",
  obligation: "Obligació",
  modality: "Modalitat",
  values: "Valors",
  note: "Nota",
  citations: "Evidència",
};

export interface ManualRequirementEditorProps {
  companyId: string;
  tenderId: string;
  onCreated: (item: CanonicalRequirement) => void;
  onClose: () => void;
  /** Registra qui pot aturar el tancament del calaix quan hi ha canvis sense desar. */
  registerCloseGuard?: (guard: (() => boolean) | null) => void;
}

/**
 * Un requisit que la vostra empresa afegeix a la revisió i que l'extracció no
 * ha trobat. Només per a aquesta empresa: mai no s'escriu a la llista global de
 * requisits. Neix «Pendent de revisar»; verificar-lo és una acció a part.
 */
export function ManualRequirementEditor({ companyId, tenderId, onCreated, onClose, registerCloseGuard }: ManualRequirementEditorProps) {
  const id = useId();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(EMPTY_REQUIREMENT));
  const [citations, setCitations] = useState<CitationDraft[]>([]);
  const [picking, setPicking] = useState(false);
  const [note, setNote] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const errors = useMemo(() => validate(draft), [draft]);
  const categoryMissing = !draft.category;
  const dirty =
    draft.title.trim() !== "" ||
    draft.description.trim() !== "" ||
    draft.category !== "" ||
    draft.obligation !== EMPTY_REQUIREMENT.obligation ||
    draft.modality !== EMPTY_REQUIREMENT.modality ||
    draft.values.length > 0 ||
    citations.length > 0 ||
    picking ||
    note.trim() !== "";

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
    setDraft((current) => ({ ...current, values: current.values.map((row) => (row.key === key ? { ...row, ...patch } : row)) }));
  }

  function addCitation(citation: CitationDraft) {
    const same = citations.some(
      (c) => c.pageId === citation.pageId && c.charStart === citation.charStart && c.charEnd === citation.charEnd,
    );
    if (!same) setCitations((current) => [...current, citation]);
    setPicking(false);
  }

  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
    if (confirmDiscard) setConfirmDiscard(false);
    else requestClose();
  }

  async function save() {
    setSubmitted(true);
    if (pending || hasErrors(errors) || categoryMissing) return;
    setPending(true);
    setServerError(null);
    try {
      const created = await createManualRequirement(companyId, tenderId, {
        title: draft.title.trim(),
        description: draft.description.trim() || null,
        category: draft.category,
        obligation: draft.obligation,
        modality: draft.modality,
        values: draft.values.map(reviewerValue),
        // Afegir-lo no vol dir haver-lo comprovat: aquí no hi ha cap drecera a «verificat».
        verification_status: "unreviewed",
        ...(note.trim() ? { note: note.trim() } : {}),
        citations: citations.map(citationPayload),
      });
      onCreated(created);
    } catch (caught) {
      const problem = actionFailure(caught);
      const fields =
        caught instanceof ApiError && problem.kind === "invalid"
          ? [...new Set(caught.fields.map((f) => FIELD_NAMES[f.field.split(".")[0]]).filter(Boolean))]
          : [];
      setServerError(fields.length ? `${problem.message} (${fields.join(", ")})` : problem.message);
    } finally {
      setPending(false);
    }
  }

  const show = submitted;

  return (
    <div ref={root} role="group" aria-labelledby={`${id}-title`} className="space-y-4 px-4 py-4" onKeyDown={onKeyDown}>
      <div>
        <p id={`${id}-title`} className="text-body font-semibold text-ink">
          Afegir un requisit a la revisió
        </p>
        <p className="text-[13px] leading-5 text-muted">
          Només per a la vostra empresa. Queda «Afegit manualment» i «Pendent de revisar»; el plec i l&apos;extracció no es
          modifiquen.
        </p>
      </div>

      <Field label="Títol" error={show ? errors.title : undefined} required>
        <Input value={draft.title} maxLength={LIMITS.title} onChange={(event) => update({ title: event.target.value })} />
      </Field>
      <Field label="Descripció" error={show ? errors.description : undefined}>
        <Textarea rows={3} value={draft.description} onChange={(event) => update({ description: event.target.value })} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Categoria" className="sm:col-span-2" error={show && categoryMissing ? "Tria una categoria." : undefined} required>
          <Select
            value={draft.category}
            placeholder="Tria una categoria"
            options={CATEGORY_OPTIONS}
            onChange={(event) => update({ category: event.target.value })}
          />
        </Field>
        <Field label="Obligació" hint="Què en fa el plec.">
          <Select value={draft.obligation} options={OBLIGATION_OPTIONS} onChange={(event) => update({ obligation: event.target.value })} />
        </Field>
        <Field label="Modalitat" hint="Com obliga.">
          <Select value={draft.modality} options={MODALITY_OPTIONS} onChange={(event) => update({ modality: event.target.value })} />
        </Field>
      </div>

      <fieldset className="space-y-2">
        <legend className={KICKER}>Valors</legend>
        {draft.values.length === 0 && <p className="text-[13px] text-muted">Sense valors estructurats.</p>}
        {draft.values.map((row, index) => (
          <ValueRow
            key={row.key}
            index={index}
            row={row}
            source={null}
            error={show ? errors.values[row.key] : undefined}
            onChange={(patch) => updateValue(row.key, patch)}
            onRemove={() => update({ values: draft.values.filter((value) => value.key !== row.key) })}
          />
        ))}
        {show && errors.general && <p className="text-[13px] text-danger">{errors.general}</p>}
        <Button size="sm" variant="ghost" onClick={() => update({ values: [...draft.values, newValue(draft, EMPTY_REQUIREMENT)] })}>
          <Plus size={14} strokeWidth={1.5} aria-hidden />
          Afegeix un valor
        </Button>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className={KICKER}>Evidència (opcional)</legend>
        {citations.length === 0 && !picking && (
          <p className="text-[13px] text-muted">Sense cita. Es pot afegir ara o més endavant.</p>
        )}
        {citations.map((citation, index) => (
          <div key={`${citation.pageId}-${citation.charStart}`} className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <CitationPreview draft={citation} />
            </div>
            <button
              type="button"
              aria-label={`Treu la cita ${index + 1}`}
              onClick={() => setCitations((current) => current.filter((c) => c !== citation))}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-muted hover:bg-subtle hover:text-ink"
            >
              <X size={14} strokeWidth={1.5} aria-hidden />
            </button>
          </div>
        ))}
        {picking ? (
          <EvidencePicker companyId={companyId} tenderId={tenderId} onChoose={addCitation} onCancel={() => setPicking(false)} />
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setPicking(true)}>
            <Plus size={14} strokeWidth={1.5} aria-hidden />
            Afegeix una cita
          </Button>
        )}
      </fieldset>

      <Field label="Nota (opcional)" hint="Queda a l'historial de revisió.">
        <Textarea rows={2} value={note} maxLength={LIMITS.note} onChange={(event) => setNote(event.target.value)} />
      </Field>

      {serverError && (
        <div role="alert" className="border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
          {serverError}
        </div>
      )}

      {confirmDiscard ? (
        <div role="alertdialog" aria-labelledby={`${id}-discard`} className="space-y-2 border border-border-strong px-3 py-2">
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
          <Button size="sm" variant="primary" loading={pending} disabled={pending} onClick={() => void save()}>
            Afegeix el requisit
          </Button>
          <Button size="sm" variant="ghost" disabled={pending} onClick={requestClose}>
            Cancel·la
          </Button>
        </div>
      )}
    </div>
  );
}
