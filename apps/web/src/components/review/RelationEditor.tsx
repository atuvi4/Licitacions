"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Button, Field, Input, StatusPill, Textarea } from "@/components/ui";
import {
  type CanonicalRequirement,
  RELATION_TYPES,
  type RequirementRelationType,
  createRelation,
  relationFailure,
  verificationLevel,
  whereLine,
} from "@/lib/review";

const MAX_RESULTS = 12;

function normalise(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

/**
 * Tria un altre requisit canònic de la mateixa licitació i empresa. Cerca al
 * client sobre la llista que ja s'ha carregat: aquí hi ha centenars d'elements,
 * no milers. Mai no s'hi ensenya cap identificador.
 */
export function RequirementPicker({
  candidates,
  excludeId,
  selected,
  onSelect,
}: {
  candidates: CanonicalRequirement[];
  excludeId: string;
  selected: CanonicalRequirement | null;
  onSelect: (item: CanonicalRequirement | null) => void;
}) {
  const [query, setQuery] = useState("");
  const results = useMemo(() => {
    const words = normalise(query).split(/\s+/).filter(Boolean);
    if (words.length === 0) return [];
    return candidates
      .filter((item) => item.canonical_id !== excludeId)
      .filter((item) => {
        const haystack = normalise([item.title, item.description ?? "", whereLine(item.documents, item.pages) ?? ""].join(" "));
        return words.every((word) => haystack.includes(word));
      });
  }, [candidates, excludeId, query]);

  if (selected) {
    return (
      <div className="flex items-start justify-between gap-2 rounded-control border border-border px-2.5 py-2">
        <div className="min-w-0">
          <p className="text-body font-semibold text-ink">{selected.title}</p>
          <p className="text-caption text-muted">
            {selected.obligation_label}
            {whereLine(selected.documents, selected.pages) && ` · ${whereLine(selected.documents, selected.pages)}`}
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => onSelect(null)}>
          Canvia
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Field label="Cerca el requisit" hint="Títol, descripció o document.">
        <Input type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
      </Field>
      {query.trim() && (
        <p className="text-caption text-muted" aria-live="polite">
          {results.length === 0
            ? "Cap requisit coincideix."
            : results.length > MAX_RESULTS
              ? `${results.length} resultats: es mostren els ${MAX_RESULTS} primers. Afina la cerca.`
              : `${results.length} ${results.length === 1 ? "resultat" : "resultats"}`}
        </p>
      )}
      {results.length > 0 && (
        <ul aria-label="Requisits trobats" className="max-h-72 divide-y divide-border overflow-y-auto border border-border">
          {results.slice(0, MAX_RESULTS).map((item) => (
            <li key={item.canonical_id}>
              <button
                type="button"
                onClick={() => onSelect(item)}
                className="block w-full px-2.5 py-2 text-left hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand"
              >
                <span className="block text-[13px] font-semibold text-ink">{item.title}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
                  <span>{item.obligation_label}</span>
                  {whereLine(item.documents, item.pages) && <span>· {whereLine(item.documents, item.pages)}</span>}
                  <StatusPill level={verificationLevel(item.verification_status)} size="sm">
                    {item.verification_label}
                  </StatusPill>
                  <StatusPill level="neutral" size="sm" outline>
                    {item.provenance_label}
                  </StatusPill>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Relaciona AQUEST requisit amb un altre. La relació és dirigida (`aquest →
 * l'altre`) i la frase de direcció es veu abans de desar. No toca el contingut,
 * l'estat ni la revisió de cap dels dos.
 */
export function RelationEditor({
  item,
  candidates,
  companyId,
  tenderId,
  onSaved,
  onRefresh,
  onClose,
}: {
  item: CanonicalRequirement;
  candidates: CanonicalRequirement[];
  companyId: string;
  tenderId: string;
  onSaved: (message: string) => Promise<void>;
  /** Torna a llegir la llista sense tancar l'editor (p. ex. després d'un 409). */
  onRefresh: () => Promise<void>;
  onClose: () => void;
}) {
  const id = useId();
  const [target, setTarget] = useState<CanonicalRequirement | null>(null);
  const [type, setType] = useState<RequirementRelationType | null>(null);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const chosen = RELATION_TYPES.find((option) => option.value === type) ?? null;

  useEffect(() => {
    root.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
    onClose();
  }

  async function save() {
    if (!target || !type || pending) return;
    setPending(true);
    setProblem(null);
    try {
      await createRelation(companyId, tenderId, {
        from_item_id: item.canonical_id,
        to_item_id: target.canonical_id,
        relation_type: type,
        note: note.trim() || null,
      });
      await onSaved(`Relació desada: ${chosen?.label.toLowerCase()} «${target.title}».`);
    } catch (caught) {
      setProblem(relationFailure(caught));
      // Un 409 vol dir que la relació ja hi és: es torna a llegir perquè es vegi.
      if (caught && typeof caught === "object" && (caught as { status?: number }).status === 409) {
        await onRefresh();
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div ref={root} role="group" aria-labelledby={`${id}-title`} className="mt-2.5 space-y-3" onKeyDown={onKeyDown}>
      <div>
        <p id={`${id}-title`} className="text-body font-semibold text-ink">
          Relacionar amb un altre requisit
        </p>
        <p className="text-[13px] leading-5 text-muted">
          La relació no canvia el contingut ni l&apos;estat de cap dels dos requisits.
        </p>
      </div>

      <RequirementPicker candidates={candidates} excludeId={item.canonical_id} selected={target} onSelect={setTarget} />

      <fieldset className="space-y-1.5">
        <legend className="text-[13px] leading-5 font-medium text-ink">Tipus de relació</legend>
        {RELATION_TYPES.map((option) => (
          <label key={option.value} className="flex items-start gap-2 text-body text-ink">
            <input
              type="radio"
              name={`${id}-type`}
              className="mt-1"
              checked={type === option.value}
              onChange={() => setType(option.value)}
            />
            <span>
              <span className="font-medium">{option.label}</span>
              <span className="block text-caption text-muted">{option.help}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {target && chosen && (
        <p className="border-l-2 border-accent-500 pl-2.5 text-[13px] leading-5 text-ink" aria-live="polite">
          {chosen.sentence(item.title, target.title)}
        </p>
      )}

      <Field label="Nota (opcional)" hint="Per aclarir-ho, no per substituir el tipus de relació.">
        <Textarea rows={2} value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} />
      </Field>

      {problem && (
        <p role="alert" className="rounded-control border border-warning bg-warning-soft px-3 py-2 text-[13px] leading-5 text-ink">
          {problem}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="primary" loading={pending} disabled={!target || !type || pending} onClick={() => void save()}>
          Desa la relació
        </Button>
        <Button size="sm" variant="ghost" disabled={pending} onClick={onClose}>
          Cancel·la
        </Button>
      </div>
    </div>
  );
}
