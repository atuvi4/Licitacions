"use client";

import { Fragment, useState } from "react";

import { EvidencePanel } from "@/components/EvidencePanel";
import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Select,
  StatusPill,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
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
  const { data, loading, error, reload } = useResource<Certification[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Certification | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Certification | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

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

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        {loading ? (
          <LoadingRows />
        ) : certifications.length === 0 ? (
          <EmptyState
            title="Cap certificació registrada"
            description="Afegeix les certificacions de l'empresa i adjunta'n el certificat com a evidència."
            action={
              <Button variant="primary" onClick={openCreate}>
                Afegeix una certificació
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Certificació</Th>
                <Th>Emissor</Th>
                <Th>Caducitat</Th>
                <Th>Vigència</Th>
                <Th>Verificació</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {certifications.map((certification) => (
                <Fragment key={certification.id}>
                  <Tr>
                    <Td>
                      <p className="font-medium">{certification.name}</p>
                      {certification.certificate_number && (
                        <p className="mt-0.5 font-mono text-[12px] text-muted">
                          {certification.certificate_number}
                        </p>
                      )}
                    </Td>
                    <Td className="text-muted">{certification.issuer ?? "—"}</Td>
                    <Td className="tabular-nums">{formatDate(certification.expires_at)}</Td>
                    <Td>
                      <StatusPill level={TEMPORAL_LEVEL[certification.temporal_status]} size="sm">
                        {TEMPORAL_STATUS_LABELS[certification.temporal_status]}
                      </StatusPill>
                    </Td>
                    <Td>
                      <StatusPill level={VERIFICATION_LEVEL[certification.verification_status]} size="sm">
                        {VERIFICATION_STATUS_LABELS[certification.verification_status]}
                      </StatusPill>
                    </Td>
                    <Td className="text-right whitespace-nowrap">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setExpanded(expanded === certification.id ? null : certification.id)
                        }
                      >
                        {expanded === certification.id ? "Amaga" : "Evidències"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openEdit(certification)}>
                        Edita
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setToDelete(certification)}>
                        Elimina
                      </Button>
                    </Td>
                  </Tr>
                  {expanded === certification.id && (
                    <tr>
                      <td colSpan={6} className="border-b border-border bg-canvas/60 p-0">
                        <EvidencePanel
                          entityType="company_certification"
                          entityId={certification.id}
                          defaultRelationship="certificate"
                          defaultCategory="certification"
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

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
