"use client";

/**
 * Que ha canviat d'aquesta licitacio i quan ho vam saber.
 *
 * No es el mateix que l'historial de versions rebudes, que hi es al costat i
 * respon a "quantes vegades hem llegit la font". Aixo respon a la pregunta que
 * es fa qui esta preparant una oferta: *que ha canviat des de l'ultima vegada
 * que ho vaig mirar, i m'obliga a tornar-hi?*
 *
 * Per aixo els canvis materials i els informatius no s'ensenyen igual, i per
 * aixo els informatius venen plegats: si un canvi de titol ocupes tant com una
 * substitucio de plec, la llista deixaria de llegir-se i el dia que importes
 * ningu la miraria.
 */

import { useState } from "react";

import { Button, Card, CardHeader, EmptyState, LoadErrorState, PageSkeleton, StatusPill } from "@/components/ui";
import { changeLevel } from "@/lib/labels";
import {
  describeChange,
  IMPACT_LABELS,
  triggerLabel,
  type TenderChangeEvent,
  type TenderHistoryRevision,
} from "@/lib/tender-changes";
import { formatDateTime } from "@/lib/tenders";

function ChangeRow({ event }: { event: TenderChangeEvent }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-1">
      <StatusPill level={changeLevel(event)} size="sm">
        {event.is_material ? "Material" : "Informatiu"}
      </StatusPill>
      <span className="text-sm text-ink">{describeChange(event)}</span>
      {event.subject && (
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

export function TenderChangeHistory({
  revisions,
  loading,
  error,
  onReload,
}: {
  revisions: TenderHistoryRevision[] | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
}) {
  const [showAll, setShowAll] = useState(false);

  const withChanges = (revisions ?? []).filter((revision) => revision.events.length > 0);
  const material = withChanges.filter((revision) =>
    revision.events.some((event) => event.is_material),
  );
  const visible = showAll ? withChanges : material;
  const hidden = withChanges.length - material.length;

  return (
    <Card>
      <CardHeader
        title="Historial de canvis"
        description="Què ha modificat l'òrgan de contractació des que seguim aquest expedient."
      />
      {error ? (
        <LoadErrorState what="l'historial de canvis" message={error} onRetry={onReload} />
      ) : loading ? (
        <PageSkeleton variant="list" context="carregant l'historial de canvis…" />
      ) : withChanges.length === 0 ? (
        <EmptyState
          title="Cap canvi detectat"
          description={
            revisions && revisions.length > 0
              ? "Des que seguim l'expedient, la font no n'ha modificat cap dada."
              : "Encara no s'ha enregistrat cap versió de l'estat d'aquest expedient."
          }
          action={
            <Button variant="secondary" size="md" onClick={onReload}>
              Torna a comprovar
            </Button>
          }
        />
      ) : (
        <div className="divide-y divide-border">
          {visible.map((revision) => (
            <div key={revision.id} className="px-5 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm font-medium text-ink">
                  {formatDateTime(revision.created_at)}
                </p>
                <p className="text-[12px] text-faint">
                  Versió {revision.revision_number} · {triggerLabel(revision.trigger)}
                </p>
              </div>
              <ul className="mt-2">
                {revision.events
                  .slice()
                  .sort((a, b) => Number(b.is_material) - Number(a.is_material))
                  .map((event, index) => (
                    <ChangeRow key={`${revision.id}-${index}`} event={event} />
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
                : `Mostra ${hidden} ${
                    hidden === 1 ? "canvi que no afecta res" : "canvis que no afecten res"
                  }`}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}
