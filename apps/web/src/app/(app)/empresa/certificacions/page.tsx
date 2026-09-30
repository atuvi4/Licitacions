"use client";

import { useState } from "react";

import { StatePills } from "@/components/knowledge/StatePills";
import { ResourceTable } from "@/components/ResourceTable";
import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  ErrorNotice,
  Field,
  Input,
  LinkButton,
  LoadErrorState,
  PageHeader,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { formatDate, TEMPORAL_STATUS_LABELS } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import {
  indexSnapshot,
  levelLabel,
  recordState,
  standardLabel,
  type KnowledgeSnapshot,
  type Vocabulary,
} from "@/lib/knowledge";
import { TEMPORAL_LEVEL } from "@/lib/labels";
import type { Certification } from "@/lib/types";

const EMPTY = {
  name: "",
  issuer: "",
  certificate_number: "",
  scope: "",
  issued_at: "",
  expires_at: "",
  notes: "",
  standard_code: "",
  level: "",
};

export default function CertificationsPage() {
  const path = useCompanyPath();
  const url = path("/certifications");
  const { data, loading, error, status, reload: reloadRows } = useResource<Certification[]>(url);
  // L'estat d'acreditació surt de la fotografia de coneixement, no de la fila:
  // una certificació només és «Verificada» si té evidència enllaçada.
  const snapshot = useResource<KnowledgeSnapshot>(path("/knowledge/snapshot"));
  const vocabulary = useResource<Vocabulary>(path("/knowledge/vocabulary"));
  const index = indexSnapshot(snapshot.data);

  async function reload() {
    void snapshot.reload();
    return reloadRows();
  }
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Certification | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Certification | null>(null);
  const standard = vocabulary.data?.standards.find((item) => item.code === form.standard_code);

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
      notes: certification.notes ?? "",
      standard_code: certification.standard_code ?? "",
      level: certification.level ?? "",
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
          <>
            <LinkButton href="/empresa/coneixement" variant="secondary" size="md">
              Abast i verificació
            </LinkButton>
            <Button variant="primary" onClick={openCreate}>
              Nova certificació
            </Button>
          </>
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
                header: "Acreditació",
                cell: (certification) =>
                  snapshot.data ? (
                    <StatePills value={recordState(certification.id, certification.lifecycle ?? "current", index)} />
                  ) : (
                    <span className="text-muted">—</span>
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
          {editing && index.claims.get(editing.id)?.source === "current_verified" && (
            <p role="note" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
              Aquesta certificació està verificada. Si en canvies el contingut tornarà a «pendent de verificar»:
              l&apos;evidència es conserva, però caldrà tornar-la a verificar.
            </p>
          )}
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
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Norma" hint="Permet comparar-la amb el que demana un plec.">
              <Select
                value={form.standard_code}
                placeholder="Sense norma del vocabulari"
                options={(vocabulary.data?.standards ?? []).map((item) => ({
                  value: item.code,
                  label: standardLabel(item.code, vocabulary.data),
                }))}
                onChange={(event) => setForm({ ...form, standard_code: event.target.value, level: "" })}
              />
            </Field>
            {standard?.levels && (
              <Field label="Nivell" error={fieldErrors.level}>
                <Select
                  value={form.level}
                  placeholder="Sense nivell"
                  options={standard.levels.map((level) => ({ value: level, label: levelLabel(level) }))}
                  onChange={(event) => setForm({ ...form, level: event.target.value })}
                />
              </Field>
            )}
          </div>
          <p className="text-caption text-muted">
            Una certificació es verifica amb evidència: adjunta&apos;n el certificat i verifica-la des de{" "}
            <span className="font-medium text-ink">Coneixement de l&apos;empresa</span>, on també s&apos;estructura
            l&apos;abast.
          </p>
          <Field label="Abast (nota)" hint="Text lliure. No es fa servir per deduir què cobreix el certificat.">
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
