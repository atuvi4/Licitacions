"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { cn } from "./cn";
import type { Level } from "./StatusPill";

export interface TabItem {
  key: string;
  label: string;
  /** Només feina pendent; en Bloqueig si bloqueja. */
  count?: number | string;
  countLevel?: Level;
  /**
   * Què compta el número, si sol no ho diu (P2-02): p. ex. «2 pendents». Es mostra
   * en lloc del número i forma part del nom accessible de la pestanya. Només té
   * efecte amb `count`: sense recompte no s'ensenya res.
   */
  countLabel?: string;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (key: string) => void;
  /** Paràmetre de la URL que guarda l'estat. */
  param?: string;
  /**
   * Si el canvi deixa entrada a l'historial. Per defecte no: en pestanyes que
   * miren facetes del mateix objecte, Enrere ha de sortir de l'objecte i no
   * desfer set canvis de pestanya. On les pestanyes separen feines diferents
   * —i on s'hi arriba des d'enllaços que sí que empenyen— val «push», perquè
   * Enrere torni on s'era.
   */
  history?: "replace" | "push";
  /**
   * Pot aturar el canvi: si torna `false` (o s'hi resol), no es navega. És
   * per a pestanyes que contenen feina sense desar i han de poder preguntar.
   */
  beforeChange?: (key: string) => boolean | Promise<boolean>;
  className?: string;
}

const COUNT_CLASS: Partial<Record<Level, string>> = {
  blocking: "text-danger",
  attention: "text-warning",
  ready: "text-success",
  running: "text-running",
};

/**
 * Navegació dins d'un objecte (handoff §2.11): màxim 4 pestanyes, estat a la
 * URL (`?tab=`) per enllaçar i compartir, sense scroll en canviar. L'estat surt
 * sempre de la URL, així que Enrere i Endavant tornen a pintar el que toca
 * sense cap estat local que els contradigui.
 */
export function Tabs({ items, value, onChange, param = "tab", history = "replace", beforeChange, className }: TabsProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  async function select(key: string) {
    if (key === value) return;
    if (beforeChange && !(await beforeChange(key))) return;
    onChange(key);
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set(param, key);
    const href = `${pathname}?${params.toString()}`;
    if (history === "push") router.push(href, { scroll: false });
    else router.replace(href, { scroll: false });
  }

  return (
    <div role="tablist" className={cn("scroll-thin flex overflow-x-auto border-b border-border", className)}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => void select(item.key)}
            className={cn(
              "-mb-px flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-body whitespace-nowrap transition-colors",
              active ? "border-brand font-semibold text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.countLabel && item.count !== undefined && item.count !== null && (
              // L'espai separa el recompte de l'etiqueta al nom accessible («Dossier 2 pendents»);
              // dins del flex no ocupa lloc.
              <> </>
            )}
            {item.count !== undefined && item.count !== null && (
              <span className={cn("tabular-nums", COUNT_CLASS[item.countLevel ?? "neutral"] ?? "text-muted")}>
                {item.countLabel ?? item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
