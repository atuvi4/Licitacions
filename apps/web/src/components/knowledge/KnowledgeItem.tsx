"use client";

import { useState, type ReactNode } from "react";

import { EvidencePanel } from "@/components/EvidencePanel";
import { useCompanyPath } from "@/components/SessionProvider";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { EVIDENCE_TARGETS, type EffectiveState, type RecordKind } from "@/lib/knowledge";

import { StatePills } from "./StatePills";

const ACTION = "min-h-11 md:min-h-0";

interface ItemProps {
  kind: RecordKind;
  recordId: string;
  title: string;
  subtitle?: ReactNode;
  state: EffectiveState;
  /** Contingut propi de la dada (abast, papers, què hi falta). */
  children?: ReactNode;
  /** Accions pròpies (Edita, Edita l'abast…), abans d'Evidència i Verifica. */
  actions?: ReactNode;
  /** Pot posar o treure evidència. Si no, el panell és de lectura. */
  canEdit: boolean;
  canVerify: boolean;
  /** Ha canviat l'evidència o la verificació: cal rellegir la fotografia. */
  onChanged: () => void;
}

/**
 * Una dada de coneixement actual amb el seu camí d'acreditació: veure o afegir
 * l'evidència (Documents + enllaços, el mateix panell de tota l'app) i, quan
 * n'hi ha, verificar-la. Si es demana verificar sense evidència, l'API ho
 * refusa (`evidence_required`) i aquí s'explica i s'obre el panell.
 */
export function KnowledgeItem({ kind, recordId, title, subtitle, state, children, actions, canEdit, canVerify, onChanged }: ItemProps) {
  const path = useCompanyPath();
  const target = EVIDENCE_TARGETS[kind];
  const counts = state.state === "verified" || state.state === "declared";
  const [open, setOpen] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function verify() {
    setVerifying(true);
    setError(null);
    try {
      await api.post(path(`/knowledge/records/${kind}/${recordId}/verify`), {});
      onChanged();
    } catch (caught) {
      // Sense `instanceof`: les proves que simulen `@/lib/api` no exporten la classe.
      const code = caught && typeof caught === "object" ? (caught as { code?: unknown }).code : undefined;
      if (code === "evidence_required") {
        setError("Per verificar cal evidència enllaçada. Puja o vincula primer el document que ho acredita.");
        setOpen(true);
      } else {
        setError(caught instanceof Error ? caught.message : "No s'ha pogut verificar.");
      }
    } finally {
      setVerifying(false);
    }
  }

  return (
    <li className="border-b border-border last:border-b-0" data-record={recordId}>
      <div className="flex flex-col gap-3 px-5 py-3.5 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <p className="text-body font-medium text-ink">{title}</p>
            <StatePills value={state} />
          </div>
          {subtitle && <p className="mt-0.5 text-caption text-muted">{subtitle}</p>}
          {children}
          {error && (
            <p role="alert" className="mt-2 text-caption text-danger">
              {error}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1 md:justify-end">
          {actions}
          {target && counts && (
            <Button size="sm" variant="ghost" className={ACTION} aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Amaga l'evidència" : canEdit && state.flags.includes("evidence_missing") ? "Afegeix evidència" : "Evidència"}
            </Button>
          )}
          {target && state.state === "declared" && (
            <Button
              size="sm"
              variant="secondary"
              className={ACTION}
              loading={verifying}
              disabled={!canVerify}
              title={canVerify ? undefined : "El teu rol no pot verificar"}
              onClick={() => void verify()}
            >
              Verifica
            </Button>
          )}
        </div>
      </div>
      {target && open && (
        <div className="border-t border-border bg-subtle/50">
          <EvidencePanel
            entityType={target.entityType}
            entityId={recordId}
            defaultRelationship={target.relationship}
            defaultCategory={target.category}
            title={`Evidència de «${title}»`}
            description="Qualsevol document enllaçat compta com a evidència. Verificar-la és dir que acredita aquesta dada."
            onChange={onChanged}
            readOnly={!canEdit}
          />
        </div>
      )}
    </li>
  );
}

interface GapProps {
  title: string;
  subtitle?: ReactNode;
  state: EffectiveState;
  children?: ReactNode;
  actions?: ReactNode;
  /** Quan la fila és un candidat de l'històric: l'observació de què parla. */
  observationId?: string;
}

/** Una clau sense dada actual: només la diu l'històric, o no se'n sap res. */
export function KnowledgeGap({ title, subtitle, state, children, actions, observationId }: GapProps) {
  return (
    <li className="border-b border-border last:border-b-0" data-gap={state.state} data-observation={observationId}>
      <div className="flex flex-col gap-3 px-5 py-3.5 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <p className="text-body font-medium text-ink">{title}</p>
            <StatePills value={state} />
          </div>
          {subtitle && <p className="mt-0.5 text-caption text-muted">{subtitle}</p>}
          {children}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-1 md:justify-end">{actions}</div>}
      </div>
    </li>
  );
}
