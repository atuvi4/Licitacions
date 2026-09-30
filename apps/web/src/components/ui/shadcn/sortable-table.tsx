"use client";

/**
 * Taula amb ordenació, cerca i paginació: el patró «data table» de shadcn/ui
 * sobre TanStack Table v8, amb els primitius `Table` d'aspecte Tuvi.
 *
 * Tot passa al client: és per a llistats d'administració (empreses, usuaris)
 * que caben sencers a la pàgina. Un llistat gran ha de paginar al servidor.
 */

import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Button } from "../Button";
import { cn } from "../cn";
import { Input } from "../Form";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";

export type { ColumnDef } from "@tanstack/react-table";

/** `meta: { align: "right" }` a una columna: xifres alineades a la dreta. */
function alignClass(meta: unknown): string | undefined {
  return (meta as { align?: string } | undefined)?.align === "right" ? "text-right tabular-nums" : undefined;
}

export interface SortableTableProps<T> {
  columns: Array<ColumnDef<T, unknown>>;
  data: T[];
  /** Nom accessible de la taula. */
  label: string;
  /** Si hi és, apareix una cerca sobre totes les columnes. */
  searchPlaceholder?: string;
  pageSize?: number;
  initialSorting?: SortingState;
  getRowId?: (row: T) => string;
  emptyState?: ReactNode;
  /** Text inicial de la cerca (p. ex. el que arriba per la URL). */
  initialSearch?: string;
}

const SORT_LABEL = { asc: "ascendent", desc: "descendent" } as const;

export function SortableTable<T>({
  columns,
  data,
  label,
  searchPlaceholder,
  pageSize = 20,
  initialSorting = [],
  getRowId,
  emptyState = "Cap resultat.",
  initialSearch = "",
}: SortableTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [globalFilter, setGlobalFilter] = useState(initialSearch);
  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageIndex: 0, pageSize } },
  });

  const rows = table.getRowModel().rows;
  const filteredCount = table.getFilteredRowModel().rows.length;
  const pageCount = Math.max(1, table.getPageCount());
  const pageIndex = table.getState().pagination.pageIndex;

  return (
    <div className="flex flex-col">
      {searchPlaceholder && (
        <div className="border-b border-border px-4 py-3">
          <Input
            type="search"
            aria-label={searchPlaceholder}
            placeholder={searchPlaceholder}
            value={globalFilter}
            onChange={(event) => {
              setGlobalFilter(event.target.value);
              table.setPageIndex(0);
            }}
            className="max-w-sm"
          />
        </div>
      )}
      <Table aria-label={label}>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                const title = header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <TableHead
                    key={header.id}
                    className={alignClass(header.column.columnDef.meta)}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : canSort ? "none" : undefined}
                  >
                    {canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="-mx-1 inline-flex items-center gap-1.5 rounded-[4px] px-1 uppercase hover:text-ink focus-visible:outline-2 focus-visible:outline-brand"
                        title={sorted ? `Ordenat ${SORT_LABEL[sorted]}` : "Ordena"}
                      >
                        {title}
                        {sorted === "asc" ? (
                          <ArrowUp size={12} aria-hidden />
                        ) : sorted === "desc" ? (
                          <ArrowDown size={12} aria-hidden />
                        ) : (
                          <ArrowUpDown size={12} aria-hidden className="opacity-50" />
                        )}
                      </button>
                    ) : (
                      title
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="py-6 text-center text-muted">
                {emptyState}
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className={alignClass(cell.column.columnDef.meta)}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2.5 text-[13px] text-muted">
        <span className="tabular-nums">
          {filteredCount} {filteredCount === 1 ? "resultat" : "resultats"}
        </span>
        {pageCount > 1 && (
          <div className="flex items-center gap-2">
            <span className={cn("tabular-nums")}>
              Pàgina {pageIndex + 1} de {pageCount}
            </span>
            <Button size="sm" variant="secondary" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
              Anterior
            </Button>
            <Button size="sm" variant="secondary" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
              Següent
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
