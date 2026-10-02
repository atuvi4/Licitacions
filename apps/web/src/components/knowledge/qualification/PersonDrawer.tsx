"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Checkbox, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import type { KnowledgeLifecycle, Vocabulary } from "@/lib/knowledge";
import type { Person } from "@/lib/types";

import { dayOrNull, errorText, fieldError, textOrNull } from "./forms";
import { profileOptions } from "./labels";
import { AsOfField, FormDrawer, MultiPicker, Row } from "./shared";
import type { DrawerProps } from "./types";

interface Props extends DrawerProps<Person> {
  vocabulary: Vocabulary | null;
}

/** «Actiu» és el cicle de vida: desmarcar-lo retira la dada; tornar-lo a marcar la torna a vigent. */
export function activeLifecycle(active: boolean, before: KnowledgeLifecycle | undefined): KnowledgeLifecycle | undefined {
  if (!active) return before === "retired" ? undefined : "retired";
  return before === "retired" ? "current" : undefined;
}

/** Una persona de l'equip: nom, càrrec, si és activa i quins perfils cobreix. Cap assignació a plecs. */
export function PersonDrawer({ record, vocabulary, verified, canDelete, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [first, setFirst] = useState(record?.first_name ?? "");
  const [last, setLast] = useState(record?.last_name ?? "");
  const [role, setRole] = useState(record?.role ?? "");
  const [active, setActive] = useState(record?.lifecycle !== "retired");
  const [profiles, setProfiles] = useState<string[]>(record?.profile_codes ?? []);
  const [asOf, setAsOf] = useState(record?.as_of ?? "");
  const [local, setLocal] = useState<Record<string, string>>({});

  function submit() {
    const errors: Record<string, string> = {};
    if (!first.trim()) errors.first_name = "Escriu el nom.";
    setLocal(errors);
    if (Object.keys(errors).length > 0) return;
    const lifecycle = record ? activeLifecycle(active, record.lifecycle) : active ? undefined : "retired";
    const body = {
      first_name: first.trim(),
      last_name: textOrNull(last),
      role: textOrNull(role),
      profile_codes: profiles.length > 0 ? profiles : null,
      as_of: dayOrNull(asOf),
      ...(lifecycle ? { lifecycle } : {}),
    };
    void run(async () => {
      if (record) await api.patch(path(`/people/${record.id}`), body);
      else await api.post(path("/people"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/people/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="person-form"
      title={record ? "Edita la persona" : "Afegeix una persona"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["first_name", "last_name", "role", "profile_codes", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Row>
        <Field label="Nom" required error={local.first_name ?? fieldError(fieldErrors, "first_name")}>
          <Input value={first} autoComplete="off" onChange={(event) => setFirst(event.target.value)} />
        </Field>
        <Field label="Cognoms" error={fieldError(fieldErrors, "last_name")}>
          <Input value={last} autoComplete="off" onChange={(event) => setLast(event.target.value)} />
        </Field>
      </Row>
      <Field label="Càrrec" error={fieldError(fieldErrors, "role")}>
        <Input value={role} onChange={(event) => setRole(event.target.value)} />
      </Field>
      <Checkbox label="Activa a l'empresa" checked={active} onChange={(event) => setActive(event.target.checked)} />
      <MultiPicker
        id="person-profiles"
        label="Perfils professionals"
        hint="Els perfils del vocabulari comú que aquesta persona pot cobrir."
        error={fieldError(fieldErrors, "profile_codes")}
        values={profiles}
        options={profileOptions(vocabulary)}
        onChange={setProfiles}
        placeholder="Cerca un perfil..."
        emptyText="Cap perfil triat."
      />
      <AsOfField value={asOf} onChange={setAsOf} error={fieldError(fieldErrors, "as_of")} />
    </FormDrawer>
  );
}
