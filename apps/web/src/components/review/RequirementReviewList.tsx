"use client";

import { useCallback, useMemo, useState } from "react";

import { Button, Card, CardHeader, Drawer, Field, Input, Select, StatusPill } from "@/components/ui";
import {
  type CanonicalRequirement,
  type FilterOption,
  NO_FILTERS,
  type RequirementFilters,
  filterOptions,
  filterRequirements,
  formatValue,
  verificationLevel,
  whereLine,
} from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** Origen i estat van separats i amb rètol: «Afegit manualment» no vol dir «dubtós». */
function ProvenanceBadge({ item }: { item: CanonicalRequirement }) {
  return (
    <StatusPill level="neutral" size="sm" outline>
      <span className="sr-only">Origen: </span>
      {item.provenance_label}
    </StatusPill>
  );
}

function VerificationBadge({ item }: { item: CanonicalRequirement }) {
  return (
    <StatusPill level={verificationLevel(item.verification_status)} size="sm">
      <span className="sr-only">Estat: </span>
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

function RequirementDetail({
  item,
  onOpenRelated,
}: {
  item: CanonicalRequirement;
  onOpenRelated: (id: string) => void;
}) {
  const relations = [...item.visible_relations, ...item.folded_relations];
  return (
    <div className="space-y-5 px-4 py-4">
      {item.description && <p className="text-body leading-relaxed text-ink">{item.description}</p>}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Fact label="Categoria">{item.category_label}</Fact>
        <Fact label="Obligació">{item.obligation_label}</Fact>
        <Fact label="Estat">
          <VerificationBadge item={item} />
        </Fact>
        <Fact label="Origen">
          <ProvenanceBadge item={item} />
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
                  <blockquote className="mt-1 text-[13px] leading-relaxed whitespace-pre-line text-ink italic">
                    «{evidence.quote}»
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
    </div>
  );
}

/**
 * Requisits canònics d'aquesta empresa: llista filtrable (estat, origen,
 * obligació, categoria i text, tot al client) i detall en un calaix.
 * Només lectura.
 */
export function RequirementReviewList({ items }: { items: CanonicalRequirement[] }) {
  const [filters, setFilters] = useState<RequirementFilters>(NO_FILTERS);
  const [openId, setOpenId] = useState<string | null>(null);
  const available = useMemo(() => filterOptions(items), [items]);
  const shown = useMemo(() => filterRequirements(items, filters), [items, filters]);
  const open = items.find((item) => item.canonical_id === openId) ?? null;
  const close = useCallback(() => setOpenId(null), []);
  const set = (key: keyof RequirementFilters) => (value: string) => setFilters((current) => ({ ...current, [key]: value }));
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);

  return (
    <Card>
      <CardHeader title="Requisits" description={`Mostrant ${shown.length} de ${items.length}`} />
      <form
        role="search"
        aria-label="Filtra els requisits"
        className="grid gap-3 border-b border-border px-3.5 py-3 sm:grid-cols-2 xl:grid-cols-5"
        onSubmit={(event) => event.preventDefault()}
      >
        <Field label="Cerca" className="min-w-0 sm:col-span-2 xl:col-span-1">
          <Input type="search" value={filters.text} onChange={(event) => set("text")(event.target.value)} placeholder="Títol, descripció o valor" />
        </Field>
        <FilterSelect label="Estat" value={filters.verification} options={available.verification} onChange={set("verification")} />
        <FilterSelect label="Origen" value={filters.provenance} options={available.provenance} onChange={set("provenance")} />
        <FilterSelect label="Obligació" value={filters.obligation} options={available.obligation} onChange={set("obligation")} />
        <FilterSelect label="Categoria" value={filters.category} options={available.category} onChange={set("category")} />
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
        {open && <RequirementDetail item={open} onOpenRelated={(id) => items.some((item) => item.canonical_id === id) && setOpenId(id)} />}
      </Drawer>
    </Card>
  );
}
