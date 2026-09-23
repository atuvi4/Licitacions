"use client";

import { useCallback, useState } from "react";

import { CitationPicker } from "@/components/CitationPicker";
import { useInvalidateReadiness } from "@/components/ReadinessProvider";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Drawer,
  Field,
  Input,
  LoadingRows,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import {
  ISSUE_STATUS_LABELS,
  ISSUE_STATUS_LEVEL,
  ISSUE_TYPES,
  ISSUE_TYPE_LABELS,
  canBlockFinalReview,
  canEditIssues,
  isOpen,
  type Issue,
  type IssueType,
} from "@/lib/issues";
import type { SourceRefCitation } from "@/lib/requirements";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Amb què ve preomplert el formulari quan s'obre des d'una entitat concreta. */
export interface IssueContext {
  requirement_id?: string;
  technical_solution_id?: string;
  solution_option_id?: string;
  economic_offer_line_id?: string;
  dimension_id?: string;
}

/**
 * Dubtes i discrepàncies.
 *
 * Viu a la capçalera de Preparar i no a cap pestanya, perquè un dubte és
 * transversal per definició: pot ser sobre un requisit, una magnitud, una
 * opció o un preu, i tenir-lo dins d'una de les quatre àrees obligaria a
 * triar-ne una d'arbitrària.
 *
 * Dues coses que la pantalla no fa mai:
 *
 * * **no obre cap dubte sola.** «Obre dubte» al costat d'una entitat només
 *   prepara el formulari amb el context; fins que algú confirma, no hi ha res;
 * * **no ofereix bloquejar la revisió final a qui no ho pot decidir.** Per a la
 *   resta d'editors el control no hi és.
 */
export function IssuesDrawer({
  workspace,
  open,
  onClose,
  context,
}: {
  workspace: WorkspaceDetail;
  open: boolean;
  onClose: () => void;
  /** Quan s'obre des d'una entitat, el formulari ja ve preomplert amb ella. */
  context?: IssueContext | null;
}) {
  const { companyId, membership } = useSession();
  const invalidateReadiness = useInvalidateReadiness();
  const canEdit = canEditIssues(membership?.role);
  const canBlock = canBlockFinalReview(membership?.role);

  const path =
    companyId && open ? `/companies/${companyId}/workspaces/${workspace.id}/issues` : null;
  const issues = useResource<Issue[]>(path);
  const rows = issues.data ?? [];

  const [showClosed, setShowClosed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    await issues.reload();
    invalidateReadiness();
  }, [invalidateReadiness, issues]);

  const visible = showClosed ? rows : rows.filter(isOpen);

  return (
    <Drawer open={open} onClose={onClose} title="Dubtes i discrepàncies">
      <div className="grid gap-3">
        {canEdit && !creating && (
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCreating(true)}>
              Obre dubte
            </Button>
            <button
              type="button"
              aria-pressed={showClosed}
              className="text-[13px] text-brand hover:underline"
              onClick={() => setShowClosed((current) => !current)}
            >
              {showClosed ? "Amaga els tancats" : "Mostra també els tancats"}
            </button>
          </div>
        )}

        {creating && (
          <CreateIssueForm
            workspace={workspace}
            context={context ?? null}
            onCancel={() => setCreating(false)}
            onCreated={async () => {
              setCreating(false);
              await refresh();
            }}
          />
        )}

        {issues.loading && <LoadingRows rows={2} />}

        {issues.error && !issues.loading && (
          <div className="grid gap-2">
            <Banner level="blocking" kicker="Dubtes" action={null}>
              No s&apos;han pogut llegir els dubtes: {issues.error}
            </Banner>
            <div>
              <Button variant="secondary" size="sm" onClick={() => void issues.reload()}>
                Torna-ho a provar
              </Button>
            </div>
          </div>
        )}

        {!issues.loading && !issues.error && visible.length === 0 && (
          <p className="text-body text-muted">
            {rows.length === 0
              ? "Cap dubte obert. S'hi anoten les preguntes, les contradiccions i el que encara no se sap."
              : "Cap dubte obert."}
          </p>
        )}

        <ul className="grid gap-2">
          {visible.map((issue) => (
            <IssueRow
              key={issue.id}
              issue={issue}
              workspace={workspace}
              canEdit={canEdit}
              canBlock={canBlock}
              expanded={openId === issue.id}
              onToggle={() => setOpenId(openId === issue.id ? null : issue.id)}
              onChanged={refresh}
            />
          ))}
        </ul>
      </div>
    </Drawer>
  );
}

/* ------------------------------------------------------------------------- */

function IssueRow({
  issue,
  workspace,
  canEdit,
  canBlock,
  expanded,
  onToggle,
  onChanged,
}: {
  issue: Issue;
  workspace: WorkspaceDetail;
  canEdit: boolean;
  canBlock: boolean;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState("");
  const [resolutionSource, setResolutionSource] = useState("");

  const root = companyId
    ? `/companies/${companyId}/workspaces/${workspace.id}/issues/${issue.id}`
    : "";

  const act = useCallback(
    async (suffix: string, body: unknown) => {
      if (!companyId) return false;
      const saved = await mutation.run(async () => {
        await api.post(`${root}${suffix}`, body);
      });
      if (saved) await onChanged();
      return saved;
    },
    [companyId, mutation, onChanged, root],
  );

  return (
    <li className="border border-border bg-canvas px-3 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={onToggle}
          className="text-left text-body font-medium text-ink hover:underline"
        >
          {issue.title}
        </button>
        <StatusPill level="neutral" size="sm">
          {ISSUE_TYPE_LABELS[issue.issue_type]}
        </StatusPill>
        <StatusPill level={ISSUE_STATUS_LEVEL[issue.status]} size="sm">
          {ISSUE_STATUS_LABELS[issue.status]}
        </StatusPill>
        {/* Destaca perquè algú ho ha decidit, no perquè el dubte sigui greu. */}
        {issue.blocks_final_review && (
          <StatusPill level="blocking" size="sm" outline>
            Impedeix la revisió final
          </StatusPill>
        )}
        {issue.due_at && (
          <span className="ml-auto text-caption text-muted">
            Per al {formatDateTime(issue.due_at)}
          </span>
        )}
      </div>

      {expanded && (
        <div className="mt-2 grid gap-2 border-t border-border pt-2">
          {mutation.error && (
            <Banner level="blocking" kicker="Dubte" action={null}>
              {mutation.error}
            </Banner>
          )}

          {issue.description && <p className="text-body text-ink">{issue.description}</p>}

          {issue.supplier_name && (
            <p className="text-caption text-muted">Proveïdor: {issue.supplier_name}</p>
          )}

          {issue.source_refs.length > 0 && (
            <div>
              <p className={KICKER}>
                {issue.source_refs.length === 1
                  ? "1 cita"
                  : `${issue.source_refs.length} cites`}
              </p>
              <ul className="mt-1 grid gap-1">
                {issue.source_refs.map((ref) => (
                  <li key={ref.id} className="text-caption text-muted">
                    {ref.document_name}
                    {ref.page_number !== null ? ` · p. ${ref.page_number}` : ""}
                    {ref.location_label ? ` · ${ref.location_label}` : ""}
                    {ref.version_number !== null ? ` · v${ref.version_number}` : ""}
                    {canEdit && isOpen(issue) && (
                      <button
                        type="button"
                        className="ml-2 text-brand hover:underline"
                        onClick={() =>
                          void mutation
                            .run(async () => {
                              await api.delete(`${root}/source-refs/${ref.id}`);
                            })
                            .then((saved) => (saved ? onChanged() : undefined))
                        }
                      >
                        Treu-la
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {issue.status === "resolved" && (
            <p className="text-caption text-muted">
              Resolt el {issue.resolved_at ? formatDateTime(issue.resolved_at) : "—"}:{" "}
              {issue.resolution}
              {issue.resolution_source ? ` (${issue.resolution_source})` : ""}
            </p>
          )}

          {canEdit && isOpen(issue) && !resolving && (
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" size="sm" onClick={() => setResolving(true)}>
                Resol
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={mutation.saving}
                onClick={() => void act("/dismiss", {})}
              >
                Descarta
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={mutation.saving}
                onClick={() =>
                  void mutation
                    .run(async () => {
                      await api.patch(root, {
                        status: issue.status === "waiting" ? "open" : "waiting",
                      });
                    })
                    .then((saved) => (saved ? onChanged() : undefined))
                }
              >
                {issue.status === "waiting" ? "Torna a obert" : "Posa en espera"}
              </Button>
              {/* Només per a qui ho pot decidir: per a la resta el control no hi és. */}
              {canBlock && (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={mutation.saving}
                  onClick={() =>
                    void act("/final-review-block", {
                      blocks_final_review: !issue.blocks_final_review,
                    })
                  }
                >
                  {issue.blocks_final_review
                    ? "Ja no impedeix la revisió"
                    : "Impedeix la revisió final"}
                </Button>
              )}
            </div>
          )}

          {canEdit && isOpen(issue) && resolving && (
            <div className="grid gap-2">
              <Field
                label="Com s'ha resolt"
                required
                hint="Qui ho llegeixi d'aquí a sis mesos no tindrà cap altra explicació."
              >
                <Textarea
                  rows={3}
                  value={resolution}
                  onChange={(event) => setResolution(event.target.value)}
                />
              </Field>
              <Field label="D'on surt la resposta">
                <Input
                  value={resolutionSource}
                  onChange={(event) => setResolutionSource(event.target.value)}
                />
              </Field>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  loading={mutation.saving}
                  disabled={resolution.trim() === ""}
                  onClick={() =>
                    void act("/resolve", {
                      resolution: resolution.trim(),
                      resolution_source: resolutionSource.trim() || null,
                    }).then((saved) => {
                      if (saved) {
                        setResolution("");
                        setResolutionSource("");
                        setResolving(false);
                      }
                    })
                  }
                >
                  Desa la resolució
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setResolving(false)}>
                  Cancel·la
                </Button>
              </div>
            </div>
          )}

          {canEdit && isOpen(issue) && (
            <CitationPicker
              tenderId={workspace.tender_id}
              onAdded={(citation: SourceRefCitation) =>
                void mutation
                  .run(async () => {
                    await api.post(`${root}/source-refs`, { source_ref_id: citation.id });
                  })
                  .then((saved) => (saved ? onChanged() : undefined))
              }
            />
          )}
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------------- */

function CreateIssueForm({
  workspace,
  context,
  onCancel,
  onCreated,
}: {
  workspace: WorkspaceDetail;
  context: IssueContext | null;
  onCancel: () => void;
  onCreated: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [type, setType] = useState<IssueType>("internal_question");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [supplier, setSupplier] = useState("");
  const [due, setDue] = useState("");

  const submit = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(`/companies/${companyId}/workspaces/${workspace.id}/issues`, {
        issue_type: type,
        title: title.trim(),
        description: description.trim() || null,
        supplier_name: supplier.trim() || null,
        due_at: due ? new Date(due).toISOString() : null,
        // El context ve preseleccionat quan s'obre des d'una entitat, però no
        // s'ha creat res fins ara: «Obre dubte» només prepara el formulari.
        ...(context ?? {}),
      });
    });
    if (saved) await onCreated();
  }, [companyId, context, description, due, mutation, onCreated, supplier, title, type, workspace.id]);

  return (
    <div className="grid gap-3 border border-border bg-subtle px-3 py-3">
      <p className={KICKER}>Nou dubte</p>

      {mutation.error && (
        <Banner level="blocking" kicker="Dubte" action={null}>
          {mutation.error}
        </Banner>
      )}

      <Field label="Tipus">
        <Select
          aria-label="Tipus"
          value={type}
          onChange={(event) => setType(event.target.value as IssueType)}
          options={ISSUE_TYPES.map((value) => ({
            value,
            label: ISSUE_TYPE_LABELS[value],
          }))}
        />
      </Field>

      <Field label="Títol" required>
        <Input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          maxLength={300}
          autoFocus
        />
      </Field>

      <Field label="Descripció">
        <Textarea
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Proveïdor">
          <Input
            value={supplier}
            onChange={(event) => setSupplier(event.target.value)}
            maxLength={300}
          />
        </Field>
        <Field label="Data límit">
          <Input type="date" value={due} onChange={(event) => setDue(event.target.value)} />
        </Field>
      </div>

      <p className="text-caption text-muted">
        Neix obert i sense impedir res. Que un dubte aturi la revisió final ho decideix
        després qui respon de l&apos;oferta.
      </p>

      <div className="flex gap-2">
        <Button
          variant="primary"
          size="sm"
          loading={mutation.saving}
          disabled={title.trim() === ""}
          onClick={() => void submit()}
        >
          Obre&apos;l
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={mutation.saving}>
          Cancel·la
        </Button>
      </div>
    </div>
  );
}
