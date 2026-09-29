"use client";

import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

/**
 * Un sol sòlid per pantalla (handoff §2.5). El primari és l'únic blau ple; el
 * secundari és blanc amb vora subtil; `ghost` és l'enllaç d'acció en blau.
 * `danger` es conserva per compatibilitat però deixa d'usar-se per al NO-GO:
 * els destructius no tenen color propi, el text ho diu.
 * `data-variant` permet identificar l'acció principal a les proves.
 */
const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  // Blanc sobre #2f5d8a (6,9:1); hover un pas més fosc, premut dos.
  primary:
    "relative border-brand bg-brand text-white shadow-control hover:border-accent-800 hover:bg-accent-800 active:border-accent-900 active:bg-accent-900 max-sm:min-h-12",
  secondary: "border-border-strong bg-surface text-ink shadow-control hover:bg-subtle active:bg-neutral-100",
  ghost: "border-transparent bg-transparent text-brand-strong hover:bg-brand-soft hover:text-accent-800 active:bg-brand-soft",
  danger: "border-danger bg-danger text-white hover:opacity-90",
};

// Sota 640 px cap acció baixa de 44 px, i el sòlid (l'acció principal) de 48 (handoff, Estats transversals 04).
const BASE_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-control border font-heading font-semibold tracking-[0.02em] whitespace-nowrap transition-colors duration-150 max-sm:min-h-11";

function sizeClass(size: ButtonSize) {
  return size === "sm" ? "ui-action-sm text-[13px]" : "ui-action-md text-[13.5px]";
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      data-variant={variant}
      disabled={disabled || loading}
      className={cn(
        BASE_CLASS,
        // Desactivat: 45 % i cursor, mai amagat. Només per validació de formulari;
        // un permís denegat es diu amb PermissionNote.
        "disabled:cursor-not-allowed disabled:opacity-45",
        sizeClass(size),
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {loading && <Spinner className="h-3.5 w-3.5" />}
      {children}
    </button>
  );
}

/**
 * Enllaç amb aspecte de botó.
 *
 * Cal perquè un <button> dins d'un <a> és niuat invàlid en HTML: el navegador
 * el pot ignorar i la navegació no es dispara.
 */
export function LinkButton({
  href,
  variant = "ghost",
  size = "sm",
  className,
  children,
}: {
  href: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link href={href} data-variant={variant} className={cn(BASE_CLASS, sizeClass(size), BUTTON_VARIANTS[variant], className)}>
      {children}
    </Link>
  );
}
