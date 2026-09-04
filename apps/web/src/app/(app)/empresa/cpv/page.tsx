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
  Table,
  Td,
  Textarea,
  Th,
  Tr,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { CPVPreference } from "@/lib/types";

const EMPTY = {
  cpv_code: "",
  description: "",
  priority: "5",
  active: true,
  excluded: false,
  notes: "",
};

export default function CpvPage() {
  const path = useCompanyPath();
  const url = path("/cpv-preferences");
  const { data, loading, error, reload } = useResource<CPVPreference[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<CPVPreference | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<CPVPreference | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(cpv: CPVPreference) {
    reset();
    setForm({
      cpv_code: cpv.cpv_code,
      description: cpv.description ?? "",
      priority: String(cpv.priority),
      active: cpv.active,
      excluded: cpv.excluded,
      notes: cpv.notes ?? "",
    });
    setEditing(cpv);
    setOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      if (editing) {
        // El codi i la descripció pertanyen al catàleg: aquí només es canvia
        // com el tracta aquesta empresa.
        await api.patch(`${url}/${editing.id}`, {
          priority: Number(form.priority) || 0,
          active: form.active,
          excluded: form.excluded,
          notes: form.notes || null,
        });
      } else {
        await api.post(url, {
          ...cleanPayload(form),
          priority: Number(form.priority) || 0,
        });
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

  const cpvs = data ?? [];
  const actius = cpvs.filter((cpv) => cpv.active && !cpv.excluded).length;
  const exclosos = cpvs.filter((cpv) => cpv.excluded).length;

  return (
    <>
      <PageHeader
        title="CPV"
        description="Vocabulari comú de contractes públics. Determina quines licitacions són del vostre àmbit i quines voleu descartar d'entrada."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nou CPV
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">CPV registrats</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{cpvs.length}</p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">D&apos;interès actiu</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{actius}</p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">Exclosos</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{exclosos}</p>
        </div>
      </div>

      <Card>
        {loading ? (
          <LoadingRows />
        ) : cpvs.length === 0 ? (
          <EmptyState
            title="Cap CPV registrat"
            description="Afegeix els codis CPV amb què treballeu. El catàleg oficial europeu no està importat: els codis es donen d'alta a mesura que els feu servir."
            action={
              <Button variant="primary" onClick={openCreate}>
                Afegeix un CPV
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Codi</Th>
                <Th>Descripció</Th>
                <Th>Prioritat</Th>
                <Th>Estat</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {cpvs.map((cpv) => (
                <Tr key={cpv.id}>
                  <Td className="font-mono font-medium">{cpv.cpv_code}</Td>
                  <Td className="text-muted">
                    {cpv.description ?? "—"}
                    {cpv.notes && (
                      <p className="mt-0.5 text-[12px] text-faint">{cpv.notes}</p>
                    )}
                  </Td>
                  <Td className="tabular-nums">{cpv.priority}</Td>
                  <Td>
                    {cpv.excluded ? (
                      <Badge tone="danger">Exclòs</Badge>
                    ) : cpv.active ? (
                      <Badge tone="success">Actiu</Badge>
                    ) : (
                      <Badge>Inactiu</Badge>
                    )}
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(cpv)}>
                      Edita
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setToDelete(cpv)}>
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
        title={editing ? `CPV ${editing.cpv_code}` : "Nou CPV"}
        description={
          editing
            ? "El codi i la descripció són del catàleg compartit; aquí es defineix com el tracta aquesta empresa."
            : undefined
        }
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="cpv-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="cpv-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          {!editing && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Codi CPV"
                required
                hint="8 dígits, opcionalment amb dígit de control."
                error={fieldErrors.cpv_code}
              >
                <Input
                  value={form.cpv_code}
                  required
                  placeholder="45314320"
                  onChange={(event) => setForm({ ...form, cpv_code: event.target.value })}
                />
              </Field>
              <Field label="Descripció" hint="S'afegirà al catàleg si el codi és nou.">
                <Input
                  value={form.description}
                  placeholder="Instal·lació de cablejat informàtic"
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>
            </div>
          )}
          <Field label="Prioritat" hint="0 = baixa, 100 = màxima. Ordena l'interès dins l'empresa.">
            <Input
              type="number"
              min={0}
              max={100}
              value={form.priority}
              onChange={(event) => setForm({ ...form, priority: event.target.value })}
            />
          </Field>
          <div className="space-y-2 rounded-md border border-border p-3">
            <Checkbox
              label="Actiu (ara mateix ens interessa)"
              checked={form.active}
              onChange={(event) => setForm({ ...form, active: event.target.checked })}
            />
            <Checkbox
              label="Exclòs (no el volem mai, encara que aparegui)"
              checked={form.excluded}
              onChange={(event) => setForm({ ...form, excluded: event.target.checked })}
            />
            <p className="text-[12px] text-faint">
              Són coses diferents: un CPV inactiu es pot reactivar; un CPV exclòs és una decisió
              explícita de no presentar-s&apos;hi.
            </p>
          </div>
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
        title="Elimina el CPV"
        message={`Segur que vols treure el CPV ${toDelete?.cpv_code} de l'empresa? El codi es manté al catàleg.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
