"use client";

import { useEffect, useState } from "react";

import { useCompanyPath, useSession } from "@/components/SessionProvider";
import {
  Button,
  Card,
  CardHeader,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Textarea,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { Company } from "@/lib/types";

type FormState = Record<string, string>;

const FIELDS = [
  { key: "legal_name", label: "Raó social", required: true, group: "identificacio" },
  { key: "trade_name", label: "Nom comercial", group: "identificacio" },
  { key: "tax_id", label: "NIF / CIF", group: "identificacio" },
  { key: "legal_form", label: "Forma jurídica", group: "identificacio" },
  { key: "incorporation_date", label: "Data de constitució", type: "date", group: "identificacio" },
  { key: "employee_count", label: "Nombre de treballadors", type: "number", group: "identificacio" },

  { key: "email", label: "Correu electrònic", type: "email", group: "contacte" },
  { key: "phone", label: "Telèfon", group: "contacte" },
  { key: "website", label: "Lloc web", group: "contacte" },

  { key: "address", label: "Adreça", group: "localitzacio" },
  { key: "postal_code", label: "Codi postal", group: "localitzacio" },
  { key: "city", label: "Població", group: "localitzacio" },
  { key: "province", label: "Província", group: "localitzacio" },
  { key: "autonomous_region", label: "Comunitat autònoma", group: "localitzacio" },
  { key: "country", label: "País (ISO-2)", group: "localitzacio", maxLength: 2 },
] as const;

const GROUPS = [
  { key: "identificacio", title: "Identificació", description: "Dades registrals de l'empresa." },
  { key: "contacte", title: "Contacte", description: "Canals de contacte corporatius." },
  { key: "localitzacio", title: "Localització", description: "Domicili social." },
];

function toFormState(company: Company): FormState {
  const state: FormState = {};
  for (const field of FIELDS) {
    const value = company[field.key as keyof Company];
    state[field.key] = value === null || value === undefined ? "" : String(value);
  }
  state.description = company.description ?? "";
  return state;
}

export default function CompanyGeneralPage() {
  const path = useCompanyPath();
  const { refresh } = useSession();
  const { data, loading, error, reload, setData } = useResource<Company>(path(""));
  const { saving, error: saveError, fieldErrors, run } = useMutation();
  const [form, setForm] = useState<FormState | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) setForm(toFormState(data));
  }, [data]);

  function update(key: string, value: string) {
    setSaved(false);
    setForm((current) => (current ? { ...current, [key]: value } : current));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!form) return;
    const payload = cleanPayload({
      ...form,
      employee_count: form.employee_count === "" ? null : Number(form.employee_count),
    });
    const ok = await run(async () => {
      const updated = await api.patch<Company>(path(""), payload);
      setData(updated);
      setForm(toFormState(updated));
      await refresh();
    });
    setSaved(ok);
  }

  if (error) {
    return (
      <>
        <PageHeader title="Dades generals" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  return (
    <form onSubmit={onSubmit}>
      <PageHeader
        title="Dades generals"
        description="Informació registral de l'empresa. És la base de qualsevol acreditació."
        actions={
          <>
            {saved && <span className="text-[13px] text-success">Canvis desats</span>}
            <Button type="submit" variant="primary" loading={saving} disabled={!form}>
              Desa els canvis
            </Button>
          </>
        }
      />

      {saveError && <div className="mb-4"><ErrorNotice message={saveError} /></div>}

      <div className="space-y-4">
        {GROUPS.map((group) => (
          <Card key={group.key}>
            <CardHeader title={group.title} description={group.description} />
            {loading || !form ? (
              <LoadingRows rows={2} />
            ) : (
              <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
                {FIELDS.filter((field) => field.group === group.key).map((field) => (
                  <Field
                    key={field.key}
                    label={field.label}
                    required={"required" in field ? field.required : false}
                    error={fieldErrors[field.key]}
                  >
                    <Input
                      type={"type" in field ? field.type : "text"}
                      value={form[field.key] ?? ""}
                      maxLength={"maxLength" in field ? field.maxLength : undefined}
                      required={"required" in field ? field.required : false}
                      onChange={(event) => update(field.key, event.target.value)}
                    />
                  </Field>
                ))}
              </div>
            )}
          </Card>
        ))}

        <Card>
          <CardHeader
            title="Descripció"
            description="Text lliure reutilitzable en presentacions i memòries."
          />
          {loading || !form ? (
            <LoadingRows rows={1} />
          ) : (
            <div className="px-5 py-5">
              <Textarea
                rows={5}
                value={form.description ?? ""}
                onChange={(event) => update("description", event.target.value)}
                placeholder="Activitat principal, anys d'experiència, capacitats diferencials…"
              />
            </div>
          )}
        </Card>
      </div>
    </form>
  );
}
