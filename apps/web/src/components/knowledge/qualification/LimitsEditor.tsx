"use client";

import { Button, Input, Select } from "@/components/ui";
import { INSURANCE_LIMIT_KIND_LABELS, SELECTABLE_INSURANCE_LIMIT_KINDS, formatAmount } from "@/lib/format";
import type { InsuranceLimitKind } from "@/lib/types";

import { parseAmount } from "./forms";

export interface LimitRow {
  key: string;
  /** El límit que ja existeix a l'API; nou si no en té. */
  id?: string;
  kind: InsuranceLimitKind | "";
  amount: string;
  /** Les notes d'un límit existent (no s'editen aquí, però no es perden). */
  notes?: string | null;
}

let next = 0;
export function limitRow(kind: LimitRow["kind"] = "", amount = "", id?: string, notes?: string | null): LimitRow {
  next += 1;
  return { key: `limit-${next}`, id, kind, amount, notes };
}

export interface LimitErrors {
  rows: Record<string, { kind?: string; amount?: string }>;
  ok: boolean;
}

/** Cada límit diu de quin tipus és i quant cobreix; dos del mateix tipus no tenen sentit. */
export function checkLimits(rows: LimitRow[]): LimitErrors {
  const errors: LimitErrors["rows"] = {};
  const seen = new Set<string>();
  for (const row of rows) {
    const rowErrors: { kind?: string; amount?: string } = {};
    if (!row.kind) rowErrors.kind = "Tria quin límit és.";
    else if (seen.has(row.kind)) rowErrors.kind = "Aquest tipus de límit ja hi és.";
    else seen.add(row.kind);
    const amount = parseAmount(row.amount);
    if (amount === null) rowErrors.amount = "Indica l'import del límit.";
    else if (amount === undefined) rowErrors.amount = "Import no vàlid. Escriu-lo sense signe i amb dos decimals com a màxim.";
    if (rowErrors.kind || rowErrors.amount) errors[row.key] = rowErrors;
  }
  return { rows: errors, ok: Object.keys(errors).length === 0 };
}

interface Props {
  rows: LimitRow[];
  onChange: (rows: LimitRow[]) => void;
  errors: LimitErrors;
  showErrors: boolean;
  currency: string;
}

/** «Límits de la pòlissa»: Tipus de límit | Import. Cap límit ve donat per cap plec. */
export function LimitsEditor({ rows, onChange, errors, showErrors, currency }: Props) {
  const update = (key: string, patch: Partial<LimitRow>) => onChange(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-[13px] leading-5 font-medium text-ink">Límits de la pòlissa</legend>
      <p className="text-caption text-muted">Cada límit tal com consta a la pòlissa. La RC patronal per víctima és un límit diferent del límit general per víctima.</p>
      <ul className="flex flex-col gap-2">
        {rows.map((row, position) => {
          const rowErrors = showErrors ? errors.rows[row.key] : undefined;
          const parsed = parseAmount(row.amount);
          // Un límit antic d'un tipus que ja no es tria es veu igualment amb el seu nom.
          const kinds: string[] = [...SELECTABLE_INSURANCE_LIMIT_KINDS];
          if (row.kind && !kinds.includes(row.kind)) kinds.push(row.kind);
          return (
            <li key={row.key} className="grid grid-cols-1 gap-2 rounded-card border border-border p-2 sm:grid-cols-[1fr_1fr_auto] sm:border-0 sm:p-0" data-limit-row={position}>
              <label className="flex flex-col gap-1">
                <span className="text-caption text-muted">Tipus de límit</span>
                <Select
                  aria-label={`Tipus de límit de la fila ${position + 1}`}
                  aria-invalid={rowErrors?.kind ? true : undefined}
                  value={row.kind}
                  placeholder="Tria el tipus"
                  options={kinds.map((kind) => ({ value: kind, label: INSURANCE_LIMIT_KIND_LABELS[kind as InsuranceLimitKind] ?? kind }))}
                  onChange={(event) => update(row.key, { kind: event.target.value as LimitRow["kind"] })}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-caption text-muted">Import</span>
                <Input
                  inputMode="decimal"
                  aria-label={`Import del límit de la fila ${position + 1}`}
                  aria-invalid={rowErrors?.amount ? true : undefined}
                  value={row.amount}
                  placeholder="0,00"
                  onChange={(event) => update(row.key, { amount: event.target.value })}
                />
                {typeof parsed === "string" && !rowErrors?.amount && <span className="text-caption text-muted tabular-nums">{formatAmount(parsed, currency)}</span>}
              </label>
              <div className="flex justify-end sm:items-end">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="min-h-11 sm:min-h-0"
                  aria-label={`Treu el límit de la fila ${position + 1}`}
                  onClick={() => onChange(rows.filter((item) => item.key !== row.key))}
                >
                  Treu
                </Button>
              </div>
              {(rowErrors?.kind || rowErrors?.amount) && (
                <p role="alert" className="text-caption text-danger sm:col-span-3">
                  {[rowErrors.kind, rowErrors.amount].filter(Boolean).join(" ")}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {rows.length === 0 && <p className="text-caption text-muted">Encara no hi ha cap límit informat.</p>}
      <div>
        <Button type="button" size="sm" variant="secondary" onClick={() => onChange([...rows, limitRow()])}>
          Afegeix un límit
        </Button>
      </div>
    </fieldset>
  );
}
