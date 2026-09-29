import type { ReactNode } from "react";

/**
 * Pàgina fora de l'aplicació d'empresa (sense barra lateral ni empresa activa):
 * el canvi de contrasenya obligatori i l'alta d'empreses de la plataforma. La
 * mateixa marca i el mateix fons que el login.
 */
export function StandalonePage({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <main className="flex min-h-screen items-start justify-center bg-canvas px-4 py-10 sm:items-center">
      <div className={wide ? "w-full max-w-xl" : "w-full max-w-sm"}>
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center bg-brand font-heading text-xs font-semibold text-white">
            LI
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">Licitacions</p>
            <p className="text-[12px] text-muted">Perfil d&apos;empresa i evidències</p>
          </div>
        </div>
        <div className="rounded-card border border-border bg-surface p-6">{children}</div>
      </div>
    </main>
  );
}
