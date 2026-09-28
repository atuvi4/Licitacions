"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { ManualRequirementEditor } from "@/components/review/ManualRequirementEditor";
import { ReviewActions } from "@/components/review/ReviewActions";
import { ReviewHistory } from "@/components/review/ReviewHistory";
import { Button, Card, CardHeader, Drawer, Field, Input, ResultNotice, Select, StatusPill } from "@/components/ui";
import {
  type CanonicalRequirement,
  type FilterOption,
  NO_FILTERS,
  type RequirementFilters,
  duplicateOf,
  restatementOf,
  filterOptions,
  filterRequirements,
  formatValue,
  verificationLevel,
  whereLine,
} from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/**
 * Origen i estat van separats i amb rètol: «Afegit manualment» no vol dir «dubtós».
 * `labelled`: ja hi ha un rètol visible al costat (el detall), no cal repetir-lo.
 */
function ProvenanceBadge({ item, labelled = false }: { item: CanonicalRequirement; labelled?: boolean }) {
  return (
    <StatusPill level="neutral" size="sm" outline>
      {!labelled && <span className="sr-only">Origen: </span>}
      {item.provenance_label}
    </StatusPill>
  );
}

function VerificationBadge({ item, labelled = false }: { item: CanonicalRequirement; labelled?: boolean }) {
  return (
    <StatusPill level={verificationLevel(item.verification_status)} size="sm">
      {!labelled && <span className="sr-only">Estat: </span>}
      {item.verification_label}
    </StatusPill>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label} className="min-w-0">
      <Select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Tots"
        options={options.map((option) => ({ value: option.value, label: `${option.label} (${option.count})` }))}
      />
    </Field>
  );
}

function RequirementRow({ item, onOpen }: { item: CanonicalRequirement; onOpen: () => void }) {
  const where = whereLine(item.documents, item.pages);
  const values = item.values.map(formatValue);
  const duplicate = duplicateOf(item);
  const restated = duplicate ? undefined : restatementOf(item);
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="block w-full px-3.5 py-3 text-left transition-colors hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand"
      >
        <span className="block text-body font-semibold text-ink">{item.title}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
          <span>{item.category_label}</span>
          <span aria-hidden>·</span>
          <span>{item.obligation_label}</span>
          {where && (
            <>
              <span aria-hidden>·</span>
              <span>{where}</span>
            </>
          )}
          {values.length > 0 && (
            <>
              <span aria-hidden>·</span>
              <span className="font-medium text-ink">{values.join(" · ")}</span>
            </>
          )}
        </span>
        {(duplicate || restated) && (
          <span className="mt-1 block text-caption text-muted">
            {duplicate
              ? `Duplicat de «${duplicate.other_item_title ?? "un altre requisit"}»`
              : `Repeteix «${restated?.other_item_title ?? "un altre requisit"}»`}
          </span>
        )}
        <span className="mt-1.5 flex flex-wrap gap-1.5">
          <VerificationBadge item={item} />
          <ProvenanceBadge item={item} />
        </span>
      </button>
    </li>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className={KICKER}>{label}</dt>
      <dd className="mt-0.5 text-body text-ink">{children}</dd>
    </div>
  );
}

/** El que cal per revisar des del detall. Sense això, el detall és només de lectura. */
export interface ReviewContext {
  companyId: string;
  tenderId: string;
  /** Rol amb permís de revisió (el backend continua manant). */
  canWrite: boolean;
  currentUserId: string | null;
  onUpdated: (item: CanonicalRequirement, statusMayChange: boolean) => void;
  /** Torna a llegir un element (després d'un 409) i el torna, o `null` si ja no hi és. */
  onStale: (itemId: string) => Promise<CanonicalRequirement | null>;
  /** L'element ja no hi és (404): cal refer la llista. */
  onGone: (itemId: string) => Promise<void>;
  /** Un requisit manual nou: es tornen a llegir la llista i el resum. */
  onCreated: (item: CanonicalRequirement) => Promise<void>;
}

function RequirementDetail({
  item,
  review,
  onOpenRelated,
  registerCloseGuard,
}: {
  item: CanonicalRequirement;
  review?: ReviewContext;
  onOpenRelated: (id: string) => void;
  registerCloseGuard: (guard: (() => boolean) | null) => void;
}) {
  const relations = [...item.visible_relations, ...item.folded_relations];
  return (
    <div className="space-y-5 px-4 py-4">
      {item.description && <p className="text-body leading-relaxed text-ink">{item.description}</p>}

      {review && (
        <ReviewActions
          item={item}
          companyId={review.companyId}
          tenderId={review.tenderId}
          canWrite={review.canWrite}
          onUpdated={review.onUpdated}
          onStale={() => review.onStale(item.canonical_id)}
          onGone={() => review.onGone(item.canonical_id)}
          registerCloseGuard={registerCloseGuard}
        />
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Fact label="Categoria">{item.category_label}</Fact>
        <Fact label="Obligació">{item.obligation_label}</Fact>
        {/* Amb la secció «Revisió», l'estat ja hi és al capdamunt: no es repeteix. */}
        {!review && (
          <Fact label="Estat">
            <VerificationBadge item={item} labelled />
          </Fact>
        )}
        <Fact label="Origen">
          <ProvenanceBadge item={item} labelled />
        </Fact>
        <Fact label="Revisió">{item.review_revision}</Fact>
      </dl>

      {item.values.length > 0 && (
        <section aria-labelledby="detall-valors">
          <h3 id="detall-valors" className={KICKER}>
            Valors
          </h3>
          <ul className="mt-1.5 divide-y divide-border border-y border-border">
            {item.values.map((value) => (
              <li key={value.key} className="py-2">
                <p className="text-body font-semibold text-ink">{formatValue(value)}</p>
                <p className="text-caption text-muted">
                  {value.source_label}
                  {value.status_label !== value.source_label && ` · ${value.status_label}`}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="detall-evidencia">
        <h3 id="detall-evidencia" className={KICKER}>
          Evidència
        </h3>
        {item.evidence.length === 0 ? (
          <p className="mt-1.5 text-body text-muted">Sense cita al plec.</p>
        ) : (
          <ul className="mt-1.5 space-y-3">
            {item.evidence.map((evidence, index) => (
              <li key={`${evidence.page}-${index}`} className="border-l-2 border-accent-500 pl-2.5">
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
                  <span className="font-medium text-ink">{evidence.document ?? "Document"}</span>
                  {evidence.page !== null && <span>p. {evidence.page}</span>}
                  {evidence.section && <span>§ {evidence.section}</span>}
                  <StatusPill level="neutral" size="sm" outline>
                    {evidence.status_label}
                  </StatusPill>
                </p>
                {evidence.quote && (
                  // Els salts de línia són els del PDF, no del text: es refà el paràgraf.
                  <blockquote className="mt-1 text-[13px] leading-relaxed text-ink italic">
                    «{evidence.quote.replace(/\s+/g, " ").trim()}»
                  </blockquote>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {relations.length > 0 && (
        <section aria-labelledby="detall-relacions">
          <h3 id="detall-relacions" className={KICKER}>
            Relacions
          </h3>
          <ul className="mt-1.5 space-y-2">
            {relations.map((relation) => (
              <li key={`${relation.relation_id}-${relation.direction}`} className="text-body">
                <span className="text-muted">{relation.relation_label}: </span>
                <button
                  type="button"
                  className="text-left text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800"
                  onClick={() => onOpenRelated(relation.other_item_id)}
                >
                  {relation.other_item_title ?? "Element sense títol"}
                </button>
                {relation.note && <p className="text-caption text-muted">{relation.note}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {review && (
        <ReviewHistory
          key={item.canonical_id}
          companyId={review.companyId}
          tenderId={review.tenderId}
          itemId={item.canonical_id}
          revision={item.review_revision}
          currentUserId={review.currentUserId}
        />
      )}
    </div>
  );
}

/**
 * Requisits canònics d'aquesta empresa: llista filtrable (estat, origen,
 * obligació, categoria i text, tot al client) i detall en un calaix. Amb
 * `review`, el detall porta les accions de revisió; els filtres es mantenen
 * quan un element canvia, i si deixa de coincidir-hi, surt de la llista.
 */
export function RequirementReviewList({ items, review }: { items: CanonicalRequirement[]; review?: ReviewContext }) {
  const [filters, setFilters] = useState<RequirementFilters>(NO_FILTERS);
  const [openId, setOpenId] = useState<string | null>(null);
  const [showDuplicates, setShowDuplicates] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  // Els duplicats d'extracció es pleguen per defecte; el detall i l'auditoria els conserven.
  const folded = useMemo(() => items.filter((item) => duplicateOf(item)), [items]);
  const listed = useMemo(
    () => (showDuplicates ? items : items.filter((item) => !duplicateOf(item))),
    [items, showDuplicates],
  );
  // Si una correcció deixa un filtre actiu sense cap element, l'opció no pot
  // desaparèixer: el desplegable diria «Tots» mentre la llista continua filtrada.
  const seen = useRef<Record<string, string>>({});
  const available = useMemo(() => {
    const found = filterOptions(listed);
    for (const list of Object.values(found)) for (const option of list) seen.current[option.value] = option.label;
    return found;
  }, [listed]);
  const keep = (options: FilterOption[], value: string): FilterOption[] =>
    !value || options.some((option) => option.value === value)
      ? options
      : [...options, { value, label: seen.current[value] ?? "Filtre actual", count: 0 }];
  const shown = useMemo(() => filterRequirements(listed, filters), [listed, filters]);
  const open = items.find((item) => item.canonical_id === openId) ?? null;
  // Un editor amb canvis sense desar pot aturar el tancament (i el canvi d'element).
  const guard = useRef<(() => boolean) | null>(null);
  const registerCloseGuard = useCallback((next: (() => boolean) | null) => {
    guard.current = next;
  }, []);
  const close = useCallback(() => {
    if (guard.current?.()) return;
    setOpenId(null);
  }, []);
  const closeCreate = useCallback(() => {
    if (guard.current?.()) return;
    setCreating(false);
  }, []);
  const openRelated = (id: string) => {
    if (guard.current?.()) return;
    if (items.some((item) => item.canonical_id === id)) setOpenId(id);
  };
  const set = (key: keyof RequirementFilters) => (value: string) => setFilters((current) => ({ ...current, [key]: value }));
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);

  return (
    <Card>
      <CardHeader
        title="Requisits"
        description={
          `Mostrant ${shown.length} de ${items.length}` +
          (folded.length && !showDuplicates
            ? ` · ${folded.length} ${folded.length === 1 ? "duplicat plegat" : "duplicats plegats"}`
            : "")
        }
        action={
          folded.length > 0 || review?.canWrite ? (
            <div className="flex flex-wrap gap-2">
              {folded.length > 0 && (
                <Button size="sm" variant="ghost" aria-pressed={showDuplicates} onClick={() => setShowDuplicates((value) => !value)}>
                  {showDuplicates ? "Amaga els duplicats" : `Mostra també els duplicats (${folded.length})`}
                </Button>
              )}
              {review?.canWrite && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setCreated(null);
                    setCreating(true);
                  }}
                >
                  Afegir requisit
                </Button>
              )}
            </div>
          ) : undefined
        }
      />
      {created && <ResultNotice message={created} tone="success" onDismiss={() => setCreated(null)} />}
      <form
        role="search"
        aria-label="Filtra els requisits"
        // La cerca ocupa tota la fila i els quatre filtres van de dos en dos: amb
        // cinc columnes, les opcions («Pendent de revisar (201)») quedaven tallades.
        className="grid gap-3 border-b border-border px-3.5 py-3 sm:grid-cols-2"
        onSubmit={(event) => event.preventDefault()}
      >
        <Field label="Cerca" className="min-w-0 sm:col-span-2">
          <Input type="search" value={filters.text} onChange={(event) => set("text")(event.target.value)} placeholder="Títol, descripció o valor" />
        </Field>
        <FilterSelect label="Estat" value={filters.verification} options={keep(available.verification, filters.verification)} onChange={set("verification")} />
        <FilterSelect label="Origen" value={filters.provenance} options={keep(available.provenance, filters.provenance)} onChange={set("provenance")} />
        <FilterSelect label="Obligació" value={filters.obligation} options={keep(available.obligation, filters.obligation)} onChange={set("obligation")} />
        <FilterSelect label="Categoria" value={filters.category} options={keep(available.category, filters.category)} onChange={set("category")} />
      </form>
      {shown.length === 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-6">
          <p className="text-body text-muted">Cap requisit no coincideix amb aquests filtres.</p>
          {filtered && (
            <Button size="sm" variant="secondary" onClick={() => setFilters(NO_FILTERS)}>
              Treu els filtres
            </Button>
          )}
        </div>
      ) : (
        <ul aria-label="Requisits" className="divide-y divide-border">
          {shown.map((item) => (
            <RequirementRow key={item.canonical_id} item={item} onOpen={() => setOpenId(item.canonical_id)} />
          ))}
        </ul>
      )}
      <Drawer open={open !== null} title={open?.title ?? ""} onClose={close}>
        {open && (
          <RequirementDetail item={open} review={review} onOpenRelated={openRelated} registerCloseGuard={registerCloseGuard} />
        )}
      </Drawer>
      {review?.canWrite && (
        <Drawer open={creating} title="Afegir requisit" onClose={closeCreate} wide>
          {creating && (
            <ManualRequirementEditor
              companyId={review.companyId}
              tenderId={review.tenderId}
              registerCloseGuard={registerCloseGuard}
              onClose={() => setCreating(false)}
              onCreated={async (item) => {
                registerCloseGuard(null);
                setCreating(false);
                setCreated(`S'ha afegit «${item.title}». Queda pendent de revisar.`);
                await review.onCreated(item);
              }}
            />
          )}
        </Drawer>
      )}
    </Card>
  );
}
