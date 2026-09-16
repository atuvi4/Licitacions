"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EvidencePanel } from "@/components/EvidencePanel";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  CardHeader,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { CLIENT_TYPE_LABELS, optionsFrom, PROJECT_STATUS_LABELS } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Project, Service } from "@/lib/types";

type FormState = {
  name: string;
  client_name: string;
  client_type: string;
  sector: string;
  amount: string;
  start_date: string;
  end_date: string;
  location: string;
  status: string;
  reference_contact: string;
  description: string;
  notes: string;
};

function toForm(project: Project): FormState {
  return {
    name: project.name,
    client_name: project.client_name ?? "",
    client_type: project.client_type,
    sector: project.sector ?? "",
    amount: project.amount ?? "",
    start_date: project.start_date ?? "",
    end_date: project.end_date ?? "",
    location: project.location ?? "",
    status: project.status,
    reference_contact: project.reference_contact ?? "",
    description: project.description ?? "",
    notes: project.notes ?? "",
  };
}

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const path = useCompanyPath();
  const url = path(`/projects/${projectId}`);

  const { data, loading, error, reload, setData } = useResource<Project>(url);
  const services = useResource<Service[]>(path("/services"));
  const { saving, error: saveError, fieldErrors, run } = useMutation();

  const [form, setForm] = useState<FormState | null>(null);
  const [saved, setSaved] = useState(false);
  const [cpvCode, setCpvCode] = useState("");
  const [cpvDescription, setCpvDescription] = useState("");

  useEffect(() => {
    if (data) setForm(toForm(data));
  }, [data]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!form) return;
    const ok = await run(async () => {
      const updated = await api.patch<Project>(url, cleanPayload(form));
      setData(updated);
    });
    setSaved(ok);
  }

  async function addCpv(event: React.FormEvent) {
    event.preventDefault();
    if (!cpvCode.trim()) return;
    const ok = await run(async () => {
      const updated = await api.post<Project>(`${url}/cpvs`, {
        cpv_code: cpvCode.trim(),
        description: cpvDescription.trim() || null,
      });
      setData(updated);
    });
    if (ok) {
      setCpvCode("");
      setCpvDescription("");
    }
  }

  async function removeCpv(code: string) {
    await run(async () => {
      const updated = await api.delete<Project>(`${url}/cpvs/${code}`);
      setData(updated);
    });
  }

  async function toggleService(serviceId: string) {
    if (!data) return;
    const current = data.services.map((service) => service.id);
    const next = current.includes(serviceId)
      ? current.filter((id) => id !== serviceId)
      : [...current, serviceId];
    await run(async () => {
      const updated = await api.patch<Project>(url, { service_ids: next });
      setData(updated);
    });
  }

  if (error) {
    return (
      <>
        <PageHeader title="Projecte" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  if (loading || !data || !form) {
    return (
      <>
        <PageHeader title="Projecte" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }

  const linkedServiceIds = new Set(data.services.map((service) => service.id));

  return (
    <>
      <Link href="/experiencia" className="mb-3 inline-block text-[13px] text-muted hover:text-ink">
        ← Torna a Experiència
      </Link>

      <PageHeader
        title={data.name}
        description={[data.client_name, data.location].filter(Boolean).join(" · ") || undefined}
        actions={
          <>
            {saved && <span className="text-[13px] text-success">Canvis desats</span>}
            <Button type="submit" form="project-detail-form" variant="primary" loading={saving}>
              Desa els canvis
            </Button>
          </>
        }
      />

      {saveError && (
        <div className="mb-4">
          <ErrorNotice message={saveError} />
        </div>
      )}

      <div className="space-y-4">
        <Card>
          <CardHeader title="Dades del projecte" />
          <form
            id="project-detail-form"
            onSubmit={save}
            className="grid gap-4 px-5 py-5 sm:grid-cols-2"
          >
            <Field label="Nom" required error={fieldErrors.name} className="sm:col-span-2">
              <Input
                value={form.name}
                required
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, name: event.target.value });
                }}
              />
            </Field>
            <Field label="Client">
              <Input
                value={form.client_name}
                onChange={(event) => setForm({ ...form, client_name: event.target.value })}
              />
            </Field>
            <Field label="Tipus de client">
              <Select
                value={form.client_type}
                options={optionsFrom(CLIENT_TYPE_LABELS)}
                onChange={(event) => setForm({ ...form, client_type: event.target.value })}
              />
            </Field>
            <Field label="Import (EUR)" error={fieldErrors.amount}>
              <Input
                type="number"
                min={0}
                step="0.01"
                value={form.amount}
                onChange={(event) => setForm({ ...form, amount: event.target.value })}
              />
            </Field>
            <Field label="Estat">
              <Select
                value={form.status}
                options={optionsFrom(PROJECT_STATUS_LABELS)}
                onChange={(event) => setForm({ ...form, status: event.target.value })}
              />
            </Field>
            <Field label="Data d'inici">
              <Input
                type="date"
                value={form.start_date}
                onChange={(event) => setForm({ ...form, start_date: event.target.value })}
              />
            </Field>
            <Field label="Data de fi" error={fieldErrors.end_date}>
              <Input
                type="date"
                value={form.end_date}
                onChange={(event) => setForm({ ...form, end_date: event.target.value })}
              />
            </Field>
            <Field label="Sector">
              <Input
                value={form.sector}
                onChange={(event) => setForm({ ...form, sector: event.target.value })}
              />
            </Field>
            <Field label="Ubicació">
              <Input
                value={form.location}
                onChange={(event) => setForm({ ...form, location: event.target.value })}
              />
            </Field>
            <Field
              label="Contacte de referència"
              hint="Persona del client que pot confirmar l'execució."
              className="sm:col-span-2"
            >
              <Input
                value={form.reference_contact}
                onChange={(event) => setForm({ ...form, reference_contact: event.target.value })}
              />
            </Field>
            <Field label="Descripció" className="sm:col-span-2">
              <Textarea
                rows={4}
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </Field>
            <Field label="Notes internes" className="sm:col-span-2">
              <Textarea
                rows={2}
                value={form.notes}
                onChange={(event) => setForm({ ...form, notes: event.target.value })}
              />
            </Field>
          </form>
        </Card>

        <Card>
          <CardHeader
            title="Serveis executats"
            description="Quins dels serveis de l'empresa van intervenir en aquest projecte."
          />
          {services.loading ? (
            <LoadingRows rows={1} />
          ) : (
            <div className="flex flex-wrap gap-2 px-5 py-5">
              {(services.data ?? []).length === 0 ? (
                <p className="text-[13px] text-muted">
                  Encara no hi ha serveis definits.{" "}
                  <Link href="/empresa/serveis" className="text-brand hover:underline">
                    Crea&apos;n un
                  </Link>
                  .
                </p>
              ) : (
                (services.data ?? []).map((service) => (
                  <button
                    key={service.id}
                    type="button"
                    disabled={saving}
                    onClick={() => void toggleService(service.id)}
                    className={
                      linkedServiceIds.has(service.id)
                        ? "border border-brand bg-brand-soft px-3 py-1 text-[12px] font-medium text-brand-strong"
                        : "border border-border-strong px-3 py-1 text-[12px] text-muted hover:bg-subtle"
                    }
                  >
                    {service.name}
                  </button>
                ))
              )}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="CPV del projecte"
            description="Sota quins codis CPV es va executar. És el que permetrà demostrar experiència en un CPV concret."
          />
          <div className="px-5 py-5">
            {data.cpvs.length === 0 ? (
              <p className="mb-4 text-[13px] text-muted">
                Encara no hi ha cap CPV assignat a aquest projecte.
              </p>
            ) : (
              <div className="mb-4 flex flex-wrap gap-2">
                {data.cpvs.map((cpv) => (
                  <span
                    key={cpv.cpv_code}
                    className="inline-flex items-center gap-2 border border-border-strong bg-subtle px-3 py-1 text-[12px]"
                  >
                    <span className="font-mono font-medium text-ink">{cpv.cpv_code}</span>
                    <span className="text-muted">{cpv.description}</span>
                    <button
                      type="button"
                      aria-label={`Treu el CPV ${cpv.cpv_code}`}
                      disabled={saving}
                      onClick={() => void removeCpv(cpv.cpv_code)}
                      className="text-faint transition-colors hover:text-danger"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}

            <form onSubmit={addCpv} className="flex flex-wrap items-end gap-3">
              <Field label="Codi CPV" className="w-40">
                <Input
                  value={cpvCode}
                  placeholder="45314320"
                  onChange={(event) => setCpvCode(event.target.value)}
                />
              </Field>
              <Field label="Descripció" className="min-w-[220px] flex-1">
                <Input
                  value={cpvDescription}
                  placeholder="Només cal si el codi encara no és al catàleg"
                  onChange={(event) => setCpvDescription(event.target.value)}
                />
              </Field>
              <Button type="submit" variant="secondary" loading={saving} disabled={!cpvCode.trim()}>
                Afegeix
              </Button>
            </form>
          </div>
        </Card>

        <Card>
          <EvidencePanel
            entityType="company_project"
            entityId={projectId}
            defaultRelationship="certificate_of_good_execution"
            defaultCategory="project_evidence"
            description="Certificats de bona execució, contractes o actes de recepció que acrediten aquest projecte."
          />
        </Card>
      </div>
    </>
  );
}
