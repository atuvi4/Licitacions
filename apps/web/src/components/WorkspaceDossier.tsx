"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, CardHeader, Select, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { RESULT_ICONS, RESULT_LABELS, type AssessmentResult } from "@/lib/compliance";
import { formatDate } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import {
  COVERAGE_LABELS,
  COVERAGE_TONES,
  EVIDENCE_STATUS_LABELS,
  EVIDENCE_STATUS_TONES,
  GROUP_LABELS,
  READINESS_BLOCKER_LABELS,
  READINESS_LABELS,
  READINESS_TONES,
  VALIDITY_LABELS,
  VALIDITY_TONES,
  type Dossier,
  type DossierItem,
  type DossierSyncResult,
  type EvidenceItemStatus,
  type WorkspaceDetail,
} from "@/lib/workspace";

interface CompanyDocument {
  id: string;
  name: string;
}

/**
 * El dossier documental.
 *
 * La columna que importa i que no existeix enlloc més: **Compliment** i
 * **Dossier** de costat. Un requisit pot estar en «Compleix» i tenir el dossier
 * en «Falta document»; això no és una contradicció, és el motiu pel qual
 * aquesta pantalla existeix. Saber que facturem prou no vol dir tenir els
 * comptes anuals a la carpeta.
 *
 * Res d'aquí tria cap document sol: un «Proposat» ho és fins que algú prem
 * «Selecciona», i llavors queda escrit qui i quan.
 */
export function DossierSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const base = `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [documents, setDocuments] = useState<CompanyDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const mutation = useMutation();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDossier(await api.get<Dossier>(`${base}/dossier`));
      setDocuments(await api.get<CompanyDocument[]>(`/companies/${workspace.company_id}/documents`));
    } finally {
      setLoading(false);
    }
  }, [base, workspace.company_id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function sync() {
    const ok = await mutation.run(async () => {
      const result = await api.post<DossierSyncResult>(`${base}/dossier/sync`, {});
      setDossier(result.dossier);
    });
    if (ok) await onChange();
  }

  async function patch(itemId: string, body: Record<string, unknown>) {
    const ok = await mutation.run(async () => {
      setDossier(await api.patch<Dossier>(`${base}/dossier/${itemId}`, body));
    });
    if (ok) await onChange();
  }

  async function createTask(itemId: string) {
    const ok = await mutation.run(async () => {
      await api.post(`${base}/dossier/${itemId}/task`, {});
    });
    if (ok) await onChange();
  }

  async function advance() {
    const ok = await mutation.run(async () => {
      await api.post(`${base}/readiness/advance`, {});
    });
    if (ok) {
      await load();
      await onChange();
    }
  }

  if (loading && !dossier) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (!dossier) return null;

  const { coverage, readiness } = dossier;

  return (
    <Card>
      <CardHeader
        title="Dossier documental"
        description="Amb quins documents acreditarem cada requisit. No és el compliment."
        action={
          <Button size="sm" onClick={() => void sync()} loading={mutation.saving}>
            Actualitza propostes
          </Button>
        }
      />

      {/* --------------------------------------------------- Capçalera --- */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-5 py-4">
        <div>
          <p className="text-[12px] uppercase tracking-wide text-muted">Readiness</p>
          <Badge tone={READINESS_TONES[readiness.state]} className="mt-1">
            {READINESS_LABELS[readiness.state]}
          </Badge>
        </div>
        <p className="text-sm text-ink">
          <span className="font-semibold tabular-nums">
            {coverage.covered}/{coverage.mandatory_total}
          </span>{" "}
          requisits obligatoris coberts
        </p>
        {coverage.missing_document > 0 && (
          <p className="text-sm text-danger">
            {coverage.missing_document} document{coverage.missing_document === 1 ? "" : "s"} que
            falta{coverage.missing_document === 1 ? "" : "n"}
          </p>
        )}
        {coverage.needs_review > 0 && (
          <p className="text-sm text-warning">{coverage.needs_review} per revisar</p>
        )}
        {coverage.expired_documents > 0 && (
          <p className="text-sm text-danger">
            {coverage.expired_documents} caduca{coverage.expired_documents === 1 ? "" : "n"} abans
            del termini
          </p>
        )}
      </div>

      {readiness.blockers.length > 0 && (
        <div className="border-b border-border px-5 py-4">
          <p className="text-[13px] font-semibold text-muted">Què falta per a la revisió final</p>
          <ul className="mt-1.5 space-y-1">
            {readiness.blockers.map((code) => (
              <li key={code} className="text-[13px] text-ink">
                • {READINESS_BLOCKER_LABELS[code] ?? code}
              </li>
            ))}
          </ul>
        </div>
      )}

      {readiness.state === "ready_for_final_review" &&
        workspace.status !== "ready_for_final_review" && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-success-soft px-5 py-4">
            <p className="text-[13px] text-ink">
              El dossier sembla complet. Passar-lo a revisió final <strong>no</strong> presenta
              res: només diu que ja es pot revisar.
            </p>
            <Button variant="primary" size="sm" onClick={() => void advance()} loading={mutation.saving}>
              Passa a revisió final
            </Button>
          </div>
        )}

      {mutation.error && <p className="px-5 py-3 text-xs text-danger">{mutation.error}</p>}

      {/* -------------------------------------------------------- Grups --- */}
      {dossier.groups.length === 0 ? (
        <p className="px-5 py-6 text-[13px] text-muted">
          Encara no hi ha cap peça. Si el compliment ja està calculat, prem «Actualitza
          propostes».
        </p>
      ) : (
        dossier.groups.map((group) => (
          <section key={group.group} className="border-b border-border last:border-b-0">
            <h3 className="bg-subtle px-5 py-2 text-[12px] font-semibold uppercase tracking-wide text-muted">
              {GROUP_LABELS[group.group] ?? group.group}
            </h3>
            <ul className="divide-y divide-border">
              {group.items.map((item) => (
                <DossierRow
                  key={item.id}
                  item={item}
                  workspace={workspace}
                  documents={documents}
                  saving={mutation.saving}
                  onPatch={patch}
                  onCreateTask={createTask}
                />
              ))}
            </ul>
          </section>
        ))
      )}
    </Card>
  );
}

function DossierRow({
  item,
  workspace,
  documents,
  saving,
  onPatch,
  onCreateTask,
}: {
  item: DossierItem;
  workspace: WorkspaceDetail;
  documents: CompanyDocument[];
  saving: boolean;
  onPatch: (itemId: string, body: Record<string, unknown>) => Promise<void>;
  onCreateTask: (itemId: string) => Promise<void>;
}) {
  const result = item.assessment_result as AssessmentResult | null;

  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink">
            {item.requirement_title ?? item.label}
            {item.mandatory && (
              <Badge tone="warning" className="ml-2">
                Obligatori
              </Badge>
            )}
          </p>
          {item.requirement_quote && (
            <p className="mt-1 line-clamp-2 text-[12px] italic text-faint">
              “{item.requirement_quote}”
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Les dues respostes, de costat i sense fusionar-les. */}
          {result && (
            <Badge tone="neutral">
              Compliment: {RESULT_ICONS[result]} {RESULT_LABELS[result]}
            </Badge>
          )}
          {item.coverage && (
            <Badge tone={COVERAGE_TONES[item.coverage]}>
              Dossier: {COVERAGE_LABELS[item.coverage]}
            </Badge>
          )}
        </div>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <div>
          <p className="text-[12px] text-muted">Document</p>
          {item.document ? (
            <p className="mt-0.5 text-[13px] text-ink">
              {item.document.name}
              <span className="ml-2 text-[12px] text-faint">
                {item.document.expires_at
                  ? `caduca ${formatDate(item.document.expires_at)}`
                  : "sense caducitat"}
              </span>
              <Badge tone={VALIDITY_TONES[item.validity]} className="ml-2">
                {VALIDITY_LABELS[item.validity]}
              </Badge>
            </p>
          ) : (
            <p className="mt-0.5 text-[13px] text-danger">Cap document seleccionat</p>
          )}
          {item.selected_by_name && item.selected_at && (
            <p className="mt-0.5 text-[12px] text-faint">
              Triat per {item.selected_by_name} · {formatDate(item.selected_at)}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={EVIDENCE_STATUS_TONES[item.status]}>
            {EVIDENCE_STATUS_LABELS[item.status]}
          </Badge>
          <Select
            className="w-44"
            value=""
            placeholder="Canvia document"
            options={documents.map((document) => ({ value: document.id, label: document.name }))}
            onChange={(event) =>
              event.target.value &&
              void onPatch(item.id, { document_id: event.target.value, status: "selected" })
            }
          />
          <Select
            className="w-40"
            value={item.status}
            options={(Object.keys(EVIDENCE_STATUS_LABELS) as EvidenceItemStatus[]).map(
              (value) => ({ value, label: EVIDENCE_STATUS_LABELS[value] }),
            )}
            onChange={(event) => void onPatch(item.id, { status: event.target.value })}
          />
          {(item.status === "missing" || !item.document) && (
            <Button size="sm" onClick={() => void onCreateTask(item.id)} disabled={saving}>
              Crea tasca
            </Button>
          )}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-3 text-[12px]">
        <Link
          href={`/licitacions/${workspace.tender_id}`}
          className="text-brand hover:underline"
        >
          Veure font del plec
        </Link>
        <Link
          href={`/licitacions/${workspace.tender_id}#compliment`}
          className="text-brand hover:underline"
        >
          Veure compliment
        </Link>
        {item.document && (
          <Link href="/documents" className="text-brand hover:underline">
            Veure document de l&apos;empresa
          </Link>
        )}
      </div>
    </li>
  );
}
