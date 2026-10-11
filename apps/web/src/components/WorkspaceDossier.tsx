"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Modal } from "@/components/Modal";
import {
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
  ResultNotice,
  Select,
  StatusPill,
  Textarea,
  type DataTableColumn,
  type DataTableGroup,
  type RowTint,
} from "@/components/ui";
import { api } from "@/lib/api";
import { RESULT_LABELS, type AssessmentResult } from "@/lib/compliance";
import { formatDate } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import {
  COVERAGE_LEVEL,
  EVIDENCE_STATUS_LEVEL,
  RESULT_LEVEL,
  VALIDITY_LEVEL,
  whoCanDecide,
} from "@/lib/labels";
import {
  COVERAGE_LABELS,
  EVIDENCE_STATUS_LABELS,
  GROUP_LABELS,
  NO_ANALYSIS_ACTION,
  NO_ANALYSIS_COUNTER,
  NO_ANALYSIS_TEXT,
  RELEVANCE_ESTABLISHED,
  RELEVANCE_HINTS,
  RELEVANCE_LABELS,
  VALIDITY_LABELS,
  hasNoAnalysis,
  readinessBlockerLabel,
  reconciliationMessage,
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

/** Longitud màxima del motiu de pertinença al backend (`RelevanceConfirmRequest.note`). */
const RELEVANCE_NOTE_MAX_LENGTH = 2000;

const STATUS_OPTIONS = (Object.keys(EVIDENCE_STATUS_LABELS) as EvidenceItemStatus[]).map((value) => ({
  value,
  label: EVIDENCE_STATUS_LABELS[value],
}));

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** Cap deducció: el tint només repeteix el que el backend ja ha dit. */
function tintOf(item: DossierItem): RowTint | undefined {
  if (item.coverage === "missing_document") return "blocking";
  if (item.validity === "expired" || item.validity === "expires_before_deadline") return "blocking";
  if (item.document && !RELEVANCE_ESTABLISHED.has(item.relevance)) return "attention";
  return undefined;
}

/**
 * El dossier documental (handoff L13, mockup 10 i cas 5).
 *
 * Dues preguntes que no es barregen mai: **Compliment** (podem acreditar-ho?)
 * i **Dossier** (el paper hi és, acredita el requisit i serà vigent el dia
 * del termini?). I dos gestos separats, tal com fa el backend: triar un
 * document (`PATCH`) i confirmar que acredita el requisit (`POST …/relevance`,
 * amb motiu obligatori i signatura). Un document triat sense pertinença
 * establerta no cobreix res: la cobertura la calcula el backend i aquí només
 * es mostra.
 */
export function DossierSection({
  workspace,
  onChange,
  onAdvanced,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
  /**
   * Només després d'un `advance` que el backend ha acceptat. Qui ho rep
   * refresca el workspace i, si l'estat real ja és a revisió final, canvia de
   * pas. Si no es passa, es fa servir `onChange`.
   */
  onAdvanced?: () => Promise<void>;
}) {
  const base = `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [documents, setDocuments] = useState<CompanyDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mutation = useMutation();
  const [rowSaving, setRowSaving] = useState(false);
  const relevanceMutation = useMutation();
  const [outcome, setOutcome] = useState<ReturnType<typeof reconciliationMessage> | null>(null);
  const [rowError, setRowError] = useState<{ itemId: string; message: string } | null>(null);
  const [confirming, setConfirming] = useState<DossierItem | null>(null);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setDossier(await api.get<Dossier>(`${base}/dossier`));
      setDocuments(await api.get<CompanyDocument[]>(`/companies/${workspace.company_id}/documents`));
    } catch (caught) {
      setLoadError(caught instanceof Error ? caught.message : "Error desconegut");
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
      // Un 200 silenciós no es distingeix d'una acció que ha fallat.
      setOutcome(
        reconciliationMessage(result.suggested + result.gaps, result.reason, {
          one: "requisit",
          many: "requisits al dossier",
        }),
      );
    });
    if (ok) await onChange();
  }

  // Les accions d'una fila deixen l'error a la fila mateixa, no al peu.
  async function rowAction(itemId: string, action: () => Promise<void>) {
    setRowError(null);
    setRowSaving(true);
    try {
      await action();
      await onChange();
    } catch (caught) {
      setRowError({ itemId, message: caught instanceof Error ? caught.message : "No s'ha pogut desar." });
    } finally {
      setRowSaving(false);
    }
  }

  function patch(itemId: string, body: Record<string, unknown>) {
    return rowAction(itemId, async () => {
      setDossier(await api.patch<Dossier>(`${base}/dossier/${itemId}`, body));
    });
  }

  function createTask(itemId: string) {
    return rowAction(itemId, async () => {
      await api.post(`${base}/dossier/${itemId}/task`, {});
    });
  }

  function openConfirm(item: DossierItem) {
    relevanceMutation.reset();
    setNote("");
    setConfirming(item);
  }

  async function confirmRelevance() {
    if (!confirming) return;
    const itemId = confirming.id;
    const ok = await relevanceMutation.run(async () => {
      setDossier(await api.post<Dossier>(`${base}/dossier/${itemId}/relevance`, { note: note.trim() }));
    });
    if (ok) {
      setConfirming(null);
      await onChange();
    }
  }

  async function advance() {
    const ok = await mutation.run(async () => {
      await api.post(`${base}/readiness/advance`, {});
    });
    // Si falla, no es mou res: ni el dossier ni el pas. L'error diu què falta.
    if (ok) {
      await load();
      await (onAdvanced ?? onChange)();
    }
  }

  if (loadError && !dossier) {
    return (
      <ErrorState
        title="No s'ha pogut carregar el dossier"
        description={loadError}
        action={
          <Button variant="primary" size="md" onClick={() => void load()}>
            Torna-ho a provar
          </Button>
        }
      />
    );
  }
  if (!dossier) {
    return loading ? (
      <PageSkeleton variant="table" title="Dossier documental" columns={["Requisit", "Compliment", "Document triat", "Dossier", "Acció"]} />
    ) : null;
  }

  const { coverage, readiness } = dossier;
  const requirementsHref = `/licitacions/${workspace.tender_id}?tab=requisits`;
  const openTasks = workspace.tasks.filter((task) => task.status === "pending" || task.status === "in_progress").length;
  const noAnalysis = hasNoAnalysis(coverage);
  // Sense anàlisi del plec, mai «Dossier complet» (el backend ja hi posa el blocador).
  const isReady = readiness.state === "ready_for_final_review" && !noAnalysis;
  const alreadyInFinalReview = workspace.status === "ready_for_final_review";
  const advanceBlockers =
    mutation.apiError?.code === "workspace_not_ready"
      ? ((mutation.apiError.details.blockers as string[] | undefined) ?? [])
      : [];
  const advanceForbidden = mutation.apiError?.status === 403;

  // Els coberts i confirmats van plegats al final: la feina pendent primer.
  const groups: Array<DataTableGroup<DossierItem>> = dossier.groups
    .map((group) => ({
      key: group.group,
      label: GROUP_LABELS[group.group] ?? group.group,
      rows: group.items.filter((item) => item.coverage !== "covered"),
    }))
    .filter((group) => group.rows.length > 0);
  const covered = dossier.groups.flatMap((group) => group.items.filter((item) => item.coverage === "covered"));
  if (covered.length > 0) {
    groups.push({ key: "__covered", label: "Coberts i confirmats", rows: covered, collapsed: true });
  }

  const columns: Array<DataTableColumn<DossierItem>> = [
    {
      key: "requirement",
      header: "Requisit",
      width: "28%",
      cell: (item) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{item.requirement_title ?? item.label}</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
            {item.mandatory ? (
              <StatusPill level="attention" size="sm" outline>
                Obligatori
              </StatusPill>
            ) : (
              <span>Valorable</span>
            )}
            {item.source === "manual" && <span>· afegit a mà</span>}
            {item.requirement_id && (
              <Link href={requirementsHref} className="text-brand hover:underline">
                Veure font del plec →
              </Link>
            )}
          </p>
          {item.requirement_quote && (
            <details className="mt-1 text-[12.5px] text-muted">
              <summary className="cursor-pointer">Cita del plec</summary>
              <p className="mt-1 italic">“{item.requirement_quote}”</p>
            </details>
          )}
        </div>
      ),
    },
    {
      key: "compliance",
      header: "Compliment",
      width: "13%",
      cell: (item) => {
        const result = item.assessment_result as AssessmentResult | null;
        return result ? (
          <StatusPill level={RESULT_LEVEL[result]} size="sm">
            {RESULT_LABELS[result]}
          </StatusPill>
        ) : (
          <span className="text-[12.5px] text-muted">— sense avaluació</span>
        );
      },
    },
    {
      key: "document",
      header: "Document triat",
      width: "24%",
      cell: (item) => (
        <div className="min-w-0">
          {item.document ? (
            <>
              <p className="text-[13.5px] leading-5 text-ink">{item.document.name}</p>
              <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
                <StatusPill level={VALIDITY_LEVEL[item.validity]} size="sm">
                  {VALIDITY_LABELS[item.validity]}
                </StatusPill>
              </p>
              {item.validity === "expires_before_deadline" && item.document.expires_at && (
                <p className="mt-1 text-[12.5px] leading-5 font-medium text-danger">
                  Caduca el {formatDate(item.document.expires_at)} · abans del termini
                  {dossier.deadline_at && ` (${formatDate(dossier.deadline_at)})`}
                </p>
              )}
              {item.validity === "expired" && item.document.expires_at && (
                <p className="mt-1 text-[12.5px] leading-5 font-medium text-danger">
                  Caducat el {formatDate(item.document.expires_at)}
                </p>
              )}
              {item.validity === "valid" && item.document.expires_at && (
                <p className="mt-1 text-[12.5px] leading-5 text-muted">Caduca el {formatDate(item.document.expires_at)}</p>
              )}
              {item.selected_by_name && item.selected_at && (
                <p className="mt-1 text-[12.5px] leading-5 text-muted">
                  Triat per {item.selected_by_name} · {formatDate(item.selected_at)}
                </p>
              )}
            </>
          ) : (
            <p className="text-[13.5px] leading-5 font-medium text-danger">Cap document triat</p>
          )}
        </div>
      ),
    },
    {
      key: "dossier",
      header: "Dossier",
      width: "17%",
      cell: (item) => (
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-1.5">
            {item.coverage ? (
              <StatusPill level={COVERAGE_LEVEL[item.coverage]} size="sm">
                {COVERAGE_LABELS[item.coverage]}
              </StatusPill>
            ) : (
              <span className="text-[12.5px] text-muted">—</span>
            )}
            <StatusPill level={EVIDENCE_STATUS_LEVEL[item.status]} size="sm" outline>
              {EVIDENCE_STATUS_LABELS[item.status]}
            </StatusPill>
          </p>
          {item.document && item.relevance === "human_confirmed" && (
            <p className="mt-1 text-[12.5px] leading-5 text-muted" title={item.relevance_note ?? undefined}>
              Confirmat per {item.relevance_confirmed_by_name ?? "una persona"}
              {item.relevance_confirmed_at && ` · ${formatDate(item.relevance_confirmed_at)}`}
            </p>
          )}
          {item.document && item.relevance === "linked_evidence" && (
            <p className="mt-1 text-[12.5px] leading-5 text-muted">Lligat a l&apos;evidència del compliment</p>
          )}
          {item.document && !RELEVANCE_ESTABLISHED.has(item.relevance) && (
            <p className="mt-1 text-[12.5px] leading-5 text-warning">
              {RELEVANCE_LABELS[item.relevance]}
              <span className="block text-muted">{RELEVANCE_HINTS[item.relevance]}</span>
            </p>
          )}
        </div>
      ),
    },
    {
      key: "action",
      header: "Acció",
      width: "18%",
      cell: (item) => (
        <div className="flex flex-col items-stretch gap-1.5">
          <Select
            aria-label="Canvia document"
            className="min-h-11 w-full md:min-h-0"
            value=""
            placeholder="Canvia document"
            disabled={rowSaving}
            options={documents.map((document) => ({ value: document.id, label: document.name }))}
            onChange={(event) =>
              event.target.value && void patch(item.id, { document_id: event.target.value, status: "selected" })
            }
          />
          <Select
            aria-label="Estat de la peça"
            className="min-h-11 w-full md:min-h-0"
            value={item.status}
            disabled={rowSaving}
            options={STATUS_OPTIONS}
            onChange={(event) => void patch(item.id, { status: event.target.value })}
          />
          {item.document && !RELEVANCE_ESTABLISHED.has(item.relevance) && (
            <Button variant="primary" size="sm" className="min-h-11 md:min-h-0" disabled={rowSaving} onClick={() => openConfirm(item)}>
              Confirma que acredita
            </Button>
          )}
          {(item.status === "missing" || !item.document) && (
            <Button variant="secondary" size="sm" className="min-h-11 md:min-h-0" disabled={rowSaving} onClick={() => void createTask(item.id)}>
              Crea tasca
            </Button>
          )}
          {rowError?.itemId === item.id && <p className="text-[12.5px] leading-5 text-danger">{rowError.message}</p>}
        </div>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Dossier documental"
        description="Amb quins documents acreditarem cada requisit. No és el compliment."
        action={
          <Button size="sm" variant="secondary" onClick={() => void sync()} loading={mutation.saving}>
            Actualitza propostes
          </Button>
        }
      />
      {outcome && (
        <div className="px-3.5 pt-3">
          <ResultNotice message={outcome.message} detail={outcome.detail} tone={outcome.tone} onDismiss={() => setOutcome(null)} />
        </div>
      )}

      <div className="px-3.5 pt-3">
        {!isReady ? (
          <Banner
            level="attention"
            kicker="Dossier incomplet"
            action={
              <LinkButton href={`/candidatures/${workspace.id}?pas=preparar&area=pla#tasques`} variant="secondary" size="sm">
                {openTasks > 0 ? `${plural(openTasks, "tasca oberta", "tasques obertes")} →` : "Crea una tasca →"}
              </LinkButton>
            }
          >
            {noAnalysis ? (
              // Sense anàlisi no hi ha fracció: «0 de 0» volia dir «ningú no ha llegit el plec».
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <strong>{NO_ANALYSIS_COUNTER}</strong>
                <span>{NO_ANALYSIS_TEXT}</span>
                <span>· no se sap quins requisits obligatoris té.</span>
                <LinkButton href={requirementsHref} variant="secondary" size="sm">
                  {NO_ANALYSIS_ACTION} →
                </LinkButton>
              </p>
            ) : (
              <p>
                <strong>
                  {coverage.covered} de {coverage.mandatory_total} requisits obligatoris coberts i confirmats.
                </strong>{" "}
                Per passar a la revisió final:
              </p>
            )}
            {readiness.blockers.length > 0 && (
              <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[13px] leading-5">
                {readiness.blockers.map((code) => (
                  <li key={code} title={code}>
                    {readinessBlockerLabel(code)}
                  </li>
                ))}
              </ol>
            )}
          </Banner>
        ) : alreadyInFinalReview ? (
          <Banner level="ready" kicker="A revisió final">
            <p>
              <strong>El dossier ja és a revisió final.</strong> Presentar-lo és un pas posterior i separat, al pas
              Tancar.
            </p>
          </Banner>
        ) : (
          <Banner
            level="ready"
            kicker="Dossier complet"
            action={
              workspace.can_decide ? (
                <Button variant="primary" size="sm" onClick={() => void advance()} loading={mutation.saving}>
                  Passa a revisió final
                </Button>
              ) : (
                <PermissionNote who={whoCanDecide()} what="passar-lo a revisió final">
                  <Button variant="primary" size="sm" disabled>
                    Passa a revisió final
                  </Button>
                </PermissionNote>
              )
            }
          >
            <p>
              <strong>
                {coverage.covered} de {coverage.mandatory_total} requisits obligatoris coberts i confirmats.
              </strong>{" "}
              Passar-lo a revisió final no presenta res. Només diu que ja es pot revisar.
            </p>
          </Banner>
        )}
        {advanceBlockers.length > 0 && (
          <div className="mt-2 border border-danger bg-danger-soft px-3.5 py-2.5 text-[13px] leading-5">
            <p className="font-semibold text-danger">El backend no ha deixat passar a revisió final. Falta:</p>
            <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-ink">
              {advanceBlockers.map((code) => (
                <li key={code} title={code}>
                    {readinessBlockerLabel(code)}
                  </li>
              ))}
            </ol>
          </div>
        )}
        {advanceForbidden && (
          <p className="mt-2 text-[13px] leading-5 text-warning">
            {mutation.error} Només {whoCanDecide()} pot passar-lo a revisió final.
          </p>
        )}
        {mutation.error && advanceBlockers.length === 0 && !advanceForbidden && (
          <p className="mt-2 text-[13px] leading-5 text-danger">{mutation.error}</p>
        )}
      </div>

      <div className="mt-3">
        {dossier.groups.length === 0 ? (
          <EmptyState
            title="Encara no hi ha cap peça"
            description="El dossier es proposa a partir del compliment calculat. Proposar no tria res: cada document el confirma una persona."
            action={
              <Button variant="primary" size="md" onClick={() => void sync()} loading={mutation.saving}>
                Actualitza propostes
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={columns}
            rows={[]}
            groups={groups}
            rowKey={(item) => item.id}
            rowTint={tintOf}
            stackBelow="md"
            footer={
              <>
                <span>
                  <strong className="text-ink">Compliment</strong> és si podem acreditar-ho; <strong className="text-ink">Dossier</strong>{" "}
                  és si el paper hi és.
                </span>
                {dossier.superseded_count > 0 && (
                  <span>
                    {plural(dossier.superseded_count, "ítem", "ítems")} a l&apos;històric: el plec ja no ho demana
                  </span>
                )}
              </>
            }
          />
        )}
      </div>

      <Modal
        open={confirming !== null}
        title="Per què aquest document acredita aquest requisit?"
        description="Triar un document i confirmar que és l'adequat són dues afirmacions diferents. Aquesta quedarà amb el teu nom, la data i el motiu."
        onClose={() => {
          if (!relevanceMutation.saving) setConfirming(null);
        }}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(null)} disabled={relevanceMutation.saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              onClick={() => void confirmRelevance()}
              loading={relevanceMutation.saving}
              disabled={note.trim().length === 0}
            >
              Confirma que acredita
            </Button>
          </>
        }
      >
        {confirming && (
          <div className="space-y-4">
            <dl className="grid gap-3 border border-border bg-subtle px-4 py-3 text-[13px] sm:grid-cols-2">
              <div>
                <dt className="text-muted">Requisit</dt>
                <dd className="mt-0.5 text-ink">{confirming.requirement_title ?? confirming.label}</dd>
              </div>
              <div>
                <dt className="text-muted">Document triat</dt>
                <dd className="mt-0.5 text-ink">{confirming.document?.name ?? "—"}</dd>
              </div>
            </dl>
            <Field
              label="Motiu"
              hint={`${note.length} de ${RELEVANCE_NOTE_MAX_LENGTH} caràcters · obligatori`}
              error={relevanceMutation.error ?? undefined}
              required
            >
              <Textarea
                rows={4}
                maxLength={RELEVANCE_NOTE_MAX_LENGTH}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Per exemple: «És el certificat de bona execució de l'obra que el plec cita, signat per l'òrgan.»"
              />
            </Field>
          </div>
        )}
      </Modal>
    </Card>
  );
}
