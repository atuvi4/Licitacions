"use client";

/**
 * Com ha evolucionat l'expedient i qui ho ha mogut.
 *
 * No és el mateix que l'historial de versions rebudes, que hi és al costat i
 * respon a «quantes vegades hem llegit la font». Això respon a la pregunta que
 * es fa qui està preparant una oferta: què ha canviat des de l'última vegada
 * que ho vaig mirar, i m'obliga a tornar-hi?
 *
 * 3B-2 §7.1.1: cada revisió porta el seu `origin` (el deriva el backend). El que
 * ha publicat la font i el que Tuvi ha processat per dins van en seccions
 * separades: atribuir a l'òrgan una descàrrega o un agrupament intern seria una
 * afirmació falsa sobre un tercer. Per això la secció interna no en parla mai, i
 * «la font no n'ha modificat cap dada» només es diu quan no hi ha cap canvi
 * publicat per la font.
 *
 * Els canvis materials i els informatius no s'ensenyen igual, i els informatius
 * venen plegats dins de cada secció: si un canvi de títol ocupés tant com una
 * substitució de plec, la llista deixaria de llegir-se.
 */

import { useState } from "react";

import { Button, Card, CardHeader, EmptyState, LoadErrorState, PageSkeleton, StatusPill } from "@/components/ui";
import { changeLevel } from "@/lib/labels";
import {
  describeChange,
  hidesSubject,
  IMPACT_LABELS,
  ORIGIN_ORDER,
  ORIGIN_SECTION_HINTS,
  ORIGIN_SECTION_TITLES,
  revisionOriginLabel,
  revisionsByOrigin,
  sourceVersionNumbers,
  triggerLabel,
  unclassifiedRevisions,
  type RevisionOrigin,
  type TenderChangeEvent,
  type TenderHistoryRevision,
} from "@/lib/tender-changes";
import { formatDateTime } from "@/lib/tenders";

/** URL de descàrrega → nom: un canvi de document es diu pel nom, mai per l'adreça. */
export interface HistoryDocumentName {
  url: string;
  name: string;
}

function ChangeRow({ event, names }: { event: TenderChangeEvent; names: ReadonlyMap<string, string> }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-1">
      <StatusPill level={changeLevel(event)} size="sm">
        {event.is_material ? "Material" : "Informatiu"}
      </StatusPill>
      <span className="text-sm text-ink">{describeChange(event, names)}</span>
      {event.subject && !hidesSubject(event) && (
        <span className="max-w-full truncate text-[12px] text-faint" title={event.subject}>
          {event.subject}
        </span>
      )}
      {event.is_material && (
        <span className="text-[12px] text-muted">
          {event.impacts
            .filter((impact) => impact !== "informational")
            .map((impact) => IMPACT_LABELS[impact])
            .join(" · ")}
        </span>
      )}
    </li>
  );
}

function OriginSection({
  origin,
  revisions,
  versions,
  names,
  unclassified,
}: {
  origin: RevisionOrigin;
  revisions: TenderHistoryRevision[];
  versions: Map<string, number>;
  names: ReadonlyMap<string, string>;
  /** Hi ha revisions sense origen conegut: no es pot afirmar què ha fet la font. */
  unclassified: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const withChanges = revisions.filter((revision) => revision.events.length > 0);
  const material = withChanges.filter((revision) => revision.events.some((event) => event.is_material));
  const visible = showAll ? withChanges : material;
  const hidden = withChanges.length - material.length;
  const headingId = `historial-${origin}`;

  return (
    <section aria-labelledby={headingId} className="border-t border-border first:border-t-0">
      <div className="px-5 pt-4">
        <h3 id={headingId} className="font-heading text-[15px] font-semibold text-ink">
          {ORIGIN_SECTION_TITLES[origin]}
        </h3>
        <p className="mt-0.5 text-[12.5px] text-muted">{ORIGIN_SECTION_HINTS[origin]}</p>
      </div>
      {withChanges.length === 0 ? (
        <p className="px-5 py-3 text-[13px] text-muted">
          {origin !== "source"
            ? "Cap canvi d'aquest tipus."
            : unclassified
              ? "Hi ha revisions sense origen informat: no es pot afirmar si la font ha modificat alguna dada."
              : "Des que seguim l'expedient, la font no n'ha modificat cap dada."}
        </p>
      ) : (
        <div className="divide-y divide-border">
          {visible.length === 0 && (
            <p className="px-5 py-3 text-[13px] text-muted">Cap canvi que afecti res; els informatius són plegats.</p>
          )}
          {visible.map((revision) => (
            <div key={revision.id} className="px-5 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm font-medium text-ink">{formatDateTime(revision.created_at)}</p>
                <p className="text-[12px] text-faint">
                  {/* «Versió N» només és una versió oficial: la numeració compta
                      les revisions de la font, mai una descàrrega o una revisió
                      interna (A3). */}
                  {revisionOriginLabel(revision, versions)} · {triggerLabel(revision.trigger)}
                </p>
              </div>
              <ul className="mt-2">
                {revision.events
                  .slice()
                  .sort((a, b) => Number(b.is_material) - Number(a.is_material))
                  .map((event, index) => (
                    <ChangeRow key={`${revision.id}-${index}`} event={event} names={names} />
                  ))}
              </ul>
            </div>
          ))}
          {hidden > 0 && (
            <button
              type="button"
              onClick={() => setShowAll((value) => !value)}
              className="w-full px-5 py-3 text-left text-[13px] text-brand hover:bg-subtle"
            >
              {showAll
                ? "Amaga els canvis que no afecten res"
                : `Mostra ${hidden} ${hidden === 1 ? "canvi que no afecta res" : "canvis que no afecten res"}`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export function TenderChangeHistory({
  revisions,
  documents,
  loading,
  error,
  onReload,
}: {
  revisions: TenderHistoryRevision[] | null;
  /** Els documents coneguts (vigents i retirats), per dir cada canvi pel nom del document. */
  documents?: HistoryDocumentName[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
}) {
  const all = revisions ?? [];
  const grouped = revisionsByOrigin(all);
  const versions = sourceVersionNumbers(all);
  const names: ReadonlyMap<string, string> = new Map((documents ?? []).map((document) => [document.url, document.name]));
  const unclassified = unclassifiedRevisions(all).length > 0;
  // La secció de la font hi és sempre que hi hagi història (és la que diu si la
  // font ha modificat res); les altres, només si en tenen alguna revisió.
  const sections = ORIGIN_ORDER.filter((origin) => origin === "source" || grouped[origin].length > 0);

  return (
    <Card>
      <CardHeader
        title="Com ha evolucionat l'expedient"
        description="Què ha publicat la font i què hem processat per dins des que seguim aquest expedient, per separat."
      />
      {error ? (
        <LoadErrorState what="l'historial de canvis" message={error} onRetry={onReload} />
      ) : loading ? (
        <PageSkeleton variant="list" context="carregant l'historial de canvis…" />
      ) : all.length === 0 ? (
        <EmptyState
          title="Encara sense historial"
          description="Encara no s'ha enregistrat cap versió de l'estat d'aquest expedient."
          action={
            <Button variant="secondary" size="md" onClick={onReload}>
              Torna a comprovar
            </Button>
          }
        />
      ) : (
        <div>
          {sections.map((origin) => (
            <OriginSection
              key={origin}
              origin={origin}
              revisions={grouped[origin]}
              versions={versions}
              names={names}
              unclassified={unclassified}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
