"use client";

/**
 * Marc de l'Administració Tuvi (disseny «Tuvi · Consola d'administració»).
 *
 * Mateix sistema visual que l'app client (barra lateral fosca, Barlow, tokens),
 * però inconfusible: etiqueta ADMIN, franja ambre a dalt, «Administració Tuvi»
 * a la barra superior i l'indicador d'entorn. Cap element de l'app client: ni
 * selector d'empresa ni «Administració de l'empresa».
 */

import { Building2, LayoutGrid, LogOut, Search, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";

import { cn } from "@/components/ui";
import { initials, PLATFORM_HOME } from "@/lib/platform";

import { usePlatformSession } from "./PlatformSession";

/** «● Entorn pilot» només quan el backend es declara pilot. */
export function EnvironmentBadge({ environment }: { environment: string | null }) {
  if (environment !== "pilot") return null;
  return (
    <span
      data-environment="pilot"
      className="inline-flex items-center gap-1.5 rounded-control border border-warning/40 bg-warning-soft px-2.5 py-1 font-heading text-kicker font-semibold tracking-[0.1em] text-warning uppercase"
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-warning" />
      Entorn pilot
    </span>
  );
}

function PlatformSidebar() {
  const pathname = usePathname();
  const { companiesCount } = usePlatformSession();
  const onCompanies = pathname.startsWith(PLATFORM_HOME);
  return (
    <aside className="hidden w-[212px] shrink-0 flex-col bg-sidebar text-sidebar-ink lg:flex">
      <div className="flex items-center justify-between px-4 pt-4 pb-5">
        <span className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-control bg-brand font-heading text-[11px] font-semibold text-white">
            TU
          </span>
          <span className="font-heading text-[17px] font-semibold text-white">Tuvi</span>
        </span>
        <span className="rounded-pill border border-white/40 px-1.5 py-0.5 font-heading text-[10px] font-semibold tracking-[0.12em] text-white uppercase">
          Admin
        </span>
      </div>
      <nav aria-label="Administració Tuvi" className="flex-1 px-3">
        <p className="mb-1.5 px-2.5 font-heading text-[10.5px] font-semibold tracking-[0.14em] text-sidebar-muted uppercase">
          Plataforma
        </p>
        <ul className="space-y-0.5">
          <li>
            <Link
              href={PLATFORM_HOME}
              aria-current={onCompanies ? "page" : undefined}
              className={cn(
                "flex min-h-9 items-center gap-2.5 px-2.5 py-2 text-[14px] leading-5 transition-colors",
                onCompanies ? "bg-sidebar-hover font-semibold text-white" : "text-sidebar-ink/80 hover:bg-white/8 hover:text-white",
              )}
            >
              <Building2 size={16} strokeWidth={1.5} aria-hidden />
              <span className="flex-1">Empreses</span>
              {companiesCount !== undefined && <span className="text-[12px] tabular-nums">{companiesCount}</span>}
            </Link>
          </li>
          <li>
            {/* El dashboard és d'una fase posterior: es veu que vindrà, però no porta enlloc. */}
            <span
              aria-disabled="true"
              className="flex min-h-9 cursor-not-allowed items-center gap-2.5 px-2.5 py-2 text-[14px] leading-5 text-sidebar-ink/45"
            >
              <LayoutGrid size={16} strokeWidth={1.5} aria-hidden />
              <span className="flex-1">Dashboard</span>
              <span className="text-[11px]">Aviat</span>
            </span>
          </li>
        </ul>
      </nav>
      <div className="border-t border-white/10 px-4 py-4 text-[12px] leading-5">
        <p className="text-sidebar-ink">Consola interna de Tuvi</p>
        <p className="text-sidebar-muted">Els clients no hi tenen accés.</p>
      </div>
    </aside>
  );
}

function PlatformTopbar() {
  const router = useRouter();
  const { operator, environment, logout } = usePlatformSession();
  const [query, setQuery] = useState("");

  function onSearch(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    router.push(q ? `${PLATFORM_HOME}?q=${encodeURIComponent(q)}` : PLATFORM_HOME);
  }

  const name = operator.full_name?.trim() || operator.email;
  return (
    <header className="flex flex-wrap items-center gap-3 border-t-[3px] border-t-warning border-b border-b-border bg-surface px-4 py-2.5 sm:px-6">
      <span className="inline-flex items-center gap-2 rounded-control border border-ink px-3 py-1.5 text-[13.5px] font-medium text-ink">
        <ShieldCheck size={15} strokeWidth={1.5} aria-hidden />
        Administració Tuvi
      </span>
      <form role="search" onSubmit={onSearch} className="relative min-w-0 flex-1 sm:max-w-md">
        <Search size={15} aria-hidden className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
        <input
          type="search"
          aria-label="Cerca empreses, NIF o propietaris"
          placeholder="Cerca empreses, NIF o propietaris"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-control border border-border-strong bg-surface py-1.5 pr-3 pl-9 text-[13.5px] text-ink placeholder:text-muted focus:outline-2 focus:outline-brand"
        />
      </form>
      <div className="ml-auto flex items-center gap-3">
        <EnvironmentBadge environment={environment} />
        <span className="flex items-center gap-2 border-l border-border pl-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-control border border-brand bg-brand-soft font-heading text-[11px] font-semibold text-brand-strong">
            {initials(name)}
          </span>
          <span className="hidden leading-tight sm:block">
            <span className="block max-w-44 truncate text-[13px] font-medium text-ink">{name}</span>
            <span className="block text-[12px] text-muted">Operador Tuvi</span>
          </span>
        </span>
        <button
          type="button"
          onClick={() => void logout()}
          aria-label="Tanca la sessió"
          title="Tanca la sessió"
          className="flex h-8 w-8 items-center justify-center rounded-control text-muted hover:bg-subtle hover:text-ink"
        >
          <LogOut size={16} strokeWidth={1.5} aria-hidden />
        </button>
      </div>
    </header>
  );
}

export function PlatformShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-canvas" data-area="administracio-tuvi">
      <PlatformSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <PlatformTopbar />
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
