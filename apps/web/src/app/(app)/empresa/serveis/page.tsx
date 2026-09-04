"use client";

import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
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
import { optionsFrom, SPECIALIZATION_LABELS } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Service } from "@/lib/types";

const EMPTY = {
  name: "",
  category: "",
  specialization_level: "",
  description: "",
  notes: "",
  active: true,
};

type FormState = typeof EMPTY;

export default function ServicesPage() {
  const path = useCompanyPath();
  const url = path("/services");
  const { data, loading, error, reload } = useResource<Service[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [editing, setEditing] = useState<Service | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [toDelete, setToDelete] = useState<Service | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setCreating(true);
  }

  function openEdit(service: Service) {
    reset();
    setForm({
      name: service.name,
      category: service.category ?? "",
      specialization_level: service.specialization_level ?? "",
      description: service.description ?? "",
      notes: service.notes ?? "",
      active: service.active,
    });
    setEditing(service);
    setCreating(false);
  }

  function close() {
    setCreating(false);
    setEditing(null);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const payload = cleanPayload(form);
    const ok = await run(async () => {
      if (editing) {
        await api.patch(`${url}/${editing.id}`, payload);
      } else {
        await api.post(url, payload);
      }
      await reload();
    });
    if (ok) close();
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${url}/${toDelete.id}`);
      await reload();
    });
    if (ok) setToDelete(null);
  }

  const services = data ?? [];

  return (
    <>
      <PageHeader
        title="Serveis"
        description="Capacitats que l'empresa pot executar. Es faran servir per vincular projectes i, més endavant, per fer matching."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nou servei
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        {loading ? (
          <LoadingRows />
        ) : services.length === 0 ? (
          <EmptyState
            title="Encara no hi ha cap servei"
            description="Defineix els serveis que ofereix l'empresa: fibra òptica, cablejat, CCTV, manteniment, ciberseguretat… el que faci falta."
            action={
              <Button variant="primary" onClick={openCreate}>
                Afegeix el primer servei
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Servei</Th>
                <Th>Categoria</Th>
                <Th>Especialització</Th>
                <Th>Estat</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {services.map((service) => (
                <Tr key={service.id}>
                  <Td>
                    <p className="font-medium">{service.name}</p>
                    {service.description && (
                      <p className="mt-0.5 max-w-md truncate text-[12px] text-muted">
                        {service.description}
                      </p>
                    )}
                  </Td>
                  <Td className="text-muted">{service.category ?? "—"}</Td>
                  <Td className="text-muted">
                    {service.specialization_level
                      ? SPECIALIZATION_LABELS[service.specialization_level]
                      : "—"}
                  </Td>
                  <Td>
                    <Badge tone={service.active ? "success" : "neutral"}>
                      {service.active ? "Actiu" : "Inactiu"}
                    </Badge>
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(service)}>
                      Edita
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setToDelete(service)}>
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
        open={creating || editing !== null}
        title={editing ? "Edita el servei" : "Nou servei"}
        onClose={close}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="service-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="service-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom" required error={fieldErrors.name}>
            <Input
              value={form.name}
              required
              maxLength={160}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Fibra òptica"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Categoria" error={fieldErrors.category}>
              <Input
                value={form.category}
                onChange={(event) => setForm({ ...form, category: event.target.value })}
                placeholder="Infraestructura"
              />
            </Field>
            <Field label="Nivell d'especialització">
              <Select
                value={form.specialization_level}
                placeholder="Sense definir"
                options={optionsFrom(SPECIALIZATION_LABELS)}
                onChange={(event) =>
                  setForm({ ...form, specialization_level: event.target.value })
                }
              />
            </Field>
          </div>
          <Field label="Descripció">
            <Textarea
              rows={3}
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
            />
          </Field>
          <Field label="Notes internes">
            <Textarea
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
          <Checkbox
            label="Servei actiu"
            checked={form.active}
            onChange={(event) => setForm({ ...form, active: event.target.checked })}
          />
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina el servei"
        message={`Segur que vols eliminar «${toDelete?.name}»? També se n'eliminaran els enllaços a documents i quedarà desvinculat dels projectes.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
