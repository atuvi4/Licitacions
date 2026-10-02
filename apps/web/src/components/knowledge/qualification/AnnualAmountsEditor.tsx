"use client";

import { Button, Input } from "@/components/ui";
import { formatAmount } from "@/lib/format";

import { parseAmount, type AnnualCheck, type AnnualRow } from "./forms";

let next = 0;
/** Una fila nova, buida: l'any no s'endevina. */
export function annualRow(year = "", amount = ""): AnnualRow {
  next += 1;
  return { key: `annual-${next}`, year, amount };
}

interface Props {
  rows: AnnualRow[];
  onChange: (rows: AnnualRow[]) => void;
  check: AnnualCheck;
  /** S'ha intentat desar: ja es poden ensenyar els errors. */
  showErrors: boolean;
  currency: string;
}

/**
 * «Imports executats per exercici»: Any | Import executat, amb afegir i treure
 * files. Les regles són les de l'API; aquí es diuen abans d'enviar.
 */
export function AnnualAmountsEditor({ rows, onChange, check, showErrors, currency }: Props) {
  const update = (key: string, patch: Partial<AnnualRow>) => onChange(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  return (
    <fieldset className="flex flex-col gap-2" aria-describedby="annual-help">
      <legend className="text-[13px] leading-5 font-medium text-ink">Imports executats per exercici</legend>
      <p id="annual-help" className="text-caption text-muted">
        Indica l&apos;import realment executat en cada exercici. No és l&apos;import ofert ni necessàriament l&apos;import total del contracte.
      </p>
      {rows.length > 0 && (
        <div className="hidden grid-cols-[7rem_1fr_auto] gap-2 text-caption font-medium text-muted sm:grid" aria-hidden="true">
          <span>Any</span>
          <span>Import executat</span>
          <span className="w-20" />
        </div>
      )}
      <ul className="flex flex-col gap-2">
        {rows.map((row, position) => {
          const errors = showErrors ? check.rows[row.key] : undefined;
          const parsed = parseAmount(row.amount);
          return (
            <li
              key={row.key}
              className="grid grid-cols-[6rem_1fr] gap-2 rounded-card border border-border p-2 sm:grid-cols-[7rem_1fr_auto] sm:border-0 sm:p-0"
              data-annual-row={position}
            >
              <label className="flex flex-col gap-1">
                <span className="text-caption text-muted sm:sr-only">Any</span>
                <Input
                  inputMode="numeric"
                  aria-label={`Any de la fila ${position + 1}`}
                  aria-invalid={errors?.year ? true : undefined}
                  value={row.year}
                  maxLength={4}
                  placeholder="AAAA"
                  onChange={(event) => update(row.key, { year: event.target.value.replace(/\D/g, "") })}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-caption text-muted sm:sr-only">Import executat</span>
                <Input
                  inputMode="decimal"
                  aria-label={`Import executat de la fila ${position + 1}`}
                  aria-invalid={errors?.amount ? true : undefined}
                  value={row.amount}
                  placeholder="0,00"
                  onChange={(event) => update(row.key, { amount: event.target.value })}
                />
                {typeof parsed === "string" && !errors?.amount && (
                  <span className="text-caption text-muted tabular-nums">{formatAmount(parsed, currency)}</span>
                )}
              </label>
              <div className="col-span-2 flex justify-end sm:col-span-1 sm:items-start">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="min-h-11 sm:min-h-0"
                  aria-label={`Treu la fila ${position + 1}`}
                  onClick={() => onChange(rows.filter((item) => item.key !== row.key))}
                >
                  Treu
                </Button>
              </div>
              {(errors?.year || errors?.amount) && (
                <p role="alert" className="col-span-2 text-caption text-danger sm:col-span-3">
                  {[errors.year, errors.amount].filter(Boolean).join(" ")}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {rows.length === 0 && (
        <p className="text-caption text-muted">
          Encara no hi ha cap import per exercici. Sense desglossament, l&apos;import d&apos;un projecte de diversos anys no s&apos;atribueix a cap exercici.
        </p>
      )}
      {showErrors && check.total && (
        <p role="alert" className="text-caption text-danger">
          {check.total}
        </p>
      )}
      <div>
        <Button type="button" size="sm" variant="secondary" onClick={() => onChange([...rows, annualRow()])}>
          Afegeix un exercici
        </Button>
      </div>
    </fieldset>
  );
}
