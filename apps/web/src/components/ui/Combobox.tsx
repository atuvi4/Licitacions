"use client";

import { useEffect, useId, useRef, useState } from "react";

import { normaliseText } from "@/lib/text";

import { cn } from "./cn";

export interface ComboboxOption {
  value: string;
  label: string;
}

export interface ComboboxProps {
  value: string;
  options: ComboboxOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  className?: string;
  /** Quan cap opció no encaixa amb el que s'ha escrit. */
  emptyText?: string;
}

const CONTROL_CLASS =
  "ui-control ui-control-single w-full rounded-control border border-border-strong bg-surface px-3 text-body text-ink shadow-control transition-colors duration-150 max-sm:min-h-11 " +
  "placeholder:text-disabled focus:border-brand focus:outline-none focus:ring-[3px] focus:ring-brand/20 " +
  "disabled:cursor-not-allowed disabled:bg-subtle disabled:text-disabled";

/**
 * Un desplegable on es pot escriure per trobar l'opció: «mobil» troba
 * «Telefonia mòbil». Pensat per a llistes tancades que creixen (les línies de
 * servei), no com a cercador general.
 *
 * El valor és sempre el codi d'una opció o buit; el text escrit només serveix
 * per filtrar. En sortir del camp, un text que no és cap opció torna al rètol
 * de l'opció triada, i un camp buidat vol dir «cap».
 */
export function Combobox({ value, options, onChange, placeholder, required, disabled, id, className, emptyText = "Cap opció coincideix" }: ComboboxProps) {
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const selected = options.find((option) => option.value === value);
  const [text, setText] = useState(selected?.label ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  // El que s'ha escrit es queda mentre el camp és obert; quan la tria canvia des de fora, es mostra.
  const [shown, setShown] = useState(selected?.label ?? "");
  if ((selected?.label ?? "") !== shown) {
    setShown(selected?.label ?? "");
    setText(selected?.label ?? "");
  }

  // Amb el text de l'opció triada tal qual, s'ofereixen totes: escriure és refinar.
  const filtered = text && text !== selected?.label ? options.filter((option) => normaliseText(option.label).includes(normaliseText(text))) : options;

  useEffect(() => {
    if (active >= filtered.length) setActive(Math.max(0, filtered.length - 1));
  }, [active, filtered.length]);

  function choose(option: ComboboxOption) {
    onChange(option.value);
    setText(option.label);
    setOpen(false);
  }

  function settle() {
    setOpen(false);
    if (!text.trim()) {
      if (value) onChange("");
      return;
    }
    const exact = options.find((option) => normaliseText(option.label) === normaliseText(text));
    if (exact) {
      if (exact.value !== value) onChange(exact.value);
      setText(exact.label);
      return;
    }
    setText(selected?.label ?? "");
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) setOpen(true);
      else setActive((current) => Math.min(current + 1, Math.max(0, filtered.length - 1)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      if (!open) return;
      event.preventDefault();
      const option = filtered[active];
      if (option) choose(option);
    } else if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      setText(selected?.label ?? "");
    } else if (event.key === "Tab") {
      if (open) settle();
    }
  }

  return (
    <div className={cn("relative", className)}>
      <input
        ref={input}
        id={id}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && filtered[active] ? `${listId}-${active}` : undefined}
        className={CONTROL_CLASS}
        value={text}
        placeholder={placeholder}
        required={required}
        disabled={disabled}
        onChange={(event) => {
          setText(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={settle}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 z-20 mt-1 max-h-60 overflow-y-auto rounded-control border border-border bg-surface py-1 shadow-dialog"
        >
          {filtered.length === 0 && <li className="px-3 py-1.5 text-caption text-muted">{emptyText}</li>}
          {filtered.map((option, position) => (
            <li
              key={option.value}
              id={`${listId}-${position}`}
              role="option"
              aria-selected={option.value === value}
              className={cn(
                "cursor-pointer px-3 py-1.5 text-body text-ink",
                position === active && "bg-brand-soft",
                option.value === value && "font-medium",
              )}
              // El clic no ha de fer perdre el focus al camp abans de triar, ni, si la
              // llista és dins d'un `<label>`, tornar-lo a obrir en activar l'etiqueta.
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(position)}
              onClick={(event) => {
                event.preventDefault();
                choose(option);
              }}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
