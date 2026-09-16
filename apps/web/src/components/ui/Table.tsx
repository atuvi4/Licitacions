import type { ReactNode } from "react";

import { cn } from "./cn";

/* Primitives de taula dels consumidors anteriors. DataTable les embolcalla;
   la capçalera adopta el rètol condensat en caixa alta del sistema. */

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
        "border-b border-border-strong bg-subtle px-3 py-2 text-left font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cn("border-b border-border px-3 py-2.5 align-middle text-ink", className)}>{children}</td>;
}

export function Tr({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <tr className={cn("transition-colors hover:bg-subtle [&:last-child>td]:border-b-0", className)}>{children}</tr>
  );
}
