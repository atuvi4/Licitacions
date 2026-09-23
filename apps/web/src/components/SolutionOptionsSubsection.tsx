"use client";

import { useCallback, useState } from "react";

import { Modal } from "@/components/Modal";
import { useInvalidateReadiness } from "@/components/ReadinessProvider";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  Field,
  Input,
  LoadingRows,
  PermissionNote,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import {
  PRICING_BASIS_LABELS,
  QUOTE_STATUSES,
  QUOTE_STATUS_LABELS,
  QUOTE_STATUS_LEVEL,
  SELECTION_LABELS,
  SELECTION_LEVEL,
  canEditOptions,
  optionTitle,
  whoCanEditOptions,
  type PricingBasis,
  type QuoteStatus,
  type SolutionOption,
  type SolutionOptionList,
} from "@/lib/solutionOptions";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/**
 * Opcions i cotitzacions: les vies possibles i la que s'ha triat.
 *
 * Viu dins de «Solució tècnica» perquè configurar proveïdors no és una feina a
 * part: és el mateix moment en què algú decideix què oferirà.
 *
 * Dues coses que la pantalla no fa mai, i són deliberades:
 *
 * * **triar una opció no en descarta cap altra.** No hi ha cap casella
 *   excloent: «Selecciona» i «Rebutja» són dues accions separades, i rebutjar
 *   demana motiu. Auto-descartar les germanes escriuria una decisió que ningú
 *   no ha pres;
 * * **el cost no apareix per a qui no el pot veure.** No hi surt un «0 €» ni un
 *   «ocult»: la columna no hi és. L'API tampoc no l'envia.
 */
export function SolutionOptionsSubsection({ workspace }: { workspace: WorkspaceDetail }) {
  const { companyId, membership } = useSession();
  const invalidateReadiness = useInvalidateReadiness();
  const canEdit = canEditOptions(membership?.role);

  const path = companyId
    ? `/companies/${companyId}/workspaces/${workspace.id}/solution-options`
    : null;
  const options = useResource<SolutionOptionList>(path);
  const data = options.data ?? null;
  const rows = data?.items ?? [];
  const canSeeCost = data?.can_view_internal_economics ?? false;

  const [createOpen, setCreateOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [rejectFor, setRejectFor] = useState<SolutionOption | null>(null);

  const refresh = useCallback(async () => {
    await options.reload();
    invalidateReadiness();
  }, [invalidateReadiness, options]);

  return (
    <Card>
      <CardHeader
        title="Opcions i cotitzacions"
        description="Les vies possibles, què en diu cada proveïdor i quina s'ha triat."
        action={
          canEdit ? (
            <Button variant="secondary" size="sm" onClick={() => setCreateOpen(true)}>
              Afegeix opció
            </Button>
          ) : undefined
        }
      />

      {!canEdit && (
        <div className="border-b border-border px-3.5 py-2">
          <PermissionNote who={whoCanEditOptions()} what="configurar les opcions de solució">
            <span className="text-caption text-muted">Només lectura.</span>
          </PermissionNote>
        </div>
      )}

      {options.loading && <LoadingRows rows={2} />}

      {options.error && !options.loading && (
        <div className="grid gap-2 px-3.5 py-3">
          <Banner level="blocking" kicker="Opcions" action={null}>
            No s&apos;han pogut llegir les opcions: {options.error}
          </Banner>
          <div>
            <Button variant="secondary" size="sm" onClick={() => void options.reload()}>
              Torna-ho a provar
            </Button>
          </div>
        </div>
      )}

      {!options.loading && !options.error && rows.length === 0 && (
        <p className="px-3.5 py-5 text-body text-muted">Cap opció encara.</p>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-b border-border text-left">
                <th className={`px-3.5 py-2 ${KICKER}`}>Proveïdor</th>
                <th className={`px-3.5 py-2 ${KICKER}`}>Producte</th>
                <th className={`px-3.5 py-2 text-right ${KICKER}`}>Quantitat</th>
                <th className={`px-3.5 py-2 ${KICKER}`}>Unitat</th>
                {canSeeCost && (
                  <th className={`px-3.5 py-2 text-right ${KICKER}`}>Cost unitari</th>
                )}
                <th className={`px-3.5 py-2 ${KICKER}`}>Cotització</th>
                <th className={`px-3.5 py-2 ${KICKER}`}>Tria</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((option) => (
                <OptionRow
                  key={option.id}
                  option={option}
                  workspace={workspace}
                  canEdit={canEdit}
                  canSeeCost={canSeeCost}
                  expanded={openId === option.id}
                  onToggle={() => setOpenId(openId === option.id ? null : option.id)}
                  onReject={() => setRejectFor(option)}
                  onChanged={refresh}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreateOptionDialog
        open={createOpen}
        workspaceId={workspace.id}
        canSeeCost={canSeeCost}
        onClose={() => setCreateOpen(false)}
        onCreated={refresh}
      />

      <RejectDialog
        option={rejectFor}
        workspaceId={workspace.id}
        onClose={() => setRejectFor(null)}
        onRejected={refresh}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------------- */

function OptionRow({
  option,
  workspace,
  canEdit,
  canSeeCost,
  expanded,
  onToggle,
  onReject,
  onChanged,
}: {
  option: SolutionOption;
  workspace: WorkspaceDetail;
  canEdit: boolean;
  canSeeCost: boolean;
  expanded: boolean;
  onToggle: () => void;
  onReject: () => void;
  onChanged: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const cost = option.internal_economics?.unit_cost_ex_vat ?? null;

  const select = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(
        `/companies/${companyId}/workspaces/${workspace.id}/solution-options/${option.id}/selection`,
        { expected_revision: option.revision, selection: "selected" },
      );
    });
    if (saved) await onChanged();
  }, [companyId, mutation, onChanged, option.id, option.revision, workspace.id]);

  const archive = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(
        `/companies/${companyId}/workspaces/${workspace.id}/solution-options/${option.id}/archive`,
        { expected_revision: option.revision },
      );
    });
    if (saved) await onChanged();
  }, [companyId, mutation, onChanged, option.id, option.revision, workspace.id]);

  return (
    <>
      <tr className="border-b border-border align-baseline">
        <td className="px-3.5 py-2.5">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={onToggle}
            className="text-left font-medium text-ink hover:underline"
          >
            {optionTitle(option)}
          </button>
        </td>
        <td className="px-3.5 py-2.5 text-muted">{option.product_name ?? "—"}</td>
        <td className="px-3.5 py-2.5 text-right tabular-nums">{option.quantity ?? "—"}</td>
        <td className="px-3.5 py-2.5 text-muted">{option.unit ?? "—"}</td>
        {/* La columna només existeix per a qui pot veure el cost. Un «0 €» seria
            una xifra falsa i un «ocult» diria que n'hi ha un. */}
        {canSeeCost && (
          <td className="px-3.5 py-2.5 text-right tabular-nums">{cost ?? "—"}</td>
        )}
        <td className="px-3.5 py-2.5">
          <StatusPill level={QUOTE_STATUS_LEVEL[option.quote_status]} size="sm">
            {QUOTE_STATUS_LABELS[option.quote_status]}
          </StatusPill>
        </td>
        <td className="px-3.5 py-2.5">
          <StatusPill level={SELECTION_LEVEL[option.selection]} size="sm">
            {SELECTION_LABELS[option.selection]}
          </StatusPill>
        </td>
      </tr>

      {expanded && (
        <tr className="border-b border-border bg-subtle">
          <td colSpan={canSeeCost ? 7 : 6} className="px-3.5 py-3">
            {mutation.error && (
              <Banner level="blocking" kicker="Opció" action={null}>
                {mutation.error}
              </Banner>
            )}

            {option.description && (
              <p className="mb-2 text-body text-ink">{option.description}</p>
            )}

            <dl className="grid gap-x-6 gap-y-1 text-caption text-muted sm:grid-cols-2">
              {option.pricing_basis && (
                <div>
                  <dt className="inline">Facturació: </dt>
                  <dd className="inline">{PRICING_BASIS_LABELS[option.pricing_basis]}</dd>
                </div>
              )}
              {option.quote_reference && (
                <div>
                  <dt className="inline">Referència: </dt>
                  <dd className="inline">{option.quote_reference}</dd>
                </div>
              )}
              {option.quote_date && (
                <div>
                  <dt className="inline">Data de la cotització: </dt>
                  <dd className="inline">{option.quote_date}</dd>
                </div>
              )}
              {option.quote_source && (
                <div>
                  <dt className="inline">Font: </dt>
                  <dd className="inline">{option.quote_source}</dd>
                </div>
              )}
              {option.requirement_is_effective === false && (
                <div className="sm:col-span-2">
                  <dd className="text-warning">
                    El requisit que responia ja no és del conjunt vigent. L&apos;opció es conserva.
                  </dd>
                </div>
              )}
            </dl>

            {option.selection === "selected" && option.selected_at && (
              <p className="mt-2 border-t border-border pt-2 text-caption text-muted">
                Seleccionada el {formatDateTime(option.selected_at)}.
              </p>
            )}
            {option.selection === "rejected" && option.selection_reason && (
              <p className="mt-2 border-t border-border pt-2 text-caption text-muted">
                Rebutjada: {option.selection_reason}
              </p>
            )}

            {canEdit && option.archived_at === null && (
              // Dues accions separades i explícites. Cap casella excloent: triar
              // una opció no diu res de les altres.
              <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
                {option.selection !== "selected" && (
                  <Button
                    variant="primary"
                    size="sm"
                    loading={mutation.saving}
                    onClick={() => void select()}
                  >
                    Selecciona
                  </Button>
                )}
                {option.selection !== "rejected" && (
                  <Button variant="secondary" size="sm" onClick={onReject}>
                    Rebutja
                  </Button>
                )}
                <Button
                  variant="secondary"
                  size="sm"
                  loading={mutation.saving}
                  onClick={() => void archive()}
                >
                  Arxiva
                </Button>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * Rebutjar una opció.
 *
 * El motiu és obligatori i el botó no s'habilita sense ell: una alternativa
 * descartada sense explicació és indistingible d'una que ningú no ha mirat.
 */
function RejectDialog({
  option,
  workspaceId,
  onClose,
  onRejected,
}: {
  option: SolutionOption | null;
  workspaceId: string;
  onClose: () => void;
  onRejected: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [reason, setReason] = useState("");

  const submit = useCallback(async () => {
    if (!companyId || !option) return;
    const saved = await mutation.run(async () => {
      await api.post(
        `/companies/${companyId}/workspaces/${workspaceId}/solution-options/${option.id}/selection`,
        {
          expected_revision: option.revision,
          selection: "rejected",
          reason: reason.trim(),
        },
      );
    });
    if (saved) {
      setReason("");
      onClose();
      await onRejected();
    }
  }, [companyId, mutation, onClose, onRejected, option, reason, workspaceId]);

  return (
    <Modal
      open={option !== null}
      title="Rebutja l'opció"
      description={option ? optionTitle(option) : ""}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button
            variant="primary"
            loading={mutation.saving}
            disabled={reason.trim() === ""}
            onClick={() => void submit()}
          >
            Rebutja-la
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        {mutation.error && (
          <Banner level="blocking" kicker="Opció" action={null}>
            {mutation.error}
          </Banner>
        )}
        <Field
          label="Per què es descarta"
          required
          hint="Les altres opcions no es toquen: rebutjar aquesta només parla d'aquesta."
        >
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */

function CreateOptionDialog({
  open,
  workspaceId,
  canSeeCost,
  onClose,
  onCreated,
}: {
  open: boolean;
  workspaceId: string;
  canSeeCost: boolean;
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [supplier, setSupplier] = useState("");
  const [product, setProduct] = useState("");
  const [description, setDescription] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState("");
  const [basis, setBasis] = useState<PricingBasis | "">("");
  const [duration, setDuration] = useState("");
  const [quoteStatus, setQuoteStatus] = useState<QuoteStatus>("draft");
  const [quoteReference, setQuoteReference] = useState("");
  const [quoteSource, setQuoteSource] = useState("");
  const [quoteDate, setQuoteDate] = useState("");
  const [cost, setCost] = useState("");

  const submit = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(`/companies/${companyId}/workspaces/${workspaceId}/solution-options`, {
        supplier_name: supplier.trim() || null,
        product_name: product.trim() || null,
        description: description.trim() || null,
        quantity: quantity.trim() || null,
        unit: unit.trim() || null,
        pricing_basis: basis || null,
        duration_months_override: duration.trim() === "" ? null : Number(duration),
        quote_status: quoteStatus,
        quote_date: quoteDate || null,
        quote_reference: quoteReference.trim() || null,
        quote_source: quoteSource.trim() || null,
        // El bloc només viatja si aquest rol pot escriure'l. Enviar-lo sense
        // potestat seria un 403, mai un camp que s'ignora en silenci.
        ...(canSeeCost && cost.trim() !== ""
          ? { internal_economics: { unit_cost_ex_vat: cost.trim() } }
          : {}),
      });
    });
    if (saved) {
      onClose();
      await onCreated();
    }
  }, [
    basis, canSeeCost, companyId, cost, description, duration, mutation, onClose, onCreated,
    product, quantity, quoteDate, quoteReference, quoteSource, quoteStatus, supplier, unit,
    workspaceId,
  ]);

  return (
    <Modal
      open={open}
      title="Nova opció"
      description="Una via possible. Triar-la és una acció a part."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button
            variant="primary"
            loading={mutation.saving}
            disabled={supplier.trim() === "" && product.trim() === ""}
            onClick={() => void submit()}
          >
            Crea-la
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        {mutation.error && (
          <Banner level="blocking" kicker="Opció" action={null}>
            {mutation.error}
          </Banner>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Proveïdor">
            <Input value={supplier} onChange={(e) => setSupplier(e.target.value)} maxLength={300} autoFocus />
          </Field>
          <Field label="Producte">
            <Input value={product} onChange={(e) => setProduct(e.target.value)} maxLength={300} />
          </Field>
        </div>

        <Field label="Descripció">
          <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Quantitat">
            <Input inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </Field>
          <Field label="Unitat">
            <Input value={unit} onChange={(e) => setUnit(e.target.value)} maxLength={50} />
          </Field>
          <Field label="Facturació">
            <Select
              aria-label="Facturació"
              value={basis}
              onChange={(e) => setBasis(e.target.value as PricingBasis | "")}
              placeholder="Sense especificar"
              options={Object.entries(PRICING_BASIS_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </Field>
          <Field label="Durada (mesos)">
            <Input inputMode="numeric" value={duration} onChange={(e) => setDuration(e.target.value)} />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Estat de la cotització">
            <Select
              aria-label="Estat de la cotització"
              value={quoteStatus}
              onChange={(e) => setQuoteStatus(e.target.value as QuoteStatus)}
              options={QUOTE_STATUSES.map((value) => ({
                value,
                label: QUOTE_STATUS_LABELS[value],
              }))}
            />
          </Field>
          <Field label="Data de la cotització">
            <Input type="date" value={quoteDate} onChange={(e) => setQuoteDate(e.target.value)} />
          </Field>
          <Field label="Referència">
            <Input value={quoteReference} onChange={(e) => setQuoteReference(e.target.value)} maxLength={200} />
          </Field>
          <Field label="Font">
            <Input value={quoteSource} onChange={(e) => setQuoteSource(e.target.value)} maxLength={1000} />
          </Field>
        </div>

        {/* Secció separada, i només per als rols econòmics. Per a la resta el
            camp no existeix: ni buit, ni desactivat. */}
        {canSeeCost && (
          <div className="grid gap-2 border-t border-border pt-3">
            <p className={KICKER}>Economia interna</p>
            <Field label="Cost unitari sense IVA" hint="El que ens costa. Mai el preu de venda.">
              <Input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
            </Field>
          </div>
        )}
      </div>
    </Modal>
  );
}
