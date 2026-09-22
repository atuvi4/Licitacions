"use client";

import { useCallback, useState } from "react";

import { CitationPicker } from "@/components/CitationPicker";
import { Modal } from "@/components/Modal";
import { useInvalidateReadiness } from "@/components/ReadinessProvider";
import { useSession } from "@/components/SessionProvider";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  EvidenceRef,
  Field,
  Input,
  LoadingRows,
  PermissionNote,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import {
  SOURCE_KINDS,
  SOURCE_KIND_LABELS,
  STATUS_LABELS,
  STATUS_LEVEL,
  canEditDimensions,
  statusExplanation,
  whoCanEditDimensions,
  workingValue,
  type Dimension,
  type DimensionObservation,
  type ObservationSourceKind,
} from "@/lib/dimensions";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { SourceRefCitation } from "@/lib/requirements";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/**
 * Dimensionament: les magnituds de què depèn l'oferta, i d'on surt cadascuna.
 *
 * Viu dins de «Solució tècnica» i no en una pestanya pròpia, perquè no és una
 * feina a part: és el mateix moment en què algú decideix què proposarà.
 *
 * Tota la pantalla està construïda al voltant d'una distinció que el producte
 * no pot perdre: **una font que diu 151 no és una decisió de fer servir 151.**
 * Per això les observacions es llisten totes, també les que contradiuen el
 * valor triat, i per això decidir obre un diàleg que demana un motiu en comptes
 * de ser un camp més del formulari.
 */
export function DimensionsSubsection({ workspace }: { workspace: WorkspaceDetail }) {
  const { companyId, membership } = useSession();
  const invalidateReadiness = useInvalidateReadiness();
  const canEdit = canEditDimensions(membership?.role);

  const path = companyId ? `/companies/${companyId}/workspaces/${workspace.id}/dimensions` : null;
  const dimensions = useResource<Dimension[]>(path);
  const rows = dimensions.data ?? [];

  const [createOpen, setCreateOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [decideFor, setDecideFor] = useState<Dimension | null>(null);

  const refresh = useCallback(async () => {
    await dimensions.reload();
    invalidateReadiness();
  }, [dimensions, invalidateReadiness]);

  return (
    <Card>
      <CardHeader
        title="Dimensionament"
        description="Les magnituds de què depèn l'oferta i què en diu cada font."
        action={
          canEdit ? (
            <Button variant="secondary" size="sm" onClick={() => setCreateOpen(true)}>
              Afegeix una magnitud
            </Button>
          ) : undefined
        }
      />

      {!canEdit && (
        <div className="border-b border-border px-3.5 py-2">
          <PermissionNote who={whoCanEditDimensions()} what="dimensionar la candidatura">
            <span className="text-caption text-muted">Només lectura.</span>
          </PermissionNote>
        </div>
      )}

      {dimensions.loading && <LoadingRows rows={2} />}

      {dimensions.error && !dimensions.loading && (
        <div className="px-3.5 py-3">
          <Banner level="blocking" kicker="Dimensionament" action={null}>
            No s&apos;ha pogut llegir el dimensionament: {dimensions.error}
          </Banner>
        </div>
      )}

      {!dimensions.loading && !dimensions.error && rows.length === 0 && (
        <p className="px-3.5 py-5 text-body text-muted">
          Cap magnitud encara. S&apos;hi afegeixen les xifres de què depèn la proposta —extensions,
          seus, hores— per deixar escrit d&apos;on surt cadascuna.
        </p>
      )}

      <ul className="divide-y divide-border">
        {rows.map((dimension) => (
          <DimensionRow
            key={dimension.id}
            dimension={dimension}
            workspace={workspace}
            canEdit={canEdit}
            expanded={openId === dimension.id}
            onToggle={() => setOpenId(openId === dimension.id ? null : dimension.id)}
            onDecide={() => setDecideFor(dimension)}
            onChanged={refresh}
          />
        ))}
      </ul>

      <CreateDimensionDialog
        open={createOpen}
        workspaceId={workspace.id}
        onClose={() => setCreateOpen(false)}
        onCreated={refresh}
      />

      <DecideDialog
        dimension={decideFor}
        workspaceId={workspace.id}
        onClose={() => setDecideFor(null)}
        onDecided={refresh}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------------- */

function DimensionRow({
  dimension,
  workspace,
  canEdit,
  expanded,
  onToggle,
  onDecide,
  onChanged,
}: {
  dimension: Dimension;
  workspace: WorkspaceDetail;
  canEdit: boolean;
  expanded: boolean;
  onToggle: () => void;
  onDecide: () => void;
  onChanged: () => Promise<void>;
}) {
  const value = workingValue(dimension);

  return (
    <li>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5 px-3.5 py-3">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={onToggle}
          className="text-left text-body font-medium text-ink hover:underline"
        >
          {dimension.label}
        </button>
        {dimension.scope_label && <span className="text-caption text-muted">{dimension.scope_label}</span>}

        <span className="ml-auto flex items-center gap-2.5">
          {value !== null ? (
            <span className="text-body font-medium tabular-nums text-ink">{value}</span>
          ) : (
            <span className="text-caption text-muted">Sense valor de treball</span>
          )}
          <StatusPill level={STATUS_LEVEL[dimension.status]} size="sm">
            {STATUS_LABELS[dimension.status]}
          </StatusPill>
        </span>
      </div>

      <p className="px-3.5 pb-2.5 text-caption text-muted">{statusExplanation(dimension)}</p>

      {expanded && (
        <div className="border-t border-border bg-subtle px-3.5 py-3">
          <p className={KICKER}>
            {dimension.observations.length === 1 ? "1 font" : `${dimension.observations.length} fonts`}
          </p>

          <ul className="mt-2 grid gap-2.5">
            {dimension.observations.map((observation) => (
              <ObservationItem
                key={observation.id}
                observation={observation}
                tenderId={workspace.tender_id}
                decided={dimension.status === "decided" ? dimension.working_value_num : null}
              />
            ))}
          </ul>

          {dimension.observations.length === 0 && (
            <p className="mt-2 text-caption text-muted">Encara no hi ha cap font.</p>
          )}

          {/* El motiu ja surt a la linia de sobre; aqui s'hi afegeix quan es va
              decidir. Repetir-lo dues vegades a la mateixa pantalla el
              convertiria en soroll. */}
          {dimension.status === "decided" && dimension.decided_at && (
            <p className="mt-3 border-t border-border pt-2.5 text-caption text-muted">
              Decidida el {formatDateTime(dimension.decided_at)}.
            </p>
          )}

          {canEdit && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
              <Button variant="primary" size="sm" onClick={onDecide}>
                {dimension.status === "decided" ? "Canvia el valor de treball" : "Fixa el valor de treball"}
              </Button>
            </div>
          )}

          {canEdit && (
            <AddObservationForm dimension={dimension} workspace={workspace} onAdded={onChanged} />
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Una font i el que deia. Mai s'edita ni s'esborra: no hi ha cap botó, i no és
 * cap oblit. Si es pogués reescriure, deixaria de dir què va llegir algú i
 * passaria a dir què li convé a algú que la mira avui.
 */
function ObservationItem({
  observation,
  tenderId,
  decided,
}: {
  observation: DimensionObservation;
  tenderId: string;
  decided: string | null;
}) {
  const ref = observation.source_ref;
  // Una font que no coincideix amb el valor decidit no s'amaga ni s'esborra:
  // és justament el que explica per què la decisió va costar.
  const differs =
    decided !== null && observation.value_num !== null && Number(observation.value_num) !== Number(decided);

  return (
    <li className="grid gap-1 border border-border bg-canvas px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <StatusPill level="neutral" size="sm">
          {SOURCE_KIND_LABELS[observation.source_kind]}
        </StatusPill>
        <span className="text-body font-medium tabular-nums text-ink">{observation.value_text}</span>
        {differs && (
          <span className="text-caption text-muted">No és el valor de treball</span>
        )}
        <span className="ml-auto text-caption text-muted">{formatDateTime(observation.created_at)}</span>
      </div>

      {observation.note && <p className="text-caption text-muted">{observation.note}</p>}

      {ref && (
        <EvidenceRef
          documentName={ref.document_name}
          pageNumber={ref.page_number}
          href={
            ref.document_id
              ? `/licitacions/${tenderId}/documents/${ref.document_id}?page=${ref.page_number ?? ""}&extraction=${ref.extraction_id ?? ""}`
              : null
          }
          quote={ref.quote ?? ref.location_label}
          compact
        />
      )}
    </li>
  );
}

/* ------------------------------------------------------------------------- */

function AddObservationForm({
  dimension,
  workspace,
  onAdded,
}: {
  dimension: Dimension;
  workspace: WorkspaceDetail;
  onAdded: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ObservationSourceKind>("tender_document");
  const [text, setText] = useState("");
  const [num, setNum] = useState("");
  const [note, setNote] = useState("");
  const [citation, setCitation] = useState<SourceRefCitation | null>(null);

  const submit = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(
        `/companies/${companyId}/workspaces/${workspace.id}/dimensions/${dimension.id}/observations`,
        {
          expected_revision: dimension.revision,
          source_kind: kind,
          value_text: text.trim(),
          value_num: num.trim() === "" ? null : num.trim(),
          note: note.trim() || null,
          source_ref_id: citation?.id ?? null,
        },
      );
    });
    if (saved) {
      setText("");
      setNum("");
      setNote("");
      setCitation(null);
      setOpen(false);
      await onAdded();
    }
  }, [citation, companyId, dimension.id, dimension.revision, kind, mutation, note, num, onAdded, text, workspace.id]);

  if (!open) {
    return (
      <div className="mt-2">
        <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
          Afegeix una font
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-3 grid gap-3 border-t border-border pt-3">
      <p className={KICKER}>Què diu aquesta font</p>

      {mutation.error && (
        <Banner level="blocking" kicker="Font" action={null}>
          {mutation.error}
        </Banner>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Mena de font">
          <Select
            aria-label="Mena de font"
            value={kind}
            onChange={(event) => setKind(event.target.value as ObservationSourceKind)}
            options={SOURCE_KINDS.map((item) => ({ value: item, label: SOURCE_KIND_LABELS[item] }))}
          />
        </Field>

        <Field label="Què hi diu" required>
          <Input value={text} onChange={(event) => setText(event.target.value)} maxLength={500} />
        </Field>

        <Field
          label="Xifra"
          hint="Només si el valor és un número. Sense això, el sistema no pot veure que dues fonts es contradiuen."
        >
          <Input inputMode="decimal" value={num} onChange={(event) => setNum(event.target.value)} />
        </Field>

        <Field label="Nota">
          <Input value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
      </div>

      {citation ? (
        <p className="text-caption text-muted">
          Cita: {citation.document_name}
          {citation.page_number !== null ? ` · p. ${citation.page_number}` : ""}
          {citation.location_label ? ` · ${citation.location_label}` : ""}{" "}
          <button type="button" className="text-brand hover:underline" onClick={() => setCitation(null)}>
            Treu-la
          </button>
        </p>
      ) : (
        <CitationPicker tenderId={workspace.tender_id} onAdded={setCitation} />
      )}

      <div className="flex gap-2">
        <Button variant="primary" size="sm" loading={mutation.saving} disabled={text.trim() === ""} onClick={() => void submit()}>
          Afegeix la font
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setOpen(false)} disabled={mutation.saving}>
          Cancel·la
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function CreateDimensionDialog({
  open,
  workspaceId,
  onClose,
  onCreated,
}: {
  open: boolean;
  workspaceId: string;
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [unit, setUnit] = useState("");
  const [scope, setScope] = useState("");

  const submit = useCallback(async () => {
    if (!companyId) return;
    const saved = await mutation.run(async () => {
      await api.post(`/companies/${companyId}/workspaces/${workspaceId}/dimensions`, {
        key: key.trim() || label.trim().toLowerCase().replace(/\s+/g, "_").slice(0, 100),
        label: label.trim(),
        unit: unit.trim() || null,
        scope_label: scope.trim() || null,
      });
    });
    if (saved) {
      setKey("");
      setLabel("");
      setUnit("");
      setScope("");
      onClose();
      await onCreated();
    }
  }, [companyId, key, label, mutation, onClose, onCreated, scope, unit, workspaceId]);

  return (
    <Modal
      open={open}
      title="Nova magnitud"
      description="Només el concepte. El valor de treball es fixa després, quan se sàpiga d'on surt."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button variant="primary" loading={mutation.saving} disabled={label.trim() === ""} onClick={() => void submit()}>
            Crea-la
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        {mutation.error && (
          <Banner level="blocking" kicker="Magnitud" action={null}>
            {mutation.error}
          </Banner>
        )}
        <Field label="Nom" required error={mutation.fieldErrors.label}>
          <Input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={300} autoFocus />
        </Field>
        <Field label="Clau" hint="Com s'hi referirà la resta del sistema. Si es deixa en blanc, surt del nom." error={mutation.fieldErrors.key}>
          <Input value={key} onChange={(event) => setKey(event.target.value)} maxLength={100} />
        </Field>
        <Field label="Unitat">
          <Input value={unit} onChange={(event) => setUnit(event.target.value)} maxLength={50} />
        </Field>
        <Field label="Àmbit" hint="Si la magnitud és d'una seu o d'un lot concret.">
          <Input value={scope} onChange={(event) => setScope(event.target.value)} maxLength={300} />
        </Field>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * Fixar el valor de treball.
 *
 * El motiu és obligatori i el botó no s'habilita sense ell. No és burocràcia:
 * d'aquí a sis mesos, quan algú es pregunti per què l'oferta deia 181 i el plec
 * en deia 151, aquesta frase és tota la resposta que hi haurà.
 */
function DecideDialog({
  dimension,
  workspaceId,
  onClose,
  onDecided,
}: {
  dimension: Dimension | null;
  workspaceId: string;
  onClose: () => void;
  onDecided: () => Promise<void>;
}) {
  const { companyId } = useSession();
  const mutation = useMutation();
  const [text, setText] = useState("");
  const [num, setNum] = useState("");
  const [reason, setReason] = useState("");

  const submit = useCallback(async () => {
    if (!companyId || !dimension) return;
    const saved = await mutation.run(async () => {
      await api.post(
        `/companies/${companyId}/workspaces/${workspaceId}/dimensions/${dimension.id}/decide`,
        {
          expected_revision: dimension.revision,
          working_value_text: text.trim(),
          working_value_num: num.trim() === "" ? null : num.trim(),
          reason: reason.trim(),
        },
      );
    });
    if (saved) {
      setText("");
      setNum("");
      setReason("");
      onClose();
      await onDecided();
    }
  }, [companyId, dimension, mutation, num, onClose, onDecided, reason, text, workspaceId]);

  const candidates = dimension
    ? Array.from(new Set(dimension.observations.filter((o) => o.value_num !== null).map((o) => o.value_text)))
    : [];

  return (
    <Modal
      open={dimension !== null}
      title="Fixa el valor de treball"
      description={dimension ? dimension.label : ""}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button
            variant="primary"
            loading={mutation.saving}
            disabled={text.trim() === "" || reason.trim() === ""}
            onClick={() => void submit()}
          >
            Fixa&apos;l
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        {mutation.error && (
          <Banner level="blocking" kicker="Decisió" action={null}>
            {mutation.error}
          </Banner>
        )}

        {candidates.length > 0 && (
          <p className="text-caption text-muted">
            Les fonts diuen: {candidates.join(", ")}. Cap no es tria sola.
          </p>
        )}

        <Field label="Valor de treball" required>
          <Input value={text} onChange={(event) => setText(event.target.value)} maxLength={500} autoFocus />
        </Field>

        <Field label="Xifra" hint="Per poder avisar si més endavant arriba una font que no hi coincideix.">
          <Input inputMode="decimal" value={num} onChange={(event) => setNum(event.target.value)} />
        </Field>

        <Field
          label="Per què aquest"
          required
          hint="Qui llegeixi això d'aquí a sis mesos no tindrà cap altra explicació."
        >
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
