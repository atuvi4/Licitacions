/**
 * Primitius Table de shadcn/ui amb l'aspecte del `DataTable` Tuvi: capçalera
 * condensada en caixa alta sobre `subtle`, files separades per vora subtil i
 * cel·les amb el mateix farciment.
 */

import type { ComponentProps } from "react";

import { cn } from "../cn";

export const TABLE_KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="scroll-thin overflow-x-auto">
      <table data-slot="table" className={cn("w-full border-collapse text-body", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={className} {...props} />;
}

export function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody data-slot="table-body" className={className} {...props} />;
}

export function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn("border-b border-border last:border-b-0 data-[state=selected]:bg-brand-soft", className)}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      scope="col"
      className={cn("border-b border-border bg-subtle px-4 py-2.5 text-left", TABLE_KICKER, className)}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn("px-4 py-3 align-top text-body text-ink", className)} {...props} />;
}

export function TableCaption({ className, ...props }: ComponentProps<"caption">) {
  return <caption data-slot="table-caption" className={cn("mt-3 text-caption text-muted", className)} {...props} />;
}
