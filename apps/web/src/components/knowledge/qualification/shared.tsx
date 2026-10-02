"use client";

import { useState, type FormEvent, type ReactNode } from "react";

import { Button, Combobox, Drawer, ErrorNotice, Field, Input, StatusPill } from "@/components/ui";

/** Les accions de cada fila, prou grans per al dit en mòbil. */
export const ACTION = "min-h-11 md:min-h-0";

interface FormDrawerProps {
  id: string;
  title: string;
  description?: string;
  editing: boolean;
  /** Verificada segons la fotografia: canviar-la la torna a «pendent de verificar». */
  verified: boolean;
  canDelete: boolean;
  saving: boolean;
  error: string | null;
  onSubmit: () => void;
  onDelete?: () => void;
  onClose: () => void;
  children: ReactNode;
}

/**
 * El calaix d'alta i edició de «Qualificació i mitjans»: el mateix Drawer de
 * tota l'app, amb el formulari, l'avís de dada verificada i el peu (eliminar
 * amb confirmació, només qui pot).
 */
export function FormDrawer({ id, title, description, editing, verified, canDelete, saving, error, onSubmit, onDelete, onClose, children }: FormDrawerProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }
  return (
    <Drawer
      open
      wide
      title={title}
      description={
        description ??
        (editing ? undefined : "Quedarà com a declarat i pendent de verificar. Es verifica després, amb el document que ho acredita enllaçat.")
      }
      onClose={onClose}
      footer={
        <div className="flex w-full flex-wrap items-center justify-end gap-2">
          {editing && canDelete && onDelete && (
            <Button type="button" variant="ghost" className="mr-auto" disabled={saving} onClick={() => (confirmDelete ? onDelete() : setConfirmDelete(true))}>
              {confirmDelete ? "Segur? Elimina-ho definitivament" : "Elimina"}
            </Button>
          )}
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>
            Cancel·la
          </Button>
          <Button variant="primary" type="submit" form={id} loading={saving}>
            {editing ? "Desa" : "Declara"}
          </Button>
        </div>
      }
    >
      <form id={id} onSubmit={submit} noValidate className="space-y-4 px-4 py-4">
        {error && <ErrorNotice message={error} />}
        {editing && verified && (
          <p role="note" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
            Aquesta dada està verificada. Si en canvies el contingut tornarà a «pendent de verificar»: l&apos;evidència es conserva, però caldrà tornar-la a verificar.
          </p>
        )}
        {children}
      </form>
    </Drawer>
  );
}

/** «Data de vigència de la informació»: opcional i mai omplerta per defecte. */
export function AsOfField({ value, onChange, error, hint }: { value: string; onChange: (value: string) => void; error?: string; hint?: string }) {
  return (
    <Field
      label="Data de vigència de la informació"
      hint={hint ?? "Opcional. De quan és aquesta informació. Si no ho saps, deixa-ho en blanc."}
      error={error}
    >
      <Input type="date" value={value} onChange={(event) => onChange(event.target.value)} />
    </Field>
  );
}

/** Dues columnes a partir de tauleta; una en mòbil. */
export function Row({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>;
}

interface MultiPickerProps {
  label: string;
  hint?: string;
  error?: string;
  values: string[];
  options: Array<{ value: string; label: string }>;
  onChange: (values: string[]) => void;
  placeholder: string;
  /** Text de la llista quan no n'hi ha cap de triat. */
  emptyText: string;
  id?: string;
}

/**
 * Tria múltiple amb el Combobox cercable: s'hi escriu, es tria i la tria queda
 * com una etiqueta amb el seu botó per treure-la.
 */
export function MultiPicker({ label, hint, error, values, options, onChange, placeholder, emptyText, id }: MultiPickerProps) {
  const labelOf = (value: string) => options.find((option) => option.value === value)?.label ?? value;
  const free = options.filter((option) => !values.includes(option.value));
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="mb-1.5 text-[13px] leading-5 font-medium text-ink">{label}</legend>
      <label className="flex flex-col">
        <span className="sr-only">{`${label}: afegeix`}</span>
        {/* La clau torna a muntar el camp buit després de cada tria. */}
        <Combobox
          key={values.join("|")}
          id={id}
          value=""
          options={free}
          placeholder={placeholder}
          onChange={(value) => {
            if (value && !values.includes(value)) onChange([...values, value]);
          }}
          emptyText={free.length === 0 ? "Ja estan totes triades" : undefined}
        />
      </label>
      {values.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={`${label}: triats`}>
          {values.map((value) => (
            <li key={value}>
              <span className="inline-flex items-center gap-1 rounded-pill border border-border-strong py-px pr-0.5 pl-3 text-[12.5px] leading-[1.6] text-ink sm:pr-1 sm:pl-2">
                {labelOf(value)}
                <button
                  type="button"
                  className="grid h-11 w-11 place-items-center rounded-pill text-muted hover:bg-subtle hover:text-ink sm:h-6 sm:w-6"
                  aria-label={`Treu ${labelOf(value)}`}
                  onClick={() => onChange(values.filter((item) => item !== value))}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-caption text-muted">{emptyText}</p>
      )}
      {hint && !error && <span className="text-caption text-muted">{hint}</span>}
      {error && <span className="text-caption text-danger">{error}</span>}
    </fieldset>
  );
}

/** Etiquetes petites per a la llista (línies, perfils, límits). */
export function Chips({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-caption text-muted">
      {label}
      {items.map((item) => (
        <StatusPill key={item} level="neutral" size="sm" outline>
          {item}
        </StatusPill>
      ))}
    </p>
  );
}
