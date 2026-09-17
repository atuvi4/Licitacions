"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "./cn";

export type RowTint = "blocking" | "attention";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  width?: string;
  cell: (row: T) => ReactNode;
  align?: "left" | "right";
  /** Amaga la columna sota el punt de tall (columnes secundàries a tauleta). */
  hideBelow?: "md" | "lg";
}

export interface DataTableGroup<T> {
  key: string;
  label: ReactNode;
  rows: T[];
  collapsed?: boolean;
  tint?: RowTint;
}

export interface DataTableProps<T> {
  columns: Array<DataTableColumn<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  /** Si hi ha grups, manen sobre `rows`. */
  groups?: Array<DataTableGroup<T>>;
  rowTint?: (row: T) => RowTint | undefined;
  isSelected?: (row: T) => boolean;
  /** Frase-resum: «N licitacions · pàgina X de Y». */
  footer?: ReactNode;
  onRowClick?: (row: T) => void;
  /** Sota el punt de tall cada fila es torna una targeta amb rètol + valor. */
  stackBelow?: "md";
  /** Es mostra dins de la taula quan no hi ha cap fila. */
  emptyState?: ReactNode;
  /** Contingut que s'obre sota una fila (evidències, detall). `null` = res. */
  expandedRow?: (row: T) => ReactNode | null;
  className?: string;
}

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const TINT_CLASS: Record<RowTint, string> = {
  blocking: "bg-row-blocking",
  attention: "bg-row-attention",
};
const HIDE_CLASS = { md: "hidden md:table-cell", lg: "hidden lg:table-cell" } as const;

/**
 * Un patró per a tots els llistats (handoff §2.12): capçalera condensada en
 * caixa alta, files amb nom (500) i motiu (13), una StatusPill, acció a la
 * dreta, grups amb fila de capçalera, selecció accent-100 i peu amb total.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  groups,
  rowTint,
  isSelected,
  footer,
  onRowClick,
  stackBelow,
  emptyState,
  expandedRow,
  className,
}: DataTableProps<T>) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() =>
    Object.fromEntries((groups ?? []).map((group) => [group.key, Boolean(group.collapsed)])),
  );
  const stacked = stackBelow === "md";
  const visibleColumns = columns.length;

  function renderRow(row: T, groupTint?: RowTint) {
    const tint = rowTint?.(row) ?? groupTint;
    const selected = isSelected?.(row) ?? false;
    const clickable = Boolean(onRowClick);
    const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>) => {
      if (!onRowClick) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onRowClick(row);
      }
    };
    const extra = expandedRow?.(row) ?? null;
    const mainRow = (
      <tr
        key={rowKey(row)}
        data-tint={tint}
        data-selected={selected ? "true" : undefined}
        tabIndex={clickable ? 0 : undefined}
        onClick={clickable ? () => onRowClick?.(row) : undefined}
        onKeyDown={clickable ? onKeyDown : undefined}
        className={cn(
          "border-b border-border last:border-b-0",
          selected ? "bg-brand-soft" : tint ? TINT_CLASS[tint] : undefined,
          clickable && "cursor-pointer hover:bg-subtle focus-visible:outline-2 focus-visible:outline-brand",
          stacked && "max-md:block max-md:px-3 max-md:py-2.5",
        )}
      >
        {columns.map((column) => (
          <td
            key={column.key}
            className={cn(
              "px-3 py-2.5 align-top text-body text-ink",
              column.align === "right" && "text-right tabular-nums",
              column.hideBelow && HIDE_CLASS[column.hideBelow],
              stacked && "max-md:flex max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1 max-md:text-left",
            )}
          >
            {stacked && <span className={cn(KICKER, "md:hidden")}>{column.header}</span>}
            {column.cell(row)}
          </td>
        ))}
      </tr>
    );
    if (!extra) return mainRow;
    return [
      mainRow,
      <tr key={`${rowKey(row)}-expanded`} data-expanded className={cn("border-b border-border", stacked && "max-md:block")}>
        <td colSpan={visibleColumns} className={cn("bg-canvas p-0", stacked && "max-md:block")}>
          {extra}
        </td>
      </tr>,
    ];
  }

  const hasRows = groups ? groups.some((group) => group.rows.length > 0) : rows.length > 0;

  return (
    <div className={cn("scroll-thin overflow-x-auto", className)}>
      <table className={cn("w-full border-collapse text-body", stacked && "max-md:block [&>tbody]:max-md:block")}>
        <thead className={cn(stacked && "max-md:hidden")}>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                style={column.width ? { width: column.width } : undefined}
                className={cn(
                  "border-b border-border-strong bg-subtle px-3 py-2 text-left",
                  KICKER,
                  column.align === "right" && "text-right",
                  column.hideBelow && HIDE_CLASS[column.hideBelow],
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {!hasRows && emptyState && (
            <tr>
              <td colSpan={visibleColumns} className="p-0">
                {emptyState}
              </td>
            </tr>
          )}
          {groups
            ? groups.map((group) => {
                const isCollapsed = collapsed[group.key] ?? false;
                return [
                  <tr key={`group-${group.key}`} className={cn("border-b border-border bg-subtle", stacked && "max-md:block")}>
                    <td colSpan={visibleColumns} className="px-3 py-1.5">
                      <button
                        type="button"
                        aria-expanded={!isCollapsed}
                        onClick={() => setCollapsed((current) => ({ ...current, [group.key]: !isCollapsed }))}
                        className="flex min-h-11 w-full items-center gap-2.5 text-left md:min-h-0"
                      >
                        <span className={KICKER}>
                          <span>{group.label}</span> · <span className="tabular-nums">{group.rows.length}</span>
                        </span>
                        <span className="text-caption text-muted">{isCollapsed ? "plegats · desplega" : "plega"}</span>
                      </button>
                    </td>
                  </tr>,
                  ...(isCollapsed ? [] : group.rows.map((row) => renderRow(row, group.tint))),
                ];
              })
            : rows.map((row) => renderRow(row))}
        </tbody>
      </table>
      {footer && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-[13px] text-muted">
          {footer}
        </div>
      )}
    </div>
  );
}
