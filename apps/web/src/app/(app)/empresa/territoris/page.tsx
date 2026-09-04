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
import { optionsFrom, TERRITORY_TYPE_LABELS } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Territory } from "@/lib/types";

const EMPTY = {
  name: "",
  territory_type: "province",
  code: "",
  priority: "5",
  willing_to_work: true,
  notes: "",
};

export default function TerritoriesPage() {
  const path = useCompanyPath();
  const url = path("/territories");
  const { data, loading, error, reload } = useResource<Territory[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Territory | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Territory | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(territory: Territory) {
    reset();
    setForm({
      name: territory.name,
      territory_type: territory.territory_type,
      code: territory.code ?? "",
      priority: String(territory.priority),
      willing_to_work: territory.willing_to_work,
      notes: territory.notes ?? "",
    });
    setEditing(territory);
    setOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const payload = cleanPayload({ ...form, priority: Number(form.priority) || 0 });
    const ok = await run(async () => {
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

  const territories = data ?? [];

  return (
    <>
      <PageHeader
        title="Àmbit geogràfic"
        description="On treballa (o vol treballar) l'empresa. La prioritat servirà per ordenar oportunitats en fases futures."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nou territori
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        {loading ? (
          <LoadingRows />
        ) : territories.length === 0 ? (
          <EmptyState
            title="Cap territori definit"
            description="Indica municipis, comarques, províncies o comunitats autònomes on l'empresa pot executar contractes."
            action={
              <Button variant="primary" onClick={openCreate}>
                Afegeix un territori
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Territori</Th>
                <Th>Tipus</Th>
                <Th>Codi</Th>
                <Th>Prioritat</Th>
                <Th>Disponibilitat</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {territories.map((territory) => (
                <Tr key={territory.id}>
                  <Td className="font-medium">{territory.name}</Td>
                  <Td className="text-muted">{TERRITORY_TYPE_LABELS[territory.territory_type]}</Td>
                  <Td className="font-mono text-[12px] text-muted">{territory.code ?? "—"}</Td>
                  <Td className="tabular-nums">{territory.priority}</Td>
                  <Td>
                    <Badge tone={territory.willing_to_work ? "success" : "neutral"}>
                      {territory.willing_to_work ? "Hi treballem" : "Descartat"}
                    </Badge>
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(territory)}>
                      Edita
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setToDelete(territory)}>
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
        title={editing ? "Edita el territori" : "Nou territori"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="territory-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="territory-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nom" required error={fieldErrors.name}>
              <Input
                value={form.name}
                required
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Girona"
              />
            </Field>
            <Field label="Tipus" required>
              <Select
                value={form.territory_type}
                options={optionsFrom(TERRITORY_TYPE_LABELS)}
                onChange={(event) => setForm({ ...form, territory_type: event.target.value })}
              />
            </Field>
            <Field label="Codi" hint="INE, NUTS o el que utilitzeu internament.">
              <Input
                value={form.code}
                onChange={(event) => setForm({ ...form, code: event.target.value })}
                placeholder="ES512"
              />
            </Field>
            <Field label="Prioritat" hint="0 = baixa, 100 = màxima.">
              <Input
                type="number"
                min={0}
                max={100}
                value={form.priority}
                onChange={(event) => setForm({ ...form, priority: event.target.value })}
              />
            </Field>
          </div>
          <Field label="Notes">
            <Textarea
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
          <Checkbox
            label="Hi volem treballar"
            checked={form.willing_to_work}
            onChange={(event) => setForm({ ...form, willing_to_work: event.target.checked })}
          />
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina el territori"
        message={`Segur que vols eliminar «${toDelete?.name}»?`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
