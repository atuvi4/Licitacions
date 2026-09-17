"use client";

import { useState, type ReactNode } from "react";

import { EvidencePanel } from "@/components/EvidencePanel";
import { Button, DataTable, EmptyState, PageSkeleton, type DataTableColumn } from "@/components/ui";
import type { DocumentCategory, LinkedEntityType, RelationshipType } from "@/lib/types";

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Amaga la columna a tauleta (secundària). */
  hideBelow?: "md" | "lg";
}

interface EvidenceConfig {
  entityType: LinkedEntityType;
  defaultRelationship: RelationshipType;
  defaultCategory: DocumentCategory;
  description?: string;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  loading?: boolean;
  /** Cap buit sense sortida: l'acció és obligatòria i real (crear, pujar…). */
  empty: { title: string; description?: string; action: ReactNode };
  onEdit?: (row: T) => void;
  onDelete?: (row: T) => void;
  /** Si s'informa, cada fila es pot desplegar per gestionar-ne les evidències. */
  evidence?: EvidenceConfig;
  extraActions?: (row: T) => ReactNode;
}

const ACTION = "min-h-11 md:min-h-0";

/**
 * Taula de recurs amb accions i, opcionalment, panell d'evidències desplegable.
 *
 * Centralitza el patró que comparteixen certificacions, registres,
 * assegurances i titulacions: una fila de dades i, a sota, els documents que
 * l'acrediten. Sobre `DataTable`: sota 768 px cada fila és una targeta.
 */
export function ResourceTable<T>({
  rows,
  columns,
  rowKey,
  loading = false,
  empty,
  onEdit,
  onDelete,
  evidence,
  extraActions,
}: Props<T>) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (loading && rows.length === 0) {
    return <PageSkeleton variant="table" columns={[...columns.map((column) => column.header), "Accions"]} />;
  }

  const tableColumns: Array<DataTableColumn<T>> = [
    ...columns.map((column, index) => ({
      key: `${index}-${column.header}`,
      header: column.header,
      hideBelow: column.hideBelow,
      cell: (row: T) => <div className={column.className}>{column.cell(row)}</div>,
    })),
    {
      key: "actions",
      header: "Accions",
      align: "right" as const,
      cell: (row: T) => {
        const key = rowKey(row);
        const isOpen = expanded === key;
        return (
          <div className="flex flex-wrap justify-end gap-1">
            {extraActions?.(row)}
            {evidence && (
              <Button size="sm" variant="ghost" className={ACTION} aria-expanded={isOpen} onClick={() => setExpanded(isOpen ? null : key)}>
                {isOpen ? "Amaga" : "Evidències"}
              </Button>
            )}
            {onEdit && (
              <Button size="sm" variant="ghost" className={ACTION} onClick={() => onEdit(row)}>
                Edita
              </Button>
            )}
            {onDelete && (
              <Button size="sm" variant="ghost" className={ACTION} onClick={() => onDelete(row)}>
                Elimina
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <DataTable
      columns={tableColumns}
      rows={rows}
      rowKey={rowKey}
      stackBelow="md"
      emptyState={<EmptyState title={empty.title} description={empty.description} action={empty.action} />}
      expandedRow={
        evidence
          ? (row) =>
              expanded === rowKey(row) ? (
                <EvidencePanel
                  entityType={evidence.entityType}
                  entityId={rowKey(row)}
                  defaultRelationship={evidence.defaultRelationship}
                  defaultCategory={evidence.defaultCategory}
                  description={evidence.description}
                />
              ) : null
          : undefined
      }
    />
  );
}
