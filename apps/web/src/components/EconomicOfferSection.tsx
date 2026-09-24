"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useInvalidateReadiness } from "@/components/ReadinessProvider";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  Field,
  Input,
  LoadingRows,
  PermissionNote,
  Select,
  StatusPill,
  Textarea,
  cn,
  type DataTableColumn,
  type DataTableGroup,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import {
  CONFLICT_FIELDS,
  PRICING_BASES,
  PRICING_BASIS_LABELS,
  activeLots,
  archivedCount,
  canEditOffer,
  createPayload,
  diffLine,
  draftFromLine,
  durationOf,
  emptyDraft,
  formatAmount,
  formatPercent,
  formatQuantity,
  isRevisionConflict,
  lineIsDirty,
  resolveEntity,
  unitSuffix,
  usesDuration,
  whoCanEditOffer,
  type EconomicOffer,
  type LineDraft,
  type LinePayload,
  type OfferCreateResult,
  type OfferLine,
  type OfferLot,
  type PricingBasis,
  SYNC_FIELD_LABELS,
  visibleDivergedFields,
} from "@/lib/economic";
import type { TenderLot } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Els errors de domini d'S5 que tenen frase pròpia. La resta, la del servidor. */
const DOMAIN_ERROR_COPY: Record<string, string> = {
  economic_offer_item_archived: "Això està arxivat i ja no es pot editar.",
  economic_offer_lot_selection_not_allowed: "Aquesta candidatura no tria lots.",
  economic_offer_link_mismatch: "El vincle que s'ha enviat no correspon a aquesta oferta.",
  economic_offer_already_exists: "Aquesta candidatura ja té oferta econòmica.",
};

function humanError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return DOMAIN_ERROR_COPY[error.code] ?? error.message;
  return error instanceof Error ? error.message : fallback;
}

/** La durada tal com s'escriu al camp. `null` és la cadena buida, mai un zero. */
function durationText(months: number | null): string {
  return months === null ? "" : String(months);
}

/** Una fila de la taula: una línia desada, o la que s'està creant. */
interface Row {
  key: string;
  sectionId: string;
  line: OfferLine | null;
}

type Editing = { kind: "edit"; lineId: string } | { kind: "new"; sectionId: string } | null;

interface Conflict {
  draft: LineDraft;
  /** El que hi havia al servidor després de rellegir. `null` si ja no hi és. */
  server: OfferLine | null;
}

interface Props {
  workspace: WorkspaceDetail;
  registerLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void;
}

/**
 * Preparar → Oferta econòmica.
 *
 * Un configurador: lot → secció → línia, amb el resum a dalt. Tots els números
 * els dona el servidor; aquí no se'n calcula cap, ni tan sols com a
 * previsualització mentre s'escriu. Per això el total no es mou fins que es
 * desa: ensenyar un total provisional calculat al navegador seria ensenyar una
 * xifra que ningú no ha comprovat.
 *
 * Cada mutació viatja amb `expected_revision` i torna l'oferta sencera. Si el
 * servidor diu que la versió ha quedat enrere, no es fusiona res.
 */
export function EconomicOfferSection({ workspace, registerLeaveGuard }: Props) {
  const { companyId, membership } = useSession();
  const invalidateReadiness = useInvalidateReadiness();
  const searchParams = useSearchParams();
  const canEdit = canEditOffer(membership.role);
  const base = `/companies/${companyId}/workspaces/${workspace.id}`;

  const offer = useResource<EconomicOffer>(`${base}/economic-offer`);
  const data = offer.data;
  const internal = data?.can_view_internal_economics === true;

  const tenderLots = useResource<TenderLot[]>(data?.can_select_lots ? `/tenders/${workspace.tender_id}/lots` : null);

  /* ----- Edició ---------------------------------------------------------- */

  const [editing, setEditing] = useState<Editing>(null);
  const [draft, setDraft] = useState<LineDraft | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rowError, setRowError] = useState<{ code: string | null; message: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  // L'esborrany que va xocar amb el servidor. Sobreviu a «Recarrega i revisa»
  // perquè la persona pugui tornar-hi a mirar el que havia escrit: si
  // desaparegues en silenci, el conflicte hauria esborrat feina seva.
  const [previousDraft, setPreviousDraft] = useState<LineDraft | null>(null);
  const [divergedLine, setDivergedLine] = useState<OfferLine | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  // Dos editors petits més. Només un obert alhora, com la línia: obrir-ne un
  // passa per la mateixa pregunta que qualsevol altra manera de perdre feina.
  const [lotEdit, setLotEdit] = useState<{ lotId: string; value: string } | null>(null);
  const [renameEdit, setRenameEdit] = useState<{ sectionId: string; value: string } | null>(null);

  const original = useMemo(() => {
    if (!data || editing?.kind !== "edit") return null;
    return data.lots.flatMap((lot) => lot.sections.flatMap((section) => section.lines)).find((line) => line.id === editing.lineId) ?? null;
  }, [data, editing]);

  const lineDirty = Boolean(
    draft && (editing?.kind === "new" ? draft.concept.trim() !== "" : original !== null && lineIsDirty(original, draft, internal)),
  );
  const lotEditDirty = Boolean(
    lotEdit && lotEdit.value.trim() !== durationText(data?.lots.find((lot) => lot.id === lotEdit.lotId)?.duration_months ?? null),
  );
  const renameDirty = Boolean(
    renameEdit &&
      renameEdit.value.trim() !==
        (data?.lots.flatMap((lot) => lot.sections).find((section) => section.id === renameEdit.sectionId)?.title ?? ""),
  );
  const dirty = lineDirty || lotEditDirty || renameDirty;

  /* ----- Sortir amb canvis oberts ---------------------------------------- */

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

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  function closeEditor() {
    setEditing(null);
    setDraft(null);
    setLotEdit(null);
    setRenameEdit(null);
    setSaved(false);
    setRowError(null);
    setPreviousDraft(null);
  }

  async function openLine(line: OfferLine) {
    if (editing?.kind === "edit" && editing.lineId === line.id) return;
    if (!(await confirmDiscard())) return;
    setEditing({ kind: "edit", lineId: line.id });
    setDraft(draftFromLine(line));
    setSaved(false);
    setRowError(null);
  }

  async function openNewLine(sectionId: string) {
    if (!(await confirmDiscard())) return;
    setEditing({ kind: "new", sectionId });
    setDraft(emptyDraft());
    setSaved(false);
    setRowError(null);
  }

  async function cancelEditor() {
    if (!(await confirmDiscard())) return;
    closeEditor();
  }

  /* ----- Mutacions ------------------------------------------------------- */

  /**
   * Tota escriptura passa per aquí: hi posa `expected_revision`, adopta
   * l'oferta que torna el servidor i torna a preguntar la preparació final.
   */
  async function mutate(
    run: (revision: number) => Promise<EconomicOffer>,
    kind: "line" | "structural",
  ): Promise<EconomicOffer | null> {
    if (!data) return null;
    setSaving(true);
    setRowError(null);
    setNotice(null);
    try {
      const next = await run(data.revision);
      offer.setData(next);
      await invalidateReadiness();
      return next;
    } catch (caught) {
      if (isRevisionConflict(caught)) {
        // La revisió és de l'oferta sencera, així que el xoc pot venir de
        // qualsevol escriptura. Es rellegeix per saber què hi ha ara —i per
        // adoptar la revisió nova—, però no es torna a provar res sol: qui
        // reintentés automàticament estaria sobreescrivint el que no ha vist.
        const fresh = await offer.reload();
        if (kind === "line" && draft && editing?.kind === "edit") {
          const server =
            fresh?.lots.flatMap((lot) => lot.sections.flatMap((section) => section.lines)).find((line) => line.id === editing.lineId) ?? null;
          setConflict({ draft, server });
        } else if (kind === "line" && draft) {
          // Una línia nova que encara no existeix al servidor: no hi ha res
          // amb què comparar-la, però l'esborrany es conserva igualment.
          setConflict({ draft, server: null });
        } else {
          setNotice(
            "L'oferta ha canviat des que la vas obrir i s'ha tornat a carregar. Res del que acabes de demanar no s'ha desat: revisa-la abans de tornar-ho a provar.",
          );
        }
        return null;
      }
      setRowError({ code: caught instanceof ApiError ? caught.code : null, message: humanError(caught, "No s'ha pogut desar.") });
      return null;
    } finally {
      setSaving(false);
    }
  }

  /** Escriu el preu proposat al preu ofert. Nomes quan algu ho demana. */
  async function applyTarget(line: OfferLine | null) {
    if (!line) return;
    await mutate(
      (revision) =>
        api.post<EconomicOffer>(`${base}/economic-offer/lines/${line.id}/apply-target`, {
          expected_revision: revision,
        }),
      "line",
    );
  }

  /** Adopta NOMES els camps triats. Cap "sincronitza-ho tot". */
  async function syncFromOption(line: OfferLine, fields: string[]) {
    await mutate(
      (revision) =>
        api.post<EconomicOffer>(
          `${base}/economic-offer/lines/${line.id}/sync-from-option`,
          { expected_revision: revision, fields },
        ),
      "line",
    );
  }

  async function saveLine() {
    if (!draft || !editing) return;
    if (draft.concept.trim() === "") return;
    if (editing.kind === "new") {
      const body = createPayload(draft, internal);
      const next = await mutate(
        (revision) => api.post<EconomicOffer>(`${base}/economic-offer/sections/${editing.sectionId}/lines`, { ...body, expected_revision: revision }),
        "line",
      );
      if (next) closeEditor();
      return;
    }
    if (!original) return;
    const body: LinePayload = diffLine(original, draft, internal);
    if (Object.keys(body).length === 0) {
      setSaved(true);
      return;
    }
    const next = await mutate(
      (revision) => api.patch<EconomicOffer>(`${base}/economic-offer/lines/${editing.lineId}`, { ...body, expected_revision: revision }),
      "line",
    );
    if (next) {
      setSaved(true);
      // Un desat correcte tanca el conflicte anterior: ja no hi ha res pendent
      // de recuperar.
      setPreviousDraft(null);
    }
  }

  async function openLotDuration(lot: OfferLot) {
    if (!(await confirmDiscard())) return;
    closeEditor();
    setLotEdit({ lotId: lot.id, value: durationText(lot.duration_months) });
  }

  async function saveLotDuration() {
    if (!lotEdit) return;
    const raw = lotEdit.value.trim();
    // Buit vol dir «ja no sabem quant dura»: el servidor distingeix ometre el
    // camp de posar-hi `null`, i aquí s'hi envia `null` a propòsit.
    const months = raw === "" ? null : Number.parseInt(raw, 10);
    if (months !== null && (Number.isNaN(months) || months < 1)) return;
    const lotId = lotEdit.lotId;
    const next = await runStructural(`duration-${lotId}`, (revision) =>
      api.patch<EconomicOffer>(`${base}/economic-offer/lots/${lotId}`, {
        duration_months: months,
        expected_revision: revision,
      }),
    );
    // Si falla, el que s'ha escrit es queda a la vista.
    if (next) setLotEdit(null);
  }

  async function openRename(section: { id: string; title: string }) {
    if (!(await confirmDiscard())) return;
    closeEditor();
    setRenameEdit({ sectionId: section.id, value: section.title });
  }

  async function saveRename() {
    if (!renameEdit) return;
    const title = renameEdit.value.trim();
    if (title === "") return;
    const sectionId = renameEdit.sectionId;
    const next = await runStructural(`rename-${sectionId}`, (revision) =>
      api.patch<EconomicOffer>(`${base}/economic-offer/sections/${sectionId}`, { title, expected_revision: revision }),
    );
    if (next) setRenameEdit(null);
  }

  const [archiveSection, setArchiveSection] = useState<{ id: string; title: string } | null>(null);
  async function confirmArchiveSection() {
    if (!archiveSection) return;
    const sectionId = archiveSection.id;
    setArchiveSection(null);
    await runStructural(`section-archive-${sectionId}`, (revision) =>
      api.post<EconomicOffer>(`${base}/economic-offer/sections/${sectionId}/archive`, { expected_revision: revision }),
    );
  }

  const [pending, setPending] = useState<string | null>(null);

  /**
   * Una escriptura d'estructura, amb protecció contra el doble clic.
   *
   * NO pregunta res: desar el que s'acaba d'escriure no és perdre-ho. La
   * pregunta és per a les accions que sí que descarten l'edició oberta, i
   * aquelles passen per `guarded`.
   */
  async function runStructural(key: string, run: (revision: number) => Promise<EconomicOffer>): Promise<EconomicOffer | null> {
    if (pending) return null;
    setPending(key);
    try {
      return await mutate(run, "structural");
    } finally {
      setPending(null);
    }
  }

  async function guarded(key: string, run: (revision: number) => Promise<EconomicOffer>): Promise<EconomicOffer | null> {
    if (pending) return null;
    if (!(await confirmDiscard())) return null;
    const next = await runStructural(key, run);
    if (next) closeEditor();
    return next;
  }

  const [creatingOffer, setCreatingOffer] = useState(false);
  async function startOffer() {
    if (creatingOffer) return;
    setCreatingOffer(true);
    setRowError(null);
    try {
      const result = await api.post<OfferCreateResult>(`${base}/economic-offer`, {});
      offer.setData(result.offer);
      await invalidateReadiness();
    } catch (caught) {
      setRowError({ code: caught instanceof ApiError ? caught.code : null, message: humanError(caught, "No s'ha pogut obrir l'oferta.") });
    } finally {
      setCreatingOffer(false);
    }
  }

  const [sectionFor, setSectionFor] = useState<string | null>(null);
  const [sectionTitle, setSectionTitle] = useState("");
  /**
   * Es pregunta en OBRIR el diàleg, no en desar-lo: si s'esperés al desat, la
   * confirmació s'obriria a sota d'aquest modal i qui la respon no la veuria.
   */
  async function openSection(lotId: string) {
    if (!(await confirmDiscard())) return;
    setSectionTitle("");
    setSectionFor(lotId);
  }
  async function createSection() {
    if (!sectionFor || sectionTitle.trim() === "") return;
    const lotId = sectionFor;
    const title = sectionTitle.trim();
    const next = await guarded(`section-${lotId}`, (revision) =>
      api.post<EconomicOffer>(`${base}/economic-offer/lots/${lotId}/sections`, { title, expected_revision: revision }),
    );
    // Si no ha anat bé, el títol escrit es queda al formulari.
    if (next) {
      setSectionFor(null);
      setSectionTitle("");
    }
  }

  const [lotPickerOpen, setLotPickerOpen] = useState(false);
  async function addLot(tenderLotId: string) {
    setLotPickerOpen(false);
    if (pending) return;
    if (!(await confirmDiscard())) return;
    setPending(`lot-${tenderLotId}`);
    try {
      await mutate(async (revision) => {
        const result = await api.post<OfferCreateResult>(`${base}/economic-offer/lots/from-tender-lot`, {
          tender_lot_id: tenderLotId,
          expected_revision: revision,
        });
        return result.offer;
      }, "structural");
    } finally {
      setPending(null);
    }
  }

  /* ----- Enllaç profund --------------------------------------------------- */

  const entity = searchParams?.get("entity") ?? null;
  const [handledEntity, setHandledEntity] = useState<string | null>(null);
  const [linkNotice, setLinkNotice] = useState<string | null>(null);
  const highlight = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!entity || entity === handledEntity || !data) return;
    setHandledEntity(entity);
    const target = resolveEntity(data, entity);
    if (target === null) {
      setLinkNotice("L'enllaç apuntava a una part de l'oferta que no hi és.");
      return;
    }
    setLinkNotice(null);
    if (target.kind === "line" && target.line.archived_at === null) {
      setEditing({ kind: "edit", lineId: target.line.id });
      setDraft(draftFromLine(target.line));
    }
  }, [entity, handledEntity, data]);
  useEffect(() => {
    if (!entity || editing?.kind !== "edit" || editing.lineId !== entity) return;
    const node = highlight.current;
    if (!node) return;
    node.scrollIntoView?.({ block: "center" });
    node.focus({ preventScroll: true });
  }, [entity, editing]);

  /* ----- Càrrega, error i oferta absent ----------------------------------- */

  if (offer.loading && !data && !offer.error) {
    return (
      <Card>
        <CardHeader title="Oferta econòmica" />
        <div aria-busy="true" aria-label="Es carrega l'oferta econòmica">
          <LoadingRows rows={5} />
        </div>
      </Card>
    );
  }

  if (!data) {
    const missing = offer.status === 404;
    return (
      <Card>
        <CardHeader title="Oferta econòmica" />
        {missing ? (
          <EmptyState
            title="Encara no hi ha oferta econòmica"
            description="L'oferta és on es decideixen els lots, les seccions, les línies i els preus. Fins que no existeixi, la candidatura no es pot tancar."
            action={
              canEdit ? (
                <Button variant="primary" size="md" loading={creatingOffer} onClick={() => void startOffer()}>
                  Obre l&apos;oferta econòmica
                </Button>
              ) : (
                <PermissionNote who={whoCanEditOffer()} what="obrir l'oferta econòmica">
                  <span className="text-caption text-muted">Encara no s&apos;ha obert.</span>
                </PermissionNote>
              )
            }
          />
        ) : (
          <EmptyState
            title="No s'ha pogut carregar l'oferta econòmica"
            description={offer.error ?? "Sense resposta del servidor."}
            action={
              <Button variant="primary" size="md" onClick={() => void offer.reload()}>
                Torna-ho a provar
              </Button>
            }
          />
        )}
        {rowError && (
          <p role="alert" className="px-3.5 pb-4 text-caption text-danger">
            {rowError.message}
          </p>
        )}
      </Card>
    );
  }

  /* ----- Pintar ----------------------------------------------------------- */

  const lots = activeLots(data);
  const singleLot = lots.length === 1 ? lots[0] : null;
  const archived = archivedCount(data);
  const currency = data.currency;
  const selectedTenderLotIds = new Set(lots.map((lot) => lot.tender_lot_id).filter((id): id is string => id !== null));
  const available = (tenderLots.data ?? []).filter((lot) => !selectedTenderLotIds.has(lot.id));

  const columns: Array<DataTableColumn<Row>> = [
    {
      key: "concept",
      header: "Concepte",
      width: internal ? "26%" : "34%",
      cell: (row) => {
        if (!row.line) return <span className="text-muted">Línia nova</span>;
        const detail = [row.line.supplier_name, row.line.product_name].filter(Boolean).join(" · ");
        return (
          <div>
            <p className="font-medium text-ink">{row.line.concept}</p>
            {detail && <p className="text-caption leading-4 text-muted">{detail}</p>}
            {row.line.archived_at !== null && (
              <StatusPill level="neutral" size="sm" outline>
                Arxivada
              </StatusPill>
            )}
            {/* Informatiu: la font ha canviat. No diu que la línia sigui
                dolenta —seguir el que es va adoptar pot ser el que es vol—,
                per això no hi ha estil d'alarma ni de blocador. */}
            {row.line.archived_at === null &&
              visibleDivergedFields(row.line, internal).length > 0 && (
                <button
                  type="button"
                  className="mt-1 text-[13px] text-brand hover:underline"
                  onClick={(event) => {
                    event.stopPropagation();
                    setDivergedLine(row.line);
                  }}
                >
                  L&apos;opció ha canviat · Revisa canvis
                </button>
              )}
          </div>
        );
      },
    },
    {
      key: "quantity",
      header: "Quantitat",
      align: "right",
      cell: (row) => (row.line ? <span>{formatQuantity(row.line.quantity)}</span> : null),
    },
    {
      key: "basis",
      header: "Base de preu",
      cell: (row) => (row.line ? <span>{PRICING_BASIS_LABELS[row.line.pricing_basis]}</span> : null),
      hideBelow: "md",
    },
    {
      key: "duration",
      header: "Durada",
      cell: (row) => {
        if (!row.line) return null;
        const duration = durationOf(row.line);
        return (
          <div>
            <span className={cn(duration.source === "unknown" && "text-danger")}>{duration.label}</span>
            {duration.hint && <p className="text-caption leading-4 text-muted">{duration.hint}</p>}
          </div>
        );
      },
      hideBelow: "md",
    },
    {
      key: "price",
      header: "Preu ofert",
      align: "right",
      cell: (row) => {
        if (!row.line) return null;
        const line = row.line;
        const suffix = unitSuffix(line.pricing_basis);
        return (
          <div>
            {line.offered_unit_price_ex_vat === null ? (
              <span className="text-danger">Sense preu</span>
            ) : (
              <span>
                {formatAmount(line.offered_unit_price_ex_vat, currency)}
                {suffix && <span className="text-caption text-muted"> {suffix}</span>}
              </span>
            )}
            {line.exceeds_unit_max === true && (
              <p className="text-caption leading-4 text-danger">
                Supera el màxim del plec · {formatAmount(line.tender_max_unit_price_ex_vat, currency)}
              </p>
            )}
            {line.exceeds_unit_max === false && (
              <p className="text-caption leading-4 text-muted">Màxim del plec {formatAmount(line.tender_max_unit_price_ex_vat, currency)}</p>
            )}
            {line.vat_rate === null ? (
              <p className="text-caption leading-4 text-warning">IVA sense definir</p>
            ) : (
              <p className="text-caption leading-4 text-muted">IVA {formatPercent(line.vat_rate)}</p>
            )}
          </div>
        );
      },
    },
    // Les columnes de dins només existeixen quan el servidor dona el bloc: no
    // es rendereixen buides ni amagades per CSS.
    ...(internal
      ? [
          {
            key: "cost",
            header: "Cost intern",
            align: "right" as const,
            cell: (row: Row) =>
              row.line ? <span>{formatAmount(row.line.internal_economics?.unit_cost_ex_vat ?? null, currency)}</span> : null,
          },
          {
            key: "margin",
            header: "Marge",
            align: "right" as const,
            cell: (row: Row) => (row.line ? <span>{formatPercent(row.line.internal_economics?.margin_on_sales_pct ?? null)}</span> : null),
          },
        ]
      : []),
    {
      key: "subtotal",
      header: "Subtotal",
      align: "right",
      cell: (row) => (row.line ? <span className="font-medium">{formatAmount(row.line.totals.total_revenue, currency)}</span> : null),
    },
    ...(canEdit
      ? [
          {
            key: "action",
            header: "Acció",
            align: "right" as const,
            cell: (row: Row) => {
              if (!row.line) return null;
              const line = row.line;
              if (line.archived_at !== null) return null;
              const open = editing?.kind === "edit" && editing.lineId === line.id;
              if (open) {
                return (
                  <Button variant="ghost" size="sm" onClick={() => void cancelEditor()}>
                    Plega
                  </Button>
                );
              }
              return (
                <div className="flex justify-end gap-1.5">
                  <Button variant="secondary" size="sm" onClick={() => void openLine(line)}>
                    Edita
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={pending === `line-${line.id}`}
                    onClick={() =>
                      void guarded(`line-${line.id}`, (revision) =>
                        api.post<EconomicOffer>(`${base}/economic-offer/lines/${line.id}/archive`, { expected_revision: revision }),
                      )
                    }
                  >
                    Arxiva
                  </Button>
                </div>
              );
            },
          },
        ]
      : []),
  ];

  const expanded = (row: Row): ReactNode => {
    const isOpen =
      (row.line && editing?.kind === "edit" && editing.lineId === row.line.id) ||
      (!row.line && editing?.kind === "new" && editing.sectionId === row.sectionId);
    if (!isOpen || !draft) return null;
    return (
      <div
        ref={highlight}
        tabIndex={-1}
        data-highlight={entity && row.line?.id === entity ? "true" : undefined}
        className={cn("outline-none", entity && row.line?.id === entity && "ring-2 ring-inset ring-brand")}
      >
        {entity && row.line?.id === entity && <span className="sr-only">Línia enllaçada des de la preparació final</span>}
        <LineEditor
          previousDraft={previousDraft}
          onDiscardPrevious={() => setPreviousDraft(null)}
          draft={draft}
          onDraft={(next) => {
            setSaved(false);
            setDraft(next);
          }}
          internal={internal}
          saving={saving}
          saved={saved}
          dirty={dirty}
          error={rowError}
          currency={currency}
          isNew={!row.line}
          line={row.line}
          onApplyTarget={() => void applyTarget(row.line)}
          applying={saving}
          onSave={() => void saveLine()}
          onCancel={() => void cancelEditor()}
        />
      </div>
    );
  };

  function rowsOf(section: { lines: OfferLine[]; id: string }): Row[] {
    const visible = section.lines.filter((line) => showArchived || line.archived_at === null);
    const rows: Row[] = visible.map((line) => ({ key: line.id, sectionId: section.id, line }));
    if (editing?.kind === "new" && editing.sectionId === section.id) {
      rows.push({ key: `new-${section.id}`, sectionId: section.id, line: null });
    }
    return rows;
  }

  return (
    <>
      {/* ----- Resum ------------------------------------------------------- */}
      <div className="sticky top-0 z-10 border-y border-border-strong bg-canvas">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3 px-3.5 py-3">
          <Summary label="Moneda" value={currency} />
          {singleLot && (
            <Summary
              label="Durada del contracte"
              value={singleLot.duration_months === null ? "No consta" : `${singleLot.duration_months} mesos`}
              hint={singleLot.duration_months === null ? "Les línies temporals no es poden calcular" : "del lot"}
            />
          )}
          <Summary
            label="Total ofert (sense IVA)"
            value={formatAmount(data.totals.total_revenue, currency)}
            hint={`${data.totals.line_count} ${data.totals.line_count === 1 ? "línia" : "línies"}`}
            strong
          />
          {singleLot && singleLot.limit.tender_max !== null && (
            <>
              <Summary label="Pressupost de licitació" value={formatAmount(singleLot.limit.tender_max, currency)} />
              <Summary
                label="Diferència"
                value={formatAmount(singleLot.limit.difference_to_maximum, currency)}
                hint={singleLot.limit.exceeds_lot_max === true ? "Supera el pressupost" : "Sota el màxim"}
                level={singleLot.limit.exceeds_lot_max === true ? "danger" : undefined}
              />
            </>
          )}
          {singleLot && singleLot.limit.tender_max === null && <Summary label="Pressupost de licitació" value="No consta" />}
          {internal && data.internal_economics && (
            <Summary
              label="Marge"
              value={formatPercent(data.internal_economics.margin_on_sales_pct)}
              hint={formatAmount(data.internal_economics.margin_amount, currency)}
            />
          )}
          {dirty && <span className="text-caption text-warning">No inclou els canvis oberts</span>}
        </div>
        {(data.totals.incomplete_revenue_lines > 0 || data.totals.incomplete_vat_lines > 0) && (
          <p className="border-t border-border px-3.5 py-1.5 text-caption text-muted">
            {data.totals.incomplete_revenue_lines > 0 && `${data.totals.incomplete_revenue_lines} sense preu. `}
            {data.totals.incomplete_vat_lines > 0 && `${data.totals.incomplete_vat_lines} sense IVA.`}
          </p>
        )}
      </div>

      {linkNotice && (
        <p role="status" className="border border-border-strong bg-subtle px-3.5 py-2 text-caption text-muted">
          {linkNotice}
        </p>
      )}
      {notice && (
        <Banner level="attention" kicker="Oferta actualitzada" action={<span className="text-caption text-muted">Revisa-la abans de continuar.</span>}>
          <p>{notice}</p>
        </Banner>
      )}

      {!canEdit && (
        <Card>
          <div className="px-3.5 py-2.5">
            <PermissionNote who={whoCanEditOffer()} what="editar els preus de l'oferta">
              <span className="text-caption text-muted">Només lectura. Veus el preu que s&apos;ofereix al client.</span>
            </PermissionNote>
          </div>
        </Card>
      )}

      {/* ----- Lots -------------------------------------------------------- */}
      {data.can_select_lots && (
        <Card>
          <CardHeader
            title="Lots als quals ens presentem"
            description="Ningú no els tria per tu: fins que no se seleccionen, no hi ha abast ni oferta."
            action={
              canEdit && available.length > 0 ? (
                <Button variant="secondary" size="sm" onClick={() => setLotPickerOpen(true)}>
                  Afegeix un lot
                </Button>
              ) : undefined
            }
          />
          {lots.length === 0 ? (
            <p className="px-3.5 py-5 text-body text-muted">Encara no s&apos;ha triat cap lot.</p>
          ) : (
            <ul className="divide-y divide-border">
              {lots.map((lot) => (
                <li key={lot.id} className="flex items-center justify-between gap-3 px-3.5 py-2">
                  <span className="text-body text-ink">{lot.label}</span>
                  {canEdit && (
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={pending === `lot-archive-${lot.id}`}
                      onClick={() =>
                        void guarded(`lot-archive-${lot.id}`, (revision) =>
                          api.post<EconomicOffer>(`${base}/economic-offer/lots/${lot.id}/archive`, { expected_revision: revision }),
                        )
                      }
                    >
                      Arxiva
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {lots.length === 0 && !data.can_select_lots && (
        <Card>
          <CardHeader title="Oferta econòmica" />
          <p className="px-3.5 py-5 text-body text-muted">Aquesta oferta encara no té cap lot actiu.</p>
        </Card>
      )}

      {/* ----- Configurador ------------------------------------------------ */}
      {lots.map((lot) => {
        const sections = lot.sections.filter((section) => showArchived || section.archived_at === null);
        const groups: Array<DataTableGroup<Row>> = sections.map((section) => ({
          key: section.id,
          label: (
            <span className="flex w-full flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-heading text-[13.5px] font-semibold text-ink">{section.title}</span>
              <span className="text-caption text-muted">
                {section.totals.line_count} {section.totals.line_count === 1 ? "línia" : "línies"}
              </span>
              {section.archived_at !== null && (
                <StatusPill level="neutral" size="sm" outline>
                  Arxivada
                </StatusPill>
              )}
              <span className="ml-auto text-body font-medium text-ink">{formatAmount(section.totals.total_revenue, currency)}</span>
            </span>
          ),
          rows: rowsOf(section),
        }));

        return (
          <Card key={lot.id}>
            <CardHeader
              title={lot.label}
              description="Les línies mensuals i anuals hereten la durada del lot si no en diuen una altra."
              action={
                canEdit ? (
                  <Button variant="secondary" size="sm" onClick={() => void openSection(lot.id)}>
                    Afegeix una secció
                  </Button>
                ) : undefined
              }
            />

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3.5 py-2">
              <span className={KICKER}>Durada del lot</span>
              {lotEdit?.lotId === lot.id ? (
                <form
                  className="flex flex-wrap items-center gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveLotDuration();
                  }}
                >
                  <label className="sr-only" htmlFor={`durada-${lot.id}`}>
                    Durada del lot en mesos
                  </label>
                  <Input
                    id={`durada-${lot.id}`}
                    inputMode="numeric"
                    className="w-24"
                    value={lotEdit.value}
                    onChange={(event) => setLotEdit({ lotId: lot.id, value: event.target.value })}
                    autoFocus
                  />
                  <span className="text-caption text-muted">mesos · buit: sense definir</span>
                  <Button type="submit" variant="primary" size="sm" loading={pending === `duration-${lot.id}`}>
                    Desa
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => void cancelEditor()}>
                    Cancel·la
                  </Button>
                </form>
              ) : (
                <>
                  <span className={cn("text-body", lot.duration_months === null ? "text-warning" : "text-ink")}>
                    {lot.duration_months === null
                      ? "Sense definir"
                      : `${lot.duration_months} ${lot.duration_months === 1 ? "mes" : "mesos"}`}
                  </span>
                  {canEdit && (
                    <Button variant="ghost" size="sm" onClick={() => void openLotDuration(lot)}>
                      Edita la durada
                    </Button>
                  )}
                </>
              )}
            </div>

            {!singleLot && lot.limit.tender_max !== null && (
              <p className="border-b border-border px-3.5 py-1.5 text-caption text-muted">
                Pressupost del lot {formatAmount(lot.limit.tender_max, currency)} · diferència{" "}
                <span className={cn(lot.limit.exceeds_lot_max === true && "text-danger")}>{formatAmount(lot.limit.difference_to_maximum, currency)}</span>
              </p>
            )}

            {sections.length === 0 ? (
              <p className="px-3.5 py-5 text-body text-muted">Encara no hi ha seccions en aquest lot.</p>
            ) : (
              <>
                <DataTable<Row>
                  columns={columns}
                  rows={[]}
                  groups={groups}
                  rowKey={(row) => row.key}
                  isSelected={(row) =>
                    Boolean(row.line && editing?.kind === "edit" && editing.lineId === row.line.id) ||
                    Boolean(!row.line && editing?.kind === "new" && editing.sectionId === row.sectionId)
                  }
                  expandedRow={expanded}
                  stackBelow="md"
                />
                {canEdit && (
                  <ul className="divide-y divide-border border-t border-border">
                    {sections
                      .filter((section) => section.archived_at === null)
                      .map((section) => (
                        <li key={section.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3.5 py-1.5">
                          {renameEdit?.sectionId === section.id ? (
                            <form
                              className="flex flex-wrap items-center gap-2"
                              onSubmit={(event) => {
                                event.preventDefault();
                                void saveRename();
                              }}
                            >
                              <label className="sr-only" htmlFor={`nom-${section.id}`}>
                                Títol de la secció
                              </label>
                              <Input
                                id={`nom-${section.id}`}
                                className="w-56"
                                maxLength={300}
                                value={renameEdit.value}
                                onChange={(event) => setRenameEdit({ sectionId: section.id, value: event.target.value })}
                                autoFocus
                              />
                              <Button
                                type="submit"
                                variant="primary"
                                size="sm"
                                loading={pending === `rename-${section.id}`}
                                disabled={renameEdit.value.trim() === ""}
                              >
                                Desa
                              </Button>
                              <Button type="button" variant="ghost" size="sm" onClick={() => void cancelEditor()}>
                                Cancel·la
                              </Button>
                            </form>
                          ) : (
                            <>
                              <span className="text-body text-ink">{section.title}</span>
                              <Button variant="ghost" size="sm" onClick={() => void openNewLine(section.id)}>
                                Afegeix una línia
                              </Button>
                              <Button variant="ghost" size="sm" onClick={() => void openRename(section)}>
                                Renombra
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                loading={pending === `section-archive-${section.id}`}
                                onClick={() => setArchiveSection({ id: section.id, title: section.title })}
                              >
                                Arxiva
                              </Button>
                            </>
                          )}
                        </li>
                      ))}
                  </ul>
                )}
              </>
            )}

            <div className="flex items-center justify-between gap-3 border-t border-border-strong px-3.5 py-2">
              <span className={KICKER}>Total del lot</span>
              <span className="text-body font-semibold text-ink">{formatAmount(lot.totals.total_revenue, currency)}</span>
            </div>
          </Card>
        );
      })}

      {archived > 0 && (
        <div className="px-3.5">
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((current) => !current)}>
            {showArchived ? "Amaga els arxivats" : `Mostra els arxivats (${archived})`}
          </Button>
        </div>
      )}

      {/* ----- Diàlegs ------------------------------------------------------ */}
      <DivergenceDrawer
        line={divergedLine}
        currency={currency}
        internal={internal}
        onClose={() => setDivergedLine(null)}
        onSync={async (fields) => {
          if (!divergedLine) return;
          await syncFromOption(divergedLine, fields);
          setDivergedLine(null);
        }}
        syncing={saving}
      />

      <ConfirmDialog
        open={discardOpen}
        title="Canvis sense desar"
        message="Si continues, els canvis que no has desat es perdran."
        confirmLabel="Descarta els canvis"
        onConfirm={() => answerDiscard(true)}
        onCancel={() => answerDiscard(false)}
      />

      <ConfirmDialog
        open={archiveSection !== null}
        title="Arxiva la secció"
        message={`«${archiveSection?.title ?? ""}» deixarà de comptar a l'oferta. Les seves línies es conserven: no compten perquè la secció no compta.`}
        confirmLabel="Arxiva-la"
        onConfirm={() => void confirmArchiveSection()}
        onCancel={() => setArchiveSection(null)}
      />

      <Modal
        open={sectionFor !== null}
        title="Nova secció"
        onClose={() => {
          setSectionFor(null);
          setSectionTitle("");
        }}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setSectionFor(null);
                setSectionTitle("");
              }}
            >
              Cancel·la
            </Button>
            <Button variant="primary" loading={saving} disabled={sectionTitle.trim() === ""} onClick={() => void createSection()}>
              Crea-la
            </Button>
          </>
        }
      >
        <Field label="Títol" required>
          <Input value={sectionTitle} onChange={(event) => setSectionTitle(event.target.value)} maxLength={300} autoFocus />
        </Field>
      </Modal>

      <Modal open={lotPickerOpen} title="Afegeix un lot" description="A quin lot del plec ens presentem." onClose={() => setLotPickerOpen(false)}>
        {available.length === 0 ? (
          <p className="text-body text-muted">No queda cap lot per afegir.</p>
        ) : (
          <ul className="divide-y divide-border">
            {available.map((lot) => (
              <li key={lot.id} className="flex items-center justify-between gap-3 py-2">
                <span className="text-body text-ink">
                  {lot.lot_number !== null ? `Lot ${lot.lot_number}` : "Lot"}
                  {lot.title ? ` · ${lot.title}` : ""}
                </span>
                <Button variant="secondary" size="sm" loading={pending === `lot-${lot.id}`} onClick={() => void addLot(lot.id)}>
                  Presenta-t&apos;hi
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Modal>

      <ConflictDialog
        conflict={conflict}
        currency={currency}
        internal={internal}
        onReload={() => {
          // Ja s'ha rellegit. L'editor passa a ensenyar EL SERVIDOR, tal com
          // és: no s'hi aboquen els valors locals, que seria fusionar sense
          // dir-ho. L'esborrany es guarda a part per poder-lo consultar.
          if (conflict) setPreviousDraft(conflict.draft);
          if (conflict?.server) {
            setDraft(draftFromLine(conflict.server));
            setSaved(false);
            setConflict(null);
          } else {
            setConflict(null);
            setEditing(null);
            setDraft(null);
            setSaved(false);
          }
        }}
        onDiscard={() => {
          setConflict(null);
          closeEditor();
        }}
      />
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Resum                                                                      */
/* ------------------------------------------------------------------------- */

function Summary({
  label,
  value,
  hint,
  strong,
  level,
}: {
  label: string;
  value: string;
  hint?: string;
  strong?: boolean;
  level?: "danger";
}) {
  return (
    <div className="min-w-0">
      <p className={KICKER}>{label}</p>
      <p className={cn("tabular-nums", strong ? "text-[17px] font-semibold text-ink" : "text-body text-ink", level === "danger" && "text-danger")}>
        {value}
      </p>
      {hint && <p className="text-caption text-muted">{hint}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Editor d'una línia                                                         */
/* ------------------------------------------------------------------------- */

function LineEditor({
  previousDraft,
  onDiscardPrevious,
  draft,
  onDraft,
  internal,
  saving,
  saved,
  dirty,
  error,
  currency,
  isNew,
  line,
  onApplyTarget,
  applying,
  onSave,
  onCancel,
}: {
  previousDraft: LineDraft | null;
  onDiscardPrevious: () => void;
  draft: LineDraft;
  onDraft: (next: LineDraft) => void;
  internal: boolean;
  saving: boolean;
  saved: boolean;
  dirty: boolean;
  error: { code: string | null; message: string } | null;
  currency: string;
  isNew: boolean;
  /** La línia desada. `null` mentre se'n crea una de nova. */
  line: OfferLine | null;
  onApplyTarget: () => void;
  applying: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const set = <K extends keyof LineDraft>(key: K, value: LineDraft[K]) => onDraft({ ...draft, [key]: value });
  const missingConcept = draft.concept.trim() === "";
  const state = saving ? "Desant…" : error ? null : dirty ? "Canvis sense desar" : saved ? "Desat" : null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (!saving && !missingConcept) onSave();
    }
  };

  return (
    <div onKeyDown={onKeyDown} className="px-3.5 py-4">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <p className="text-caption text-muted">
          Els totals es calculen en desar, no mentre escrius. {isNew && "La línia no existirà fins que la desis."}
        </p>

        {previousDraft && (
          <details className="border border-border-strong bg-subtle px-3 py-2">
            <summary className="cursor-pointer text-caption text-ink">El teu esborrany anterior</summary>
            <p className="mt-1 text-caption text-muted">
              És el que havies escrit abans del conflicte. No s&apos;ha desat i no s&apos;ha aplicat enlloc: el tens aquí per si vols
              tornar-hi a posar alguna cosa a mà.
            </p>
            <dl className="mt-1.5 divide-y divide-border border-t border-border">
              {CONFLICT_FIELDS.filter((field) => internal || !field.internal).map((field) => (
                <div key={field.key} className="flex justify-between gap-3 py-1 text-caption">
                  <dt className="text-muted">{field.label}</dt>
                  <dd className="text-ink">
                    {previousDraft[field.key].trim() === ""
                      ? "—"
                      : field.money
                        ? formatAmount(previousDraft[field.key], currency)
                        : previousDraft[field.key]}
                  </dd>
                </div>
              ))}
            </dl>
            <Button type="button" variant="ghost" size="sm" className="mt-1.5" onClick={onDiscardPrevious}>
              Descarta l&apos;esborrany anterior
            </Button>
          </details>
        )}

        <div className="grid gap-3 md:grid-cols-12">
          <Field label="Concepte" required className="md:col-span-6" error={missingConcept ? "Cal un concepte." : undefined}>
            <Input value={draft.concept} onChange={(event) => set("concept", event.target.value)} maxLength={500} />
          </Field>
          <Field label="Quantitat" className="md:col-span-2">
            <Input inputMode="decimal" value={draft.quantity} onChange={(event) => set("quantity", event.target.value)} />
          </Field>
          <Field label="Base de preu" className="md:col-span-4">
            <Select
              options={PRICING_BASES.map((basis) => ({ value: basis, label: PRICING_BASIS_LABELS[basis] }))}
              value={draft.pricing_basis}
              onChange={(event) => set("pricing_basis", event.target.value as PricingBasis)}
            />
          </Field>
        </div>

        <div className="grid gap-3 md:grid-cols-12">
          <Field label="Proveïdor" className="md:col-span-4">
            <Input value={draft.supplier_name} onChange={(event) => set("supplier_name", event.target.value)} maxLength={300} />
          </Field>
          <Field label="Producte" className="md:col-span-4">
            <Input value={draft.product_name} onChange={(event) => set("product_name", event.target.value)} maxLength={300} />
          </Field>
          {usesDuration(draft.pricing_basis) && (
            <Field
              label="Durada pròpia (mesos)"
              className="md:col-span-4"
              hint="Buida: hereta la del lot. Si el lot tampoc no en té, la durada queda pendent."
            >
              <Input inputMode="numeric" value={draft.duration_months_override} onChange={(event) => set("duration_months_override", event.target.value)} />
            </Field>
          )}
        </div>

        <div className="grid gap-3 md:grid-cols-12">
          <Field label={`Preu ofert (${currency}, sense IVA)`} className="md:col-span-3">
            <Input inputMode="decimal" value={draft.offered_unit_price_ex_vat} onChange={(event) => set("offered_unit_price_ex_vat", event.target.value)} />
          </Field>
          {/* El cost només existeix per a qui el pot veure: sense el bloc del
              servidor no hi ha ni camp ni valor per enviar. */}
          {internal && (
            <Field label={`Cost intern (${currency})`} className="md:col-span-3">
              <Input inputMode="decimal" value={draft.unit_cost_ex_vat} onChange={(event) => set("unit_cost_ex_vat", event.target.value)} />
            </Field>
          )}
          <Field label="Màxim unitari del plec" className="md:col-span-3" hint="Transcrit a mà del plec. Buit: no consta.">
            <Input
              inputMode="decimal"
              value={draft.tender_max_unit_price_ex_vat}
              onChange={(event) => set("tender_max_unit_price_ex_vat", event.target.value)}
            />
          </Field>
          <Field label="IVA (%)" className="md:col-span-3" hint="Buit: sense definir. No s'hi suposa cap tipus.">
            <Input inputMode="decimal" value={draft.vat_rate} onChange={(event) => set("vat_rate", event.target.value)} />
          </Field>
        </div>

        {/* L'objectiu comercial va en un bloc propi i separat del preu: la
            separacio visual ES el missatge. Nomes per a rols economics. */}
        {internal && !isNew && line && (
          <TargetBlock
            line={line}
            currency={currency}
            draft={draft.target_markup_pct}
            onChange={(value) => set("target_markup_pct", value)}
            onApply={onApplyTarget}
            applying={applying}
          />
        )}

        <Field label="Notes">
          <Textarea rows={2} value={draft.notes} onChange={(event) => set("notes", event.target.value)} maxLength={4000} />
        </Field>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button type="submit" variant="primary" size="sm" loading={saving} disabled={missingConcept || (!isNew && !dirty)}>
            Desa
          </Button>
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
              {error.message} Res no ha quedat desat.
            </span>
          )}
        </div>
        <p className="text-caption text-faint">Esc cancel·la · Ctrl/⌘+S desa</p>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Conflicte de revisió                                                       */
/* ------------------------------------------------------------------------- */

function ConflictDialog({
  conflict,
  currency,
  internal,
  onReload,
  onDiscard,
}: {
  conflict: Conflict | null;
  currency: string;
  internal: boolean;
  onReload: () => void;
  onDiscard: () => void;
}) {
  if (!conflict) return null;
  const server = conflict.server ? draftFromLine(conflict.server) : null;
  const fields = CONFLICT_FIELDS.filter((field) => internal || !field.internal);
  const show = (value: string, money: boolean) => (value.trim() === "" ? "—" : money ? formatAmount(value, currency) : value);

  return (
    <Modal
      open
      title="Aquesta oferta s'ha modificat des que vas començar a editar"
      description="El servidor té una versió més nova que la que vas obrir. El teu esborrany es conserva aquí i no es desa res fins que triïs."
      size="lg"
      onClose={onReload}
      footer={
        <>
          <Button variant="secondary" onClick={onDiscard}>
            Descarta els meus canvis
          </Button>
          <Button variant="primary" onClick={onReload}>
            Recarrega i revisa
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <section className="border border-border-strong">
          <h3 className={cn(KICKER, "border-b border-border bg-subtle px-3 py-1.5")}>El que has escrit tu</h3>
          <dl className="divide-y divide-border">
            {fields.map((field) => (
              <div key={field.key} className="flex justify-between gap-3 px-3 py-1.5 text-body">
                <dt className="text-muted">{field.label}</dt>
                <dd className="text-ink">{show(conflict.draft[field.key] as string, field.money)}</dd>
              </div>
            ))}
          </dl>
          <p className="border-t border-border px-3 py-1.5 text-caption text-muted">Esborrany local, sense desar</p>
        </section>

        <section className="border border-border-strong">
          <h3 className={cn(KICKER, "border-b border-border bg-subtle px-3 py-1.5")}>El valor actual del servidor</h3>
          {server ? (
            <dl className="divide-y divide-border">
              {fields.map((field) => (
                <div key={field.key} className="flex justify-between gap-3 px-3 py-1.5 text-body">
                  <dt className="text-muted">{field.label}</dt>
                  <dd className="text-ink">{show(server[field.key] as string, field.money)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="px-3 py-3 text-body text-muted">Aquesta línia ja no hi és.</p>
          )}
          <p className="border-t border-border px-3 py-1.5 text-caption text-muted">Llegit en recarregar</p>
        </section>
      </div>
      <p className="mt-3 text-caption text-muted">No es fusiona res: el preu d&apos;una oferta no es pot endevinar.</p>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * L'objectiu comercial d'una línia.
 *
 * Tres coses en tres línies, i la separació és el missatge: **l'objectiu no és
 * el preu**. Editar el markup mou el preu proposat i deixa el preu ofert
 * exactament on era; escriure'l és una acció a part, amb el seu botó, perquè
 * és una decisió.
 *
 * Només per a rols econòmics: sense el bloc `internal_economics` del servidor
 * aquí no hi ha ni camp ni valor.
 */
function TargetBlock({
  line,
  currency,
  draft,
  onChange,
  onApply,
  applying,
}: {
  line: OfferLine;
  currency: string;
  draft: string;
  onChange: (value: string) => void;
  onApply: () => void;
  applying: boolean;
}) {
  const internal = line.internal_economics;
  const proposed = internal?.proposed_unit_price_ex_vat ?? null;
  const matches = internal?.price_matches_target ?? null;
  const offered = line.offered_unit_price_ex_vat;
  // Si ja hi ha un preu escrit i no és el proposat, aplicar-lo el substituirà.
  // Val més dir-ho abans que ensenyar un penediment després.
  const replaces = proposed !== null && offered !== null && matches === false;

  return (
    <div className="grid gap-3 rounded-card border border-border px-4 py-3">
      <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">
        Objectiu comercial
      </p>

      <div className="grid gap-3 md:grid-cols-12">
        <Field
          label="Objectiu de markup (%)"
          className="md:col-span-4"
          hint="Sobre cost. Editar-lo no toca el preu ofert."
        >
          <Input
            inputMode="decimal"
            value={draft}
            onChange={(event) => onChange(event.target.value)}
          />
        </Field>

        <Field label={`Preu proposat (${currency})`} className="md:col-span-4">
          <p className="text-body tabular-nums text-ink">
            {proposed === null ? "—" : formatAmount(proposed, currency)}
          </p>
        </Field>

        <Field label="Estat" className="md:col-span-4">
          {matches === null ? (
            <StatusPill level="neutral" size="sm">
              Sense determinar
            </StatusPill>
          ) : matches ? (
            <StatusPill level="ready" size="sm">
              El preu ofert ja és l&apos;objectiu
            </StatusPill>
          ) : (
            <StatusPill level="attention" size="sm">
              El preu ofert no és l&apos;objectiu
            </StatusPill>
          )}
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          loading={applying}
          disabled={proposed === null || matches === true}
          onClick={onApply}
        >
          Aplica objectiu
        </Button>
        {replaces && (
          <span className="text-caption text-muted">
            Substituirà el preu ofert actual ({formatAmount(offered, currency)}).
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * Què ha canviat a l'opció des que aquesta línia la va adoptar.
 *
 * **Informatiu, no un error.** Que l'oferta segueixi el que es va adoptar pot
 * ser exactament el que es vol —potser el preu ja està negociat—, i per això no
 * hi ha cap estil d'alarma ni cap «Sincronitza-ho tot».
 *
 * Una casella per camp, cap marcada, i el botó adopta només el que s'ha triat.
 */
function DivergenceDrawer({
  line,
  currency,
  internal,
  onClose,
  onSync,
  syncing,
}: {
  line: OfferLine | null;
  currency: string;
  internal: boolean;
  onClose: () => void;
  onSync: (fields: string[]) => Promise<void>;
  syncing: boolean;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const fields = line ? visibleDivergedFields(line, internal) : [];

  return (
    <Modal
      open={line !== null}
      title="L'opció ha canviat"
      description="Tria què vols adoptar. El que no marquis es queda com està."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={syncing}>
            Tanca
          </Button>
          <Button
            variant="primary"
            loading={syncing}
            disabled={picked.length === 0}
            onClick={() => void onSync(picked).then(() => setPicked([]))}
          >
            Actualitza els camps seleccionats
          </Button>
        </>
      }
    >
      <div className="grid gap-2">
        <p className="text-caption text-muted">
          El preu ofert i l&apos;objectiu no es toquen: no són de l&apos;opció.
        </p>
        {fields.map((field) => (
          <label key={field} className="flex items-center gap-2.5 text-body">
            <input
              type="checkbox"
              className="h-4 w-4 shrink-0 accent-brand"
              checked={picked.includes(field)}
              onChange={(event) =>
                setPicked((current) =>
                  event.target.checked
                    ? [...current, field]
                    : current.filter((item) => item !== field),
                )
              }
            />
            <span>{SYNC_FIELD_LABELS[field] ?? field}</span>
          </label>
        ))}
        {fields.length === 0 && (
          <p className="text-caption text-muted">
            No hi ha res que aquest rol pugui revisar.
          </p>
        )}
        <p className="text-caption text-faint">{currency}</p>
      </div>
    </Modal>
  );
}
