"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "./cn";

export interface DrawerProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/**
 * Panell lateral per a lectures transversals que no han de fer perdre el lloc.
 *
 * Comparteix les decisions del `Modal` —Escape, bloqueig del desplaçament de
 * fons, teló que tanca, `role="dialog"`— i hi afegeix el que allà falta i aquí
 * és imprescindible: el focus queda atrapat a dins i torna al control que l'ha
 * obert. Sense això, tabular se'n va a la pàgina de sota, que segueix visible.
 */
export function Drawer({ open, title, description, onClose, children, footer }: DrawerProps) {
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !panel.current.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    // El primer element del calaix rep el focus: qui l'obre amb el teclat hi ha
    // de poder llegir sense tabular a cegues.
    const target = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    target?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      opener.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/45">
      <div className="absolute inset-0" aria-hidden onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "animate-fade-in relative z-10 flex h-full w-full max-w-[26rem] flex-col",
          "border-l border-border bg-surface shadow-dialog",
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-4 py-3.5">
          <div className="min-w-0">
            <h2 className="font-heading text-[17px] leading-tight font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-caption text-muted">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Tanca"
            className="-mt-0.5 -mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-muted transition-colors hover:bg-subtle hover:text-ink"
          >
            <X size={16} strokeWidth={1.5} aria-hidden />
          </button>
        </header>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && <footer className="border-t border-border px-4 py-3">{footer}</footer>}
      </div>
    </div>
  );
}
