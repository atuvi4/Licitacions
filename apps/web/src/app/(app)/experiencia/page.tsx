"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LinkButton,
  LoadErrorState,
  PageHeader,
  PageSkeleton,
  Select,
  Textarea,
  type DataTableColumn,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import {
  CLIENT_TYPE_LABELS,
  formatDate,
  formatMoney,
  optionsFrom,
  PROJECT_STATUS_LABELS,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { Project, Service } from "@/lib/types";

const EMPTY = {
  name: "",
  client_name: "",
  client_type: "public",
  sector: "",
  amount: "",
  start_date: "",
  end_date: "",
  location: "",
  status: "completed",
  description: "",
};

export default function ExperiencePage() {
  const path = useCompanyPath();
  const url = path("/projects");
  const { data, loading, error, status, reload } = useResource<Project[]>(url);
  const services = useResource<Service[]>(path("/services"));
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [selectedServices, setSelectedServices] = useState<string[]>([]);
  const [toDelete, setToDelete] = useState<Project | null>(null);
  const [search, setSearch] = useState("");
  const [minAmount, setMinAmount] = useState("");

  const projects = useMemo(() => {
    const list = data ?? [];
    const min = Number(minAmount);
    return list.filter((project) => {
      const matchesSearch = search
        ? `${project.name} ${project.client_name ?? ""}`.toLowerCase().includes(search.toLowerCase())
        : true;
      const matchesAmount =
        minAmount && !Number.isNaN(min) ? Number(project.amount ?? 0) >= min : true;
      return matchesSearch && matchesAmount;
    });
  }, [data, search, minAmount]);

  const totalAccredited = (data ?? []).reduce(
    (accumulator, project) => accumulator + Number(project.amount ?? 0),
    0,
  );

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      await api.post(url, { ...cleanPayload(form), service_ids: selectedServices });
      await reload();
    });
    if (ok) {
      setOpen(false);
      setForm(EMPTY);
      setSelectedServices([]);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${url}/${toDelete.id}`);
      await reload();
    });
    if (ok) setToDelete(null);
  }

  function toggleService(id: string) {
    setSelectedServices((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  const columns: Array<DataTableColumn<Project>> = [
    {
      key: "project",
      header: "Projecte",
      cell: (project) => (
        <>
          <Link href={`/experiencia/${project.id}`} className="font-medium text-ink hover:text-brand">
            {project.name}
          </Link>
          <p className="mt-0.5 text-[12px] text-muted">
            {PROJECT_STATUS_LABELS[project.status]}
            {project.location && ` · ${project.location}`}
          </p>
        </>
      ),
    },
    {
      key: "client",
      header: "Client",
      cell: (project) => (
        <>
          <p className="text-ink">{project.client_name ?? "—"}</p>
          <p className="text-[12px] text-muted">{CLIENT_TYPE_LABELS[project.client_type]}</p>
        </>
      ),
    },
    { key: "amount", header: "Import", align: "right", width: "13%", cell: (project) => <span className="tabular-nums">{formatMoney(project.amount, project.currency)}</span> },
    {
      key: "period",
      header: "Període",
      hideBelow: "lg",
      cell: (project) => (
        <span className="whitespace-nowrap text-muted">
          {formatDate(project.start_date)} – {formatDate(project.end_date)}
        </span>
      ),
    },
    {
      key: "services",
      header: "Serveis",
      hideBelow: "lg",
      cell: (project) => (
        <div className="flex flex-wrap gap-1">
          {project.services.length === 0 ? (
            <span className="text-[12px] text-faint">Sense serveis</span>
          ) : (
            project.services.map((service) => (
              <span key={service.id} className="bg-subtle px-1.5 py-0.5 text-[12px] text-muted">
                {service.name}
              </span>
            ))
          )}
        </div>
      ),
    },
    {
      key: "actions",
      header: "Accions",
      align: "right",
      width: "16%",
      cell: (project) => (
        <div className="flex flex-wrap justify-end gap-1">
          <LinkButton href={`/experiencia/${project.id}`} className="min-h-11 md:min-h-0">
            Detall
          </LinkButton>
          <Button size="sm" variant="ghost" className="min-h-11 md:min-h-0" onClick={() => setToDelete(project)}>
            Elimina
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Experiència"
        description="Projectes executats. És la base per acreditar solvència tècnica: import, dates, serveis i evidència documental."
        actions={
          <Button
            variant="primary"
            onClick={() => {
              reset();
              setForm(EMPTY);
              setSelectedServices([]);
              setOpen(true);
            }}
          >
            Nou projecte
          </Button>
        }
      />

      {error && data && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">Projectes registrats</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{(data ?? []).length}</p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">Import acumulat</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {formatMoney(totalAccredited)}
          </p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
          <p className="text-[12px] text-muted">Projectes finalitzats</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {(data ?? []).filter((project) => project.status === "completed").length}
          </p>
        </div>
      </div>

      {error && !data ? (
        <LoadErrorState what="els projectes" message={error} status={status} onRetry={() => void reload()} />
      ) : loading && !data ? (
        <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-5 py-4">
          <Field label="Cerca" className="min-w-[220px] flex-1">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Nom del projecte o client"
            />
          </Field>
          <Field label="Import mínim" className="w-40">
            <Input
              type="number"
              min={0}
              value={minAmount}
              onChange={(event) => setMinAmount(event.target.value)}
              placeholder="150000"
            />
          </Field>
        </div>

          <PageSkeleton variant="table" columns={["Projecte", "Client", "Import", "Període", "Serveis", "Accions"]} />
        </Card>
      ) : (
        <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-5 py-4">
          <Field label="Cerca" className="min-w-[220px] flex-1">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Nom del projecte o client"
            />
          </Field>
          <Field label="Import mínim" className="w-40">
            <Input
              type="number"
              min={0}
              value={minAmount}
              onChange={(event) => setMinAmount(event.target.value)}
              placeholder="150000"
            />
          </Field>
        </div>

          <DataTable<Project>
            columns={columns}
            rows={projects}
            rowKey={(row) => row.id}
            stackBelow="md"
            emptyState={
        <EmptyState
          title={(data ?? []).length === 0 ? "Cap projecte registrat" : "Cap resultat"}
          description={
            (data ?? []).length === 0
              ? "Registra els projectes executats amb import, dates i serveis. Després podràs adjuntar-hi els certificats de bona execució."
              : "Prova de canviar els filtres de cerca."
          }
          action={
            (data ?? []).length === 0 ? (
              <Button
                variant="primary"
                size="md"
                onClick={() => {
                  reset();
                  setForm(EMPTY);
                  setSelectedServices([]);
                  setOpen(true);
                }}
              >
                Registra el primer projecte
              </Button>
            ) : (
              <Button
                variant="secondary"
                size="md"
                onClick={() => {
                  setSearch("");
                  setMinAmount("");
                }}
              >
                Treu els filtres
              </Button>
            )
          }
        />
            }
          />
        </Card>
      )}

      <Modal
        open={open}
        title="Nou projecte"
        description="Després de crear-lo podràs adjuntar-hi les evidències."
        size="lg"
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="project-form" type="submit" loading={saving}>
              Crea el projecte
            </Button>
          </>
        }
      >
        <form id="project-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom del projecte" required error={fieldErrors.name}>
            <Input
              value={form.name}
              required
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
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
          </div>

          <Field label="Serveis executats" hint="Marca els serveis que van intervenir en el projecte.">
            <div className="flex flex-wrap gap-2 border border-border p-3">
              {(services.data ?? []).length === 0 ? (
                <span className="text-[12px] text-faint">
                  Encara no hi ha serveis definits. Crea&apos;ls a Empresa → Serveis.
                </span>
              ) : (
                (services.data ?? []).map((service) => (
                  <button
                    key={service.id}
                    type="button"
                    onClick={() => toggleService(service.id)}
                    className={
                      selectedServices.includes(service.id)
                        ? "border border-brand bg-brand-soft px-3 py-1 text-[12px] font-medium text-brand-strong"
                        : "border border-border-strong px-3 py-1 text-[12px] text-muted hover:bg-subtle"
                    }
                  >
                    {service.name}
                  </button>
                ))
              )}
            </div>
          </Field>

          <Field label="Descripció">
            <Textarea
              rows={3}
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
            />
          </Field>
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina el projecte"
        message={`Segur que vols eliminar «${toDelete?.name}»? Els documents seguiran a la biblioteca, però perdran l'enllaç amb aquest projecte.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
