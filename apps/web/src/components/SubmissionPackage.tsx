"use client";

import { useCallback, useEffect, useState } from "react";

import { Modal } from "@/components/Modal";
import {
  Attribution,
  Banner,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  LinkButton,
  PageSkeleton,
  PermissionNote,
  StatusPill,
  Textarea,
  type DataTableColumn,
} from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatFileSize } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import { PACKAGE_STATUS_LEVEL, VALIDITY_LEVEL, deadlineLevel, whoCanDecide } from "@/lib/labels";
import { formatDateTime } from "@/lib/tenders";
import {
  PACKAGE_STATUS_LABELS,
  VALIDITY_LABELS,
  WORKSPACE_STATUS_LABELS,
  deadlineLabel,
  readinessBlockerLabel,
  staleReasonLabel,
  type FreezeResult,
  type PackageDetail,
  type PackageItem,
  type PackageSummary,
  type WorkspaceDetail,
} from "@/lib/workspace";

/** Longitud màxima del comentari d'aprovació al backend (`ApprovalRequest.comment`). */
const COMMENT_MAX_LENGTH = 4000;

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function shortHash(hash: string | null | undefined): string {
  return hash ? hash.slice(0, 8) : "—";
}

/**
 * Tancar (handoff L14, mockup 11): congelar una revisió immutable, revisar
 * exactament què conté, aprovar aquella revisió concreta i deixar clar que
 * **aprovar no és presentar**.
 *
 * Tot surt del contracte real de `PackageDetail`: `can_sign` (rol),
 * `can_approve` (congelat, dossier a punt, no obsolet, anàlisi real),
 * `is_stale` + `stale_reasons`, `simulation_only`, `approval` (immutable) i
 * `is_submitted`. Cap estat de negoci local; cap endpoint que no existeixi.
 */
export function SubmissionPackageSection({
  workspace,
  onChange,
  signerName,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
  /** Qui signaria l'aprovació: el nom real de la sessió. */
  signerName: string;
}) {
  const root = `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
  const [revisions, setRevisions] = useState<PackageSummary[]>([]);
  const [current, setCurrent] = useState<PackageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [comment, setComment] = useState("");
  const freezeMutation = useMutation();
  const approveMutation = useMutation();

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await api.get<PackageSummary[]>(`${root}/packages`);
      setRevisions(list);
      setCurrent(list.length > 0 ? await api.get<PackageDetail>(`${root}/packages/${list[0].id}`) : null);
    } catch (caught) {
      setLoadError(caught instanceof Error ? caught.message : "Error desconegut");
    } finally {
      setLoading(false);
    }
  }, [root]);

  useEffect(() => {
    void load();
  }, [load]);

  async function freeze() {
    const ok = await freezeMutation.run(async () => {
      await api.post<FreezeResult>(`${root}/packages`, {});
    });
    // Si falla, la revisió actual es queda tal com es veia.
    if (ok) {
      await load();
      await onChange();
    }
  }

  function openApproval() {
    approveMutation.reset();
    setComment("");
    setConfirming(true);
  }

  async function approve() {
    if (!current) return;
    const packageId = current.id;
    const ok = await approveMutation.run(async () => {
      await api.post(`${root}/packages/${packageId}/approvals`, {
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

  if (loadError && !current && revisions.length === 0) {
    return (
      <ErrorState
        title="No s'ha pogut carregar el paquet"
        description={loadError}
        action={
          <Button variant="primary" size="md" onClick={() => void load()}>
            Torna-ho a provar
          </Button>
        }
      />
    );
  }
  if (loading && !current && revisions.length === 0) {
    return <PageSkeleton variant="detail" title="Revisió final" context="carregant el paquet…" />;
  }

  const requirementsHref = `/candidatures/${workspace.id}?pas=preparar`;
  const canFreeze = workspace.can_decide && workspace.workspace_actionable;
  const freezeBlockers = freezeMutation.apiError?.code === "package_not_ready"
    ? ((freezeMutation.apiError.details.blockers as string[] | undefined) ?? [])
    : [];

  /** El botó de congelar, amb la raó visible si no es pot prémer. */
  function freezeButton(label: string, enabledByReadiness: boolean) {
    const button = (
      <Button
        variant="primary"
        size="md"
        className="min-h-12 md:min-h-0"
        onClick={() => void freeze()}
        loading={freezeMutation.saving}
        disabled={!canFreeze || !enabledByReadiness}
      >
        {label}
      </Button>
    );
    if (!workspace.can_decide) {
      return (
        <PermissionNote who={whoCanDecide()} what="congelar el paquet">
          {button}
        </PermissionNote>
      );
    }
    return button;
  }

  const freezeError =
    freezeMutation.error && (
      <ErrorState
        className="mt-3"
        title="No s'ha pogut congelar la revisió"
        description={freezeMutation.error}
        action={
          <Button variant="secondary" size="md" onClick={() => void freeze()} loading={freezeMutation.saving}>
            Torna-ho a provar
          </Button>
        }
        secondary={
          freezeBlockers.length > 0 ? (
            <ol className="list-decimal pl-5 text-left text-[13px] leading-5 text-ink">
              {freezeBlockers.map((code) => (
                <li key={code} title={code}>
                  {readinessBlockerLabel(code)}
                </li>
              ))}
            </ol>
          ) : undefined
        }
        technical={{ code: freezeMutation.apiError?.code ?? null, method: "POST" }}
      />
    );

  const statusLine = (status: WorkspaceDetail["status"], submitted: boolean) => (
    <p className="text-[13px] leading-5 text-muted">
      Estat: <strong className="text-ink">{WORKSPACE_STATUS_LABELS[status]}</strong> ·{" "}
      {submitted ? "presentada" : "encara no s'ha presentat res"}
    </p>
  );

  // ----------------------------------------------------------------- Buit ---
  if (!current) {
    const ready = workspace.readiness === "ready_for_final_review";
    return (
      <div className="space-y-4">
        {!ready && (
          <Banner
            level="attention"
            kicker="Dossier incomplet"
            action={
              <LinkButton href={requirementsHref} variant="secondary" size="sm">
                Torna a Preparar →
              </LinkButton>
            }
          >
            <p>
              <strong>El dossier encara no està a punt per congelar-lo.</strong> Per poder-ho fer:
            </p>
            <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[13px] leading-5">
              {workspace.readiness_blockers.map((code) => (
                <li key={code} title={code}>
                  {readinessBlockerLabel(code)}
                </li>
              ))}
            </ol>
          </Banner>
        )}
        <Card>
          <EmptyState
            title="Encara no s'ha congelat cap paquet"
            description="Quan el dossier estigui a punt, congela una revisió immutable dels documents que presentaràs."
            action={freezeButton("Congela el paquet", ready)}
          />
          <p className="px-3.5 pb-3 text-center text-[12.5px] text-muted md:hidden">
            Congelar i revisar el paquet funciona aquí, però és millor des de l&apos;escriptori.
          </p>
          {!workspace.workspace_actionable && (
            <p className="px-3.5 pb-3 text-center text-[13px] text-warning">
              Aquesta licitació ja no admet cap presentació nova.
            </p>
          )}
          {freezeError && <div className="px-3.5 pb-3">{freezeError}</div>}
          <div className="border-t border-border px-3.5 py-3">{statusLine(workspace.status, workspace.status === "submitted")}</div>
        </Card>
      </div>
    );
  }

  // --------------------------------------------------------- Paquet actual ---
  const changed = current.items.filter((item) => item.changed_since_freeze).length;
  const superseded = current.status === "superseded";
  const obsolete = current.is_stale || superseded;
  const nextRevision = current.revision_number + 1;
  const approval = current.approval;

  // Per què no es pot aprovar: raons reals del contracte, la més concreta primer.
  let disabledReason: string | null = null;
  if (!approval && !current.can_approve) {
    if (superseded) disabledReason = `Aquesta revisió ha quedat superada: congela la revisió ${nextRevision} i aprova aquella.`;
    else if (current.is_stale) disabledReason = "Desactivat perquè aquesta revisió ja no descriu els documents d'ara.";
    else if (current.simulation_only) disabledReason = "Paquet de simulació: no es pot aprovar.";
    else if (current.readiness_blockers.length > 0) disabledReason = "El dossier ja no està a punt:";
    else if (current.status !== "frozen") disabledReason = `Aquesta revisió és «${PACKAGE_STATUS_LABELS[current.status]}» i no es pot aprovar.`;
    else disabledReason = "El backend no permet aprovar aquesta revisió ara mateix.";
  }
  const approveDisabled = !current.can_approve || !current.can_sign;
  const approvalApiError = approveMutation.apiError;
  const approvalDetailList: string[] =
    approvalApiError?.code === "package_stale"
      ? ((approvalApiError.details.reasons as string[] | undefined) ?? []).map(staleReasonLabel)
      : approvalApiError?.code === "package_not_ready"
        ? ((approvalApiError.details.blockers as string[] | undefined) ?? []).map(readinessBlockerLabel)
        : [];

  const columns: Array<DataTableColumn<PackageItem>> = [
    {
      key: "position",
      header: "#",
      width: "5%",
      cell: (item) => <span className="tabular-nums text-muted">{item.position}</span>,
    },
    {
      key: "document",
      header: "Document",
      width: "30%",
      cell: (item) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{item.filename}</p>
          {item.changed_since_freeze && (
            <p className="mt-0.5 text-[12.5px] leading-5 font-medium text-danger">Els bytes d&apos;ara no són els congelats</p>
          )}
        </div>
      ),
    },
    {
      key: "requirement",
      header: "Acredita",
      width: "27%",
      cell: (item) => (
        <span className={item.requirement_title ? "text-ink" : "text-muted"}>
          {item.requirement_title ?? "Sense requisit associat"}
        </span>
      ),
    },
    {
      key: "bytes",
      header: "Bytes",
      width: "14%",
      cell: (item) => (
        <div className="min-w-0">
          <code className="font-mono text-[12.5px] text-ink" title={item.document_sha256}>
            {shortHash(item.document_sha256)}…
          </code>
          <p className="text-[12px] text-muted">{formatFileSize(item.file_size)}</p>
        </div>
      ),
    },
    {
      key: "validity",
      header: "El dia del termini",
      width: "24%",
      cell: (item) => (
        <div className="flex flex-wrap items-center gap-1.5">
          {item.changed_since_freeze ? (
            <StatusPill level="blocking" size="sm">
              Canviat des de congelar
            </StatusPill>
          ) : item.validity ? (
            <StatusPill level={VALIDITY_LEVEL[item.validity]} size="sm">
              {VALIDITY_LABELS[item.validity]}
            </StatusPill>
          ) : (
            <span className="text-muted">—</span>
          )}
          {item.document_expires_at && (
            <span className="text-[12.5px] text-muted">caduca {formatDate(item.document_expires_at)}</span>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      {obsolete && (
        <Banner
          level="blocking"
          kicker="Paquet obsolet"
          action={freezeButton(`Congela la revisió ${nextRevision}`, true)}
        >
          <p>
            <strong>
              {changed > 0
                ? `${plural(changed, "document ha", "documents han")} canviat des de congelar.`
                : superseded && current.stale_reasons.length === 0
                  ? staleReasonLabel("superseded")
                  : staleReasonLabel(current.stale_reasons[0] ?? "superseded")}
            </strong>{" "}
            Aquesta revisió continua descrivint el que hi havia en congelar-la; aprovar-la signaria una foto que ja
            no és certa.
          </p>
          {current.stale_reasons.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px] leading-5">
              {current.stale_reasons.map((code) => (
                <li key={code} title={code}>
                  {staleReasonLabel(code)}
                </li>
              ))}
            </ul>
          )}
        </Banner>
      )}
      {freezeError}

      <div className="grid gap-4 lg:grid-cols-12">
        {/* ------------------------------------------------------ Manifest --- */}
        <div className="space-y-4 lg:col-span-7">
          <Card>
            <CardHeader
              title={`Revisió ${current.revision_number} del paquet`}
              action={<StatusPill level={PACKAGE_STATUS_LEVEL[current.status]}>{PACKAGE_STATUS_LABELS[current.status]}</StatusPill>}
            />
            <dl className="grid gap-x-4 gap-y-2 px-3.5 py-3 text-[13px] sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-muted">Congelat</dt>
                <dd className="mt-0.5 text-ink">{current.frozen_at ? formatDateTime(current.frozen_at) : "—"}</dd>
              </div>
              <div>
                <dt className="text-muted">Manifest</dt>
                <dd className="mt-0.5 flex flex-wrap items-center gap-x-2 text-ink">
                  <code className="font-mono break-all" title={current.manifest_hash ?? undefined}>
                    {current.manifest_hash_short ?? shortHash(current.manifest_hash)}
                  </code>
                  <a href={`/bff${root}/packages/${current.id}/manifest`} className="whitespace-nowrap text-brand underline decoration-1 underline-offset-2 hover:text-accent-800">
                    manifest.json
                  </a>
                </dd>
              </div>
              <div>
                <dt className="text-muted">Documents</dt>
                <dd className="mt-0.5 text-ink tabular-nums">{current.coverage.document_count}</dd>
              </div>
              <div>
                <dt className="text-muted">Obligatoris coberts</dt>
                <dd className="mt-0.5 text-ink tabular-nums">
                  {current.coverage.mandatory_covered} de {current.coverage.mandatory_total}
                </dd>
              </div>
              <div className="sm:col-span-2 lg:col-span-4">
                <dt className="text-muted">Termini</dt>
                <dd className="mt-0.5 flex flex-wrap items-center gap-2 text-ink">
                  {current.deadline_at ? formatDateTime(current.deadline_at) : "No consta"}
                  {current.days_remaining !== null && (
                    <StatusPill level={deadlineLevel(current.days_remaining)} size="sm">
                      {deadlineLabel(current.days_remaining)}
                    </StatusPill>
                  )}
                </dd>
              </div>
            </dl>
            <DataTable
              columns={columns}
              rows={current.items}
              rowKey={(item) => item.id}
              rowTint={(item) => (item.changed_since_freeze ? "blocking" : undefined)}
              stackBelow="md"
              footer={
                <>
                  <span>
                    {current.coverage.document_count} documents · {current.coverage.mandatory_covered} de{" "}
                    {current.coverage.mandatory_total} obligatoris coberts
                  </span>
                  <span>El manifest es calcula sobre els bytes, no sobre els noms.</span>
                </>
              }
            />
          </Card>

          <Card>
            <CardHeader title="Revisions" description="Cap revisió no s'esborra; una de nova no hereta l'aprovació de l'anterior." />
            <ol className="divide-y divide-border">
              {revisions.map((revision) => (
                <li key={revision.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2.5 text-[13px]">
                  <span className="font-medium text-ink">Revisió {revision.revision_number}</span>
                  <StatusPill level={PACKAGE_STATUS_LEVEL[revision.status]} size="sm">
                    {PACKAGE_STATUS_LABELS[revision.status]}
                  </StatusPill>
                  {revision.id === current.id && (
                    <StatusPill level="action" size="sm" outline>
                      actual
                    </StatusPill>
                  )}
                  <span className="text-muted">{revision.frozen_at ? formatDateTime(revision.frozen_at) : "—"}</span>
                  <code className="font-mono text-[12.5px] text-muted" title={revision.manifest_hash ?? undefined}>
                    {revision.manifest_hash_short ?? shortHash(revision.manifest_hash)}
                  </code>
                  {revision.is_approved && (
                    <span className="text-muted">
                      aprovada per {revision.approved_by_name ?? "una persona"}
                      {revision.approved_at && ` · ${formatDate(revision.approved_at)}`}
                    </span>
                  )}
                  {revision.tender_changed_since_freeze && (
                    <span className="text-warning">la licitació ha canviat des de congelar</span>
                  )}
                </li>
              ))}
            </ol>
          </Card>
        </div>

        {/* ------------------------------------------------------ Aprovació --- */}
        <div className="space-y-4 lg:col-span-5">
          <Card frame>
            <div className="flex flex-col gap-4 px-5 py-5">
              <div>
                <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-brand-strong uppercase">Revisió final</p>
                <h2 className="mt-1.5 font-heading text-[22px] leading-tight font-semibold text-ink">Aprovem aquest paquet?</h2>
                <p className="mt-2 text-[14px] leading-6 text-muted">
                  Quedarà signat per <strong className="text-ink">{signerName}</strong> contra el manifest{" "}
                  <code className="font-mono text-ink" title={current.manifest_hash ?? undefined}>
                    {current.manifest_hash_short ?? shortHash(current.manifest_hash)}
                  </code>
                  . Una aprovació no es desfà mai: si després canvia res, es manté com a història i el paquet deixa
                  de ser presentable.
                </p>
              </div>

              {approval ? (
                <div className="border border-success bg-success-soft px-4 py-3">
                  <StatusPill level="ready">Aprovat per presentar</StatusPill>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Attribution kind="human" person={{ name: approval.approved_by_name ?? "Una persona" }} at={approval.approved_at} />
                    <code className="font-mono text-[12.5px] text-muted" title={approval.package_manifest_hash}>
                      manifest {shortHash(approval.package_manifest_hash)}
                    </code>
                  </div>
                  {approval.comment && <p className="mt-2 text-[13px] leading-5 text-ink">“{approval.comment}”</p>}
                  {obsolete && (
                    <p className="mt-2 text-[12.5px] leading-5 text-muted">
                      L&apos;aprovació es conserva tal com era; el que ha canviat és que aquesta revisió ja no es pot presentar.
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {current.can_sign ? (
                    <Button
                      variant="primary"
                      size="md"
                      className="min-h-12 justify-center text-[16px]"
                      disabled={approveDisabled}
                      onClick={openApproval}
                    >
                      Aprova per presentar
                    </Button>
                  ) : (
                    <PermissionNote who={whoCanDecide()} what="signar l'aprovació" className="w-full [&>div]:w-full">
                      <Button variant="primary" size="md" className="min-h-12 w-full justify-center text-[16px]" disabled>
                        Aprova per presentar
                      </Button>
                    </PermissionNote>
                  )}
                  {disabledReason && (
                    <div className="text-[13px] leading-5 text-warning">
                      <p>{disabledReason}</p>
                      {current.readiness_blockers.length > 0 && !current.is_stale && !superseded && !current.simulation_only && (
                        <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-ink">
                          {current.readiness_blockers.map((code) => (
                            <li key={code} title={code}>
                              {readinessBlockerLabel(code)}
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  )}
                </div>
              )}

              <LinkButton href={requirementsHref} variant="secondary" size="md" className="min-h-12 justify-center md:min-h-0">
                Torna a Preparar
              </LinkButton>
            </div>
          </Card>

          <Card>
            <CardHeader title="Aprovar no és presentar" />
            <ol className="list-decimal space-y-2 px-3.5 py-3 pl-9 text-[13px] leading-5 text-ink">
              <li>
                Descarrega el paquet: els {current.coverage.document_count} documents i l&apos;índex amb el manifest (
                <a href={`/bff${root}/packages/${current.id}/manifest`} className="text-brand underline decoration-1 underline-offset-2 hover:text-accent-800">
                  manifest.json
                </a>
                ).
              </li>
              <li>Presenta&apos;l tu a la plataforma de l&apos;òrgan de contractació. Licitacions no hi envia res.</li>
              <li>Deixa&apos;n constància amb la data i el justificant. Aquí encara no es pot marcar: la candidatura no es tanca sola.</li>
            </ol>
            <div className="border-t border-border px-3.5 py-2.5">{statusLine(current.workspace_status, current.is_submitted)}</div>
          </Card>
        </div>
      </div>

      <Modal
        open={confirming}
        title={`Estàs aprovant exactament la revisió ${current.revision_number} d'aquest paquet`}
        description="Quedarà registrat qui aprova, quan i contra quin manifest. No es desfà."
        onClose={() => {
          if (!approveMutation.saving) setConfirming(false);
        }}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={approveMutation.saving}>
              Cancel·la
            </Button>
            <Button variant="primary" onClick={() => void approve()} loading={approveMutation.saving}>
              Aprova per presentar
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border border-border bg-subtle px-4 py-3 text-[13px]">
            <div>
              <dt className="text-muted">Revisió</dt>
              <dd className="mt-0.5 text-ink">Revisió {current.revision_number}</dd>
            </div>
            <div>
              <dt className="text-muted">Documents</dt>
              <dd className="mt-0.5 text-ink">
                {current.coverage.document_count} documents · {current.coverage.mandatory_covered} de {current.coverage.mandatory_total}{" "}
                obligatoris
              </dd>
            </div>
            <div>
              <dt className="text-muted">Manifest</dt>
              <dd className="mt-0.5">
                <code className="font-mono text-ink" title={current.manifest_hash ?? undefined}>
                  {current.manifest_hash_short ?? shortHash(current.manifest_hash)}
                </code>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Termini</dt>
              <dd className="mt-0.5 text-ink">{current.deadline_at ? formatDateTime(current.deadline_at) : "No consta"}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-muted">Signada per</dt>
              <dd className="mt-0.5 text-ink">{signerName} · ara, en registrar-la</dd>
            </div>
          </dl>

          <p className="border border-border px-4 py-3 text-[13px] leading-5 text-ink">
            <strong>Aprovar aquest paquet NO el presenta a la plataforma pública.</strong> Vol dir que aquesta és
            exactament la documentació que una persona dona per bona.
          </p>

          <Field label="Comentari (opcional)" hint={`${comment.length} de ${COMMENT_MAX_LENGTH} caràcters`}>
            <Textarea
              rows={3}
              maxLength={COMMENT_MAX_LENGTH}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Dossier revisat."
            />
          </Field>

          {approveMutation.error && (
            <div className="border border-danger bg-danger-soft px-4 py-3 text-[13px] leading-5">
              <p className="font-semibold text-danger">No s&apos;ha registrat cap aprovació</p>
              <p className="mt-1 text-ink">{approveMutation.error}</p>
              {approvalDetailList.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink">
                  {approvalDetailList.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
              {approvalApiError?.status === 403 && (
                <p className="mt-1 text-ink">Només {whoCanDecide()} pot signar l&apos;aprovació.</p>
              )}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
