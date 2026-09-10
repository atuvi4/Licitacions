"use client";

import { useCallback, useEffect, useState } from "react";

import { Modal } from "@/components/Modal";
import { Badge, Button, Card, CardHeader, Field, Skeleton, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatFileSize } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import { formatDateTime } from "@/lib/tenders";
import {
  PACKAGE_STATUS_LABELS,
  PACKAGE_STATUS_TONES,
  READINESS_BLOCKER_LABELS,
  STALE_REASON_LABELS,
  VALIDITY_LABELS,
  VALIDITY_TONES,
  deadlineLabel,
  deadlineTone,
  type FreezeResult,
  type PackageDetail,
  type PackageSummary,
  type WorkspaceDetail,
} from "@/lib/workspace";

/**
 * Revisió final: exactament què s'aprova.
 *
 * La pregunta que aquesta pantalla ha de respondre és «quin paquet estem
 * aprovant», i per això ensenya el `manifest_hash` i el SHA-256 de cada fitxer.
 * Un identificador de paquet no ho respondria: el que demostra que el contingut
 * no ha canviat és el hash.
 *
 * Aprovar aquí **no presenta res**. Ho diu el modal i ho diu la resposta de
 * l'API amb `is_submitted`, perquè no s'hagi de deduir.
 */
export function SubmissionPackageSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const root = `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
  const [revisions, setRevisions] = useState<PackageSummary[]>([]);
  const [current, setCurrent] = useState<PackageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [comment, setComment] = useState("");
  const mutation = useMutation();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.get<PackageSummary[]>(`${root}/packages`);
      setRevisions(list);
      setCurrent(
        list.length > 0 ? await api.get<PackageDetail>(`${root}/packages/${list[0].id}`) : null,
      );
    } finally {
      setLoading(false);
    }
  }, [root]);

  useEffect(() => {
    void load();
  }, [load]);

  async function freeze() {
    const ok = await mutation.run(async () => {
      const result = await api.post<FreezeResult>(`${root}/packages`, {});
      setCurrent(result.package);
    });
    if (ok) {
      await load();
      await onChange();
    }
  }

  async function approve() {
    if (!current) return;
    const ok = await mutation.run(async () => {
      await api.post(`${root}/packages/${current.id}/approvals`, {
        decision: "approved_for_submission",
        comment: comment.trim() || null,
      });
    });
    if (ok) {
      setConfirming(false);
      setComment("");
      await load();
      await onChange();
    }
  }

  if (loading && !current && revisions.length === 0) {
    return <Skeleton className="h-48 w-full" />;
  }

  return (
    <Card>
      <CardHeader
        title="Revisió final"
        description="Exactament quin paquet s'aprova per presentar. Aprovar-lo no és presentar-lo."
        action={
          <Button size="sm" onClick={() => void freeze()} loading={mutation.saving}>
            {revisions.length === 0 ? "Congela el paquet" : "Congela una revisió nova"}
          </Button>
        }
      />

      {mutation.error && <p className="px-5 py-3 text-[13px] text-danger">{mutation.error}</p>}

      {!current ? (
        <p className="px-5 py-6 text-[13px] text-muted">
          Encara no s&apos;ha congelat cap paquet. Quan el dossier estigui a punt, congela&apos;l
          per fixar exactament què es presentaria.
        </p>
      ) : (
        <>
          {/* ------------------------------------------------ Capçalera --- */}
          <div className="grid gap-4 border-b border-border px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-[12px] uppercase tracking-wide text-muted">Revisió</p>
              <p className="mt-1 text-lg font-semibold text-ink">
                #{current.revision_number}{" "}
                <Badge tone={PACKAGE_STATUS_TONES[current.status]}>
                  {PACKAGE_STATUS_LABELS[current.status]}
                </Badge>
              </p>
            </div>
            <div>
              <p className="text-[12px] uppercase tracking-wide text-muted">Manifest</p>
              <p className="mt-1 font-mono text-sm text-ink">{current.manifest_hash_short}…</p>
              <a
                href={`/bff${root}/packages/${current.id}/manifest`}
                className="text-[12px] text-brand hover:underline"
              >
                Descarrega manifest.json
              </a>
            </div>
            <div>
              <p className="text-[12px] uppercase tracking-wide text-muted">Documents</p>
              <p className="mt-1 text-lg font-semibold tabular-nums text-ink">
                {current.coverage.document_count}
              </p>
              <p className="text-[12px] text-faint">
                {current.coverage.mandatory_covered}/{current.coverage.mandatory_total} obligatoris
                coberts
              </p>
            </div>
            <div>
              <p className="text-[12px] uppercase tracking-wide text-muted">Termini</p>
              <p className="mt-1 text-sm text-ink">
                {current.deadline_at ? formatDateTime(current.deadline_at) : "—"}
              </p>
              {current.days_remaining !== null && (
                <Badge tone={deadlineTone(current.days_remaining)}>
                  {deadlineLabel(current.days_remaining)}
                </Badge>
              )}
            </div>
          </div>

          {/* --------------------------------------------------- Avisos --- */}
          {current.is_stale && (
            <div className="border-b border-border bg-warning-soft px-5 py-4">
              <p className="text-[13px] font-semibold text-warning">
                {current.approval
                  ? "Aquesta revisió està aprovada, però ja no es pot presentar"
                  : "Aquesta revisió ja no descriu el que hi ha ara"}
              </p>
              <ul className="mt-1 space-y-0.5">
                {current.stale_reasons.map((code) => (
                  <li key={code} className="text-[13px] text-ink">
                    • {STALE_REASON_LABELS[code] ?? code}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[12px] text-muted">
                Congela una revisió nova. L&apos;aprovació d&apos;aquesta, si en té, es conserva
                tal com era.
              </p>
            </div>
          )}
          {current.readiness_blockers.length > 0 && (
            <div className="border-b border-border px-5 py-4">
              <p className="text-[13px] font-semibold text-muted">Pendents</p>
              <ul className="mt-1 space-y-0.5">
                {current.readiness_blockers.map((code) => (
                  <li key={code} className="text-[13px] text-ink">
                    • {READINESS_BLOCKER_LABELS[code] ?? code}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* ------------------------------------------------ Documents --- */}
          <ul className="divide-y divide-border">
            {current.items.map((item) => (
              <li key={item.id} className="px-5 py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm text-ink">{item.filename}</p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {item.validity && (
                      <Badge tone={VALIDITY_TONES[item.validity]}>
                        {VALIDITY_LABELS[item.validity]}
                      </Badge>
                    )}
                    {item.changed_since_freeze && (
                      <Badge tone="danger">Canviat des del congelat</Badge>
                    )}
                  </div>
                </div>
                <p className="mt-0.5 text-[12px] text-faint">
                  {item.requirement_title ?? "Sense requisit associat"} ·{" "}
                  {formatFileSize(item.file_size)}
                  {item.document_expires_at && ` · caduca ${formatDate(item.document_expires_at)}`}
                </p>
                <p className="mt-0.5 font-mono text-[11px] text-faint">
                  sha256 {item.document_sha256.slice(0, 24)}…
                </p>
              </li>
            ))}
          </ul>

          {/* Un paquet de simulació es congela per poder provar aquesta
              pantalla, però no s'aprova mai: signar «això és exactament el que
              presentarem» sobre requisits que ningú ha tret de cap plec seria
              convertir una prova en un document amb aparença d'aprovat. */}
          {current.simulation_only && (
            <div className="border-t border-border bg-warning-soft px-5 py-3 text-[13px]">
              <p className="font-medium text-warning">
                Paquet de simulació — no es pot aprovar per presentar
              </p>
              <p className="mt-0.5 text-muted">
                L&apos;anàlisi que el sosté no surt de llegir cap plec
                {current.analysis_bases.length > 0 && (
                  <> ({current.analysis_bases.join(", ")})</>
                )}
                . Es pot congelar per provar el circuit, però l&apos;aprovació final
                queda reservada a una anàlisi real.
              </p>
            </div>
          )}

          {/* ------------------------------------------------ Aprovació --- */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-4">
            {current.approval ? (
              <div>
                <p className="text-[13px] text-ink">
                  <Badge tone="success">Aprovat per presentar</Badge>{" "}
                  {current.approval.approved_by_name} ·{" "}
                  {formatDateTime(current.approval.approved_at)}
                </p>
                {current.approval.comment && (
                  <p className="mt-1 text-[13px] text-muted">“{current.approval.comment}”</p>
                )}
                <p className="mt-1 font-mono text-[11px] text-faint">
                  manifest {current.approval.package_manifest_hash.slice(0, 24)}…
                </p>
              </div>
            ) : (
              <p className="text-[13px] text-muted">
                Cap persona no ha aprovat encara aquesta revisió.
              </p>
            )}
            {current.can_sign && !current.approval && (
              <Button
                variant="primary"
                onClick={() => setConfirming(true)}
                disabled={!current.can_approve}
                title={
                  current.simulation_only
                    ? "Un paquet de simulació no es pot aprovar per presentar."
                    : undefined
                }
              >
                Aprova per presentar
              </Button>
            )}
          </div>

          {/* Estat explícit: aprovat no és presentat. */}
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            Estat de la candidatura: <strong>{current.workspace_status}</strong> ·{" "}
            {current.is_submitted ? "presentada" : "encara NO s'ha presentat res"}
          </p>

          {revisions.length > 1 && (
            <div className="border-t border-border px-5 py-4">
              <p className="text-[13px] font-semibold text-muted">Historial de revisions</p>
              <ul className="mt-1.5 space-y-1">
                {revisions.map((revision) => (
                  <li key={revision.id} className="text-[13px] text-ink">
                    #{revision.revision_number} · {PACKAGE_STATUS_LABELS[revision.status]} ·{" "}
                    <span className="font-mono text-[11px]">{revision.manifest_hash_short}</span>
                    {revision.is_approved &&
                      ` · aprovada per ${revision.approved_by_name} el ${formatDate(
                        revision.approved_at,
                      )}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <Modal
        open={confirming}
        title={`Estàs aprovant exactament la revisió ${current?.revision_number} d'aquest paquet.`}
        description="Quedarà registrat qui aprova, quan i amb quin manifest."
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={mutation.saving}>
              Cancel·la
            </Button>
            <Button variant="primary" onClick={() => void approve()} loading={mutation.saving}>
              Aprova per presentar
            </Button>
          </>
        }
      >
        {current && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 rounded-md bg-subtle px-3 py-3 text-[13px]">
              <div>
                <dt className="text-muted">Documents</dt>
                <dd className="text-ink">{current.coverage.document_count}</dd>
              </div>
              <div>
                <dt className="text-muted">Manifest</dt>
                <dd className="font-mono text-ink">{current.manifest_hash_short}…</dd>
              </div>
              <div>
                <dt className="text-muted">Termini</dt>
                <dd className="text-ink">
                  {current.deadline_at ? formatDateTime(current.deadline_at) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Obligatoris coberts</dt>
                <dd className="text-ink">
                  {current.coverage.mandatory_covered}/{current.coverage.mandatory_total}
                </dd>
              </div>
            </dl>

            <p className="rounded-md border border-border bg-canvas px-3 py-2 text-[13px] text-muted">
              Aprovar aquest paquet <strong>no el presenta</strong>. Vol dir que aquesta és
              exactament la documentació que una persona dona per bona.
            </p>

            <Field label="Comentari">
              <Textarea
                rows={3}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Dossier revisat."
              />
            </Field>
          </div>
        )}
      </Modal>
    </Card>
  );
}
