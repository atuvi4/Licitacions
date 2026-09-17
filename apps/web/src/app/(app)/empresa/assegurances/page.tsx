"use client";

import { useEffect, useState } from "react";

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
  formatMoney,
  INSURANCE_LIMIT_KIND_LABELS,
  INSURANCE_TYPE_LABELS,
  optionsFrom,
  SELECTABLE_INSURANCE_LIMIT_KINDS,
  TEMPORAL_STATUS_LABELS,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { TEMPORAL_LEVEL } from "@/lib/labels";
import type { Insurance, InsuranceLimit, InsuranceLimitKind } from "@/lib/types";

/** Una fila del formulari de limits. Sense `id` = encara no existeix. */
type LimitRow = { id?: string; kind: InsuranceLimitKind; amount: string };

const LIMIT_OPTIONS = SELECTABLE_INSURANCE_LIMIT_KINDS.map((kind) => ({
  value: kind,
  label: INSURANCE_LIMIT_KIND_LABELS[kind],
}));

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
  const { data, loading, error, status, reload } = useResource<Insurance[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Insurance | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [toDelete, setToDelete] = useState<Insurance | null>(null);
  // Els limits de la polissa que s'esta editant. Es carreguen en obrir el
  // formulari i es sincronitzen en desar: la fila que desapareix s'esborra, la
  // que canvia s'actualitza i la nova es crea.
  const [limits, setLimits] = useState<LimitRow[]>([]);
  const [initialLimits, setInitialLimits] = useState<InsuranceLimit[]>([]);
  const [limitsByPolicy, setLimitsByPolicy] = useState<Record<string, InsuranceLimit[]>>({});

  async function loadLimits(policies: Insurance[]) {
    const entries = await Promise.all(
      policies.map(async (policy) => {
        try {
          return [policy.id, await api.get<InsuranceLimit[]>(`${url}/${policy.id}/limits`)] as const;
        } catch {
          return [policy.id, [] as InsuranceLimit[]] as const;
        }
      }),
    );
    setLimitsByPolicy(Object.fromEntries(entries));
  }

  useEffect(() => {
    if (data) void loadLimits(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  function openCreate() {
    reset();
    setForm(EMPTY);
    setEditing(null);
    setLimits([]);
    setInitialLimits([]);
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
    const existing = limitsByPolicy[insurance.id] ?? [];
    setInitialLimits(existing);
    setLimits(existing.map((limit) => ({ id: limit.id, kind: limit.kind, amount: limit.amount })));
    setOpen(true);
  }

  /** Envia nomes el que ha canviat: crea, actualitza i esborra el que cal. */
  async function syncLimits(policyId: string) {
    const base = `${url}/${policyId}/limits`;
    const kept = new Set(limits.filter((row) => row.id).map((row) => row.id));
    for (const previous of initialLimits) {
      if (!kept.has(previous.id)) await api.delete(`${base}/${previous.id}`);
    }
    for (const row of limits) {
      if (!row.amount) continue;
      if (row.id) {
        const previous = initialLimits.find((limit) => limit.id === row.id);
        if (previous && previous.kind === row.kind && previous.amount === row.amount) continue;
        await api.patch(`${base}/${row.id}`, { kind: row.kind, amount: row.amount });
      } else {
        await api.post(base, { kind: row.kind, amount: row.amount, currency: "EUR" });
      }
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      const payload = cleanPayload(form);
      const policy = editing
        ? await api.patch<Insurance>(`${url}/${editing.id}`, payload)
        : await api.post<Insurance>(url, payload);
      await syncLimits(policy.id);
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

      {error && data && <ErrorNotice message={error} onRetry={() => void reload()} />}

      {error && !data ? (
        <LoadErrorState what="les pòlisses" message={error} status={status} onRetry={() => void reload()} />
      ) : (
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
                cell: (insurance) => {
                  const rows = limitsByPolicy[insurance.id] ?? [];
                  return (
                    <>
                      {rows.length > 0 ? (
                        <ul className="space-y-0.5">
                          {rows.map((limit) => (
                            <li key={limit.id} className="whitespace-nowrap">
                              <span className="text-muted">
                                {INSURANCE_LIMIT_KIND_LABELS[limit.kind]}:
                              </span>{" "}
                              <span className="tabular-nums">
                                {formatMoney(limit.amount, limit.currency)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span className="tabular-nums">
                          {formatMoney(insurance.coverage_amount, insurance.currency)}
                        </span>
                      )}
                      {insurance.coverage_description && (
                        <p className="mt-0.5 max-w-xs truncate text-[12px] text-muted">
                          {insurance.coverage_description}
                        </p>
                      )}
                    </>
                  );
                },
              },
              {
                header: "Vigència",
                cell: (insurance) => (
                  <>
                    <span className="whitespace-nowrap tabular-nums text-muted">
                      {formatDate(insurance.starts_at)} – {formatDate(insurance.expires_at)}
                    </span>
                    <StatusPill level={TEMPORAL_LEVEL[insurance.temporal_status]} size="sm" className="ml-2">
                      {TEMPORAL_STATUS_LABELS[insurance.temporal_status]}
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
          <div className="space-y-2 border-t border-line pt-4">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">Límits de la pòlissa</p>
              <Button
                variant="secondary"
                onClick={() =>
                  setLimits([...limits, { kind: "total", amount: "" }])
                }
              >
                Afegeix un límit
              </Button>
            </div>
            <p className="text-[12px] text-muted">
              Molts plecs exigeixen una cobertura total i, a més, un sublímit per víctima o
              per sinistre. Sense el sublímit registrat, el compliment no pot afirmar que la
              pòlissa el cobreixi.
            </p>
            {limits.map((row, index) => (
              <div key={row.id ?? `nou-${index}`} className="flex items-end gap-2">
                <Field label="Tipus" className="flex-1">
                  <Select
                    value={row.kind}
                    options={LIMIT_OPTIONS}
                    onChange={(event) =>
                      setLimits(
                        limits.map((item, position) =>
                          position === index
                            ? { ...item, kind: event.target.value as InsuranceLimitKind }
                            : item,
                        ),
                      )
                    }
                  />
                </Field>
                <Field label="Import (EUR)" className="flex-1">
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={row.amount}
                    onChange={(event) =>
                      setLimits(
                        limits.map((item, position) =>
                          position === index ? { ...item, amount: event.target.value } : item,
                        ),
                      )
                    }
                  />
                </Field>
                <Button
                  variant="secondary"
                  onClick={() =>
                    setLimits(limits.filter((_item, position) => position !== index))
                  }
                >
                  Treu
                </Button>
              </div>
            ))}
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
        title="Elimina la pòlissa"
        message={`Segur que vols eliminar aquesta pòlissa${toDelete?.policy_number ? ` (${toDelete.policy_number})` : ""}?`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
