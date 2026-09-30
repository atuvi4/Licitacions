"use client";

import { X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "./cn";

export interface DrawerProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** Més ample, per a formularis que ensenyen text de pàgina. */
  wide?: boolean;
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
export function Drawer(props: DrawerProps) {
  // El panell només existeix mentre el calaix és obert: obrir-lo és muntar-lo.
  if (!props.open) return null;
  return <DrawerPanel {...props} />;
}

/** L'element que té el focus ara mateix. Al servidor no n'hi ha cap. */
function focusedElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

function DrawerPanel({ title, description, onClose, children, footer, wide = false }: DrawerProps) {
  const panel = useRef<HTMLDivElement>(null);
  // Qui tenia el focus abans d'obrir. Es llegeix al primer render i no a
  // l'efecte: React aplica l'`autoFocus` dels fills abans de cap efecte, i
  // llavors «qui tenia el focus» ja seria un camp de dins del calaix.
  const [opener] = useState(focusedElement);
  // L'`onClose` vigent, sense que sigui dependència de l'efecte de sota. Un pare
  // que la crea a cada render (el cas habitual) el tornava a executar a cada
  // tecla escrita en un camp del calaix, i el focus saltava al botó de tancar.
  const latestOnClose = useRef(onClose);
  useEffect(() => {
    latestOnClose.current = onClose;
  }, [onClose]);

  // Només en obrir i en tancar: moure el focus és cosa d'aquests dos moments.
  useEffect(() => {
    const node = panel.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        latestOnClose.current();
        return;
      }
      if (event.key !== "Tab" || !node) return;
      const focusable = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    // Si un fill ha demanat el focus amb `autoFocus`, ja el té i és seu. Si no
    // l'ha demanat ningú, el rep el primer element del calaix: qui l'obre amb
    // el teclat hi ha de poder llegir sense tabular a cegues.
    if (!node?.contains(document.activeElement)) {
      node?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    }

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      // El focus torna a qui l'havia obert quan el calaix ha marxat de la
      // pantalla. El mode estricte de React desfà i refà els efectes amb el
      // calaix encara a lloc; moure'l llavors el prendria al camp que el té.
      if (!node?.isConnected) opener?.focus();
    };
  }, [opener]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/45">
      <div className="absolute inset-0" aria-hidden onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "animate-fade-in relative z-10 flex h-full w-full flex-col",
          wide ? "max-w-[40rem]" : "max-w-[26rem]",
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
