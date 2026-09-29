"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StandalonePage } from "@/components/StandalonePage";
import { Button, Field, Input } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

/** La mateixa regla que el backend (`MIN_PASSWORD_LENGTH`): es torna a comprovar allà. */
const MIN_PASSWORD_LENGTH = 10;

/**
 * Canvi de contrasenya. Quan el compte té una contrasenya temporal (alta feta
 * per la plataforma), és l'única pàgina a què es pot arribar: el backend
 * bloqueja la resta fins que es canvia.
 */
export default function ChangePasswordPage() {
  const router = useRouter();
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .get<CurrentUser>("/auth/me")
      .then(setMe)
      .catch((caught) => {
        if (caught instanceof ApiError && caught.isUnauthorized) router.replace("/login");
      });
  }, [router]);

  const forced = me?.must_change_password ?? false;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (next.length < MIN_PASSWORD_LENGTH) {
      setError(`La contrasenya nova ha de tenir almenys ${MIN_PASSWORD_LENGTH} caràcters.`);
      return;
    }
    if (next !== confirm) {
      setError("Les dues contrasenyes noves no coincideixen.");
      return;
    }
    setSaving(true);
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      router.replace("/avui");
      router.refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "No s'ha pogut connectar amb el servidor.");
    } finally {
      setSaving(false);
    }
  }

  async function logout() {
    try {
      await api.post("/auth/logout", {});
    } finally {
      router.replace("/login");
    }
  }

  return (
    <StandalonePage>
      <h1 className="mb-1 text-base font-semibold text-ink">
        {forced ? "Tria una contrasenya nova" : "Canvia la contrasenya"}
      </h1>
      <p className="mb-5 text-[13px] text-muted">
        {forced
          ? "Has entrat amb una contrasenya temporal. Abans de continuar, tria'n una que només sàpigues tu."
          : "Escriu l'actual i la nova."}
      </p>
      <form onSubmit={onSubmit} className="space-y-4">
        {me && (
          // Perquè el gestor de contrasenyes la desi al compte bo.
          <input type="email" value={me.email} autoComplete="username" readOnly hidden />
        )}
        <Field label={forced ? "Contrasenya temporal" : "Contrasenya actual"} required>
          <Input
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            autoComplete="current-password"
            required
            autoFocus
          />
        </Field>
        <Field label="Contrasenya nova" hint={`Mínim ${MIN_PASSWORD_LENGTH} caràcters, amb lletres i números o símbols.`} required>
          <Input
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>
        <Field label="Repeteix la contrasenya nova" required>
          <Input
            type="password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>

        {error && (
          <p role="alert" className="rounded-control border border-danger bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" loading={saving} className="w-full">
          {forced ? "Desa i continua" : "Desa la contrasenya"}
        </Button>
      </form>
      <div className="mt-4 flex items-center justify-between text-[13px]">
        {forced ? (
          <span />
        ) : (
          <Link href="/avui" className="text-brand-strong underline decoration-1 underline-offset-2">
            Torna
          </Link>
        )}
        <button type="button" onClick={() => void logout()} className="text-muted underline decoration-1 underline-offset-2">
          Tanca la sessió
        </button>
      </div>
    </StandalonePage>
  );
}
