"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Field, Input, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import { FACT_LABELS, NUMERIC_FACT_UNITS, type OperatingFactRecord } from "@/lib/knowledge";

import { dayOrNull, errorText, fieldError, parseCount, textOrNull } from "./forms";
import { AsOfField, FormDrawer } from "./shared";
import type { DrawerProps } from "./types";

interface Props extends DrawerProps<OperatingFactRecord> {
  /** La clau del fet quantitatiu (`ipv4_assignable_count`). */
  factKey: string;
}

/** Un recurs quantitatiu de l'empresa: un nombre enter, mai negatiu (p. ex. IPv4 assignables). */
export function NumericFactDrawer({ factKey, record, verified, canDelete, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const label = FACT_LABELS[factKey] ?? factKey;
  const unit = NUMERIC_FACT_UNITS[factKey];
  const [count, setCount] = useState(record?.value_number != null ? String(Number(record.value_number)) : "");
  const [detail, setDetail] = useState(record?.detail ?? "");
  const [asOf, setAsOf] = useState("");
  const [local, setLocal] = useState<string | undefined>();

  function submit() {
    const value = parseCount(count);
    if (value === null || value === undefined) {
      setLocal("Escriu un nombre enter, sense decimals ni signe (0 o més).");
      return;
    }
    setLocal(undefined);
    const body: Record<string, unknown> = { value_number: value, detail: textOrNull(detail) };
    const day = dayOrNull(asOf);
    if (day) body.as_of = day;
    void run(async () => {
      if (record) await api.patch(path(`/knowledge/records/operating_fact/${record.id}`), body);
      else await api.post(path("/knowledge/records/operating_fact"), { key: factKey, value: true, ...body });
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/knowledge/records/operating_fact/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="numeric-fact-form"
      title={record ? `Edita: ${label}` : `Informa: ${label}`}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["value_number", "detail", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Field label={unit ? `${label} (${unit})` : label} required hint="Un nombre enter, 0 o més." error={local ?? fieldError(fieldErrors, "value_number")}>
        <Input inputMode="numeric" value={count} onChange={(event) => setCount(event.target.value)} />
      </Field>
      <Field label="Detall" hint="Opcional. Per exemple, de quins blocs o rangs es tracta." error={fieldError(fieldErrors, "detail")}>
        <Textarea rows={2} value={detail} onChange={(event) => setDetail(event.target.value)} />
      </Field>
      <AsOfField
        value={asOf}
        onChange={setAsOf}
        error={fieldError(fieldErrors, "as_of")}
        hint={
          record
            ? `Ara consta el ${formatDay(record.as_of)}. Deixa-ho en blanc per no canviar-la.`
            : "Opcional. Si no indiques cap data, Tuvi considerarà la data d'alta."
        }
      />
    </FormDrawer>
  );
}
