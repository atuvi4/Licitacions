"use client";

import { Info, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button, Card, CardHeader, ErrorState, LoadingRows, StatusPill } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import {
  type AwardCriteriaView,
  type CanonicalAwardCriterion,
  checkItemNames,
  checkTone,
  formatPoints,
  reviewPath,
  scoringBlocks,
  structureSections,
  whereLine,
} from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function Points({ points, muted = false }: { points: number | null; muted?: boolean }) {
  return (
    <span className={muted ? "shrink-0 text-body text-muted tabular-nums" : "shrink-0 text-body font-semibold text-ink tabular-nums"}>
      {formatPoints(points)}
    </span>
  );
}

function FormulaLine({ row }: { row: CanonicalAwardCriterion }) {
  if (!row.formula) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[13px]">
      <span className="font-mono text-ink">{row.formula}</span>
      {row.formula_label && (
        <StatusPill level={row.formula_status === "verified" ? "ready" : "neutral"} size="sm">
          {row.formula_label}
        </StatusPill>
      )}
    </div>
  );
}

function ScoringRow({ row, nested = false }: { row: CanonicalAwardCriterion; nested?: boolean }) {
  const where = whereLine(row.documents, row.pages);
  return (
    <li className={nested ? "py-2 pl-4" : "py-3"}>
      <div className="flex items-baseline justify-between gap-4">
        <p className={nested ? "min-w-0 text-body text-ink" : "min-w-0 text-body font-semibold text-ink"}>{row.name}</p>
        <Points points={row.points} />
      </div>
      {where && <p className="mt-0.5 text-caption text-faint">{where}</p>}
      <FormulaLine row={row} />
    </li>
  );
}

function ScoringView({ view }: { view: AwardCriteriaView }) {
  return (
    <ul aria-label="Criteris que puntuen" className="divide-y divide-border">
      {scoringBlocks(view).map((block) =>
        block.kind === "single" ? (
          <ScoringRow key={block.row.canonical_id} row={block.row} />
        ) : (
          <li key={block.parent.canonical_id} className="py-3">
            <div className="flex items-baseline justify-between gap-4">
              <p className="min-w-0 text-body font-semibold text-ink">{block.parent.name}</p>
              <Points points={block.parent.points} />
            </div>
            <p className="mt-0.5 text-caption text-faint">
              {block.rows.length} criteris · sumen {formatPoints(block.rows.reduce((sum, row) => sum + (row.points ?? 0), 0))}
            </p>
            <ul aria-label={`Criteris de ${block.parent.name}`} className="mt-1 border-l-2 border-accent-500">
              {block.rows.map((row) => (
                <ScoringRow key={row.canonical_id} row={row} nested />
              ))}
            </ul>
          </li>
        ),
      )}
    </ul>
  );
}

function StructureView({ view }: { view: AwardCriteriaView }) {
  return (
    <div className="space-y-5">
      {structureSections(view).map((section) => (
        <section key={section.key} aria-labelledby={`estructura-${section.key}`}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h4 id={`estructura-${section.key}`} className="font-heading text-[15px] font-semibold text-ink">
              {section.title}
            </h4>
            <StatusPill level={section.counts ? "ready" : "neutral"} size="sm" outline>
              {section.counts ? "Compta als 100 punts" : "No compta al total"}
            </StatusPill>
          </div>
          <p className="mt-0.5 text-caption text-muted">{section.description}</p>
          <ul className="mt-2 divide-y divide-border border-y border-border">
            {section.rows.map((row) => (
              <li key={row.canonical_id} className="py-2">
                <div className="flex items-baseline justify-between gap-4">
                  <p className={section.counts ? "min-w-0 text-body text-ink" : "min-w-0 text-body text-muted"}>{row.name}</p>
                  <Points points={row.points} muted={!section.counts} />
                </div>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-faint">
                  <StatusPill level="neutral" size="sm">
                    {row.role_label}
                  </StatusPill>
                  {row.folded_into && <span>Repeteix «{row.folded_into.name}»</span>}
                  {row.parent && <span>Dins de «{row.parent.name}»</span>}
                  {whereLine(row.documents, row.pages) && <span>{whereLine(row.documents, row.pages)}</span>}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Checks({ view }: { view: AwardCriteriaView }) {
  if (view.checks.length === 0) return null;
  return (
    <section aria-labelledby="avisos-criteris" className="border-t border-border px-3.5 py-3">
      <h3 id="avisos-criteris" className={KICKER}>
        Per tenir en compte
      </h3>
      <ul className="mt-2 space-y-2">
        {view.checks.map((check) => {
          const tone = checkTone(check);
          const names = checkItemNames(view, check);
          return (
            <li key={check.label} className="flex items-start gap-2 text-[13px] leading-5">
              {tone === "info" ? (
                <Info size={16} strokeWidth={1.5} aria-hidden className="mt-0.5 shrink-0 text-muted" />
              ) : (
                <TriangleAlert size={16} strokeWidth={1.5} aria-hidden className="mt-0.5 shrink-0 text-warning" />
              )}
              <div className="min-w-0">
                <p className="text-ink">
                  <span className="sr-only">{tone === "info" ? "Informació: " : "Atenció: "}</span>
                  {check.label}
                </p>
                {names.length > 0 && <p className="text-muted">{names.join(" · ")}</p>}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * Criteris d'adjudicació canònics: per defecte, només els que puntuen (preu i
 * blocs amb els seus criteris); «Veure estructura completa» mostra totes les
 * files i diu quines NO compten als punts.
 */
export interface ScoringResource {
  data: AwardCriteriaView | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<unknown>;
}

export function AwardCriteriaPanel({
  companyId,
  tenderId,
  scoring,
}: {
  companyId: string;
  tenderId: string;
  /** La vista de puntuació la llegeix la pàgina: també en surten les xifres del resum. */
  scoring: ScoringResource;
}) {
  const [full, setFull] = useState(false);
  const all = useResource<AwardCriteriaView>(full ? reviewPath(companyId, tenderId, "/award-criteria?view=all") : null);

  const view = scoring.data;
  const unprepared = view !== null && view.criteria.length === 0;
  const description = view && !unprepared ? `${formatPoints(view.total_points)} totals · ${view.scored_count} criteris puntuats` : undefined;

  return (
    <Card>
      <CardHeader
        title="Criteris d'adjudicació"
        description={description}
        action={
          view && !unprepared ? (
            <Button size="sm" variant="secondary" aria-pressed={full} onClick={() => setFull((value) => !value)}>
              {full ? "Veure només els que puntuen" : "Veure estructura completa"}
            </Button>
          ) : undefined
        }
      />
      {scoring.loading && !view && (
        <div aria-busy="true" aria-label="Carregant els criteris">
          <LoadingRows rows={4} />
        </div>
      )}
      {scoring.error && !scoring.loading && (
        <ErrorState
          title="No s'han pogut llegir els criteris"
          description="La resta de la revisió es pot consultar igualment."
          action={
            <Button size="sm" variant="secondary" onClick={() => void scoring.reload()}>
              Torna-ho a provar
            </Button>
          }
          className="py-8"
        />
      )}
      {view && unprepared && (
        <p className="px-3.5 py-6 text-body text-muted">
          {view.checks.some((check) => check.code === "role_unassigned")
            ? "Encara no s'ha decidit quins criteris puntuen. Quan es faci, aquí apareixerà el repartiment dels punts."
            : "Els criteris d'adjudicació d'aquesta licitació encara no s'han preparat per a la revisió."}
        </p>
      )}
      {view && !unprepared && (
        <>
          <div className="px-3.5">
            {!full && <ScoringView view={view} />}
            {full && all.loading && <LoadingRows rows={4} />}
            {full && all.error && (
              <p className="py-4 text-body text-muted">
                No s&apos;ha pogut llegir l&apos;estructura completa.{" "}
                <button type="button" className="text-brand-strong underline" onClick={() => void all.reload()}>
                  Torna-ho a provar
                </button>
              </p>
            )}
            {full && all.data && (
              <div className="py-3">
                <StructureView view={all.data} />
              </div>
            )}
          </div>
          <div className="flex items-baseline justify-between gap-4 border-t border-border-strong px-3.5 py-2.5">
            <p className="text-body font-semibold text-ink">Total</p>
            <p className="text-body font-semibold text-ink tabular-nums">{formatPoints(view.total_points)}</p>
          </div>
          <Checks view={view} />
        </>
      )}
    </Card>
  );
}
