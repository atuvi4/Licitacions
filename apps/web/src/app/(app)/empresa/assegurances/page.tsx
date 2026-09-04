"use client";

import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { ResourceTable } from "@/components/ResourceTable";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  Card,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import {
  formatDate,
  formatMoney,
  INSURANCE_TYPE_LABELS,
  optionsFrom,
  TEMPORAL_STATUS_LABELS,
  TEMPORAL_TONE,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Insurance } from "@/lib/types";

const EMPTY = {
  type: "civil_liability",
  provider: "",
  policy_number: "",
  coverage_description: "",
  coverage_amount: "",
  starts_at: "",
  expires_at: "",
  notes: "",
};

export default function InsurancesPage() {
  const path = useCompanyPath();
  const url = path("/insurances");
  const { data, loading, error, reload } = useResource<Insurance[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Insurance | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Insurance | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(insurance: Insurance) {
    reset();
    setForm({
      type: insurance.type,
      provider: insurance.provider ?? "",
      policy_number: insurance.policy_number ?? "",
      coverage_description: insurance.coverage_description ?? "",
      coverage_amount: insurance.coverage_amount ?? "",
      starts_at: insurance.starts_at ?? "",
      expires_at: insurance.expires_at ?? "",
      notes: insurance.notes ?? "",
    });
    setEditing(insurance);
    setOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      const payload = cleanPayload(form);
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

  return (
    <>
      <PageHeader
        title="Assegurances"
        description="Pòlisses vigents i imports de cobertura. Molts plecs exigeixen responsabilitat civil per un import mínim: aquí hi ha la dada i la pòlissa que la demostra."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nova pòlissa
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        <ResourceTable
          rows={data ?? []}
          loading={loading}
          rowKey={(insurance) => insurance.id}
          onEdit={openEdit}
          onDelete={setToDelete}
          evidence={{
            entityType: "company_insurance",
            defaultRelationship: "policy",
            defaultCategory: "insurance",
            description: "Pòlissa, condicions particulars o rebut que acrediten la cobertura.",
          }}
          empty={{
            title: "Cap pòlissa registrada",
            description:
              "Registra les assegurances de l'empresa amb el seu import de cobertura i adjunta-hi la pòlissa.",
            action: (
              <Button variant="primary" onClick={openCreate}>
                Afegeix una pòlissa
              </Button>
            ),
          }}
          columns={[
            {
              header: "Tipus",
              cell: (insurance) => (
                <>
                  <p className="font-medium">{INSURANCE_TYPE_LABELS[insurance.type]}</p>
                  {insurance.policy_number && (
                    <p className="mt-0.5 font-mono text-[12px] text-muted">
                      {insurance.policy_number}
                    </p>
                  )}
                </>
              ),
            },
            {
              header: "Companyia",
              cell: (insurance) => (
                <span className="text-muted">{insurance.provider ?? "—"}</span>
              ),
            },
            {
              header: "Cobertura",
              cell: (insurance) => (
                <>
                  <span className="tabular-nums">
                    {formatMoney(insurance.coverage_amount, insurance.currency)}
                  </span>
                  {insurance.coverage_description && (
                    <p className="mt-0.5 max-w-xs truncate text-[12px] text-muted">
                      {insurance.coverage_description}
                    </p>
                  )}
                </>
              ),
            },
            {
              header: "Vigència",
              cell: (insurance) => (
                <>
                  <span className="whitespace-nowrap tabular-nums text-muted">
                    {formatDate(insurance.starts_at)} – {formatDate(insurance.expires_at)}
                  </span>
                  <Badge tone={TEMPORAL_TONE[insurance.temporal_status]} className="ml-2">
                    {TEMPORAL_STATUS_LABELS[insurance.temporal_status]}
                  </Badge>
                </>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={open}
        title={editing ? "Edita la pòlissa" : "Nova pòlissa"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="insurance-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="insurance-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tipus" required>
              <Select
                value={form.type}
                options={optionsFrom(INSURANCE_TYPE_LABELS)}
                onChange={(event) => setForm({ ...form, type: event.target.value })}
              />
            </Field>
            <Field label="Companyia">
              <Input
                value={form.provider}
                onChange={(event) => setForm({ ...form, provider: event.target.value })}
              />
            </Field>
            <Field label="Número de pòlissa">
              <Input
                value={form.policy_number}
                onChange={(event) => setForm({ ...form, policy_number: event.target.value })}
              />
            </Field>
            <Field label="Import de cobertura (EUR)" error={fieldErrors.coverage_amount}>
              <Input
                type="number"
                min={0}
                step="0.01"
                value={form.coverage_amount}
                onChange={(event) => setForm({ ...form, coverage_amount: event.target.value })}
              />
            </Field>
            <Field label="Inici de vigència">
              <Input
                type="date"
                value={form.starts_at}
                onChange={(event) => setForm({ ...form, starts_at: event.target.value })}
              />
            </Field>
            <Field label="Fi de vigència" error={fieldErrors.expires_at}>
              <Input
                type="date"
                value={form.expires_at}
                onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
              />
            </Field>
          </div>
          <Field label="Descripció de la cobertura">
            <Textarea
              rows={2}
              value={form.coverage_description}
              onChange={(event) =>
                setForm({ ...form, coverage_description: event.target.value })
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
        title="Elimina la pòlissa"
        message={`Segur que vols eliminar aquesta pòlissa${toDelete?.policy_number ? ` (${toDelete.policy_number})` : ""}?`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
