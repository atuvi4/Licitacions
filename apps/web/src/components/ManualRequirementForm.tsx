"use client";

import { useCallback, useMemo, useState } from "react";

import { CitationList, CitationPicker } from "@/components/CitationPicker";
import { Modal } from "@/components/Modal";
import { useSession } from "@/components/SessionProvider";
import { Banner, Button, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import {
  CATEGORY_LABELS,
  type SourceRefCitation,
  OBLIGATION_LABELS,
  type Requirement,
  type RequirementCategory,
  type RequirementObligation,
} from "@/lib/requirements";

/** Qui prepara la candidatura. Inclou `technical`: escriure requisits és feina tècnica. */
const WRITE_ROLES = ["owner", "admin", "bid_manager", "technical"] as const;

export function canWriteRequirements(role: string | null | undefined): boolean {
  return !!role && (WRITE_ROLES as readonly string[]).includes(role);
}

const SCOPES = [
  { value: "tender", label: "Tota la licitació" },
  { value: "lot", label: "Un lot" },
  { value: "unknown", label: "No consta" },
] as const;

interface FormState {
  title: string;
  description: string;
  category: RequirementCategory;
  obligation: RequirementObligation;
  scope: string;
}

const EMPTY: FormState = {
  title: "",
  description: "",
  category: "other",
  obligation: "mandatory",
  scope: "tender",
};

function stateOf(requirement: Requirement | null): FormState {
  if (!requirement) return EMPTY;
  return {
    title: requirement.title,
    description: requirement.description ?? "",
    category: requirement.category,
    obligation: requirement.obligation,
    scope: requirement.scope,
  };
}

/**
 * Escriure o corregir un requisit a mà.
 *
 * Només camps que una persona pot DEFINIR. No hi ha confiança, ni origen, ni
 * run: són fets sobre com ha nascut la fila i els fixa el servidor. Deixar-los
 * escriure voldria dir deixar que algú digués «això ho ha extret un model amb
 * confiança alta» d'una cosa que acaba d'escriure ell.
 *
 * Un requisit escrit aquí neix **sense revisar**. Que l'hagi escrit una persona
 * no vol dir que estigui comprovat, i citar-ne la font tampoc no el verifica.
 */
export function ManualRequirementForm({
  open,
  tenderId,
  requirement,
  onClose,
  onSaved,
}: {
  open: boolean;
  tenderId: string;
  /** `null` per crear-ne un de nou. */
  requirement: Requirement | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const { companyId } = useSession();
  const [form, setForm] = useState<FormState>(() => stateOf(requirement));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  // Les cites que sostindran aquest requisit. En un requisit nou, es creen
  // ABANS que ell i s'hi enganxen en desar-lo; si el desat falla, queden
  // inertes: son immutables i no les referencia ningu.
  const [citations, setCitations] = useState<SourceRefCitation[]>([]);

  // En obrir amb un requisit diferent, el formulari parteix del seu estat.
  const key = requirement?.id ?? "new";
  if (open && loaded !== key) {
    setLoaded(key);
    setForm(stateOf(requirement));
    setCitations(requirement?.citations ?? []);
    setError(null);
  }

  const invalid = useMemo(() => !form.title.trim(), [form.title]);

  const save = useCallback(async () => {
    if (!companyId || invalid) return;
    setSaving(true);
    setError(null);
    const body = {
      title: form.title.trim(),
      description: form.description.trim() || null,
      category: form.category,
      obligation: form.obligation,
      scope: form.scope,
    };
    const root = `/companies/${companyId}/tenders/${tenderId}/requirements`;
    try {
      if (requirement) {
        await api.patch(`${root}/${requirement.id}`, body);
      } else {
        // Les cites ja existeixen: nomes cal dir quines el sostenen.
        await api.post(root, { ...body, source_ref_ids: citations.map((item) => item.id) });
      }
      await onSaved();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No s'ha pogut desar.");
    } finally {
      setSaving(false);
    }
  }, [citations, companyId, form, invalid, onClose, onSaved, requirement, tenderId]);

  const addCitation = useCallback(
    async (citation: SourceRefCitation) => {
      setCitations((current) => [...current, citation]);
      if (!requirement || !companyId) return;
      await api.post(
        `/companies/${companyId}/tenders/${tenderId}/requirements/${requirement.id}/citations`,
        { source_ref_id: citation.id },
      );
      await onSaved();
    },
    [companyId, onSaved, requirement, tenderId],
  );

  const removeCitation = useCallback(
    async (citation: SourceRefCitation) => {
      setCitations((current) => current.filter((item) => item.id !== citation.id));
      if (!requirement || !companyId) return;
      // Desenganxa l'enllac. La cita NO s'esborra: pot sostenir-ne d'altres i
      // era certa el dia que es va crear.
      await api.delete(
        `/companies/${companyId}/tenders/${tenderId}/requirements/${requirement.id}` +
          `/citations/${citation.id}`,
      );
      await onSaved();
    },
    [companyId, onSaved, requirement, tenderId],
  );

  return (
    <Modal
      open={open}
      title={requirement ? "Corregeix el requisit" : "Afegeix un requisit"}
      description={
        requirement
          ? "Només es poden corregir els requisits escrits a mà."
          : "Per al que el plec exigeix i l'anàlisi no ha trobat. Queda sense revisar fins que algú ho confirmi."
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel·la
          </Button>
          <Button variant="primary" loading={saving} disabled={invalid} onClick={() => void save()}>
            Desa
          </Button>
        </>
      }
    >
      {error && (
        <Banner level="blocking" kicker="No s'ha desat" className="mb-4" action={null}>
          {error}
        </Banner>
      )}
      <div className="grid gap-4">
        <Field label="Què exigeix el plec" required>
          <Input
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="Assegurança de responsabilitat civil de 600.000 €"
            autoFocus
          />
        </Field>
        <Field label="Detall" hint="Opcional. El que calgui per entendre'l sense obrir el plec.">
          <Textarea
            rows={3}
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
          />
        </Field>
        <Field label="Categoria">
          <Select
            aria-label="Categoria"
            value={form.category}
            onChange={(event) =>
              setForm({ ...form, category: event.target.value as RequirementCategory })
            }
            options={Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label }))}
          />
        </Field>
        <Field label="Com obliga">
          <Select
            aria-label="Com obliga"
            value={form.obligation}
            onChange={(event) =>
              setForm({ ...form, obligation: event.target.value as RequirementObligation })
            }
            options={Object.entries(OBLIGATION_LABELS).map(([value, label]) => ({ value, label }))}
          />
        </Field>
        <Field label="A què aplica">
          <Select
            aria-label="A què aplica"
            value={form.scope}
            onChange={(event) => setForm({ ...form, scope: event.target.value })}
            options={SCOPES.map((option) => ({ ...option }))}
          />
        </Field>

        <section className="grid gap-2 border-t border-border pt-4">
          <h3 className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">
            Evidència
          </h3>
          {/*
            Adjuntar-ne no el verifica: una cita diu d'on surt l'afirmació, no
            que algú l'hagi comprovada. El requisit segueix sense revisar fins
            que algú ho digui.
          */}
          <CitationList citations={citations} onRemove={(item) => void removeCitation(item)} />
          <CitationPicker tenderId={tenderId} onAdded={(item) => void addCitation(item)} />
        </section>
      </div>
    </Modal>
  );
}
