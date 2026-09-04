"use client";

import Link from "next/link";
import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LinkButton,
  LoadingRows,
  PageHeader,
  Select,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { AVAILABILITY_LABELS, optionsFrom } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Person } from "@/lib/types";

const EMPTY = {
  first_name: "",
  last_name: "",
  role: "",
  department: "",
  years_experience: "",
  availability_status: "unknown",
  professional_profile: "",
  notes: "",
};

const AVAILABILITY_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  available: "success",
  partially_available: "warning",
  unavailable: "danger",
  unknown: "neutral",
};

export default function TeamPage() {
  const path = useCompanyPath();
  const url = path("/people");
  const { data, loading, error, reload } = useResource<Person[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Person | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Person | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(person: Person) {
    reset();
    setForm({
      first_name: person.first_name,
      last_name: person.last_name ?? "",
      role: person.role ?? "",
      department: person.department ?? "",
      years_experience: person.years_experience?.toString() ?? "",
      availability_status: person.availability_status,
      professional_profile: person.professional_profile ?? "",
      notes: person.notes ?? "",
    });
    setEditing(person);
    setOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      const payload = {
        ...cleanPayload(form),
        years_experience: form.years_experience === "" ? null : Number(form.years_experience),
      };
      if (editing) {
        await api.patch(`${url}/${editing.id}`, payload);
      } else {
        await api.post(url, payload);
      }
      await reload();
    });
    if (ok) setOpen(false);
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${url}/${toDelete.id}`);
      await reload();
    });
    if (ok) setToDelete(null);
  }

  const people = data ?? [];

  return (
    <>
      <PageHeader
        title="Equip humà"
        description="Perfils professionals amb què s'acredita la solvència tècnica. Només informació professional: cap dada personal innecessària."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nova persona
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        {loading ? (
          <LoadingRows />
        ) : people.length === 0 ? (
          <EmptyState
            title="Cap persona registrada"
            description="Registra els perfils clau de l'equip i les seves titulacions. Molts plecs demanen adscriure personal amb una titulació concreta."
            action={
              <Button variant="primary" onClick={openCreate}>
                Afegeix una persona
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Persona</Th>
                <Th>Departament</Th>
                <Th>Experiència</Th>
                <Th>Disponibilitat</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {people.map((person) => (
                <Tr key={person.id}>
                  <Td>
                    <Link
                      href={`/empresa/equip/${person.id}`}
                      className="font-medium text-ink hover:text-brand"
                    >
                      {[person.first_name, person.last_name].filter(Boolean).join(" ")}
                    </Link>
                    {person.role && (
                      <p className="mt-0.5 text-[12px] text-muted">{person.role}</p>
                    )}
                  </Td>
                  <Td className="text-muted">{person.department ?? "—"}</Td>
                  <Td className="tabular-nums">
                    {person.years_experience !== null ? `${person.years_experience} anys` : "—"}
                  </Td>
                  <Td>
                    <Badge tone={AVAILABILITY_TONE[person.availability_status]}>
                      {AVAILABILITY_LABELS[person.availability_status]}
                    </Badge>
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <LinkButton href={`/empresa/equip/${person.id}`}>Titulacions</LinkButton>
                    <Button size="sm" variant="ghost" onClick={() => openEdit(person)}>
                      Edita
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setToDelete(person)}>
                      Elimina
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Modal
        open={open}
        title={editing ? "Edita la persona" : "Nova persona"}
        size="lg"
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="person-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="person-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nom" required error={fieldErrors.first_name}>
              <Input
                value={form.first_name}
                required
                onChange={(event) => setForm({ ...form, first_name: event.target.value })}
              />
            </Field>
            <Field label="Cognoms">
              <Input
                value={form.last_name}
                onChange={(event) => setForm({ ...form, last_name: event.target.value })}
              />
            </Field>
            <Field label="Càrrec">
              <Input
                value={form.role}
                placeholder="Cap de projectes"
                onChange={(event) => setForm({ ...form, role: event.target.value })}
              />
            </Field>
            <Field label="Departament">
              <Input
                value={form.department}
                onChange={(event) => setForm({ ...form, department: event.target.value })}
              />
            </Field>
            <Field label="Anys d'experiència" error={fieldErrors.years_experience}>
              <Input
                type="number"
                min={0}
                max={80}
                value={form.years_experience}
                onChange={(event) => setForm({ ...form, years_experience: event.target.value })}
              />
            </Field>
            <Field label="Disponibilitat">
              <Select
                value={form.availability_status}
                options={optionsFrom(AVAILABILITY_LABELS)}
                onChange={(event) =>
                  setForm({ ...form, availability_status: event.target.value })
                }
              />
            </Field>
          </div>
          <Field
            label="Perfil professional"
            hint="Resum reutilitzable a les memòries tècniques."
          >
            <Textarea
              rows={4}
              value={form.professional_profile}
              onChange={(event) =>
                setForm({ ...form, professional_profile: event.target.value })
              }
            />
          </Field>
          <Field label="Observacions">
            <Textarea
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina la persona"
        message={`Segur que vols eliminar ${toDelete?.first_name ?? ""} ${toDelete?.last_name ?? ""}? També se n'eliminaran les titulacions i els seus enllaços a documents.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
