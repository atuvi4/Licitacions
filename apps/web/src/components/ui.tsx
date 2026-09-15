"use client";

import Link from "next/link";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

/* -------------------------------------------------------------------------- */
/* Button                                                                      */
/* -------------------------------------------------------------------------- */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-transparent bg-brand text-white shadow-control hover:bg-brand-strong",
  secondary: "border-border bg-surface text-ink shadow-control hover:border-border-strong hover:bg-subtle",
  ghost: "bg-transparent text-muted hover:bg-subtle hover:text-ink border-transparent",
  danger: "border-transparent bg-danger text-white shadow-control hover:opacity-90",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
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
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-control border font-medium transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-55",
        size === "sm" ? "ui-action-sm" : "ui-action-md",
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
  size?: "sm" | "md";
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-control border font-medium transition-colors duration-150",
        size === "sm" ? "ui-action-sm" : "ui-action-md",
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {children}
    </Link>
  );
}

/* -------------------------------------------------------------------------- */
/* Camps de formulari                                                          */
/* -------------------------------------------------------------------------- */

const CONTROL_CLASS =
  "ui-control rounded-control border border-border-strong bg-surface px-3 text-body text-ink shadow-control transition-colors duration-150 " +
  "placeholder:text-faint focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20 " +
  "disabled:cursor-not-allowed disabled:bg-subtle disabled:text-muted";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(CONTROL_CLASS, "ui-control-single", className)} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(CONTROL_CLASS, "py-2 leading-relaxed", className)} />;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: Array<{ value: string; label: string }>;
  placeholder?: string;
}

export function Select({ options, placeholder, className, ...props }: SelectProps) {
  return (
    <select {...props} className={cn(CONTROL_CLASS, "ui-control-single pr-8", className)}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-[13px] leading-5 font-medium text-ink">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </span>
      {children}
      {hint && !error && <span className="text-caption text-muted">{hint}</span>}
      {error && <span className="text-caption text-danger">{error}</span>}
    </label>
  );
}

export function Checkbox({
  label,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className={cn("flex cursor-pointer items-center gap-2.5 text-body text-ink", className)}>
      <input
        type="checkbox"
        {...props}
        className="h-4 w-4 shrink-0 rounded border-border-strong accent-brand disabled:cursor-not-allowed"
      />
      {label}
    </label>
  );
}

/* -------------------------------------------------------------------------- */
/* Superficies                                                                 */
/* -------------------------------------------------------------------------- */

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section
      className={cn(
        "rounded-card border border-border bg-surface shadow-card",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div className="min-w-0">
        <h2 className="text-section font-semibold text-ink">{title}</h2>
        {description && <p className="mt-1 text-[13px] leading-5 text-muted">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        <h1 className="text-page font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1.5 text-body leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* Estat                                                                       */
/* -------------------------------------------------------------------------- */

type BadgeTone = "neutral" | "brand" | "success" | "warning" | "danger";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-subtle text-muted",
  brand: "bg-brand-soft text-brand-strong",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
};

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption font-medium ring-1 ring-current/10 ring-inset",
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Carregant"
      className={cn(
        "ui-spinner inline-block shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent",
        className,
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-skeleton rounded-control bg-subtle", className)} />;
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-section font-semibold text-ink">{title}</p>
      {description && <p className="max-w-md text-body leading-6 text-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-control border border-danger/25 bg-danger-soft px-4 py-3">
      <p className="min-w-0 text-body break-words text-danger">{message}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Reintenta
        </Button>
      )}
    </div>
  );
}

/**
 * Resultat d'una acció que ha anat bé.
 *
 * Existeix perquè una acció que retorna 200 i no canvia res a la pantalla és
 * indistingible d'una que ha fallat: qui l'ha premuda es queda sense saber si
 * el problema és seu o del sistema. Va passar amb «Reconcilia amb l'anàlisi»,
 * que era literalment el botó que un avís et demanava prémer.
 *
 * `tone="info"` per al cas neutre —s'ha fet i no hi havia res a fer— i
 * `tone="success"` quan alguna cosa ha canviat de veritat.
 */
export function ResultNotice({
  message,
  detail,
  tone = "info",
  onDismiss,
}: {
  message: string;
  detail?: string;
  tone?: "info" | "success";
  onDismiss?: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 rounded-control border px-4 py-3",
        tone === "success"
          ? "border-success/25 bg-success-soft"
          : "border-border bg-subtle",
      )}
    >
      <div className="min-w-0">
        <p className={cn("text-body font-medium", tone === "success" ? "text-success" : "text-ink")}>
          {message}
        </p>
        {detail && <p className="mt-1 text-[13px] leading-5 text-muted">{detail}</p>}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="-my-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-lg text-muted transition-colors hover:bg-surface/70 hover:text-ink"
          aria-label="Tanca l'avís"
        >
          ×
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Taules                                                                      */
/* -------------------------------------------------------------------------- */

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-body">{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "border-b border-border bg-canvas/70 px-5 py-3 text-left text-caption font-semibold text-muted",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <td className={cn("border-b border-border px-5 py-3.5 align-middle text-ink", className)}>
      {children}
    </td>
  );
}

export function Tr({ children, className }: { children: ReactNode; className?: string }) {
  return <tr className={cn("transition-colors hover:bg-subtle/70 [&:last-child>td]:border-b-0", className)}>{children}</tr>;
}
