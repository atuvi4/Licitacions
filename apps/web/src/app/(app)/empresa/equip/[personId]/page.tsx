"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { ResourceTable } from "@/components/ResourceTable";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import {
  AVAILABILITY_LABELS,
  formatDate,
  optionsFrom,
  QUALIFICATION_TYPE_LABELS,
  TEMPORAL_STATUS_LABELS,
  TEMPORAL_TONE,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Person, Qualification } from "@/lib/types";

const EMPTY = {
  name: "",
  type: "degree",
  issuer: "",
  credential_number: "",
  issued_at: "",
  expires_at: "",
  notes: "",
};

export default function PersonDetailPage() {
  const params = useParams<{ personId: string }>();
  const personId = params.personId;
  const path = useCompanyPath();

  const person = useResource<Person>(path(`/people/${personId}`));
  const qualificationsUrl = path(`/people/${personId}/qualifications`);
  const qualifications = useResource<Qualification[]>(qualificationsUrl);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Qualification | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Qualification | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setOpen(true);
  }

  function openEdit(qualification: Qualification) {
    reset();
    setForm({
      name: qualification.name,
      type: qualification.type,
      issuer: qualification.issuer ?? "",
      credential_number: qualification.credential_number ?? "",
      issued_at: qualification.issued_at ?? "",
      expires_at: qualification.expires_at ?? "",
      notes: qualification.notes ?? "",
    });
    setEditing(qualification);
    setOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      const payload = cleanPayload(form);
      if (editing) {
        await api.patch(`${qualificationsUrl}/${editing.id}`, payload);
      } else {
        await api.post(qualificationsUrl, payload);
      }
      await qualifications.reload();
    });
    if (ok) setOpen(false);
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${qualificationsUrl}/${toDelete.id}`);
      await qualifications.reload();
    });
    if (ok) setToDelete(null);
  }

  if (person.error) {
    return (
      <>
        <PageHeader title="Persona" />
        <ErrorNotice message={person.error} onRetry={() => void person.reload()} />
      </>
    );
  }

  if (person.loading || !person.data) {
    return (
      <>
        <PageHeader title="Persona" />
        <Skeleton className="h-48 w-full" />
      </>
    );
  }

  const data = person.data;
  const fullName = [data.first_name, data.last_name].filter(Boolean).join(" ");

  return (
    <>
      <Link href="/empresa/equip" className="mb-3 inline-block text-[13px] text-muted hover:text-ink">
        ← Torna a Equip
      </Link>

      <PageHeader
        title={fullName}
        description={[data.role, data.department].filter(Boolean).join(" · ") || undefined}
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nova titulació
          </Button>
        }
      />

      <div className="space-y-4">
        <Card>
          <CardHeader title="Perfil professional" />
          <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
            <div>
              <p className="text-[12px] text-muted">Experiència</p>
              <p className="mt-0.5 text-sm tabular-nums text-ink">
                {data.years_experience !== null ? `${data.years_experience} anys` : "—"}
              </p>
            </div>
            <div>
              <p className="text-[12px] text-muted">Disponibilitat</p>
              <p className="mt-0.5">
                <Badge>{AVAILABILITY_LABELS[data.availability_status]}</Badge>
              </p>
            </div>
            <div>
              <p className="text-[12px] text-muted">Titulacions</p>
              <p className="mt-0.5 text-sm tabular-nums text-ink">
                {(qualifications.data ?? []).length}
              </p>
            </div>
            {data.professional_profile && (
              <p className="whitespace-pre-line text-sm leading-relaxed text-ink sm:col-span-3">
                {data.professional_profile}
              </p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Titulacions i certificacions professionals"
            description="Amb el títol o certificat adjunt, aquesta persona es pot adscriure a una licitació que l'exigeixi."
          />
          {qualifications.error && (
            <div className="p-4">
              <ErrorNotice message={qualifications.error} />
            </div>
          )}
          <ResourceTable
            rows={qualifications.data ?? []}
            loading={qualifications.loading}
            rowKey={(qualification) => qualification.id}
            onEdit={openEdit}
            onDelete={setToDelete}
            evidence={{
              entityType: "person_qualification",
              defaultRelationship: "diploma",
              defaultCategory: "cv",
              description: "Títol, diploma o certificat que acredita aquesta titulació.",
            }}
            empty={{
              title: "Cap titulació registrada",
              description:
                "Afegeix titulacions acadèmiques, certificacions professionals, formació o habilitacions.",
              action: (
                <Button variant="primary" onClick={openCreate}>
                  Afegeix una titulació
                </Button>
              ),
            }}
            columns={[
              {
                header: "Titulació",
                cell: (qualification) => (
                  <>
                    <p className="font-medium">{qualification.name}</p>
                    {qualification.credential_number && (
                      <p className="mt-0.5 font-mono text-[12px] text-muted">
                        {qualification.credential_number}
                      </p>
                    )}
                  </>
                ),
              },
              {
                header: "Tipus",
                cell: (qualification) => (
                  <Badge tone="brand">{QUALIFICATION_TYPE_LABELS[qualification.type]}</Badge>
                ),
              },
              {
                header: "Organisme",
                cell: (qualification) => (
                  <span className="text-muted">{qualification.issuer ?? "—"}</span>
                ),
              },
              {
                header: "Emissió",
                cell: (qualification) => formatDate(qualification.issued_at),
                className: "tabular-nums",
              },
              {
                header: "Vigència",
                cell: (qualification) => (
                  <>
                    <span className="tabular-nums">{formatDate(qualification.expires_at)}</span>
                    <Badge tone={TEMPORAL_TONE[qualification.temporal_status]} className="ml-2">
                      {TEMPORAL_STATUS_LABELS[qualification.temporal_status]}
                    </Badge>
                  </>
                ),
              },
            ]}
          />
        </Card>
      </div>

      <Modal
        open={open}
        title={editing ? "Edita la titulació" : "Nova titulació"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="qualification-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="qualification-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom" required error={fieldErrors.name}>
            <Input
              value={form.name}
              required
              placeholder="Enginyeria de Telecomunicacions"
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tipus">
              <Select
                value={form.type}
                options={optionsFrom(QUALIFICATION_TYPE_LABELS)}
                onChange={(event) => setForm({ ...form, type: event.target.value })}
              />
            </Field>
            <Field label="Organisme emissor">
              <Input
                value={form.issuer}
                onChange={(event) => setForm({ ...form, issuer: event.target.value })}
              />
            </Field>
            <Field label="Número">
              <Input
                value={form.credential_number}
                onChange={(event) =>
                  setForm({ ...form, credential_number: event.target.value })
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
              hint="Les habilitacions solen caducar; els títols no."
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
        title="Elimina la titulació"
        message={`Segur que vols eliminar «${toDelete?.name}»?`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
