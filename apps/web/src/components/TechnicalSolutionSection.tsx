"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { DimensionsSubsection } from "@/components/DimensionsSubsection";
import { ConfirmDialog, Modal } from "@/components/Modal";
import { useInvalidateReadiness, useReadiness } from "@/components/ReadinessProvider";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  EvidenceRef,
  Field,
  Input,
  LoadingRows,
  PermissionNote,
  Select,
  StatusPill,
  Textarea,
  cn,
  type DataTableColumn,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import { CATEGORY_LABELS, OBLIGATION_LABELS, type Requirement, type RequirementAnalysis, type RequirementEvidence } from "@/lib/requirements";
import {
  ROW_FILTERS,
  SOLUTION_STATUS_LABELS,
  SOLUTION_STATUS_LEVEL,
  WORK_STATUSES,
  applyFilter,
  awaitsValidation,
  canEditSolutions,
  diffDraft,
  draftFrom,
  groupRows,
  isDirty,
  isRequirementMissing,
  joinRequirements,
  memberName,
  requirementsInScope,
  scopeDependsOnOffer,
  staleReasonLabel,
  whoCanEditSolutions,
  type RequirementRow,
  type RowFilter,
  type SolutionDraft,
  type TechnicalSolution,
  type TechnicalSolutionCreateResult,
  type TechnicalSolutionPatch,
} from "@/lib/technical";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const LINK = "text-[13px] text-brand hover:underline";

/** Els errors de domini d'S3 que tenen una frase pròpia. La resta, el missatge del servidor. */
const DOMAIN_ERROR_COPY: Record<string, string> = {
  technical_solution_not_validatable: "No es pot validar una solució sense proposta escrita.",
  technical_solution_status_not_writable: "Una solució només es pot donar per validada des de la validació.",
  technical_scope_not_confirmable: "Primer cal triar a quins lots es presenta la candidatura.",
};

function humanError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return DOMAIN_ERROR_COPY[error.code] ?? error.message;
  return error instanceof Error ? error.message : fallback;
}

/** El mateix enllaç que la fitxa de la licitació: a la revisió citada, no a la vigent. */
function evidenceHref(tenderId: string, evidence: RequirementEvidence): string | null {
  return evidence.tender_document_id
    ? `/licitacions/${tenderId}/documents/${evidence.tender_document_id}?page=${evidence.page_number}&extraction=${evidence.extraction_id}`
    : null;
}

interface OfferLots {
  /** Cert només al cas C: plec amb lots i candidatura global. */
  can_select_lots: boolean;
  lots: Array<{ tender_lot_id: string | null; archived_at: string | null }>;
}

interface Props {
  workspace: WorkspaceDetail;
  /**
   * Perquè la pàgina pugui preguntar abans de canviar d'àrea. Rep una funció
   * que resol `true` si es pot marxar (després de demanar-ho, si cal).
   */
  registerLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void;
}

/**
 * Preparar → Solució tècnica.
 *
 * Del requisit a la validació, en una sola taula: què demana el plec, si algú
 * hi ha posat una solució, en quin punt és, qui la porta i què toca. L'edició
 * s'obre a dins, sota el requisit, perquè el text del plec no marxi de la vista
 * mentre s'escriu.
 *
 * El que NO fa: deduir que un requisit necessita solució (ho decideix una
 * persona amb «Defineix una solució»), ni calcular la preparació final (la diu
 * S6 i viu al context).
 */
export function TechnicalSolutionSection({ workspace, registerLeaveGuard }: Props) {
  const { companyId, membership, user } = useSession();
  const readiness = useReadiness();
  const invalidateReadiness = useInvalidateReadiness();
  const searchParams = useSearchParams();
  const canEdit = canEditSolutions(membership.role);
  const base = `/companies/${companyId}/workspaces/${workspace.id}`;

  const analysis = useResource<RequirementAnalysis>(`/tenders/${workspace.tender_id}/analysis/requirements`);
  const solutions = useResource<TechnicalSolution[]>(`${base}/technical-solutions`);
  // Cas C de l'abast: candidatura global sobre un plec amb lots. Els lots on
  // ens presentem els diu l'oferta, i només llavors es llegeix.
  const needsOffer = analysis.data ? scopeDependsOnOffer(analysis.data.requirements, workspace) : false;
  const offer = useResource<OfferLots>(needsOffer ? `${base}/economic-offer` : null);

  const selectedLotIds = useMemo(() => {
    const ids = new Set<string>();
    for (const lot of offer.data?.lots ?? []) {
      if (lot.tender_lot_id && !lot.archived_at) ids.add(lot.tender_lot_id);
    }
    return ids;
  }, [offer.data]);

  const inScope = useMemo(
    () => (analysis.data ? requirementsInScope(analysis.data.requirements, workspace, selectedLotIds) : []),
    [analysis.data, workspace, selectedLotIds],
  );
  const joined = useMemo(
    () => joinRequirements(inScope, analysis.data?.requirements ?? [], solutions.data ?? []),
    [inScope, analysis.data, solutions.data],
  );

  /* ----- Fila oberta i esborrany ---------------------------------------- */

  const [openId, setOpenId] = useState<string | null>(null);
  const [original, setOriginal] = useState<TechnicalSolution | null>(null);
  const [draft, setDraft] = useState<SolutionDraft | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useMutation();
  const dirty = Boolean(original && draft && isDirty(original, draft));

  // «Vols perdre els canvis?» com a promesa: qui pregunta espera la resposta.
  const [discardOpen, setDiscardOpen] = useState(false);
  const discardResolver = useRef<((ok: boolean) => void) | null>(null);
  const confirmDiscard = useCallback((): Promise<boolean> => {
    if (!dirty) return Promise.resolve(true);
    return new Promise((resolve) => {
      discardResolver.current = resolve;
      setDiscardOpen(true);
    });
  }, [dirty]);
  const answerDiscard = (ok: boolean) => {
    setDiscardOpen(false);
    discardResolver.current?.(ok);
    discardResolver.current = null;
  };

  useEffect(() => {
    registerLeaveGuard?.(dirty ? confirmDiscard : null);
    return () => registerLeaveGuard?.(null);
  }, [dirty, confirmDiscard, registerLeaveGuard]);

  // Tancar la pestanya o recarregar amb canvis oberts: el navegador pregunta.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  function showRow(solution: TechnicalSolution) {
    setOpenId(solution.id);
    setOriginal(solution);
    setDraft(draftFrom(solution));
    setSaved(false);
    save.reset();
  }

  async function openRow(solution: TechnicalSolution) {
    if (openId === solution.id) return;
    if (!(await confirmDiscard())) return;
    showRow(solution);
  }

  async function fold() {
    if (!(await confirmDiscard())) return;
    setOpenId(null);
    setOriginal(null);
    setDraft(null);
  }

  const [filter, setFilterState] = useState<RowFilter>("all");
  async function setFilter(next: RowFilter) {
    if (next === filter) return;
    if (!(await confirmDiscard())) return;
    setFilterState(next);
  }

  /** Substitueix una fila a la llista amb el que ha tornat el servidor. */
  function replaceSolution(next: TechnicalSolution) {
    solutions.setData((solutions.data ?? []).map((item) => (item.id === next.id ? next : item)));
    if (openId === next.id) {
      setOriginal(next);
      setDraft(draftFrom(next));
    }
  }

  /* ----- Mutacions ------------------------------------------------------ */

  async function persist(patch: TechnicalSolutionPatch): Promise<boolean> {
    if (!original) return false;
    if (Object.keys(patch).length === 0) return true;
    let next: TechnicalSolution | null = null;
    const ok = await save.run(async () => {
      next = await api.patch<TechnicalSolution>(`${base}/technical-solutions/${original.id}`, patch);
    });
    if (ok && next) {
      replaceSolution(next);
      setSaved(true);
      await invalidateReadiness();
    }
    return ok;
  }

  async function saveDraft() {
    if (!original || !draft) return;
    if (draft.title.trim() === "") return;
    await persist(diffDraft(original, draft));
  }

  async function saveAndSend() {
    if (!original || !draft) return;
    if (draft.title.trim() === "") return;
    await persist({ ...diffDraft(original, draft), status: "needs_validation" });
  }

  // Doble clic a «Defineix una solució»: la segona crida no surt. El ref es
  // marca abans de qualsevol `await`, que és l'única manera que el segon clic
  // el trobi ja posat; l'estat només serveix per pintar el botó.
  const pendingRef = useRef<string | null>(null);
  const [pendingRequirement, setPendingRequirement] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
  async function defineSolution(requirement: Requirement) {
    if (pendingRef.current) return;
    pendingRef.current = requirement.id;
    if (!(await confirmDiscard())) {
      pendingRef.current = null;
      return;
    }
    setPendingRequirement(requirement.id);
    setRowError(null);
    try {
      const result = await api.post<TechnicalSolutionCreateResult>(`${base}/technical-solutions/from-requirement`, {
        requirement_id: requirement.id,
      });
      const list = await solutions.reload();
      showRow(list?.find((item) => item.id === result.solution.id) ?? result.solution);
      await invalidateReadiness();
    } catch (caught) {
      setRowError({ key: requirement.id, message: humanError(caught, "No s'ha pogut definir la solució.") });
    } finally {
      pendingRef.current = null;
      setPendingRequirement(null);
    }
  }

  const [manualOpen, setManualOpen] = useState(false);
  const [manualTitle, setManualTitle] = useState("");
  const manual = useMutation();
  async function createManual() {
    if (manualTitle.trim() === "") return;
    let created: TechnicalSolution | null = null;
    const ok = await manual.run(async () => {
      created = await api.post<TechnicalSolution>(`${base}/technical-solutions`, { title: manualTitle.trim() });
    });
    if (!ok || !created) return;
    setManualOpen(false);
    setManualTitle("");
    const list = await solutions.reload();
    showRow(list?.find((item) => item.id === created!.id) ?? created);
    await invalidateReadiness();
  }

  const [validating, setValidating] = useState<TechnicalSolution | null>(null);
  const [note, setNote] = useState("");
  const validation = useMutation();
  function openValidation(solution: TechnicalSolution) {
    validation.reset();
    setNote("");
    setValidating(solution);
  }
  async function validate() {
    if (!validating || note.trim() === "") return;
    let next: TechnicalSolution | null = null;
    const ok = await validation.run(async () => {
      next = await api.post<TechnicalSolution>(`${base}/technical-solutions/${validating.id}/validation`, { note: note.trim() });
    });
    if (!ok || !next) return;
    replaceSolution(next);
    setValidating(null);
    await invalidateReadiness();
  }

  const scope = useMutation();
  async function reviewScope() {
    const ok = await scope.run(async () => {
      await api.post(`${base}/technical-scope/review`, {});
    });
    // Si el servidor diu que encara no es pot confirmar (409), no s'ha revisat
    // res: la preparació no es toca i el missatge queda a la vista.
    if (ok) await invalidateReadiness();
  }
  const scopeError = scope.error ? humanError(scope.apiError ?? new Error(scope.error), scope.error) : null;

  /* ----- Enllaç profund (?entity=) -------------------------------------- */

  const entity = searchParams?.get("entity") ?? null;
  const [handledEntity, setHandledEntity] = useState<string | null>(null);
  const [linkNotice, setLinkNotice] = useState<string | null>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!entity || entity === handledEntity || !solutions.data) return;
    setHandledEntity(entity);
    const target = solutions.data.find((item) => item.id === entity);
    if (!target) {
      setLinkNotice("L'enllaç apuntava a una solució que no és en aquesta llista.");
      return;
    }
    setLinkNotice(null);
    showRow(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- showRow és estable en el que importa aquí
  }, [entity, handledEntity, solutions.data]);
  useEffect(() => {
    if (!entity || openId !== entity) return;
    const node = highlightRef.current;
    if (!node) return;
    node.scrollIntoView?.({ block: "center" });
    node.focus({ preventScroll: true });
  }, [entity, openId]);

  /* ----- Càrrega i error ------------------------------------------------ */

  const loading = (analysis.loading && !analysis.data) || (solutions.loading && !solutions.data) || (needsOffer && offer.loading && !offer.data && !offer.error);
  const loadError = (analysis.error && !analysis.data ? analysis.error : null) ?? (solutions.error && !solutions.data ? solutions.error : null);

  if (loadError) {
    return (
      <Card>
        <CardHeader title="Solució tècnica" />
        <EmptyState
          title="No s'ha pogut carregar la solució tècnica"
          description={loadError}
          action={
            <Button
              variant="primary"
              size="md"
              onClick={() => {
                void analysis.reload();
                void solutions.reload();
              }}
            >
              Torna-ho a provar
            </Button>
          }
        />
      </Card>
    );
  }
  if (loading) {
    return (
      <Card>
        <CardHeader title="Solució tècnica" />
        <div aria-busy="true" aria-label="Es carrega la solució tècnica">
          <LoadingRows rows={4} />
        </div>
      </Card>
    );
  }

  /* ----- Dades derivades per pintar ------------------------------------- */

  const visible = applyFilter(joined.rows, filter, user.id);
  const groups = groupRows(visible, (category) => CATEGORY_LABELS[category as keyof typeof CATEGORY_LABELS] ?? category);
  const manualRows: RequirementRow[] = joined.manual.map((solution) => ({ key: `manual-${solution.id}`, requirement: null, solution }));
  // Els comptadors parlen de l'abast d'ara: el que és història no hi suma.
  const withSolution = joined.rows.filter((row) => row.solution).length;
  const validated = joined.rows.filter((row) => row.solution?.status === "validated" && !row.solution.validation_stale).length;
  const technical = readiness.data?.technical ?? null;
  const scopeState: "unknown" | "unreviewed" | "reviewed" | "stale" = !technical
    ? "unknown"
    : technical.scope_stale
      ? "stale"
      : technical.scope_reviewed
        ? "reviewed"
        : "unreviewed";
  // L'abast que pinta la pantalla i el que compta el servidor han de ser el
  // mateix. Si no ho són, la pantalla no representa l'abast i ningú no pot
  // confirmar-lo: no se substitueix el número, es para.
  const scopeMismatch = technical !== null && technical.requirements_in_scope !== joined.rows.length;
  const scopeVerified = technical !== null && !scopeMismatch;
  // Cas C de C-16: plec amb lots, candidatura global i cap lot triat. L'abast
  // no és buit, és desconegut, i el servidor rebutja confirmar-lo.
  const lotsUndecided = offer.data?.can_select_lots === true && selectedLotIds.size === 0;
  const canReviewScope = canEdit && scopeVerified && !lotsUndecided;

  const expanded = (row: RequirementRow): ReactNode => {
    if (!row.solution || row.solution.id !== openId || !draft || !original) return null;
    return (
      <div ref={highlightRef} tabIndex={-1} data-highlight={entity === row.solution.id ? "true" : undefined} className={cn("outline-none", entity === row.solution.id && "ring-2 ring-inset ring-brand")}>
        {entity === row.solution.id && <span className="sr-only">Solució enllaçada des de la preparació final</span>}
        {canEdit ? (
          <SolutionEditor
            workspace={workspace}
            requirement={row.requirement}
            original={original}
            draft={draft}
            onDraft={(next) => {
              setSaved(false);
              setDraft(next);
            }}
            saving={save.saving}
            saved={saved}
            error={save.error}
            fieldErrors={save.fieldErrors}
            onSave={() => void saveDraft()}
            onSaveAndSend={() => void saveAndSend()}
            onCancel={() => void fold()}
            onValidate={() => openValidation(original)}
          />
        ) : (
          <SolutionDetails workspace={workspace} requirement={row.requirement} solution={row.solution} onClose={() => void fold()} />
        )}
      </div>
    );
  };

  const statusCell = (row: RequirementRow) => {
    const solution = row.solution;
    if (!solution) return <span className="text-muted">—</span>;
    const stale = solution.status === "validated" && solution.validation_stale;
    return (
      <div className="flex flex-col gap-0.5">
        {stale ? (
          <StatusPill level="attention" size="sm">
            Validació caducada
          </StatusPill>
        ) : (
          <StatusPill level={SOLUTION_STATUS_LEVEL[solution.status]} size="sm">
            {SOLUTION_STATUS_LABELS[solution.status]}
          </StatusPill>
        )}
        {stale && (
          <span className="text-caption leading-4 text-muted">
            {solution.validation_stale_reasons.map((reason) => staleReasonLabel(reason)).join(" · ")}
          </span>
        )}
        {isRequirementMissing(solution) ? (
          <StatusPill level="attention" size="sm" outline>
            Requisit no disponible
          </StatusPill>
        ) : (
          row.requirement === null &&
          solution.source === "requirement" && (
            <StatusPill level="neutral" size="sm" outline>
              Fora de l&apos;abast actual
            </StatusPill>
          )
        )}
      </div>
    );
  };

  const actionCell = (row: RequirementRow) => {
    const { requirement, solution } = row;
    const isOpen = solution !== null && solution.id === openId;
    if (!solution) {
      if (!requirement || !canEdit) return null;
      const pending = pendingRequirement === requirement.id;
      return (
        <div className="flex flex-col items-end gap-1">
          <Button variant="secondary" size="sm" loading={pending} disabled={pendingRequirement !== null} onClick={() => void defineSolution(requirement)}>
            Defineix una solució
          </Button>
          {rowError?.key === requirement.id && <span className="text-caption text-danger">{rowError.message}</span>}
        </div>
      );
    }
    if (isOpen) {
      return (
        <Button variant="ghost" size="sm" onClick={() => void fold()}>
          Plega
        </Button>
      );
    }
    if (!canEdit) {
      return (
        <Button variant="ghost" size="sm" onClick={() => void openRow(solution)}>
          Obre
        </Button>
      );
    }
    const label = solution.status === "not_started" ? "Comença" : solution.status === "drafting" ? "Continua" : "Obre";
    return (
      <div className="flex flex-wrap justify-end gap-1.5">
        {awaitsValidation(solution) && (
          <Button variant="primary" size="sm" onClick={() => openValidation(solution)}>
            {solution.validation_stale ? "Torna a validar" : "Valida-la"}
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={() => void openRow(solution)}>
          {label}
        </Button>
      </div>
    );
  };

  const requirementCell = (row: RequirementRow) => {
    const requirement = row.requirement;
    if (!requirement) {
      // Sense requisit a la mà hi ha dos motius possibles, i no són el mateix:
      // que el servidor no el pugui recuperar, o que simplement no sigui a
      // l'abast d'ara. Dir-ne «no disponible» sempre seria inventar-ne un.
      const lost = row.solution !== null && isRequirementMissing(row.solution);
      return (
        <div>
          <p className="font-medium text-ink">{lost ? "Requisit no disponible" : "Requisit fora de l'abast"}</p>
          <p className="text-caption leading-4 text-muted">
            {lost
              ? "El servidor no el pot recuperar."
              : "No consta a l'anàlisi vigent d'aquesta candidatura."}
          </p>
        </div>
      );
    }
    const primary = requirement.evidence.find((item) => item.evidence_role === "primary") ?? requirement.evidence[0] ?? null;
    const where = [OBLIGATION_LABELS[requirement.obligation], primary?.document_name ?? null, primary ? `p. ${primary.page_number}` : null]
      .filter((part): part is string => Boolean(part))
      .join(" · ");
    return (
      <div>
        <p className="font-medium text-ink">{requirement.title}</p>
        <p className="text-caption leading-4 text-muted">{where}</p>
      </div>
    );
  };

  const solutionCell = (row: RequirementRow) => {
    const solution = row.solution;
    if (!solution) return <span className="text-muted">Sense solució</span>;
    const detail = [solution.supplier_name, solution.product_name].filter(Boolean).join(" · ");
    return (
      <div>
        <p className="text-ink">{solution.title}</p>
        {detail && <p className="text-caption leading-4 text-muted">{detail}</p>}
      </div>
    );
  };

  const ownerCell = (row: RequirementRow) => {
    if (!row.solution) return null;
    const name = memberName(workspace, row.solution.owner_id);
    return name ? <span>{name}</span> : <span className="text-muted">Sense assignar</span>;
  };

  const columns: Array<DataTableColumn<RequirementRow>> = [
    { key: "requirement", header: "Requisit", width: "34%", cell: requirementCell },
    { key: "solution", header: "Solució", width: "26%", cell: solutionCell },
    { key: "status", header: "Estat", width: "16%", cell: statusCell },
    { key: "owner", header: "Responsable", width: "12%", cell: ownerCell, hideBelow: "md" },
    { key: "action", header: "Acció", align: "right", cell: actionCell },
  ];
  const manualColumns = columns.filter((column) => column.key !== "requirement");

  const reviewLabel = scopeState === "reviewed" ? "Torna a revisar l'abast" : "Confirma l'abast";
  const reviewButton = canReviewScope ? (
    <Button variant={scopeState === "stale" || scopeState === "unreviewed" ? "primary" : "secondary"} size="sm" loading={scope.saving} onClick={() => void reviewScope()}>
      {reviewLabel}
    </Button>
  ) : null;

  return (
    <>
      {/* ----- Abast tècnic ------------------------------------------------ */}
      {scopeMismatch && (
        <Banner
          level="blocking"
          kicker="Abast no representable"
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void analysis.reload();
                void solutions.reload();
                void invalidateReadiness();
              }}
            >
              Torna-ho a carregar
            </Button>
          }
        >
          <p>
            <strong>No s&apos;ha pogut representar l&apos;abast tècnic amb seguretat.</strong>{" "}
            <span className="text-muted">
              Aquesta pantalla mostra {joined.rows.length} {joined.rows.length === 1 ? "requisit" : "requisits"} i el servidor
              en compta {technical?.requirements_in_scope}. Mentre no coincideixin, l&apos;abast no es pot confirmar.
            </span>
          </p>
        </Banner>
      )}

      {scopeState === "stale" && !scopeMismatch && (
        <Banner
          level="attention"
          kicker="Abast caducat"
          action={reviewButton ?? <span className="text-caption text-muted">{canEdit ? "Ara mateix no es pot revisar." : `Només ${whoCanEditSolutions()} pot revisar-lo.`}</span>}
        >
          <p>
            <strong>El contingut que defineix l&apos;abast ha canviat des de l&apos;última revisió.</strong>{" "}
            <span className="text-muted">Cal revisar-lo de nou. Les solucions es conserven; només caduquen les validacions que S3 marqui com a caducades.</span>
          </p>
        </Banner>
      )}

      {linkNotice && (
        <p role="status" className="border border-border-strong bg-subtle px-3.5 py-2 text-caption text-muted">
          {linkNotice}
        </p>
      )}

      <Card>
        <CardHeader
          title="Solució tècnica"
          description={`${joined.rows.length} ${joined.rows.length === 1 ? "requisit" : "requisits"} · ${withSolution} amb solució · ${validated} ${validated === 1 ? "validada" : "validades"}`}
          action={canEdit ? <Button variant="secondary" size="sm" onClick={() => setManualOpen(true)}>Afegeix una solució</Button> : undefined}
        />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-3.5 py-2.5">
          <ScopeStatus state={scopeState} reviewedAt={technical?.scope_reviewed_at ?? null} reviewedBy={memberName(workspace, technical?.scope_reviewed_by ?? null)} />
          {scopeState !== "stale" && reviewButton}
          {scopeError && (
            <span role="alert" className="text-caption text-danger">
              {scopeError}
            </span>
          )}
          {canEdit && lotsUndecided && !scopeMismatch && (
            <span className="text-caption text-muted">
              Primer cal triar a quins lots es presenta la candidatura, a l&apos;oferta econòmica: fins llavors no se sap quins
              requisits formen l&apos;abast.
            </span>
          )}
          {offer.error && offer.status !== 404 && (
            <span className="text-caption text-warning">No s&apos;han pogut llegir els lots de l&apos;oferta: es mostren només els requisits generals.</span>
          )}
        </div>

        {!canEdit && (
          <div className="border-b border-border px-3.5 py-2">
            <PermissionNote who={whoCanEditSolutions()} what="editar i validar les solucions tècniques">
              <span className="text-caption text-muted">Només lectura.</span>
            </PermissionNote>
          </div>
        )}

        <div role="group" aria-label="Filtra les files" className="flex flex-wrap gap-1 border-b border-border px-3.5 py-2">
          {ROW_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={filter === item.key}
              onClick={() => void setFilter(item.key)}
              className={cn(
                "rounded-control px-2.5 py-1 text-[13px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-500",
                filter === item.key ? "bg-ink text-white" : "text-muted hover:text-ink",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>

        <DataTable<RequirementRow>
          columns={columns}
          rows={visible}
          groups={groups}
          rowKey={(row) => row.key}
          isSelected={(row) => row.solution !== null && row.solution.id === openId}
          expandedRow={expanded}
          stackBelow="md"
          emptyState={
            joined.rows.length === 0 ? (
              <EmptyState
                title="Cap requisit a l'abast"
                description={
                  analysis.data?.run
                    ? "L'anàlisi vigent no té cap requisit que apliqui a aquesta candidatura."
                    : "Encara no hi ha cap anàlisi de requisits del plec."
                }
                action={
                  <a href={`/licitacions/${workspace.tender_id}?tab=requisits`} className={LINK}>
                    Obre l&apos;anàlisi del plec →
                  </a>
                }
              />
            ) : (
              <p className="px-3.5 py-6 text-body text-muted">Cap fila amb aquest filtre.</p>
            )
          }
        />
      </Card>

      {/* ----- Fora de l'abast actual -------------------------------------- */}
      {joined.outOfScope.length > 0 && (
        <Card>
          <CardHeader
            title="Fora de l'abast actual"
            description="El requisit existeix, però ara no forma part d'aquesta candidatura. Es conserva i no demana res."
          />
          <DataTable<RequirementRow>
            columns={columns}
            rows={joined.outOfScope}
            rowKey={(row) => row.key}
            isSelected={(row) => row.solution !== null && row.solution.id === openId}
            expandedRow={expanded}
            stackBelow="md"
          />
        </Card>
      )}

      {/* ----- Requisit no disponible --------------------------------------- */}
      {joined.missing.length > 0 && (
        <Card>
          <CardHeader
            title="Requisit no disponible"
            description="El servidor no pot recuperar el requisit que aquestes solucions deien resoldre. Es conserven tal com eren."
          />
          <DataTable<RequirementRow>
            columns={columns}
            rows={joined.missing}
            rowKey={(row) => row.key}
            isSelected={(row) => row.solution !== null && row.solution.id === openId}
            expandedRow={expanded}
            stackBelow="md"
          />
        </Card>
      )}

      {/* ----- Dimensionament ---------------------------------------------- */}
      {/* Aqui i no en una pestanya propia: les xifres de que depen la proposta
          es decideixen mentre s'escriu la proposta, no en un altre moment. */}
      <DimensionsSubsection workspace={workspace} />

      {/* ----- Solucions addicionals -------------------------------------- */}
      <Card>
        <CardHeader title="Solucions addicionals" description="No responen a cap requisit del plec; sumen a la memòria tècnica." />
        <DataTable<RequirementRow>
          columns={manualColumns}
          rows={manualRows}
          rowKey={(row) => row.key}
          isSelected={(row) => row.solution !== null && row.solution.id === openId}
          expandedRow={expanded}
          stackBelow="md"
          emptyState={<p className="px-3.5 py-5 text-body text-muted">Cap solució addicional.</p>}
        />
      </Card>

      {/* ----- Diàlegs, fora de la taula perquè Escape no plegui la fila --- */}
      <ConfirmDialog
        open={discardOpen}
        title="Canvis sense desar"
        message="Si continues, els canvis que no has desat es perdran."
        confirmLabel="Descarta els canvis"
        onConfirm={() => answerDiscard(true)}
        onCancel={() => answerDiscard(false)}
      />

      <Modal
        open={manualOpen}
        title="Nova solució addicional"
        description="Una solució que no respon a cap requisit del plec."
        onClose={() => setManualOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setManualOpen(false)} disabled={manual.saving}>
              Cancel·la
            </Button>
            <Button variant="primary" onClick={() => void createManual()} loading={manual.saving} disabled={manualTitle.trim() === ""}>
              Crea-la
            </Button>
          </>
        }
      >
        <Field label="Títol" required error={manual.fieldErrors.title ?? (manual.error ?? undefined)}>
          <Input value={manualTitle} onChange={(event) => setManualTitle(event.target.value)} maxLength={500} autoFocus />
        </Field>
      </Modal>

      <Modal
        open={validating !== null}
        title={validating?.validation_stale ? "Torna a validar la solució" : "Valida la solució"}
        description={validating?.title}
        onClose={() => setValidating(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setValidating(null)} disabled={validation.saving}>
              Cancel·la
            </Button>
            <Button variant="primary" onClick={() => void validate()} loading={validation.saving} disabled={note.trim() === ""}>
              {validating?.validation_stale ? "Torna a validar" : "Valida"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-body text-muted">Signes que aquesta solució serveix per al requisit tal com és ara. Qui, quan i per què quedaran registrats.</p>
          <Field label="Per què serveix" required error={validation.fieldErrors.note ?? (validation.error ? humanError(validation.apiError ?? new Error(validation.error), validation.error) : undefined)}>
            <Textarea rows={4} value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} autoFocus />
          </Field>
        </div>
      </Modal>
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Estat de l'abast                                                           */
/* ------------------------------------------------------------------------- */

function ScopeStatus({ state, reviewedAt, reviewedBy }: { state: "unknown" | "unreviewed" | "reviewed" | "stale"; reviewedAt: string | null; reviewedBy: string | null }) {
  const pill =
    state === "reviewed" ? (
      <StatusPill level="ready" size="sm">
        Abast revisat
      </StatusPill>
    ) : state === "stale" ? (
      <StatusPill level="attention" size="sm">
        Revisió caducada
      </StatusPill>
    ) : state === "unreviewed" ? (
      <StatusPill level="neutral" size="sm">
        Abast sense revisar
      </StatusPill>
    ) : (
      <StatusPill level="neutral" size="sm">
        Abast: estat desconegut
      </StatusPill>
    );
  return (
    <div className="flex flex-wrap items-center gap-2">
      {pill}
      {state === "reviewed" && reviewedAt && (
        <span className="text-caption text-muted">
          {formatDateTime(reviewedAt)}
          {reviewedBy ? ` · ${reviewedBy}` : ""}
        </span>
      )}
      {state === "unreviewed" && <span className="text-caption text-muted">Ningú no ha dit encara quins requisits necessiten solució tècnica.</span>}
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Context del requisit                                                       */
/* ------------------------------------------------------------------------- */

function RequirementContext({ workspace, requirement, solution }: { workspace: WorkspaceDetail; requirement: Requirement | null; solution: TechnicalSolution }) {
  if (!requirement) {
    return (
      <div>
        <p className={KICKER}>Requisit</p>
        <p className="mt-1 text-body text-muted">
          {isRequirementMissing(solution)
            ? "El servidor no pot recuperar el requisit que aquesta solució deia resoldre. Es conserva tal com era."
            : solution.source === "manual"
              ? "Aquesta solució no respon a cap requisit del plec."
              : "El requisit d'aquesta solució no forma part de l'abast d'ara. Es conserva i no demana res."}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div>
        <p className={KICKER}>Requisit</p>
        <p className="mt-1 text-body font-medium text-ink">{requirement.title}</p>
        <p className="text-caption text-muted">
          {OBLIGATION_LABELS[requirement.obligation]} · {CATEGORY_LABELS[requirement.category]}
        </p>
      </div>
      {requirement.description && (
        <div>
          <p className={KICKER}>Descripció</p>
          <p className="mt-1 text-body leading-relaxed text-ink">{requirement.description}</p>
        </div>
      )}
      <div>
        {/* Només `evidence.quote` és text literal del plec: la resta és resum. */}
        <p className={KICKER}>{requirement.evidence.length > 0 ? "El plec, literal" : "Cita"}</p>
        <div className="mt-1 space-y-1.5">
          {requirement.evidence.length === 0 && <EvidenceRef documentName={null} pageNumber={null} href={null} />}
          {requirement.evidence.map((item) => (
            <EvidenceRef
              key={item.id}
              documentName={item.document_name}
              pageNumber={item.page_number}
              extractionRevision={item.extraction_revision}
              quote={item.quote}
              href={evidenceHref(workspace.tender_id, item)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ValidationSummary({ workspace, solution }: { workspace: WorkspaceDetail; solution: TechnicalSolution }) {
  const by = memberName(workspace, solution.validated_by);
  return (
    <div>
      <p className={KICKER}>Validació</p>
      {solution.status === "validated" && solution.validated_at ? (
        <div className="mt-1 space-y-0.5 text-body">
          <p className="text-ink">
            {solution.validation_stale ? "Validada, però ha deixat de valer" : "Validada"} · {formatDateTime(solution.validated_at)}
            {by ? ` · ${by}` : ""}
          </p>
          {solution.validated_note && <p className="text-muted">«{solution.validated_note}»</p>}
          {solution.validation_stale && (
            <ul className="list-disc pl-5 text-caption text-warning">
              {solution.validation_stale_reasons.map((reason) => (
                <li key={reason}>{staleReasonLabel(reason)}</li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <p className="mt-1 text-body text-muted">
          {solution.status === "needs_validation" ? "Pendent de validació. La pot validar qualsevol rol autoritzat." : "Encara no s'ha enviat a validar."}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Editor                                                                     */
/* ------------------------------------------------------------------------- */

function SolutionEditor({
  workspace,
  requirement,
  original,
  draft,
  onDraft,
  saving,
  saved,
  error,
  fieldErrors,
  onSave,
  onSaveAndSend,
  onCancel,
  onValidate,
}: {
  workspace: WorkspaceDetail;
  requirement: Requirement | null;
  original: TechnicalSolution;
  draft: SolutionDraft;
  onDraft: (next: SolutionDraft) => void;
  saving: boolean;
  saved: boolean;
  error: string | null;
  fieldErrors: Record<string, string>;
  onSave: () => void;
  onSaveAndSend: () => void;
  onCancel: () => void;
  onValidate: () => void;
}) {
  const dirty = isDirty(original, draft);
  const titleMissing = draft.title.trim() === "";
  const set = <K extends keyof SolutionDraft>(key: K, value: SolutionDraft[K]) => onDraft({ ...draft, [key]: value });
  const people = [
    { value: "", label: "Sense assignar" },
    ...workspace.members.map((person) => ({ value: person.id, label: person.full_name || person.email })),
  ];
  const state = saving ? "Desant…" : error ? null : dirty ? "Canvis sense desar" : saved ? "Desat" : null;

  // Esc plega; Ctrl/⌘+S desa. Només dins de l'editor, i sense tocar res més.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (!saving && !titleMissing) onSave();
    }
  };

  return (
    <div onKeyDown={onKeyDown} className="grid gap-5 px-3.5 py-4 lg:grid-cols-12">
      <form
        className="space-y-3 lg:col-span-7"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <p className="text-caption text-muted">Els canvis es desen quan prems Desa; sortir amb canvis oberts demana confirmació.</p>
        <Field label="Títol de la solució" required error={fieldErrors.title ?? (titleMissing ? "Cal un títol." : undefined)}>
          <Input value={draft.title} onChange={(event) => set("title", event.target.value)} maxLength={500} />
        </Field>
        <Field label="Proposta tècnica" hint="Text lliure, sense límit." error={fieldErrors.proposed_solution}>
          <Textarea rows={8} value={draft.proposed_solution} onChange={(event) => set("proposed_solution", event.target.value)} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Proveïdor" error={fieldErrors.supplier_name}>
            <Input value={draft.supplier_name} onChange={(event) => set("supplier_name", event.target.value)} maxLength={300} />
          </Field>
          <Field label="Producte" error={fieldErrors.product_name}>
            <Input value={draft.product_name} onChange={(event) => set("product_name", event.target.value)} maxLength={300} />
          </Field>
          <Field label="Responsable" error={fieldErrors.owner_id}>
            <Select options={people} value={draft.owner_id} onChange={(event) => set("owner_id", event.target.value)} />
          </Field>
          {original.status !== "validated" ? (
            <Field label="Estat" error={fieldErrors.status}>
              <Select
                options={WORK_STATUSES.map((status) => ({ value: status, label: SOLUTION_STATUS_LABELS[status] }))}
                value={draft.status}
                onChange={(event) => set("status", event.target.value as SolutionDraft["status"])}
              />
            </Field>
          ) : (
            <Field label="Estat" hint="Validada. Si en canvies el contingut, la validació deixarà de valer.">
              <Input value={SOLUTION_STATUS_LABELS.validated} readOnly />
            </Field>
          )}
        </div>
        <Field label="Notes" error={fieldErrors.notes}>
          <Textarea rows={2} value={draft.notes} onChange={(event) => set("notes", event.target.value)} maxLength={4000} />
        </Field>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button type="submit" variant="primary" size="sm" loading={saving} disabled={titleMissing || !dirty}>
            Desa
          </Button>
          {original.status !== "needs_validation" && (
            <Button type="button" variant="secondary" size="sm" disabled={saving || titleMissing} onClick={onSaveAndSend}>
              Desa i envia a validar
            </Button>
          )}
          <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={onCancel}>
            Cancel·la
          </Button>
          {state && (
            <span role="status" className={cn("text-caption", state === "Desat" ? "text-success" : "text-muted")}>
              {state}
            </span>
          )}
          {error && (
            <span role="alert" className="text-caption text-danger">
              {error} Res no ha quedat desat.
            </span>
          )}
        </div>
        <p className="text-caption text-faint">Esc plega la fila · Ctrl/⌘+S desa</p>
      </form>

      <aside className="space-y-4 border-t border-border pt-4 lg:col-span-5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-5">
        <RequirementContext workspace={workspace} requirement={requirement} solution={original} />
        <ValidationSummary workspace={workspace} solution={original} />
        {awaitsValidation(original) && (
          <Button type="button" variant="primary" size="sm" onClick={onValidate} disabled={dirty}>
            {original.validation_stale ? "Torna a validar" : "Valida-la"}
          </Button>
        )}
        {awaitsValidation(original) && dirty && <p className="text-caption text-muted">Desa abans de validar: es valida el que hi ha desat.</p>}
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Lectura (rol de consulta)                                                  */
/* ------------------------------------------------------------------------- */

function SolutionDetails({ workspace, requirement, solution, onClose }: { workspace: WorkspaceDetail; requirement: Requirement | null; solution: TechnicalSolution; onClose: () => void }) {
  const owner = memberName(workspace, solution.owner_id);
  return (
    <div className="grid gap-5 px-3.5 py-4 lg:grid-cols-12">
      <div className="space-y-3 lg:col-span-7">
        <div>
          <p className={KICKER}>Proposta tècnica</p>
          {solution.proposed_solution ? (
            <p className="mt-1 text-body leading-relaxed whitespace-pre-wrap text-ink">{solution.proposed_solution}</p>
          ) : (
            <p className="mt-1 text-body text-muted">Encara no hi ha proposta escrita.</p>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-body">
          <dt className="text-muted">Proveïdor</dt>
          <dd>{solution.supplier_name ?? "—"}</dd>
          <dt className="text-muted">Producte</dt>
          <dd>{solution.product_name ?? "—"}</dd>
          <dt className="text-muted">Responsable</dt>
          <dd>{owner ?? "Sense assignar"}</dd>
        </dl>
        {solution.notes && <p className="text-caption text-muted">{solution.notes}</p>}
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Plega
        </Button>
      </div>
      <aside className="space-y-4 border-t border-border pt-4 lg:col-span-5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-5">
        <RequirementContext workspace={workspace} requirement={requirement} solution={solution} />
        <ValidationSummary workspace={workspace} solution={solution} />
      </aside>
    </div>
  );
}
