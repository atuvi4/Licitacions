"use client";

import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { ResourceTable } from "@/components/ResourceTable";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  ErrorNotice,
  Field,
  Input,
  LoadErrorState,
  PageHeader,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import {
  formatDate,
  optionsFrom,
  REGISTRATION_TYPE_LABELS,
  TEMPORAL_STATUS_LABELS,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { TEMPORAL_LEVEL } from "@/lib/labels";
import type { Registration } from "@/lib/types";

const EMPTY = {
  name: "",
  type: "rolece",
  registration_number: "",
  issuer: "",
  issued_at: "",
  expires_at: "",
  notes: "",
};

export default function RegistrationsPage() {
  const path = useCompanyPath();
  const url = path("/registrations");
  const { data, loading, error, status, reload } = useResource<Registration[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Registration | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Registration | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(registration: Registration) {
    reset();
    setForm({
      name: registration.name,
      type: registration.type,
      registration_number: registration.registration_number ?? "",
      issuer: registration.issuer ?? "",
      issued_at: registration.issued_at ?? "",
      expires_at: registration.expires_at ?? "",
      notes: registration.notes ?? "",
    });
    setEditing(registration);
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
        title="Registres i classificacions"
        description="ROLECE, RELI, classificació empresarial i altres acreditacions administratives. Sense el document inscrit, el registre no és acreditable."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nou registre
          </Button>
        }
      />

      {error && data && <ErrorNotice message={error} onRetry={() => void reload()} />}

      {error && !data ? (
        <LoadErrorState what="els registres" message={error} status={status} onRetry={() => void reload()} />
      ) : (
        <Card>
          <ResourceTable
            rows={data ?? []}
            loading={loading}
            rowKey={(registration) => registration.id}
            onEdit={openEdit}
            onDelete={setToDelete}
            evidence={{
              entityType: "company_registration",
              defaultRelationship: "accreditation",
              defaultCategory: "registration",
              description:
                "Certificat d'inscripció, resolució o classificació que acredita aquest registre.",
            }}
            empty={{
              title: "Cap registre donat d'alta",
              description:
                "Registra la inscripció al ROLECE, al RELI o la classificació empresarial i adjunta'n el certificat.",
              action: (
                <Button variant="primary" onClick={openCreate}>
                  Afegeix un registre
                </Button>
              ),
            }}
            columns={[
              {
                header: "Registre",
                cell: (registration) => (
                  <>
                    <p className="font-medium">{registration.name}</p>
                    {registration.registration_number && (
                      <p className="mt-0.5 font-mono text-[12px] text-muted">
                        {registration.registration_number}
                      </p>
                    )}
                  </>
                ),
              },
              {
                header: "Tipus",
                cell: (registration) => (
                  <span className="text-muted">{REGISTRATION_TYPE_LABELS[registration.type]}</span>
                ),
              },
              {
                header: "Organisme",
                cell: (registration) => (
                  <span className="text-muted">{registration.issuer ?? "—"}</span>
                ),
                className: "text-muted",
              },
              {
                header: "Emissió",
                cell: (registration) => formatDate(registration.issued_at),
                className: "tabular-nums",
              },
              {
                header: "Vigència",
                cell: (registration) => (
                  <>
                    <span className="tabular-nums">{formatDate(registration.expires_at)}</span>
                    <StatusPill level={TEMPORAL_LEVEL[registration.temporal_status]} size="sm" className="ml-2">
                      {TEMPORAL_STATUS_LABELS[registration.temporal_status]}
                    </StatusPill>
                  </>
                ),
              },
            ]}
          />
        </Card>
      )}

      <Modal
        open={open}
        title={editing ? "Edita el registre" : "Nou registre"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="registration-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="registration-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nom" required error={fieldErrors.name}>
              <Input
                value={form.name}
                required
                placeholder="ROLECE — Inscripció"
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </Field>
            <Field label="Tipus">
              <Select
                value={form.type}
                options={optionsFrom(REGISTRATION_TYPE_LABELS)}
                onChange={(event) => setForm({ ...form, type: event.target.value })}
              />
            </Field>
            <Field label="Número d'inscripció">
              <Input
                value={form.registration_number}
                onChange={(event) =>
                  setForm({ ...form, registration_number: event.target.value })
                }
              />
            </Field>
            <Field label="Organisme">
              <Input
                value={form.issuer}
                onChange={(event) => setForm({ ...form, issuer: event.target.value })}
              />
            </Field>
            <Field label="Data d'emissió" error={fieldErrors.issued_at}>
              <Input
                type="date"
                value={form.issued_at}
                onChange={(event) => setForm({ ...form, issued_at: event.target.value })}
              />
            </Field>
            <Field
              label="Data de caducitat"
              hint="Deixa-ho buit si la inscripció no caduca."
              error={fieldErrors.expires_at}
            >
              <Input
                type="date"
                value={form.expires_at}
                onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
              />
            </Field>
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
        title="Elimina el registre"
        message={`Segur que vols eliminar «${toDelete?.name}»? Els documents es mantenen a la biblioteca però perdran aquest enllaç.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
