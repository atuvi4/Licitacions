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
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (key: string) => void;
  /** Paràmetre de la URL que guarda l'estat. */
  param?: string;
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
 * URL (`?tab=`) per enllaçar i compartir, sense scroll en canviar.
 */
export function Tabs({ items, value, onChange, param = "tab", className }: TabsProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  function select(key: string) {
    onChange(key);
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set(param, key);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
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
            onClick={() => select(item.key)}
            className={cn(
              "-mb-px flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-body whitespace-nowrap transition-colors",
              active ? "border-accent-500 font-semibold text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.count !== undefined && item.count !== null && (
              <span className={cn("tabular-nums", COUNT_CLASS[item.countLevel ?? "neutral"] ?? "text-muted")}>
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
