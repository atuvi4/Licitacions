"use client";

/**
 * «Nova empresa» (calaix lateral). És el mateix flux d'alta d'abans
 * (`POST /platform/companies`): empresa + propietari inicial amb contrasenya
 * temporal, ara amb l'estat inicial (Pilot per defecte, 60 dies) i el NIF
 * obligatori. La temporal es mostra una sola vegada i no es desa enlloc.
 */

import { useState } from "react";

import { Button, cn, Drawer, Field, Input } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { type CompanyOnboarded, DEFAULT_PILOT_DAYS, isoDatePlus } from "@/lib/platform";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const FORM_ID = "nova-empresa";

function emptyForm() {
  return {
    legal_name: "",
    trade_name: "",
    tax_id: "",
    initial_status: "pilot" as "pilot" | "active",
    pilot_ends_at: isoDatePlus(DEFAULT_PILOT_DAYS),
    owner_full_name: "",
    owner_email: "",
  };
}

function StatusOption({
  value,
  checked,
  title,
  hint,
  onSelect,
}: {
  value: "pilot" | "active";
  checked: boolean;
  title: string;
  hint: string;
  onSelect: (value: "pilot" | "active") => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2.5 rounded-control border px-3 py-2.5",
        checked ? "border-brand bg-brand-soft" : "border-border-strong bg-surface hover:bg-subtle",
      )}
    >
      <input type="radio" name="initial_status" value={value} checked={checked} onChange={() => onSelect(value)} className="mt-1" />
      <span>
        <span className="block text-[14px] font-semibold text-ink">{title}</span>
        <span className="block text-caption text-muted">{hint}</span>
      </span>
    </label>
  );
}

export function NewCompanyDrawer({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (created: CompanyOnboarded) => void;
}) {
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<CompanyOnboarded | null>(null);
  const [copied, setCopied] = useState(false);

  const set = (key: keyof ReturnType<typeof emptyForm>) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  function close() {
    // La temporal no sobreviu al calaix.
    setResult(null);
    setForm(emptyForm());
    setError(null);
    setCopied(false);
    onClose();
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const pilot = form.initial_status === "pilot";
      const created = await api.post<CompanyOnboarded>("/platform/companies", {
        legal_name: form.legal_name,
        trade_name: form.trade_name.trim() || null,
        tax_id: form.tax_id,
        initial_status: form.initial_status,
        pilot_ends_at: pilot ? form.pilot_ends_at : null,
        owner_full_name: form.owner_full_name,
        owner_email: form.owner_email,
      });
      setResult(created);
      setForm(emptyForm());
      onCreated(created);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "No s'ha pogut connectar amb el servidor.");
    } finally {
      setSaving(false);
    }
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // Sense porta-retalls (HTTP a la LAN): es pot seleccionar a mà.
    }
  }

  const footer = result ? (
    <div className="flex justify-end gap-2">
      <Button variant="secondary" onClick={() => setResult(null)}>
        Dona d&apos;alta una altra
      </Button>
      <Button variant="primary" onClick={close}>
        Fet
      </Button>
    </div>
  ) : (
    <div className="flex justify-end gap-2">
      <Button variant="secondary" onClick={close} disabled={saving}>
        Cancel·lar
      </Button>
      <Button variant="primary" type="submit" form={FORM_ID} loading={saving}>
        Crear empresa
      </Button>
    </div>
  );

  return (
    <Drawer
      open={open}
      wide
      title={result ? "Empresa creada" : "Nova empresa"}
      description={result ? undefined : "Empreses › Nova empresa. La resta de dades es completen des de la fitxa."}
      onClose={close}
      footer={footer}
    >
      {result ? (
        <div className="space-y-4 px-4 py-4">
          <dl className="grid gap-3 text-[13px] sm:grid-cols-2">
            <div>
              <dt className={KICKER}>Empresa</dt>
              <dd className="font-medium text-ink">{result.company.legal_name}</dd>
              <dd className="text-muted">NIF {result.company.tax_id}</dd>
            </div>
            <div>
              <dt className={KICKER}>Propietari inicial</dt>
              <dd className="font-medium text-ink">{result.owner.full_name}</dd>
              <dd className="text-muted">{result.owner.email}</dd>
            </div>
            <div>
              <dt className={KICKER}>Estat</dt>
              <dd className="text-ink">
                {result.company.platform_status === "pilot"
                  ? `Pilot fins al ${formatDate(result.company.pilot_ends_at)}`
                  : "Activa"}
              </dd>
            </div>
          </dl>
          <section aria-label="Contrasenya temporal" className="rounded-control border border-warning bg-warning-soft px-4 py-3">
            <p className="text-[13px] font-semibold text-ink">Contrasenya temporal</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="rounded-control border border-border bg-surface px-2.5 py-1.5 font-mono text-[15px] text-ink select-all">
                {result.temporary_password}
              </code>
              <Button size="sm" variant="secondary" onClick={() => void copy(result.temporary_password)}>
                {copied ? "Copiada" : "Copia"}
              </Button>
            </div>
            <p className="mt-2 text-[13px] text-ink">
              Només es mostra ara. Fes-la arribar al propietari per un canal segur: l&apos;haurà de canviar en entrar per
              primer cop.
            </p>
          </section>
        </div>
      ) : (
        <form id={FORM_ID} onSubmit={onSubmit} className="divide-y divide-border">
          <section className="space-y-4 px-4 py-4">
            <p className={KICKER}>Dades de l&apos;empresa</p>
            <Field label="Nom legal" hint="Tal com consta al registre mercantil." required>
              <Input value={form.legal_name} onChange={set("legal_name")} maxLength={255} required autoFocus />
            </Field>
            <Field label="Nom comercial" hint="Si el deixes buit, s'utilitza el nom legal.">
              <Input value={form.trade_name} onChange={set("trade_name")} maxLength={255} />
            </Field>
            <Field label="NIF" hint="Comprovarem que no hi hagi cap altra empresa amb aquest NIF." required>
              <Input value={form.tax_id} onChange={set("tax_id")} maxLength={64} required className="max-w-[12rem]" />
            </Field>
          </section>
          <section className="space-y-4 px-4 py-4">
            <p className={KICKER}>Estat inicial</p>
            <div role="radiogroup" aria-label="Estat inicial" className="grid gap-2 sm:grid-cols-2">
              <StatusOption
                value="pilot"
                checked={form.initial_status === "pilot"}
                title="Pilot"
                hint="Prova amb seguiment de Tuvi"
                onSelect={(value) => setForm((current) => ({ ...current, initial_status: value }))}
              />
              <StatusOption
                value="active"
                checked={form.initial_status === "active"}
                title="Activa"
                hint="Accés complet des de l'alta"
                onSelect={(value) => setForm((current) => ({ ...current, initial_status: value }))}
              />
            </div>
            {form.initial_status === "pilot" && (
              <Field label="Fi del pilot" hint={`Per defecte, ${DEFAULT_PILOT_DAYS} dies a partir d'avui.`}>
                <Input
                  type="date"
                  value={form.pilot_ends_at}
                  min={isoDatePlus(0)}
                  onChange={set("pilot_ends_at")}
                  className="max-w-[12rem]"
                />
              </Field>
            )}
          </section>
          <section className="space-y-4 px-4 py-4">
            <p className={KICKER}>Propietari inicial</p>
            <Field label="Nom i cognoms" required>
              <Input value={form.owner_full_name} onChange={set("owner_full_name")} maxLength={200} required />
            </Field>
            <Field label="Correu del propietari" hint="Serà el seu usuari. Ha de ser diferent del teu." required>
              <Input type="email" value={form.owner_email} onChange={set("owner_email")} autoComplete="off" required />
            </Field>
            <div className="rounded-control border border-border bg-subtle px-3 py-2.5">
              <p className={KICKER}>Administració del client</p>
              <p className="mt-1 text-[13px] text-ink">
                El propietari administrarà l&apos;empresa des de la seva pròpia administració, dins de l&apos;app Tuvi.
                Des d&apos;aquí només l&apos;assignes; tu no quedes com a usuari de l&apos;empresa.
              </p>
            </div>
            {error && (
              <p role="alert" className="rounded-control border border-danger bg-danger-soft px-3 py-2 text-[13px] text-danger">
                {error}
              </p>
            )}
          </section>
        </form>
      )}
    </Drawer>
  );
}
