"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import type { ContractPreferences, FinancialYear } from "@/lib/types";

const PREFERENCE_FIELDS = [
  {
    key: "minimum_interesting_contract",
    label: "Import mínim interessant",
    hint: "Per sota d'aquest import, un contracte no compensa.",
  },
  { key: "preferred_contract_min", label: "Rang preferent (mínim)" },
  { key: "preferred_contract_max", label: "Rang preferent (màxim)" },
  {
    key: "maximum_recommended_contract",
    label: "Màxim recomanable",
    hint: "Per sobre, l'execució comença a ser arriscada.",
  },
  {
    key: "maximum_assumable_contract",
    label: "Màxim assumible",
    hint: "Sostre absolut de capacitat.",
  },
] as const;

const EMPTY_YEAR = { year: String(new Date().getFullYear() - 1), revenue: "", notes: "" };

export default function SolvencyPage() {
  const path = useCompanyPath();
  const yearsUrl = path("/financial-years");
  const preferencesUrl = path("/contract-preferences");

  const years = useResource<FinancialYear[]>(yearsUrl);
  const preferences = useResource<ContractPreferences>(preferencesUrl);

  const yearMutation = useMutation();
  const preferenceMutation = useMutation();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<FinancialYear | null>(null);
  const [form, setForm] = useState(EMPTY_YEAR);
  const [toDelete, setToDelete] = useState<FinancialYear | null>(null);
  const [preferenceForm, setPreferenceForm] = useState<Record<string, string>>({});
  const [savedPreferences, setSavedPreferences] = useState(false);

  useEffect(() => {
    if (!preferences.data) return;
    const state: Record<string, string> = {};
    for (const field of PREFERENCE_FIELDS) {
      const value = preferences.data[field.key];
      state[field.key] = value ?? "";
    }
    state.notes = preferences.data.notes ?? "";
    setPreferenceForm(state);
  }, [preferences.data]);

  async function saveYear(event: React.FormEvent) {
    event.preventDefault();
    const payload = cleanPayload({
      year: Number(form.year),
      revenue: form.revenue,
      notes: form.notes,
    });
    const ok = await yearMutation.run(async () => {
      if (editing) {
        await api.patch(`${yearsUrl}/${editing.id}`, payload);
      } else {
        await api.post(yearsUrl, payload);
      }
      await years.reload();
    });
    if (ok) setOpen(false);
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await yearMutation.run(async () => {
      await api.delete(`${yearsUrl}/${toDelete.id}`);
      await years.reload();
    });
    if (ok) setToDelete(null);
  }

  async function savePreferences(event: React.FormEvent) {
    event.preventDefault();
    const ok = await preferenceMutation.run(async () => {
      await api.put(preferencesUrl, cleanPayload(preferenceForm));
      await preferences.reload();
    });
    setSavedPreferences(ok);
  }

  const list = years.data ?? [];

  return (
    <>
      <PageHeader
        title="Solvència"
        description="Facturació per exercicis i capacitat contractual. Aquestes dades alimentaran el futur Fit econòmic."
      />

      <div className="space-y-4">
        <Card>
          <CardHeader
            title="Facturació anual"
            description="Un únic registre per any. Serveix per acreditar solvència econòmica."
            action={
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  yearMutation.reset();
                  setForm(EMPTY_YEAR);
                  setEditing(null);
                  setOpen(true);
                }}
              >
                Nou exercici
              </Button>
            }
          />
          {years.error && <div className="p-4"><ErrorNotice message={years.error} /></div>}
          {years.loading ? (
            <LoadingRows rows={2} />
          ) : list.length === 0 ? (
            <EmptyState
              title="Cap exercici registrat"
              description="Introdueix la facturació dels darrers anys per poder acreditar la solvència econòmica."
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Exercici</Th>
                  <Th>Facturació</Th>
                  <Th>Notes</Th>
                  <Th className="text-right">Accions</Th>
                </tr>
              </thead>
              <tbody>
                {list.map((year) => (
                  <Tr key={year.id}>
                    <Td className="font-medium tabular-nums">{year.year}</Td>
                    <Td className="tabular-nums">{formatMoney(year.revenue, year.currency)}</Td>
                    <Td className="max-w-sm truncate text-muted">{year.notes ?? "—"}</Td>
                    <Td className="text-right whitespace-nowrap">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          yearMutation.reset();
                          setForm({
                            year: String(year.year),
                            revenue: year.revenue ?? "",
                            notes: year.notes ?? "",
                          });
                          setEditing(year);
                          setOpen(true);
                        }}
                      >
                        Edita
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setToDelete(year)}>
                        Elimina
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Capacitat contractual"
            description="Llindars d'import amb què l'empresa se sent còmoda. No es calcula cap puntuació encara."
            action={
              <>
                {savedPreferences && (
                  <span className="mr-2 text-[13px] text-success">Desat</span>
                )}
                <Button
                  variant="primary"
                  size="sm"
                  type="submit"
                  form="preferences-form"
                  loading={preferenceMutation.saving}
                >
                  Desa
                </Button>
              </>
            }
          />
          {preferences.loading ? (
            <LoadingRows rows={2} />
          ) : (
            <form id="preferences-form" onSubmit={savePreferences} className="px-5 py-5">
              {preferenceMutation.error && (
                <div className="mb-4">
                  <ErrorNotice message={preferenceMutation.error} />
                </div>
              )}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {PREFERENCE_FIELDS.map((field) => (
                  <Field
                    key={field.key}
                    label={field.label}
                    hint={"hint" in field ? field.hint : undefined}
                    error={preferenceMutation.fieldErrors[field.key]}
                  >
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={preferenceForm[field.key] ?? ""}
                      onChange={(event) => {
                        setSavedPreferences(false);
                        setPreferenceForm({
                          ...preferenceForm,
                          [field.key]: event.target.value,
                        });
                      }}
                    />
                  </Field>
                ))}
              </div>
              <div className="mt-4">
                <Field label="Notes">
                  <Textarea
                    rows={2}
                    value={preferenceForm.notes ?? ""}
                    onChange={(event) => {
                      setSavedPreferences(false);
                      setPreferenceForm({ ...preferenceForm, notes: event.target.value });
                    }}
                  />
                </Field>
              </div>
            </form>
          )}
        </Card>
      </div>

      <Modal
        open={open}
        title={editing ? "Edita l'exercici" : "Nou exercici"}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={yearMutation.saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="year-form" type="submit" loading={yearMutation.saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="year-form" onSubmit={saveYear} className="space-y-4">
          {yearMutation.error && <ErrorNotice message={yearMutation.error} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Any" required error={yearMutation.fieldErrors.year}>
              <Input
                type="number"
                min={1900}
                max={2200}
                required
                value={form.year}
                onChange={(event) => setForm({ ...form, year: event.target.value })}
              />
            </Field>
            <Field label="Facturació (EUR)" error={yearMutation.fieldErrors.revenue}>
              <Input
                type="number"
                min={0}
                step="0.01"
                value={form.revenue}
                onChange={(event) => setForm({ ...form, revenue: event.target.value })}
              />
            </Field>
          </div>
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
        title="Elimina l'exercici"
        message={`Segur que vols eliminar l'exercici ${toDelete?.year}?`}
        loading={yearMutation.saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
