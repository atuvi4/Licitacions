"use client";

import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import { CoverageEditor } from "@/components/knowledge/CoverageEditor";
import {
  Button,
  Card,
  Combobox,
  Checkbox,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadErrorState,
  PageHeader,
  PageSkeleton,
  Select,
  StatusPill,
  Textarea,
  type DataTableColumn,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { optionsFrom, SPECIALIZATION_LABELS } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import {
  DELIVERY_MODE_LABELS,
  NEUTRAL_COVERAGE,
  cleanCoverage,
  coverageSummary,
  sameCoverage,
  serviceLineLabel,
  type Coverage,
  type Vocabulary,
} from "@/lib/knowledge";
import { activeLevel } from "@/lib/labels";
import type { Service } from "@/lib/types";

const EMPTY = {
  name: "",
  category: "",
  specialization_level: "",
  description: "",
  notes: "",
  active: true,
  service_line_code: "",
  delivery_mode: "",
};

type FormState = typeof EMPTY;

export default function ServicesPage() {
  const path = useCompanyPath();
  const url = path("/services");
  const { data, loading, error, status, reload } = useResource<Service[]>(url);
  // Les línies del vocabulari global: el que permet comparar un servei amb un plec.
  const vocabulary = useResource<Vocabulary>(path("/knowledge/vocabulary"));
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [editing, setEditing] = useState<Service | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY);
  // La cobertura territorial (0047) no és cap camp de text: va a part del formulari.
  const [coverage, setCoverage] = useState<Coverage>(NEUTRAL_COVERAGE);
  const [toDelete, setToDelete] = useState<Service | null>(null);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setCoverage(NEUTRAL_COVERAGE);
    setEditing(null);
    setCreating(true);
  }

  function openEdit(service: Service) {
    reset();
    setForm({
      name: service.name,
      category: service.category ?? "",
      specialization_level: service.specialization_level ?? "",
      description: service.description ?? "",
      notes: service.notes ?? "",
      active: service.active,
      service_line_code: service.service_line_code ?? "",
      delivery_mode: service.delivery_mode ?? "",
    });
    setCoverage(service.coverage ?? NEUTRAL_COVERAGE);
    setEditing(service);
    setCreating(false);
  }

  function close() {
    setCreating(false);
    setEditing(null);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    // La cobertura només viatja si s'ha tocat: un servei antic no està obligat a dir-ne res.
    const before = editing?.coverage ?? NEUTRAL_COVERAGE;
    const payload = { ...cleanPayload(form), ...(sameCoverage(coverage, before) ? {} : { coverage: cleanCoverage(coverage) }) };
    const ok = await run(async () => {
      if (editing) {
        await api.patch(`${url}/${editing.id}`, payload);
      } else {
        await api.post(url, payload);
      }
      await reload();
    });
    if (ok) close();
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${url}/${toDelete.id}`);
      await reload();
    });
    if (ok) setToDelete(null);
  }

  const services = data ?? [];

  const columns: Array<DataTableColumn<Service>> = [
    {
      key: "service",
      header: "Servei",
      cell: (service) => (
        <>
          <p className="font-medium">{service.name}</p>
          {service.description && <p className="mt-0.5 max-w-md truncate text-[12px] text-muted">{service.description}</p>}
        </>
      ),
    },
    {
      key: "line",
      header: "Línia de servei",
      hideBelow: "md",
      cell: (service) => (
        <>
          {service.service_line_code ? (
            <span className="text-muted">{serviceLineLabel(service.service_line_code, vocabulary.data)}</span>
          ) : (
            <span className="text-warning">Sense línia</span>
          )}
          {coverageSummary(service.coverage) && <p className="mt-0.5 text-[12px] text-muted">{coverageSummary(service.coverage)}</p>}
        </>
      ),
    },
    { key: "category", header: "Categoria", hideBelow: "lg", cell: (service) => <span className="text-muted">{service.category ?? "—"}</span> },
    {
      key: "specialization",
      header: "Especialització",
      hideBelow: "lg",
      cell: (service) => (
        <span className="text-muted">{service.specialization_level ? SPECIALIZATION_LABELS[service.specialization_level] : "—"}</span>
      ),
    },
    {
      key: "state",
      header: "Estat",
      width: "12%",
      cell: (service) => (
        <StatusPill level={activeLevel(service.active)} size="sm">
          {service.active ? "Actiu" : "Inactiu"}
        </StatusPill>
      ),
    },
    {
      key: "actions",
      header: "Accions",
      align: "right",
      width: "16%",
      cell: (service) => (
        <div className="flex flex-wrap justify-end gap-1">
          <Button size="sm" variant="ghost" className="min-h-11 md:min-h-0" onClick={() => openEdit(service)}>
            Edita
          </Button>
          <Button size="sm" variant="ghost" className="min-h-11 md:min-h-0" onClick={() => setToDelete(service)}>
            Elimina
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Serveis"
        description="Capacitats que l'empresa pot executar. Es faran servir per vincular projectes i, més endavant, per fer matching."
        actions={
          <Button variant="primary" onClick={openCreate}>
            Nou servei
          </Button>
        }
      />

      {error && data && <ErrorNotice message={error} onRetry={() => void reload()} />}

      {error && !data ? (
        <LoadErrorState what="els serveis" message={error} status={status} onRetry={() => void reload()} />
      ) : loading && !data ? (
        <Card>
          <PageSkeleton variant="table" columns={["Servei", "Línia de servei", "Categoria", "Especialització", "Estat", "Accions"]} />
        </Card>
      ) : (
        <Card>
          <DataTable<Service>
            columns={columns}
            rows={services}
            rowKey={(row) => row.id}
            stackBelow="md"
            emptyState={
        <EmptyState
          title="Encara no hi ha cap servei"
          description="Defineix els serveis que ofereix l'empresa: fibra òptica, cablejat, CCTV, manteniment, ciberseguretat… el que faci falta."
          action={
            <Button variant="primary" onClick={openCreate}>
              Afegeix el primer servei
            </Button>
          }
        />
            }
          />
        </Card>
      )}

      <Modal
        open={creating || editing !== null}
        title={editing ? "Edita el servei" : "Nou servei"}
        onClose={close}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="service-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="service-form" onSubmit={save} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom" required error={fieldErrors.name}>
            <Input
              value={form.name}
              required
              maxLength={160}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Fibra òptica"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Línia de servei"
              hint="Sense línia, el servei no compta al coneixement de l'empresa."
              error={fieldErrors.service_line_code}
            >
              <Combobox
                value={form.service_line_code}
                placeholder="Sense línia"
                options={(vocabulary.data?.service_lines ?? []).map((line) => ({
                  value: line.code,
                  label: serviceLineLabel(line.code, vocabulary.data),
                }))}
                onChange={(value) => setForm({ ...form, service_line_code: value })}
              />
            </Field>
            <Field label="Com es presta">
              <Select
                value={form.delivery_mode}
                placeholder="Sense especificar"
                options={optionsFrom(DELIVERY_MODE_LABELS)}
                onChange={(event) => setForm({ ...form, delivery_mode: event.target.value })}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Categoria" error={fieldErrors.category}>
              <Input
                value={form.category}
                onChange={(event) => setForm({ ...form, category: event.target.value })}
                placeholder="Infraestructura"
              />
            </Field>
            <Field label="Nivell d'especialització">
              <Select
                value={form.specialization_level}
                placeholder="Sense definir"
                options={optionsFrom(SPECIALIZATION_LABELS)}
                onChange={(event) =>
                  setForm({ ...form, specialization_level: event.target.value })
                }
              />
            </Field>
          </div>
          <Field label="Descripció">
            <Textarea
              rows={3}
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
            />
          </Field>
          <Field label="Notes internes">
            <Textarea
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
          <Checkbox
            label="Servei actiu"
            checked={form.active}
            onChange={(event) => setForm({ ...form, active: event.target.checked })}
          />
          <CoverageEditor value={coverage} onChange={setCoverage} />
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina el servei"
        message={`Segur que vols eliminar «${toDelete?.name}»? També se n'eliminaran els enllaços a documents i quedarà desvinculat dels projectes.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
