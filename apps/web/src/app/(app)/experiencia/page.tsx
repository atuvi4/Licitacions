"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { StatePills } from "@/components/knowledge/StatePills";
import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  CardHeader,
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
import { indexSnapshot, recordState, type KnowledgeSnapshot } from "@/lib/knowledge";
import type { Project, Service } from "@/lib/types";

/**
 * Experiència (addendum 3B-2, B7): l'històric de licitacions NO és cap
 * referència acreditable. Una oferta presentada no acredita res; el que
 * acredita és una referència d'execució amb evidència vinculada (certificat de
 * bona execució, contracte, acta). Per això la pantalla separa les referències
 * amb evidència de les declarades sense, i remet l'històric a on viu.
 *
 * Qui té evidència ho diu la fotografia del coneixement (`/knowledge/snapshot`,
 * secció `execution_references`): la mateixa regla que a «Coneixement».
 */

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

const COLUMN_HEADERS = ["Projecte", "Client", "Import", "Període", "Serveis", "Accions"];

export default function ExperiencePage() {
  const path = useCompanyPath();
  const url = path("/projects");
  const { data, loading, error, status, reload } = useResource<Project[]>(url);
  const services = useResource<Service[]>(path("/services"));
  const snapshot = useResource<KnowledgeSnapshot>(path("/knowledge/snapshot"));
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [selectedServices, setSelectedServices] = useState<string[]>([]);
  const [toDelete, setToDelete] = useState<Project | null>(null);
  const [search, setSearch] = useState("");
  const [minAmount, setMinAmount] = useState("");

  const index = useMemo(() => indexSnapshot(snapshot.data), [snapshot.data]);
  const snapshotMissing = snapshot.data === null;

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

  // Acreditable = la fotografia diu que té evidència vinculada. Sense fotografia, res no ho és.
  const accredited = projects.filter((project) => index.claims.get(project.id)?.has_evidence === true);
  const declared = projects.filter((project) => index.claims.get(project.id)?.has_evidence !== true);

  const totalAccredited = (data ?? []).reduce(
    (accumulator, project) => accumulator + Number(project.amount ?? 0),
    0,
  );

  function reloadAll() {
    void reload();
    void snapshot.reload();
  }

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
      reloadAll();
    });
    if (ok) setToDelete(null);
  }

  function toggleService(id: string) {
    setSelectedServices((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  function openCreate() {
    reset();
    setForm(EMPTY);
    setSelectedServices([]);
    setOpen(true);
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
          {index.claims.has(project.id) && (
            <div className="mt-1">
              <StatePills value={recordState(project.id, "current", index)} />
            </div>
          )}
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

  const filtering = search !== "" || minAmount !== "";
  const filters = (
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
      {filtering && (
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11 md:min-h-0"
          onClick={() => {
            setSearch("");
            setMinAmount("");
          }}
        >
          Treu els filtres
        </Button>
      )}
    </div>
  );

  return (
    <>
      <PageHeader
        title="Experiència"
        description="Referències d'execució: contractes fets o en curs, amb import, dates i serveis. Només les que tenen evidència vinculada es poden acreditar davant d'un òrgan de contractació."
        actions={
          <Button variant="primary" onClick={openCreate}>
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
          {filters}
          <PageSkeleton variant="table" columns={COLUMN_HEADERS} />
        </Card>
      ) : (data ?? []).length === 0 ? (
        <Card>
          <EmptyState
            title="Cap projecte registrat"
            description="Registra els projectes executats amb import, dates i serveis. Després podràs adjuntar-hi els certificats de bona execució."
            action={
              <Button variant="primary" size="md" onClick={openCreate}>
                Registra el primer projecte
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          <Card>{filters}</Card>

          <Card>
            <CardHeader
              title="Referències acreditables"
              description="Referències d'execució amb evidència vinculada (certificat de bona execució, contracte o acta de recepció). Són les que es poden acreditar davant d'un òrgan de contractació."
            />
            {snapshotMissing ? (
              <p className="px-5 py-4 text-body text-muted">
                No s&apos;ha pogut comprovar quines referències tenen evidència: mentrestant, totes surten com a declarades.{" "}
                <button type="button" className="text-brand-strong underline decoration-1 underline-offset-2" onClick={() => void snapshot.reload()}>
                  Torna-ho a provar
                </button>
              </p>
            ) : (
              <DataTable<Project>
                columns={columns}
                rows={accredited}
                rowKey={(row) => row.id}
                stackBelow="md"
                emptyState={
                  <p className="px-5 py-4 text-body text-muted">
                    {filtering
                      ? "Cap referència amb evidència encaixa amb els filtres."
                      : "Cap referència amb evidència vinculada encara. Obre un projecte i adjunta-hi el certificat de bona execució."}
                  </p>
                }
              />
            )}
          </Card>

          <Card>
            <CardHeader
              title="Referències declarades sense evidència"
              description="Projectes registrats sense cap document vinculat. Compten com a declarats: l'evidència només es demana quan una licitació la requereix."
            />
            <DataTable<Project>
              columns={columns}
              rows={declared}
              rowKey={(row) => row.id}
              stackBelow="md"
              emptyState={
                <p className="px-5 py-4 text-body text-muted">
                  {filtering ? "Cap referència declarada encaixa amb els filtres." : "Totes les referències tenen evidència vinculada."}
                </p>
              }
            />
          </Card>

          <Card>
            <CardHeader
              title="Històric de licitacions"
              description="Les licitacions a què l'empresa s'ha presentat en el passat."
              action={
                <LinkButton href="/empresa/coneixement?tab=historic" variant="secondary" size="sm">
                  Obre l&apos;històric detectat
                </LinkButton>
              }
            />
            <p className="px-5 py-4 text-body text-muted">
              Una oferta presentada no acredita res: l&apos;històric no és cap referència d&apos;execució i no es barreja amb
              les d&apos;aquí dalt. El que l&apos;històric suggereix sobre l&apos;empresa es revisa i es confirma a «Coneixement
              · Històric detectat». El detall de cada oferta presentada no es mostra en aquesta versió.
            </p>
          </Card>
        </div>
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
            <div className="flex flex-wrap gap-2 rounded-card border border-border p-3">
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
