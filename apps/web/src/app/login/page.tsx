"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, Field, Input } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const user = await api.post<CurrentUser>("/auth/login", { email, password });
      const next = searchParams.get("next");
      // Una contrasenya temporal es canvia abans d'anar enlloc més.
      if (user?.must_change_password) router.replace("/compte/contrasenya");
      else router.replace(next && next.startsWith("/") ? next : "/avui");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "No s'ha pogut connectar amb el servidor.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <Field label="Correu electrònic" required>
        <Input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="username"
          required
          autoFocus
        />
      </Field>
      <Field label="Contrasenya" required>
        <Input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
      </Field>

      {error && (
        <p role="alert" className="border border-danger bg-danger-soft px-3 py-2 text-[13px] text-danger">
          {error}
        </p>
      )}

      <Button type="submit" variant="primary" loading={loading} className="w-full">
        Entra
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center bg-brand font-heading text-xs font-semibold text-white">
            LI
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">Licitacions</p>
            <p className="text-[12px] text-muted">Perfil d&apos;empresa i evidències</p>
          </div>
        </div>

        <div className="rounded-card border border-border bg-surface p-6">
          <h1 className="mb-1 text-base font-semibold text-ink">Inicia la sessió</h1>
          <p className="mb-5 text-[13px] text-muted">
            Accedeix amb el compte que t&apos;han facilitat.
          </p>
          <Suspense fallback={null}>
            <LoginForm />
          </Suspense>
        </div>

        <p className="mt-4 text-center text-[12px] text-faint">
          Entorn de desenvolupament: demo@licitacions.dev
        </p>
      </div>
    </main>
  );
}
