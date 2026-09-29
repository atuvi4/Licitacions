"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StandalonePage } from "@/components/StandalonePage";
import { Button, Field, Input, LinkButton, PageSkeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

interface Onboarded {
  company: { id: string; legal_name: string; trade_name: string | null; tax_id: string | null };
  owner: { id: string; email: string; full_name: string | null };
  temporary_password: string;
}

const EMPTY = { legal_name: "", trade_name: "", tax_id: "", owner_full_name: "", owner_email: "" };

/**
 * Alta d'una empresa client i del seu propietari. Només l'administració de la
 * plataforma; el backend ho torna a comprovar. La contrasenya temporal es
 * mostra una sola vegada i no es desa enlloc del navegador.
 */
export default function NewCompanyPage() {
  const router = useRouter();
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<Onboarded | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api
      .get<CurrentUser>("/auth/me")
      .then((user) => {
        if (user.must_change_password) router.replace("/compte/contrasenya");
        else setMe(user);
      })
      .catch((caught) => {
        if (caught instanceof ApiError && caught.isUnauthorized) router.replace("/login");
      });
  }, [router]);

  const set = (key: keyof typeof EMPTY) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const created = await api.post<Onboarded>("/platform/companies", {
        legal_name: form.legal_name,
        trade_name: form.trade_name.trim() || null,
        tax_id: form.tax_id.trim() || null,
        owner_full_name: form.owner_full_name,
        owner_email: form.owner_email,
      });
      setResult(created);
      setForm(EMPTY);
      setCopied(false);
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
      // Sense porta-retalls (HTTP a la LAN): es pot seleccionar i copiar a mà.
      setCopied(false);
    }
  }

  if (!me) {
    return <PageSkeleton variant="detail" title="Alta d'empresa" context="comprovant l'accés…" />;
  }

  if (!me.is_platform_admin) {
    return (
      <StandalonePage>
        <h1 className="mb-1 text-base font-semibold text-ink">Alta d&apos;empresa</h1>
        <p className="mb-5 text-[13px] text-muted">
          Aquesta pàgina és de l&apos;administració de la plataforma. El teu compte no hi té accés.
        </p>
        <LinkButton href="/avui" variant="primary" size="md">
          Torna a l&apos;aplicació
        </LinkButton>
      </StandalonePage>
    );
  }

  if (result) {
    return (
      <StandalonePage wide>
        <h1 className="mb-1 text-base font-semibold text-ink">Empresa donada d&apos;alta</h1>
        <dl className="mt-4 grid gap-3 text-[13px] sm:grid-cols-2">
          <div>
            <dt className="text-muted">Empresa</dt>
            <dd className="font-medium text-ink">{result.company.legal_name}</dd>
            {result.company.tax_id && <dd className="text-muted">NIF {result.company.tax_id}</dd>}
          </div>
          <div>
            <dt className="text-muted">Propietari</dt>
            <dd className="font-medium text-ink">{result.owner.full_name}</dd>
            <dd className="text-muted">{result.owner.email}</dd>
          </div>
        </dl>
        <section aria-label="Contrasenya temporal" className="mt-5 rounded-control border border-warning bg-warning-soft px-4 py-3">
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
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => setResult(null)}>
            Dona d&apos;alta una altra empresa
          </Button>
          {me.memberships.length > 0 && (
            <LinkButton href="/avui" variant="secondary" size="md">
              Torna a l&apos;aplicació
            </LinkButton>
          )}
        </div>
      </StandalonePage>
    );
  }

  return (
    <StandalonePage wide>
      <h1 className="mb-1 text-base font-semibold text-ink">Alta d&apos;empresa</h1>
      <p className="mb-5 text-[13px] text-muted">
        Crea l&apos;empresa client i el compte del seu propietari. El propietari rebrà una contrasenya temporal i
        l&apos;haurà de canviar en entrar.
      </p>
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nom legal" required className="sm:col-span-2">
            <Input value={form.legal_name} onChange={set("legal_name")} maxLength={255} required autoFocus />
          </Field>
          <Field label="Nom comercial">
            <Input value={form.trade_name} onChange={set("trade_name")} maxLength={255} />
          </Field>
          <Field label="NIF" hint="Si el poses, no es podrà repetir.">
            <Input value={form.tax_id} onChange={set("tax_id")} maxLength={64} />
          </Field>
          <Field label="Nom del propietari" required>
            <Input value={form.owner_full_name} onChange={set("owner_full_name")} maxLength={200} required />
          </Field>
          <Field label="Correu del propietari" hint="Serà el seu usuari." required>
            <Input type="email" value={form.owner_email} onChange={set("owner_email")} autoComplete="off" required />
          </Field>
        </div>

        {error && (
          <p role="alert" className="rounded-control border border-danger bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" loading={saving}>
            Dona d&apos;alta
          </Button>
          {me.memberships.length > 0 && (
            <LinkButton href="/avui" variant="ghost" size="md">
              Cancel·la
            </LinkButton>
          )}
        </div>
      </form>
    </StandalonePage>
  );
}
