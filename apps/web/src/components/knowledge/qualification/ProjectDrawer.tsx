"use client";

import { useMemo, useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { PROJECT_STATUS_LABELS, formatAmount, optionsFrom } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import { serviceLineLabel, type Vocabulary } from "@/lib/knowledge";
import type { Project, ProjectStatus } from "@/lib/types";

import { AnnualAmountsEditor, annualRow } from "./AnnualAmountsEditor";
import { ProjectCpvEditor } from "./ProjectCpvEditor";
import { amountText, checkAnnualAmounts, dayOrNull, errorText, fieldError, parseAmount, textOrNull, type AnnualRow } from "./forms";
import { AsOfField, FormDrawer, MultiPicker, Row } from "./shared";
import type { DrawerProps } from "./types";

interface Props extends DrawerProps<Project> {
  vocabulary: Vocabulary | null;
  /** Afegir i treure CPV (l'API ho refusa igualment a qui només consulta). */
  canEdit?: boolean;
}

/**
 * Una referència d'execució: client, objecte, dates, estat, import total, les
 * línies de servei que acredita i els imports executats any per any. Les línies
 * són del vocabulari: una referència històrica no depèn dels serveis d'avui.
 */
export function ProjectDrawer({ record, verified, canDelete, onClose, onSaved, vocabulary, canEdit = true }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [name, setName] = useState(record?.name ?? "");
  const [client, setClient] = useState(record?.client_name ?? "");
  const [description, setDescription] = useState(record?.description ?? "");
  const [start, setStart] = useState(record?.start_date ?? "");
  const [end, setEnd] = useState(record?.end_date ?? "");
  const [status, setStatus] = useState<ProjectStatus>(record?.status ?? "completed");
  const [amount, setAmount] = useState(amountText(record?.amount));
  const [currency] = useState(record?.currency ?? "EUR");
  const [lines, setLines] = useState<string[]>(record?.service_line_codes ?? []);
  const [rows, setRows] = useState<AnnualRow[]>(
    (record?.annual_amounts ?? []).map((item) => annualRow(String(item.year), amountText(item.amount))),
  );
  const [asOf, setAsOf] = useState(record?.as_of ?? "");
  const [tried, setTried] = useState(false);
  const [local, setLocal] = useState<Record<string, string>>({});

  const parsedAmount = parseAmount(amount);
  const check = useMemo(
    () => checkAnnualAmounts(rows, { startDate: start || null, endDate: end || null, total: typeof parsedAmount === "string" ? parsedAmount : null }),
    [rows, start, end, parsedAmount],
  );
  const lineOptions = (vocabulary?.service_lines ?? []).map((line) => ({ value: line.code, label: serviceLineLabel(line.code, vocabulary) }));

  function submit() {
    setTried(true);
    const errors: Record<string, string> = {};
    if (name.trim().length < 2) errors.name = "Escriu el nom o l'objecte del contracte (mínim dos caràcters).";
    if (parsedAmount === undefined) errors.amount = "Import no vàlid. Escriu-lo sense signe i amb dos decimals com a màxim.";
    if (start && end && end < start) errors.end_date = "La data de fi no pot ser anterior a la d'inici.";
    setLocal(errors);
    if (Object.keys(errors).length > 0 || !check.value) return;
    const body = {
      name: name.trim(),
      client_name: textOrNull(client),
      description: textOrNull(description),
      start_date: dayOrNull(start),
      end_date: dayOrNull(end),
      status,
      amount: parsedAmount,
      service_line_codes: lines,
      annual_amounts: check.value,
      as_of: dayOrNull(asOf),
    };
    void run(async () => {
      if (record) await api.patch(path(`/projects/${record.id}`), body);
      else await api.post(path("/projects"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/projects/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="project-form"
      title={record ? "Edita la referència d'execució" : "Afegeix una referència d'execució"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["name", "client_name", "description", "start_date", "end_date", "status", "amount", "service_line_codes", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Field label="Nom o objecte del contracte" required error={local.name ?? fieldError(fieldErrors, "name")}>
        <Input value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Client" error={fieldError(fieldErrors, "client_name")}>
        <Input value={client} onChange={(event) => setClient(event.target.value)} />
      </Field>
      <Field label="Descripció" error={fieldError(fieldErrors, "description")}>
        <Textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      <Row>
        <Field label="Data d'inici" error={fieldError(fieldErrors, "start_date")}>
          <Input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
        </Field>
        <Field label="Data de fi" error={local.end_date ?? fieldError(fieldErrors, "end_date")}>
          <Input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
        </Field>
      </Row>
      <Row>
        <Field label="Estat" error={fieldError(fieldErrors, "status")}>
          <Select value={status} options={optionsFrom(PROJECT_STATUS_LABELS)} onChange={(event) => setStatus(event.target.value as ProjectStatus)} />
        </Field>
        <Field
          label="Import total del contracte"
          hint={typeof parsedAmount === "string" ? formatAmount(parsedAmount, currency) : "Opcional."}
          error={local.amount ?? fieldError(fieldErrors, "amount")}
        >
          <Input inputMode="decimal" placeholder="0,00" value={amount} onChange={(event) => setAmount(event.target.value)} />
        </Field>
      </Row>
      <MultiPicker
        id="project-lines"
        label="Línies de servei que acredita"
        hint="Les del contracte tal com es va executar, encara que avui l'empresa ja no les ofereixi."
        error={fieldError(fieldErrors, "service_line_codes")}
        values={lines}
        options={lineOptions}
        onChange={setLines}
        placeholder="Cerca una línia de servei..."
        emptyText="Cap línia triada."
      />
      <AnnualAmountsEditor rows={rows} onChange={setRows} check={check} showErrors={tried} currency={currency} />
      {record ? (
        <ProjectCpvEditor projectId={record.id} initial={record.cpvs ?? []} canEdit={canEdit} onChanged={onSaved} />
      ) : (
        <p className="text-caption text-muted">Els CPV del projecte s&apos;hi poden afegir un cop desat.</p>
      )}
      <AsOfField value={asOf} onChange={setAsOf} error={fieldError(fieldErrors, "as_of")} />
    </FormDrawer>
  );
}
