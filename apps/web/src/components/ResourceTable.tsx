"use client";

import { Fragment, useState, type ReactNode } from "react";

import { EvidencePanel } from "@/components/EvidencePanel";
import {
  Button,
  EmptyState,
  LoadingRows,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import type { DocumentCategory, LinkedEntityType, RelationshipType } from "@/lib/types";

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
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
  empty: { title: string; description?: string; action?: ReactNode };
  onEdit?: (row: T) => void;
  onDelete?: (row: T) => void;
  /** Si s'informa, cada fila es pot desplegar per gestionar-ne les evidències. */
  evidence?: EvidenceConfig;
  extraActions?: (row: T) => ReactNode;
}

/**
 * Taula de recurs amb accions i, opcionalment, panell d'evidències desplegable.
 *
 * Centralitza el patró que comparteixen certificacions, registres,
 * assegurances i titulacions: una fila de dades i, a sota, els documents que
 * l'acrediten.
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
  const actionCount = columns.length + 1;

  if (loading) return <LoadingRows />;
  if (rows.length === 0) {
    return <EmptyState title={empty.title} description={empty.description} action={empty.action} />;
  }

  return (
    <Table>
      <thead>
        <tr>
          {columns.map((column) => (
            <Th key={column.header} className={column.className}>
              {column.header}
            </Th>
          ))}
          <Th className="text-right">Accions</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const key = rowKey(row);
          const isOpen = expanded === key;
          return (
            <Fragment key={key}>
              <Tr>
                {columns.map((column) => (
                  <Td key={column.header} className={column.className}>
                    {column.cell(row)}
                  </Td>
                ))}
                <Td className="text-right whitespace-nowrap">
                  {extraActions?.(row)}
                  {evidence && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setExpanded(isOpen ? null : key)}
                    >
                      {isOpen ? "Amaga" : "Evidències"}
                    </Button>
                  )}
                  {onEdit && (
                    <Button size="sm" variant="ghost" onClick={() => onEdit(row)}>
                      Edita
                    </Button>
                  )}
                  {onDelete && (
                    <Button size="sm" variant="ghost" onClick={() => onDelete(row)}>
                      Elimina
                    </Button>
                  )}
                </Td>
              </Tr>
              {evidence && isOpen && (
                <tr>
                  <td colSpan={actionCount} className="border-b border-border bg-canvas/60 p-0">
                    <EvidencePanel
                      entityType={evidence.entityType}
                      entityId={key}
                      defaultRelationship={evidence.defaultRelationship}
                      defaultCategory={evidence.defaultCategory}
                      description={evidence.description}
                    />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </Table>
  );
}
