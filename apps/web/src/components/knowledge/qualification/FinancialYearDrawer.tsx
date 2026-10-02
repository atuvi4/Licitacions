"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Field, Input, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import type { FinancialYear } from "@/lib/types";

import { amountText, dayOrNull, errorText, fieldError, parseAmount, textOrNull } from "./forms";
import { AsOfField, FormDrawer, Row } from "./shared";
import type { DrawerProps } from "./types";

/** Un exercici econòmic: any, xifra de negoci i moneda. Cap càlcul de solvència aquí. */
export function FinancialYearDrawer({ record, verified, canDelete, onClose, onSaved }: DrawerProps<FinancialYear>) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [year, setYear] = useState(record ? String(record.year) : "");
  const [revenue, setRevenue] = useState(amountText(record?.revenue));
  const [currency, setCurrency] = useState(record?.currency ?? "EUR");
  const [asOf, setAsOf] = useState(record?.as_of ?? "");
  const [notes, setNotes] = useState(record?.notes ?? "");
  const [local, setLocal] = useState<Record<string, string>>({});

  const parsedRevenue = parseAmount(revenue);

  function submit() {
    const errors: Record<string, string> = {};
    if (!/^\d{4}$/.test(year.trim())) errors.year = "Escriu l'any de l'exercici amb quatre xifres.";
    if (parsedRevenue === undefined) errors.revenue = "Import no vàlid. Escriu-lo sense signe i amb dos decimals com a màxim (p. ex. 150.000,00).";
    if (!/^[A-Za-z]{3}$/.test(currency.trim())) errors.currency = "La moneda és un codi de tres lletres (EUR).";
    setLocal(errors);
    if (Object.keys(errors).length > 0) return;
    const body = {
      year: Number(year),
      revenue: parsedRevenue,
      currency: currency.trim().toUpperCase(),
      as_of: dayOrNull(asOf),
      notes: textOrNull(notes),
    };
    void run(async () => {
      if (record) await api.patch(path(`/financial-years/${record.id}`), body);
      else await api.post(path("/financial-years"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/financial-years/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="financial-year-form"
      title={record ? `Edita l'exercici ${record.year}` : "Afegeix un exercici econòmic"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["year", "revenue", "currency", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Row>
        <Field label="Any de l'exercici" required error={local.year ?? fieldError(fieldErrors, "year")}>
          <Input inputMode="numeric" maxLength={4} placeholder="AAAA" value={year} onChange={(event) => setYear(event.target.value.replace(/\D/g, ""))} />
        </Field>
        <Field label="Moneda" error={local.currency ?? fieldError(fieldErrors, "currency")}>
          <Input maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} />
        </Field>
      </Row>
      <Field
        label="Xifra de negoci (volum anual de negoci)"
        hint={typeof parsedRevenue === "string" ? formatAmount(parsedRevenue, currency || "EUR") : "Tal com consta als comptes de l'exercici. Si encara no se sap, deixa-ho en blanc."}
        error={local.revenue ?? fieldError(fieldErrors, "revenue")}
      >
        <Input inputMode="decimal" placeholder="0,00" value={revenue} onChange={(event) => setRevenue(event.target.value)} />
      </Field>
      <AsOfField value={asOf} onChange={setAsOf} error={fieldError(fieldErrors, "as_of")} />
      <Field label="Notes">
        <Textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
    </FormDrawer>
  );
}
