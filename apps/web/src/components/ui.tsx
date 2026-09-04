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
  primary: "bg-brand text-white hover:bg-brand-strong border-transparent",
  secondary: "bg-surface text-ink hover:bg-subtle border-border-strong",
  ghost: "bg-transparent text-muted hover:bg-subtle hover:text-ink border-transparent",
  danger: "bg-danger text-white hover:opacity-90 border-transparent",
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
        "inline-flex items-center justify-center gap-2 rounded-md border font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-55",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-3.5 text-sm",
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
        "inline-flex items-center justify-center gap-2 rounded-md border font-medium transition-colors",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-3.5 text-sm",
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
  "w-full rounded-md border border-border-strong bg-surface px-3 text-sm text-ink " +
  "placeholder:text-faint focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20 " +
  "disabled:bg-subtle disabled:text-muted";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(CONTROL_CLASS, "h-9", className)} />;
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
    <select {...props} className={cn(CONTROL_CLASS, "h-9 pr-8", className)}>
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
      <span className="text-[13px] font-medium text-ink">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </span>
      {children}
      {hint && !error && <span className="text-xs text-faint">{hint}</span>}
      {error && <span className="text-xs text-danger">{error}</span>}
    </label>
  );
}

export function Checkbox({
  label,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className={cn("flex cursor-pointer items-center gap-2 text-sm text-ink", className)}>
      <input
        type="checkbox"
        {...props}
        className="h-4 w-4 rounded border-border-strong accent-brand"
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
        "rounded-[var(--radius-card)] border border-border bg-surface shadow-[0_1px_2px_rgba(16,24,40,0.04)]",
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
    <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
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
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
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
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
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
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent",
        className,
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-skeleton rounded bg-subtle", className)} />;
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-5">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
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
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-[13px] text-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-danger/25 bg-danger-soft px-4 py-3">
      <p className="text-[13px] text-danger">{message}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Reintenta
        </Button>
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
      <table className="w-full min-w-[640px] border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "border-b border-border px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-faint",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <td className={cn("border-b border-border px-5 py-3 align-middle text-ink", className)}>
      {children}
    </td>
  );
}

export function Tr({ children, className }: { children: ReactNode; className?: string }) {
  return <tr className={cn("hover:bg-subtle/60", className)}>{children}</tr>;
}
