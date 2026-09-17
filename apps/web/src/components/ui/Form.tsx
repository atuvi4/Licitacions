import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import { cn } from "./cn";

/* Camps de formulari, tal com eren: només canvien els valors dels tokens
   (cantonades quadrades, cap ombra, fons del canvas amb vora hairline). */

const CONTROL_CLASS =
  "ui-control rounded-control border border-border-strong bg-surface px-3 text-body text-ink shadow-control transition-colors duration-150 max-sm:min-h-11 " +
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
        className="h-4 w-4 shrink-0 rounded-none border-border-strong accent-brand disabled:cursor-not-allowed"
      />
      {label}
    </label>
  );
}
