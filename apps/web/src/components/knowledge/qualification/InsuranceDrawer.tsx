"use client";

import { useMemo, useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { INSURANCE_TYPE_LABELS, optionsFrom } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import type { Insurance, InsuranceType } from "@/lib/types";

import { amountText, dayOrNull, errorText, fieldError, parseAmount, textOrNull } from "./forms";
import { LimitsEditor, checkLimits, limitRow, type LimitRow } from "./LimitsEditor";
import { AsOfField, FormDrawer, Row } from "./shared";
import type { DrawerProps } from "./types";

/**
 * Una assegurança: tipus, asseguradora, vigència i els seus límits. Un sol
 * «Desa» envia la fitxa sencera (pòlissa + conjunt complet de límits) i el
 * servidor l'aplica en una sola transacció: o tot o res.
 */
export function InsuranceDrawer({ record, verified, canDelete, onClose, onSaved }: DrawerProps<Insurance>) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [type, setType] = useState<InsuranceType>(record?.type ?? "civil_liability");
  const [provider, setProvider] = useState(record?.provider ?? "");
  const [policy, setPolicy] = useState(record?.policy_number ?? "");
  const [starts, setStarts] = useState(record?.starts_at ?? "");
  const [expires, setExpires] = useState(record?.expires_at ?? "");
  const [rows, setRows] = useState<LimitRow[]>(
    (record?.limits ?? []).map((item) => limitRow(item.kind, amountText(item.amount), item.id, item.notes)),
  );
  const [asOf, setAsOf] = useState(record?.as_of ?? "");
  const [notes, setNotes] = useState(record?.notes ?? "");
  const [tried, setTried] = useState(false);
  const [local, setLocal] = useState<Record<string, string>>({});
  const currency = record?.currency ?? "EUR";
  const limitErrors = useMemo(() => checkLimits(rows), [rows]);

  function submit() {
    setTried(true);
    const errors: Record<string, string> = {};
    if (starts && expires && expires < starts) errors.expires_at = "La data de caducitat no pot ser anterior a la d'inici.";
    setLocal(errors);
    if (Object.keys(errors).length > 0 || !limitErrors.ok) return;
    const body = {
      type,
      provider: textOrNull(provider),
      policy_number: textOrNull(policy),
      starts_at: dayOrNull(starts),
      expires_at: dayOrNull(expires),
      as_of: dayOrNull(asOf),
      notes: textOrNull(notes),
      // El CONJUNT COMPLET: el que no hi és, es treu.
      limits: rows.map((row) => ({ kind: row.kind, amount: parseAmount(row.amount), currency, notes: row.notes ?? null })),
    };
    void run(async () => {
      if (record) await api.patch(path(`/insurances/${record.id}`), body);
      else await api.post(path("/insurances"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/insurances/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="insurance-form"
      title={record ? "Edita l'assegurança" : "Afegeix una assegurança"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["type", "provider", "policy_number", "starts_at", "expires_at", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Row>
        <Field label="Tipus d'assegurança" required error={fieldError(fieldErrors, "type")}>
          <Select value={type} options={optionsFrom(INSURANCE_TYPE_LABELS)} onChange={(event) => setType(event.target.value as InsuranceType)} />
        </Field>
        <Field label="Asseguradora" error={fieldError(fieldErrors, "provider")}>
          <Input value={provider} onChange={(event) => setProvider(event.target.value)} />
        </Field>
      </Row>
      <Field label="Número de pòlissa" hint="Opcional." error={fieldError(fieldErrors, "policy_number")}>
        <Input value={policy} onChange={(event) => setPolicy(event.target.value)} />
      </Field>
      <Row>
        <Field label="Vigent des de" error={fieldError(fieldErrors, "starts_at")}>
          <Input type="date" value={starts} onChange={(event) => setStarts(event.target.value)} />
        </Field>
        <Field label="Vigent fins al" error={local.expires_at ?? fieldError(fieldErrors, "expires_at")}>
          <Input type="date" value={expires} onChange={(event) => setExpires(event.target.value)} />
        </Field>
      </Row>
      <LimitsEditor rows={rows} onChange={setRows} errors={limitErrors} showErrors={tried} currency={currency} />
      <AsOfField value={asOf} onChange={setAsOf} error={fieldError(fieldErrors, "as_of")} />
      <Field label="Notes">
        <Textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
    </FormDrawer>
  );
}
