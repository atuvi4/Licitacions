"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Combobox, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { REGISTRATION_TYPE_LABELS, optionsFrom } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import { LIFECYCLE_LABELS, serviceLineLabel, type KnowledgeLifecycle, type Vocabulary } from "@/lib/knowledge";
import type { Registration, RegistrationType } from "@/lib/types";

import { dayOrNull, errorText, fieldError, textOrNull } from "./forms";
import { AsOfField, FormDrawer, MultiPicker, Row } from "./shared";
import type { DrawerProps } from "./types";

interface Props extends DrawerProps<Registration> {
  vocabulary: Vocabulary | null;
}

/**
 * Un registre o inscripció: tipus, emissor, identificador, estat i les línies
 * de servei que inscriu. L'emissor s'escriu: no se'n proposa cap.
 */
export function RegistrationDrawer({ record, vocabulary, verified, canDelete, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [name, setName] = useState(record?.name ?? "");
  const [type, setType] = useState<RegistrationType | "">(record?.type ?? "");
  const [issuer, setIssuer] = useState(record?.issuer ?? "");
  const [number, setNumber] = useState(record?.registration_number ?? "");
  const [issued, setIssued] = useState(record?.issued_at ?? "");
  const [expires, setExpires] = useState(record?.expires_at ?? "");
  const [lifecycle, setLifecycle] = useState<KnowledgeLifecycle>(record?.lifecycle ?? "current");
  const [lines, setLines] = useState<string[]>(record?.scope_service_lines ?? []);
  const [asOf, setAsOf] = useState(record?.as_of ?? "");
  const [notes, setNotes] = useState(record?.notes ?? "");
  const [local, setLocal] = useState<Record<string, string>>({});

  function submit() {
    const errors: Record<string, string> = {};
    if (name.trim().length < 2) errors.name = "Escriu el nom del registre (mínim dos caràcters).";
    if (!type) errors.type = "Tria el tipus de registre.";
    if (issued && expires && expires < issued) errors.expires_at = "La data de caducitat no pot ser anterior a la d'emissió.";
    setLocal(errors);
    if (Object.keys(errors).length > 0) return;
    const body = {
      name: name.trim(),
      type,
      issuer: textOrNull(issuer),
      registration_number: textOrNull(number),
      issued_at: dayOrNull(issued),
      expires_at: dayOrNull(expires),
      lifecycle,
      // Sense cap línia triada, l'abast queda «sense estructurar» (mai una llista buida).
      scope_service_lines: lines.length > 0 ? lines : null,
      as_of: dayOrNull(asOf),
      notes: textOrNull(notes),
    };
    void run(async () => {
      if (record) await api.patch(path(`/registrations/${record.id}`), body);
      else await api.post(path("/registrations"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/registrations/${record.id}`));
      onSaved();
      onClose();
    });
  }

  const lineOptions = (vocabulary?.service_lines ?? []).map((line) => ({ value: line.code, label: serviceLineLabel(line.code, vocabulary) }));

  return (
    <FormDrawer
      id="registration-form"
      title={record ? "Edita el registre" : "Afegeix un registre"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["name", "type", "issuer", "registration_number", "issued_at", "expires_at", "lifecycle", "scope_service_lines", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Field label="Nom del registre" required error={local.name ?? fieldError(fieldErrors, "name")}>
        <Input value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Row>
        <Field label="Tipus de registre" required error={local.type ?? fieldError(fieldErrors, "type")}>
          <Combobox
            id="registration-type"
            value={type}
            options={optionsFrom(REGISTRATION_TYPE_LABELS)}
            placeholder="Cerca el tipus..."
            onChange={(value) => setType(value as RegistrationType)}
          />
        </Field>
        <Field label="Emissor" hint="L'organisme que el gestiona, tal com consta al certificat." error={fieldError(fieldErrors, "issuer")}>
          <Input value={issuer} onChange={(event) => setIssuer(event.target.value)} />
        </Field>
      </Row>
      <Row>
        <Field label="Identificador o número d'inscripció" error={fieldError(fieldErrors, "registration_number")}>
          <Input value={number} onChange={(event) => setNumber(event.target.value)} />
        </Field>
        <Field label="Estat" error={fieldError(fieldErrors, "lifecycle")}>
          <Select value={lifecycle} options={optionsFrom(LIFECYCLE_LABELS)} onChange={(event) => setLifecycle(event.target.value as KnowledgeLifecycle)} />
        </Field>
      </Row>
      <Row>
        <Field label="Data d'inscripció" error={fieldError(fieldErrors, "issued_at")}>
          <Input type="date" value={issued} onChange={(event) => setIssued(event.target.value)} />
        </Field>
        <Field label="Vàlid fins al" hint="En blanc si no caduca o no consta." error={local.expires_at ?? fieldError(fieldErrors, "expires_at")}>
          <Input type="date" value={expires} onChange={(event) => setExpires(event.target.value)} />
        </Field>
      </Row>
      <MultiPicker
        id="registration-lines"
        label="Abast: línies de servei inscrites"
        hint="Si no en tries cap, l'abast queda pendent d'estructurar."
        error={fieldError(fieldErrors, "scope_service_lines")}
        values={lines}
        options={lineOptions}
        onChange={setLines}
        placeholder="Cerca una línia de servei..."
        emptyText="Cap línia triada: abast pendent d'estructurar."
      />
      <AsOfField value={asOf} onChange={setAsOf} error={fieldError(fieldErrors, "as_of")} />
      <Field label="Notes">
        <Textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
    </FormDrawer>
  );
}
