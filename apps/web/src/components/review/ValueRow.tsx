"use client";

import { X } from "lucide-react";

import { Field, Input, Select } from "@/components/ui";
import { type DraftValue, VALUE_TYPE_OPTIONS, type ValueType, operatorKey, operatorOptions } from "@/lib/correction";

/**
 * Una fila de valor estructurat (operador, tipus, valor, unitat), compartida
 * per l'editor de correccions i pel formulari de requisit manual. La clau
 * interna no s'ensenya mai.
 */
export function ValueRow({
  index,
  row,
  sourceLabel,
  error,
  onChange,
  onRemove,
}: {
  index: number;
  row: DraftValue;
  /** D'on surt el valor, ja dit segons l'element («corregit pel revisor»…). */
  sourceLabel?: string | null;
  error?: string;
  onChange: (patch: Partial<DraftValue>) => void;
  onRemove: () => void;
}) {
  const name = `Valor ${index + 1}`;
  return (
    <div role="group" aria-label={name} className="space-y-2 rounded-control border border-border px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption text-muted">
          {name}
          {sourceLabel && ` · ${sourceLabel}`}
          {row.isNew && " · nou"}
        </p>
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Treu el ${name.toLowerCase()}`}
          className="flex h-7 w-7 items-center justify-center rounded-control text-muted hover:bg-subtle hover:text-ink"
        >
          <X size={14} strokeWidth={1.5} aria-hidden />
        </button>
      </div>
      <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2">
        <Field label="Operador">
          <Select
            value={operatorKey(row.operator)}
            options={operatorOptions(row.operator)}
            onChange={(event) => onChange({ operator: event.target.value || null })}
          />
        </Field>
        <Field label="Tipus">
          <Select value={row.type} options={VALUE_TYPE_OPTIONS} onChange={(event) => onChange({ type: event.target.value as ValueType })} />
        </Field>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-2">
        {row.type === "boolean" ? (
          <Field label="Valor">
            <Select
              value={row.bool ? "true" : "false"}
              options={[
                { value: "true", label: "Sí" },
                { value: "false", label: "No" },
              ]}
              onChange={(event) => onChange({ bool: event.target.value === "true" })}
            />
          </Field>
        ) : row.type === "none" ? (
          <p className="self-end pb-2 text-[13px] text-muted">Sense valor</p>
        ) : (
          <Field label="Valor">
            <Input
              value={row.text}
              inputMode={row.type === "number" ? "decimal" : undefined}
              aria-invalid={error ? true : undefined}
              onChange={(event) => onChange({ text: event.target.value })}
            />
          </Field>
        )}
        <Field label="Unitat">
          <Input value={row.unit} placeholder="ms, EUR, trucades…" onChange={(event) => onChange({ unit: event.target.value })} />
        </Field>
      </div>
      {error && <p className="text-[13px] text-danger">{error}</p>}
    </div>
  );
}
