"use client";

import { useState } from "react";

import { ResourceTable } from "@/components/ResourceTable";
import { ConfirmDialog, Modal } from "@/components/Modal";
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
  TEMPORAL_STATUS_LABELS,
  VERIFICATION_STATUS_LABELS,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { TEMPORAL_LEVEL, VERIFICATION_LEVEL } from "@/lib/labels";
import type { Certification } from "@/lib/types";

const EMPTY = {
  name: "",
  issuer: "",
  certificate_number: "",
  scope: "",
  issued_at: "",
  expires_at: "",
  verification_status: "pending_verification",
  notes: "",
};

export default function CertificationsPage() {
  const path = useCompanyPath();
  const url = path("/certifications");
  const { data, loading, error, status, reload } = useResource<Certification[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Certification | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Certification | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(certification: Certification) {
    reset();
    setForm({
      name: certification.name,
      issuer: certification.issuer ?? "",
      certificate_number: certification.certificate_number ?? "",
      scope: certification.scope ?? "",
      issued_at: certification.issued_at ?? "",
      expires_at: certification.expires_at ?? "",
      verification_status: certification.verification_status,
      notes: certification.notes ?? "",
    });
    setEditing(certification);
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

  const certifications = data ?? [];

  return (
    <>
      <PageHeader
        title="Certificacions"
        description="ISO, acreditacions i certificats. L'estat de vigència es calcula sempre a partir de la data de caducitat."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nova certificació
          </Button>
        }
      />

      {error && data && <ErrorNotice message={error} onRetry={() => void reload()} />}

      {error && !data ? (
        <LoadErrorState what="les certificacions" message={error} status={status} onRetry={() => void reload()} />
      ) : (
        <Card>
          <ResourceTable
            rows={certifications}
            loading={loading}
            rowKey={(certification) => certification.id}
            onEdit={openEdit}
            onDelete={setToDelete}
            evidence={{
              entityType: "company_certification",
              defaultRelationship: "certificate",
              defaultCategory: "certification",
              description: "El certificat emès per l'entitat acreditadora.",
            }}
            empty={{
              title: "Cap certificació registrada",
              description: "Afegeix les certificacions de l'empresa i adjunta'n el certificat com a evidència.",
              action: (
                <Button variant="primary" size="md" onClick={openCreate}>
                  Afegeix una certificació
                </Button>
              ),
            }}
            columns={[
              {
                header: "Certificació",
                cell: (certification) => (
                  <>
                    <p className="font-medium">{certification.name}</p>
                    {certification.certificate_number && (
                      <p className="mt-0.5 font-mono text-[12px] text-muted">{certification.certificate_number}</p>
                    )}
                  </>
                ),
              },
              { header: "Emissor", hideBelow: "lg", cell: (certification) => <span className="text-muted">{certification.issuer ?? "—"}</span> },
              { header: "Caducitat", hideBelow: "lg", cell: (certification) => <span className="tabular-nums">{formatDate(certification.expires_at)}</span> },
              {
                header: "Vigència",
                cell: (certification) => (
                  <StatusPill level={TEMPORAL_LEVEL[certification.temporal_status]} size="sm">
                    {TEMPORAL_STATUS_LABELS[certification.temporal_status]}
                  </StatusPill>
                ),
              },
              {
                header: "Verificació",
                cell: (certification) => (
                  <StatusPill level={VERIFICATION_LEVEL[certification.verification_status]} size="sm">
                    {VERIFICATION_STATUS_LABELS[certification.verification_status]}
                  </StatusPill>
                ),
              },
            ]}
          />
        </Card>
      )}

      <Modal
        open={open}
        title={editing ? "Edita la certificació" : "Nova certificació"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="certification-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="certification-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom" required error={fieldErrors.name}>
            <Input
              value={form.name}
              required
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="ISO 9001:2015"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Entitat emissora">
              <Input
                value={form.issuer}
                onChange={(event) => setForm({ ...form, issuer: event.target.value })}
              />
            </Field>
            <Field label="Número de certificat">
              <Input
                value={form.certificate_number}
                onChange={(event) =>
                  setForm({ ...form, certificate_number: event.target.value })
                }
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
              hint="L'estat vigent / caduca aviat / caducat es dedueix d'aquí."
              error={fieldErrors.expires_at}
            >
              <Input
                type="date"
                value={form.expires_at}
                onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
              />
            </Field>
          </div>
          <Field label="Estat de verificació">
            <Select
              value={form.verification_status}
              options={[
                { value: "pending_verification", label: "Pendent de verificar" },
                { value: "verified", label: "Verificat" },
              ]}
              onChange={(event) =>
                setForm({ ...form, verification_status: event.target.value })
              }
            />
          </Field>
          <Field label="Abast">
            <Textarea
              rows={2}
              value={form.scope}
              onChange={(event) => setForm({ ...form, scope: event.target.value })}
            />
          </Field>
          <Field label="Notes">
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
        title="Elimina la certificació"
        message={`Segur que vols eliminar «${toDelete?.name}»? Els documents seguiran a la biblioteca, però perdran aquest enllaç.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
