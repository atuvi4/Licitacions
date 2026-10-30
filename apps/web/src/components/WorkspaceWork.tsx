"use client";

import { AlertTriangle, ChevronRight, Folder, FolderOpen } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";

import {
  Attribution,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  Field,
  Input,
  ResultNotice,
  Select,
  StatusPill,
  Textarea,
  cn,
  type DataTableColumn,
  type Level,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { Compliance } from "@/lib/compliance";
import {
  LIMIT_VERDICT_LABEL,
  formatAmount,
  formatDecimal,
  formatPercent,
  type EconomicOffer,
} from "@/lib/economic";
import {
  ENVELOPE_LABELS,
  documentsSummary,
  folderSummary,
  groupChecklist,
  nextActions,
  offerSummary,
  personnelPending,
  solvencyTopics,
  technicalSummary,
  type ChecklistFolder,
  type EnvelopeKey,
  type NextAction,
  type TopicTone,
} from "@/lib/envelopes";
import { useMutation, useResource, type Resource } from "@/lib/hooks";
import { CHECKLIST_LEVEL, TASK_LEVEL } from "@/lib/labels";
import { destinationHref, type ReadinessFinding } from "@/lib/readiness";
import type { TechnicalSolution } from "@/lib/technical";
import { formatDateTime } from "@/lib/tenders";
import {
  CHECKLIST_STATUS_LABELS,
  checklistReconciliationMessage,
  TASK_STATUS_LABELS,
  type ChecklistItem,
  type ChecklistItemStatus,
  type ChecklistRegenerateResult,
  type Dossier,
  type TaskStatus,
  type WorkspaceDetail,
  type WorkspaceNote,
  type WorkspaceTask,
} from "@/lib/workspace";

function base(workspace: WorkspaceDetail): string {
  return `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
}

function peopleOptions(workspace: WorkspaceDetail) {
  return [
    { value: "", label: "Sense assignar" },
    ...workspace.members.map((person) => ({
      value: person.id,
      // Només la gent de l'empresa: la llista ve dels memberships, no d'un
      // sistema de persones a part.
      label: person.full_name ?? person.email,
    })),
  ];
}

const CHECKLIST_OPTIONS = (Object.keys(CHECKLIST_STATUS_LABELS) as ChecklistItemStatus[]).map((value) => ({
  value,
  label: CHECKLIST_STATUS_LABELS[value],
}));
const TASK_OPTIONS = (Object.keys(TASK_STATUS_LABELS) as TaskStatus[]).map((value) => ({
  value,
  label: TASK_STATUS_LABELS[value],
}));
const CONTROL = "min-h-11 w-full md:min-h-0";

/* ------------------------------------------------------------------------- */
/* Tauler dels tres sobres                                                     */
/* ------------------------------------------------------------------------- */

const TONE_LEVEL: Record<TopicTone, Level> = {
  ready: "ready",
  attention: "attention",
  blocking: "blocking",
  neutral: "neutral",
};

/**
 * Enllaç relatiu a la mateixa candidatura: només canvia el pas i l'àrea. Sense dependre de la ruta actual, el
 * tauler es pot pintar (i provar) igual a qualsevol lloc; la licitació, en canvi, és una altra pàgina.
 */
function areaHref(area: "solucio" | "oferta" | "dossier" | "pla"): string {
  return destinationHref({ kind: "area", area }, { pathname: "", search: "", tenderId: null });
}

function EnvelopeCard({
  envelope,
  title,
  subtitle,
  href,
  linkLabel,
  children,
}: {
  envelope: EnvelopeKey;
  title: string;
  subtitle: string;
  href: string;
  linkLabel: string;
  children: ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <section data-envelope-card={envelope} aria-label={`${title} · ${subtitle}`} className="flex h-full flex-col">
        <header className="flex items-start gap-2.5 border-b border-border px-4 py-3">
          <Folder size={18} strokeWidth={1.75} className="mt-0.5 shrink-0 text-brand" aria-hidden />
          <div className="min-w-0">
            <h3 className="font-heading text-[15px] leading-tight font-semibold text-ink">{title}</h3>
            <p className="mt-0.5 text-[12.5px] text-muted">{subtitle}</p>
          </div>
        </header>
        <div className="flex-1 space-y-3 px-4 py-3 text-[13.5px] text-ink">{children}</div>
        <footer className="border-t border-border px-4 py-2.5 text-[13px]">
          <Link href={href} className="font-medium text-brand hover:underline">
            {linkLabel} →
          </Link>
        </footer>
      </section>
    </Card>
  );
}

/** Una lectura que no ha arribat (carregant o error) no és mai un «tot bé». */
function ResourceNote({
  resource,
  missing,
  failed,
}: {
  resource: Pick<Resource<unknown>, "data" | "loading" | "status">;
  missing: string;
  failed: string;
}) {
  if (resource.loading && !resource.data) return <p className="text-muted">Carregant…</p>;
  if (resource.status === 404) return <p className="text-muted">{missing}</p>;
  return <p className="text-muted">{failed}</p>;
}

function EnvelopeACard({ compliance, dossier }: { compliance: Resource<Compliance>; dossier: Resource<Dossier> }) {
  const run = compliance.data?.run ?? null;
  const topics = run && Array.isArray(compliance.data?.assessments) ? solvencyTopics(compliance.data) : [];
  const documents = dossier.data?.coverage ? documentsSummary(dossier.data.coverage) : null;
  return (
    <EnvelopeCard
      envelope="A"
      title="Sobre A"
      subtitle="Requisits i documentació administrativa"
      href={areaHref("dossier")}
      linkLabel="Obre el dossier"
    >
      {run ? (
        <dl className="space-y-2">
          {topics.map((topic) => (
            <div key={topic.key} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
              <dt className="text-muted">{topic.label}</dt>
              <dd className="text-right">
                <StatusPill level={TONE_LEVEL[topic.tone]} size="sm">
                  {topic.text}
                </StatusPill>
                {topic.companyHas && (
                  <span className="mt-0.5 block text-[12px] text-muted">L&apos;empresa declara: {topic.companyHas}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : compliance.data && !compliance.loading ? (
        <p className="text-muted">Encara no s&apos;ha calculat el compliment d&apos;aquesta licitació.</p>
      ) : (
        <ResourceNote resource={compliance} missing="Encara no s'ha calculat el compliment." failed="No s'ha pogut llegir el compliment." />
      )}
      <div className="border-t border-border pt-2.5">
        <p className="text-[12.5px] font-medium text-muted">Documents obligatoris</p>
        {documents ? (
          <p className="mt-1">
            <StatusPill level={TONE_LEVEL[documents.tone]} size="sm">
              {documents.text}
            </StatusPill>
          </p>
        ) : dossier.data ? (
          <p className="mt-1 text-muted">Sense anàlisi del plec no se sap quins documents són obligatoris.</p>
        ) : (
          <ResourceNote resource={dossier} missing="Encara no hi ha dossier." failed="No s'ha pogut llegir el dossier." />
        )}
      </div>
    </EnvelopeCard>
  );
}

function pointsText(points: number): string {
  return `${Number.isInteger(points) ? points : String(points).replace(".", ",")} punts`;
}

function EnvelopeBCard({ solutions, compliance }: { solutions: Resource<TechnicalSolution[]>; compliance: Compliance | null }) {
  const list = Array.isArray(solutions.data) ? solutions.data : null;
  const summary = list ? technicalSummary(list) : null;
  const personnel = personnelPending(compliance);
  const alerts: string[] = [];
  for (const title of personnel.titles) alerts.push(`Personal per confirmar: «${title}»`);
  if (personnel.total > personnel.titles.length) {
    alerts.push(`i ${personnel.total - personnel.titles.length} requisits de personal més per revisar`);
  }
  if (summary && summary.confirmations > 0) {
    alerts.push(
      `${summary.confirmations} ${summary.confirmations === 1 ? "punt marcat" : "punts marcats"} «Cal confirmar» als capítols`,
    );
  }
  if (summary && summary.stale > 0) {
    alerts.push(`${summary.stale} ${summary.stale === 1 ? "validació ha" : "validacions han"} deixat de valer`);
  }
  return (
    <EnvelopeCard
      envelope="B"
      title="Sobre B"
      subtitle="Proposta tècnica (judici de valor)"
      href={areaHref("solucio")}
      linkLabel="Obre la solució tècnica"
    >
      {summary ? (
        summary.total === 0 ? (
          <p className="text-muted">Encara no hi ha cap capítol de la memòria tècnica.</p>
        ) : (
          <>
            <p className="font-heading text-[18px] leading-tight font-semibold">
              {summary.drafted} de {summary.total} capítols redactats
              {summary.points !== null && <span className="text-[14px] font-normal text-muted"> · {pointsText(summary.points)}</span>}
            </p>
            <p className="flex flex-wrap gap-1.5">
              <StatusPill level={summary.validated === summary.total ? "ready" : "neutral"} size="sm">
                {summary.validated} {summary.validated === 1 ? "validat" : "validats"}
              </StatusPill>
              {summary.awaitingValidation > 0 && (
                <StatusPill level="attention" size="sm">
                  {summary.awaitingValidation} pendents de validar
                </StatusPill>
              )}
            </p>
          </>
        )
      ) : (
        <ResourceNote resource={solutions} missing="Encara no hi ha cap capítol." failed="No s'han pogut llegir les solucions tècniques." />
      )}
      {alerts.length > 0 && (
        <div className="border-t border-border pt-2.5">
          <p className="text-[12.5px] font-medium text-muted">Requereix atenció</p>
          <ul className="mt-1 space-y-1">
            {alerts.map((alert) => (
              <li key={alert} className="flex gap-1.5 text-[13px]">
                <span aria-hidden className="text-warning">●</span>
                <span>{alert}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </EnvelopeCard>
  );
}

function Figure({ label, value, detail }: { label: string; value: string; detail?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] text-muted">{label}</dt>
      <dd className="font-heading text-[17px] leading-tight font-semibold tabular-nums">{value}</dd>
      {detail && <dd className="text-[12px] text-muted">{detail}</dd>}
    </div>
  );
}

function EnvelopeCCard({ offer }: { offer: Resource<EconomicOffer> }) {
  const summary = offer.data && offer.data.totals ? offerSummary(offer.data) : null;
  const discount = summary?.discountPct ? formatDecimal(summary.discountPct, 2) : null;
  return (
    <EnvelopeCard
      envelope="C"
      title="Sobre C"
      subtitle="Oferta econòmica"
      href={areaHref("oferta")}
      linkLabel="Obre l'oferta econòmica"
    >
      {summary ? (
        <>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5">
            <Figure label="Preu ofert (sense IVA)" value={formatAmount(summary.revenue)} />
            <Figure label="Baixa sobre el PBL" value={discount ? `${discount} %` : "—"} />
            <Figure label="PBL màxim (sense IVA)" value={formatAmount(summary.tenderMax)} />
            {summary.margin !== null && (
              <Figure
                label="Marge de la cistella"
                value={formatAmount(summary.margin)}
                detail={summary.marginPct !== null ? `${formatPercent(summary.marginPct)} sobre vendes` : undefined}
              />
            )}
          </dl>
          <p className="flex flex-wrap items-center gap-1.5">
            {summary.verdict && (
              <StatusPill level={summary.verdict === "within" ? "ready" : summary.verdict === "exceeds" ? "blocking" : "neutral"} size="sm">
                {LIMIT_VERDICT_LABEL[summary.verdict]}
              </StatusPill>
            )}
            <span className="text-[12.5px] text-muted">
              {summary.lines} {summary.lines === 1 ? "línia" : "línies"}
              {summary.incompleteLines > 0 && ` · ${summary.incompleteLines} incompletes`}
            </span>
          </p>
          {summary.margin !== null && (
            <p className="text-[12px] leading-snug text-muted" data-margin-note>
              El marge de la cistella surt de les línies de l&apos;oferta (quantitats i costos que hi ha escrits), no de la
              facturació real.
              {summary.hasNotes && " El marge net previst de l'escandall és a les notes de l'oferta."}
            </p>
          )}
        </>
      ) : (
        <ResourceNote resource={offer} missing="Encara no hi ha oferta econòmica." failed="No s'ha pogut llegir l'oferta econòmica." />
      )}
    </EnvelopeCard>
  );
}

function NextActionsBox({
  actions,
  loading,
  tenderId,
}: {
  actions: NextAction[] | null;
  loading: boolean;
  tenderId: string;
}) {
  return (
    <Card className="border-l-4 border-l-brand">
      <section aria-label="Què et falta per tancar l'oferta?" data-next-actions>
        <CardHeader
          title="Què et falta per tancar l'oferta?"
          description="Només el que impedeix passar a la revisió final, agrupat. El detall és a cada àrea."
        />
        {actions === null ? (
          <p className="px-5 py-3 text-[13.5px] text-muted">
            {loading ? "Comprovant la preparació final…" : "No s'ha pogut llegir la preparació final: no es pot dir què falta."}
          </p>
        ) : actions.length === 0 ? (
          <p className="px-5 py-3 text-[13.5px] text-ink">Res no impedeix passar a la revisió final.</p>
        ) : (
          <ol className="divide-y divide-border">
            {actions.map((action) => (
              <li key={action.key} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-5 py-3">
                <div className="flex min-w-0 items-start gap-2.5">
                  {action.countable ? (
                    <StatusPill level="blocking" size="sm" className="mt-0.5 shrink-0 tabular-nums">
                      {action.count}
                    </StatusPill>
                  ) : (
                    <AlertTriangle size={16} strokeWidth={1.75} aria-hidden className="mt-0.5 shrink-0 text-danger" />
                  )}
                  <div className="min-w-0">
                    <p className="text-[14px] leading-5 font-medium text-ink">{action.label}</p>
                    {action.hint && <p className="mt-0.5 text-[12.5px] text-muted">{action.hint}</p>}
                  </div>
                </div>
                {action.destination && action.action && (
                  <Link
                    href={destinationHref(action.destination, { pathname: "", search: "", tenderId })}
                    className="shrink-0 text-[13px] font-medium text-brand hover:underline"
                  >
                    {action.action} →
                  </Link>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </Card>
  );
}

/**
 * El tauler executiu del pas Preparar: els tres sobres (A, B i C) i el que falta per tancar l'oferta.
 *
 * Només llegeix. Cap xifra no es calcula aquí que el servidor no hagi calculat abans: el compliment, les
 * solucions, l'oferta i la preparació final són els mateixos recursos que pinten les àrees.
 */
export function PrepareOverview({
  workspace,
  findings,
  readinessLoading = false,
}: {
  workspace: WorkspaceDetail;
  findings: ReadinessFinding[] | null;
  readinessLoading?: boolean;
}) {
  const compliance = useResource<Compliance>(`/companies/${workspace.company_id}/tenders/${workspace.tender_id}/compliance`);
  const solutions = useResource<TechnicalSolution[]>(`${base(workspace)}/technical-solutions`);
  const offer = useResource<EconomicOffer>(`${base(workspace)}/economic-offer`);
  const dossier = useResource<Dossier>(`${base(workspace)}/dossier`);
  const complianceData = compliance.data?.run && Array.isArray(compliance.data.assessments) ? compliance.data : null;
  const technical = Array.isArray(solutions.data) ? technicalSummary(solutions.data) : null;
  const actions = Array.isArray(findings) ? nextActions(findings, complianceData, technical) : null;
  return (
    <div className="space-y-4" data-prepare-overview>
      <div className="grid gap-4 lg:grid-cols-3">
        <EnvelopeACard compliance={compliance} dossier={dossier} />
        <EnvelopeBCard solutions={solutions} compliance={complianceData} />
        <EnvelopeCCard offer={offer} />
      </div>
      <NextActionsBox actions={actions} loading={readinessLoading} tenderId={workspace.tender_id} />
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Checklist                                                                   */
/* ------------------------------------------------------------------------- */

/**
 * Una carpeta de la checklist, plegada per defecte. Un `details` i no un botó: dins d'un `fieldset`
 * desactivat (Preparar sense GO) un botó no es pot prémer, i la checklist s'ha de poder consultar igual.
 * La taula només es munta en obrir-la: 256 files amb dos selectors cadascuna no s'han de pintar per res.
 */
function ChecklistFolderView({
  folder,
  columns,
}: {
  folder: ChecklistFolder;
  columns: Array<DataTableColumn<ChecklistItem>>;
}) {
  const [open, setOpen] = useState(false);
  const label = ENVELOPE_LABELS[folder.key];
  const Icon = open ? FolderOpen : Folder;
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      data-checklist-folder={folder.key}
      className="group border-b border-border last:border-b-0"
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2.5 px-3.5 py-3 hover:bg-subtle [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} aria-hidden className={cn("shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <Icon size={18} strokeWidth={1.75} aria-hidden className="shrink-0 text-brand" />
        <span className="min-w-0 flex-1">
          <span className="text-[14px] font-medium text-ink">{label.title}</span>
          <span className="text-[13px] text-muted"> · {label.subtitle}</span>
          <span className="mt-0.5 block text-[12.5px] text-muted" data-folder-summary>
            {folderSummary(folder)}
          </span>
        </span>
        {folder.mandatoryOpen > 0 && (
          <StatusPill level="attention" size="sm" outline className="shrink-0">
            {folder.mandatoryOpen} {folder.mandatoryOpen === 1 ? "obligatori" : "obligatoris"}
          </StatusPill>
        )}
      </summary>
      {open &&
        (folder.total > 0 ? (
          <DataTable columns={columns} rows={folder.items} rowKey={(item) => item.id} stackBelow="md" />
        ) : (
          <p className="px-3.5 pb-3 text-[13px] text-muted">
            {folder.key === "C" ? (
              <>
                La proposta econòmica no surt de la checklist: es prepara a{" "}
                <Link href={areaHref("oferta")} className="font-medium text-brand hover:underline">
                  l&apos;oferta econòmica
                </Link>
                .
              </>
            ) : (
              "Cap ítem en aquesta carpeta."
            )}
          </p>
        ))}
    </details>
  );
}

/**
 * Checklist derivada dels requisits.
 *
 * Cada item generat conserva l'enllaç al requisit original: «Veure requisit» no
 * és decoració, és el que permet comprovar d'on surt la tasca sense sortir a
 * buscar-ho al plec. Restil (handoff L13): la lògica i els endpoints són els
 * de sempre.
 */
export function ChecklistSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [title, setTitle] = useState("");
  const [outcome, setOutcome] = useState<ReturnType<typeof checklistReconciliationMessage> | null>(null);

  const done = workspace.checklist.filter((item) => item.status === "completed").length;

  async function update(itemId: string, patch: Record<string, unknown>) {
    await mutation.run(async () => {
      await api.patch(`${base(workspace)}/checklist/${itemId}`, patch);
    });
    await onChange();
  }

  async function addManual() {
    if (!title.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/checklist`, { title: title.trim() });
    });
    if (ok) {
      setTitle("");
      await onChange();
    }
  }

  async function regenerate() {
    await mutation.run(async () => {
      const result = await api.post<ChecklistRegenerateResult>(`${base(workspace)}/checklist/regenerate`, {});
      // Sense això l'acció responia 200 i no passava res visible: ni un item,
      // ni un missatge, ni una pista. I era el botó que l'avís et demanava
      // prémer.
      setOutcome(checklistReconciliationMessage(result));
    });
    await onChange();
  }

  const columns: Array<DataTableColumn<ChecklistItem>> = [
    {
      key: "title",
      header: "Cal fer",
      cell: (item) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{item.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
            {item.mandatory && (
              <StatusPill level="attention" size="sm" outline>
                Obligatori
              </StatusPill>
            )}
            {item.assigned_to_name && <span>{item.assigned_to_name}</span>}
            {item.note && <span>· {item.note}</span>}
          </p>
        </div>
      ),
    },
    {
      key: "status",
      header: "Estat",
      width: "22%",
      cell: (item) => (
        <div className="flex flex-col gap-1.5">
          <StatusPill level={CHECKLIST_LEVEL[item.status]} size="sm" className="self-start">
            {CHECKLIST_STATUS_LABELS[item.status]}
          </StatusPill>
          <Select
            aria-label="Estat"
            className={CONTROL}
            value={item.status}
            options={CHECKLIST_OPTIONS}
            onChange={(event) => void update(item.id, { status: event.target.value })}
          />
        </div>
      ),
    },
    {
      key: "assignee",
      header: "Responsable",
      width: "22%",
      cell: (item) => (
        <Select
          aria-label="Responsable"
          className={CONTROL}
          value={item.assigned_to ?? ""}
          options={peopleOptions(workspace)}
          onChange={(event) => void update(item.id, { assigned_to: event.target.value || null })}
        />
      ),
    },
    {
      key: "origin",
      header: "Origen",
      width: "16%",
      cell: (item) =>
        item.requirement_id ? (
          <Link href={`/licitacions/${workspace.tender_id}?tab=requisits`} className="text-[13px] text-brand hover:underline">
            Veure requisit →
          </Link>
        ) : (
          <StatusPill level="neutral" size="sm" outline>
            Manual
          </StatusPill>
        ),
    },
  ];

  const folders = groupChecklist(workspace.checklist);

  return (
    <Card>
      <CardHeader
        title="Checklist"
        description={`${done} de ${workspace.checklist.length} completats · derivada dels requisits del plec · agrupada per sobres`}
        action={
          <Button size="sm" variant="secondary" onClick={() => void regenerate()} loading={mutation.saving}>
            Reconcilia amb l&apos;anàlisi
          </Button>
        }
      />
      {outcome && (
        <div className="px-3.5 pt-3">
          <ResultNotice message={outcome.message} detail={outcome.detail} tone={outcome.tone} onDismiss={() => setOutcome(null)} />
        </div>
      )}
      {workspace.checklist.length === 0 ? (
        <EmptyState
          title="Encara no hi ha cap item"
          description="La checklist es deriva dels requisits del plec. Si el plec ja està analitzat, reconcilia-la; si no, afegeix els items a mà."
          action={
            <Button variant="primary" size="md" onClick={() => void regenerate()} loading={mutation.saving}>
              Reconcilia amb l&apos;anàlisi
            </Button>
          }
        />
      ) : (
        <div data-checklist-folders>
          {folders.map((folder) => (
            <ChecklistFolderView key={folder.key} folder={folder} columns={columns} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-3.5 py-3">
        <Field label="Afegeix un item manual" className="min-w-64 flex-1">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Preparar annex 3" />
        </Field>
        <Button variant="secondary" onClick={() => void addManual()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Afegeix
        </Button>
      </div>
      {mutation.error && <p className="px-3.5 pb-3 text-[13px] text-danger">{mutation.error}</p>}
    </Card>
  );
}

/** Tasques manuals: la feina que no surt de cap requisit. */
export function TasksSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dueAt, setDueAt] = useState("");
  const titleField = useRef<HTMLDivElement>(null);

  async function create() {
    if (!title.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/tasks`, {
        title: title.trim(),
        assigned_to: assignee || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
    });
    if (ok) {
      setTitle("");
      setAssignee("");
      setDueAt("");
      await onChange();
    }
  }

  async function update(taskId: string, patch: Record<string, unknown>) {
    await mutation.run(async () => {
      await api.patch(`${base(workspace)}/tasks/${taskId}`, patch);
    });
    await onChange();
  }

  const open = workspace.tasks.filter((task) => task.status === "pending" || task.status === "in_progress").length;

  const columns: Array<DataTableColumn<WorkspaceTask>> = [
    {
      key: "title",
      header: "Tasca",
      cell: (task) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{task.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
            {task.mandatory && (
              <StatusPill level="attention" size="sm" outline>
                Obligatòria
              </StatusPill>
            )}
            {task.description && <span>{task.description}</span>}
          </p>
        </div>
      ),
    },
    {
      key: "status",
      header: "Estat",
      width: "22%",
      cell: (task) => (
        <div className="flex flex-col gap-1.5">
          <StatusPill level={TASK_LEVEL[task.status]} size="sm" className="self-start">
            {TASK_STATUS_LABELS[task.status]}
          </StatusPill>
          <Select
            aria-label="Estat"
            className={CONTROL}
            value={task.status}
            options={TASK_OPTIONS}
            onChange={(event) => void update(task.id, { status: event.target.value })}
          />
        </div>
      ),
    },
    {
      key: "assignee",
      header: "Responsable",
      width: "22%",
      cell: (task) => (
        <Select
          aria-label="Responsable"
          className={CONTROL}
          value={task.assigned_to ?? ""}
          options={peopleOptions(workspace)}
          onChange={(event) => void update(task.id, { assigned_to: event.target.value || null })}
        />
      ),
    },
    {
      key: "due",
      header: "Venciment",
      width: "18%",
      cell: (task) => <span className="text-[13px] text-muted">{task.due_at ? formatDateTime(task.due_at) : "—"}</span>,
    },
  ];

  return (
    <Card id="tasques">
      <CardHeader
        title="Tasques"
        description={`${open === 0 ? "Cap tasca oberta" : `${open} ${open === 1 ? "oberta" : "obertes"}`} · feina de preparació de la candidatura`}
      />
      {workspace.tasks.length === 0 ? (
        <EmptyState
          title="Cap tasca encara"
          description="Les tasques són la feina que no surt de cap requisit. Escriu la primera aquí sota."
          action={
            <Button variant="secondary" size="md" onClick={() => titleField.current?.querySelector("input")?.focus()}>
              Escriu la primera tasca
            </Button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={workspace.tasks} rowKey={(task) => task.id} stackBelow="md" />
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-3.5 py-3">
        <div ref={titleField} className="flex min-w-56 flex-1">
          <Field label="Nova tasca" className="flex-1">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Demanar certificat bancari" />
          </Field>
        </div>
        <Field label="Responsable" className="w-52">
          <Select value={assignee} options={peopleOptions(workspace)} onChange={(event) => setAssignee(event.target.value)} />
        </Field>
        <Field label="Venciment" className="w-44">
          <Input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
        </Field>
        <Button variant="secondary" onClick={() => void create()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Crea
        </Button>
      </div>
      {mutation.error && <p className="px-3.5 pb-3 text-[13px] text-danger">{mutation.error}</p>}
    </Card>
  );
}

/**
 * Notes: text pla, ordenades de la més recent a la més antiga. Es poden corregir i
 * esborrar (Bloc 1) amb la mateixa guarda que escriure-les: dins de `StudyArea`, un
 * NO-GO vigent desactiva tots els botons. Corregir i esborrar, només els rols que
 * també treballen els dubtes (`canEdit`, com l'API).
 */
export function NotesSection({
  workspace,
  onChange,
  canEdit = true,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
  canEdit?: boolean;
}) {
  const mutation = useMutation();
  const [text, setText] = useState("");

  async function create() {
    if (!text.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/notes`, { text: text.trim() });
    });
    if (ok) {
      setText("");
      await onChange();
    }
  }

  return (
    <Card>
      <CardHeader title="Notes" description={workspace.notes.length === 0 ? "Cap nota encara." : undefined} />
      <div className="space-y-3 px-3.5 py-3">
        <Field label="Escriu una nota">
          <Textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder="Parlat amb el responsable del contracte…" />
        </Field>
        <Button variant="secondary" onClick={() => void create()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Afegeix la nota
        </Button>
        {mutation.error && <p className="text-[13px] text-danger">{mutation.error}</p>}
      </div>
      {workspace.notes.length > 0 && (
        <ol className="divide-y divide-border border-t border-border">
          {workspace.notes.map((note) => (
            <NoteItem key={note.id} note={note} workspace={workspace} onChange={onChange} canEdit={canEdit} />
          ))}
        </ol>
      )}
    </Card>
  );
}

/** Una nota: es llegeix, es corregeix al mateix lloc o s'esborra amb confirmació. */
function NoteItem({
  note,
  workspace,
  onChange,
  canEdit,
}: {
  note: WorkspaceNote;
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
  canEdit: boolean;
}) {
  const mutation = useMutation();
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const [draft, setDraft] = useState(note.text);
  // Per als lectors de pantalla, cada botó diu de quina nota parla.
  const which = `la nota de ${note.author_name ?? "una persona"} del ${formatDateTime(note.created_at)}`;
  const emptyDraft = !draft.trim();

  function startEditing() {
    setDraft(note.text);
    mutation.reset();
    setMode("edit");
  }

  function startDeleting() {
    mutation.reset();
    setMode("delete");
  }

  function cancel() {
    mutation.reset();
    setMode("view");
  }

  async function save() {
    const text = draft.trim();
    if (!text) return;
    if (text === note.text) {
      setMode("view");
      return;
    }
    const ok = await mutation.run(async () => {
      await api.patch(`${base(workspace)}/notes/${note.id}`, { text });
    });
    if (ok) {
      setMode("view");
      await onChange();
    }
  }

  async function remove() {
    const ok = await mutation.run(async () => {
      await api.delete(`${base(workspace)}/notes/${note.id}`);
    });
    if (ok) await onChange();
  }

  return (
    <li className="px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Attribution kind="human" person={{ name: note.author_name ?? "Una persona" }} at={note.created_at} />
        {mode === "view" && canEdit && (
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" onClick={startEditing} aria-label={`Corregeix ${which}`}>
              Corregeix
            </Button>
            <Button variant="ghost" size="sm" onClick={startDeleting} aria-label={`Esborra ${which}`}>
              Esborra
            </Button>
          </div>
        )}
      </div>
      {mode === "edit" ? (
        <div className="mt-2 space-y-2">
          <Field label="Text de la nota" hint={emptyDraft ? "Una nota no pot quedar buida: per treure-la, esborra-la." : undefined}>
            <Textarea rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => void save()} loading={mutation.saving} disabled={emptyDraft}>
              Desa la correcció
            </Button>
            <Button variant="ghost" size="sm" onClick={cancel} disabled={mutation.saving}>
              Cancel·la
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-1 text-[13px] leading-5 whitespace-pre-line text-ink">{note.text}</p>
      )}
      {note.updated_at && mode !== "edit" && (
        <p className="mt-1 text-[12px] text-muted">Corregida el {formatDateTime(note.updated_at)}</p>
      )}
      {mode === "delete" && (
        <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Confirma l'esborrat">
          <p className="text-[13px] text-ink">S&apos;esborrarà definitivament. Ho vols fer?</p>
          <Button variant="danger" size="sm" onClick={() => void remove()} loading={mutation.saving} autoFocus>
            Esborra-la
          </Button>
          <Button variant="ghost" size="sm" onClick={cancel} disabled={mutation.saving}>
            Cancel·la
          </Button>
        </div>
      )}
      {mutation.error && <p className="mt-1 text-[13px] text-danger">{mutation.error}</p>}
    </li>
  );
}
