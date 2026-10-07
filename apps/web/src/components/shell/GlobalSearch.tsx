"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { cn } from "@/components/ui";

/**
 * Cerca global del Sprint 1 (handoff L4, diferència B): no hi ha endpoint de
 * cerca, així que porta el text a Oportunitats com a `?q=`, que és el filtre
 * que el catàleg («Totes») ja entén. La cerca de documents queda fora fins que hi hagi
 * contracte.
 */
export function GlobalSearch({
  catalogueTotal,
  autoFocus = false,
  className,
}: {
  /** Licitacions del catàleg visible (`summary.total_tenders`), no les candidates; sense dada, no hi ha comptador. */
  catalogueTotal: number | null | undefined;
  autoFocus?: boolean;
  className?: string;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [shortcut, setShortcut] = useState("Ctrl K");

  useEffect(() => {
    if (/Mac|iPhone|iPad/.test(navigator.platform)) setShortcut("⌘K");
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const placeholder = "Cerca per títol, expedient, organisme o descripció";
  const counter =
    typeof catalogueTotal === "number"
      ? catalogueTotal === 1
        ? "1 licitació al catàleg"
        : `${catalogueTotal} licitacions al catàleg`
      : null;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const q = value.trim();
    if (!q) return;
    const params = new URLSearchParams({ q });
    router.push(`/licitacions?${params.toString()}`);
  }

  return (
    <form
      role="search"
      onSubmit={onSubmit}
      className={cn(
        "flex h-9 min-w-0 flex-1 items-center gap-2 border border-neutral-400 px-2.5 text-[13.5px] text-muted focus-within:border-brand",
        className,
      )}
    >
      <Search size={16} strokeWidth={1.5} aria-hidden className="shrink-0" />
      <input
        ref={inputRef}
        type="search"
        name="q"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        aria-label="Cerca"
        autoFocus={autoFocus}
        className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-muted"
      />
      {counter && <span className="hidden shrink-0 text-[12px] text-muted md:inline">{counter}</span>}
      <kbd className="hidden shrink-0 border border-neutral-400 px-1.5 font-sans text-[11.5px] text-muted lg:inline">
        {shortcut}
      </kbd>
    </form>
  );
}
