"use client";

import { ChevronRight } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Card, CardHeader, StatusPill, cn, type Level } from "@/components/ui";
import { LABELS, dimensionValue, explanation, label, levelOf, resolveFor, type AnalysisRun, type FitAssessment } from "@/lib/analysis";
import { CATEGORY_LABELS } from "@/lib/requirements";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, requirementTitle } from "./Citation";

const FILTERS = Object.keys(LABELS.fit_result);

/** Botó de filtre amb el patró de `RequirementAnalysis`: premut = píndola plena d'acció i negreta (no només color). */
function FilterButton({ pressed, level, onClick, children }: { pressed: boolean; level: Level; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className="flex min-h-11 items-center rounded-pill focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:min-h-0"
    >
      <StatusPill level={pressed ? "action" : level} outline={!pressed} className={cn(pressed && "font-semibold")}>
        {children}
      </StatusPill>
    </button>
  );
}

function Row({ fit, lookup, tenderId }: { fit: FitAssessment; lookup: AnalysisLookup; tenderId: string }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const destination = resolveFor(fit.explanation_code);
  const requirement = lookup.requirements.get(fit.requirement_id);
  return (
    <li id={`req-${fit.requirement_id}`} className="scroll-mt-4 space-y-2 px-5 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-body font-medium text-ink">{requirementTitle(lookup, fit.requirement_id)}</p>
        <StatusPill level={levelOf("fit_result", fit.fit_result)} size="sm">
          {label("fit_result", fit.fit_result)}
        </StatusPill>
      </div>
      <Citation tenderId={tenderId} requirement={requirement} />
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-caption sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="inline text-muted">Prova: </dt><dd className="inline">{dimensionValue("proof_status", fit.proof_status)}</dd></div>
        <div><dt className="inline text-muted">Font del coneixement: </dt><dd className="inline">{dimensionValue("analysis_knowledge_source", fit.knowledge_source)}</dd></div>
        <div><dt className="inline text-muted">Verificació: </dt><dd className="inline">{dimensionValue("assessment_verification", fit.verification_status)}</dd></div>
        <div><dt className="inline text-muted">Territori: </dt><dd className="inline">{dimensionValue("territory_verdict", fit.territory_verdict)}</dd></div>
      </dl>
      <button
        type="button"
        className="inline-flex min-h-11 items-center gap-1 text-caption font-medium text-brand hover:underline md:min-h-0"
        aria-label={`Per què? ${requirementTitle(lookup, fit.requirement_id)}`}
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen(!open)}
      >
        {/* Obert/tancat també a la vista: el xevró gira; el nom accessible no canvia (ho diu aria-expanded). */}
        <ChevronRight size={14} aria-hidden className={cn("transition-transform duration-150", open && "rotate-90")} />
        Per què?
      </button>
      <div id={panel} hidden={!open} className="text-body text-ink">
        <p>{explanation(fit.explanation_code)}</p>
        {destination && (
          <a href={destination.href} className="inline-flex min-h-11 items-center text-brand hover:underline md:min-h-0">
            On es resol: {destination.label}
          </a>
        )}
      </div>
    </li>
  );
}

export function RequirementsFit({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const [filter, setFilter] = useState<string | null>(null);
  const shown = run.fit_assessments.filter((fit) => !filter || fit.fit_result === filter);
  const groups = new Map<string, FitAssessment[]>();
  for (const fit of shown) {
    const category = lookup.requirements.get(fit.requirement_id)?.category;
    const key = category ? (CATEGORY_LABELS[category] ?? "Altres") : "Sense categoria";
    groups.set(key, [...(groups.get(key) ?? []), fit]);
  }
  return (
    <Card id="requisits-analisi">
      <CardHeader title="Requisits" description="Cada requisit amb el seu estat i el perquè. «No se sap» vol dir que falta informació, no que no es compleixi." />
      <div className="flex flex-wrap gap-2 px-5 pt-3" role="group" aria-label="Filtra per estat">
        <FilterButton pressed={filter === null} level="neutral" onClick={() => setFilter(null)}>
          Tots ({run.fit_assessments.length})
        </FilterButton>
        {FILTERS.map((value) => (
          <FilterButton key={value} pressed={filter === value} level={levelOf("fit_result", value)} onClick={() => setFilter(filter === value ? null : value)}>
            {label("fit_result", value)} ({run.fit_assessments.filter((f) => f.fit_result === value).length})
          </FilterButton>
        ))}
      </div>
      <p role="status" aria-live="polite" aria-label="Recompte de requisits" className="px-5 pt-2 text-caption text-muted">
        {shown.length === 1 ? "1 requisit" : `${shown.length} requisits`}
      </p>
      {shown.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">Cap requisit amb aquest estat.</p>
      ) : (
        [...groups.entries()].map(([category, fits]) => (
          <section key={category} aria-label={category}>
            <h3 className="bg-subtle px-5 py-2 text-caption font-semibold text-muted uppercase">{category}</h3>
            <ul className="divide-y divide-border">
              {fits.map((fit) => (
                <Row key={fit.id} fit={fit} lookup={lookup} tenderId={tenderId} />
              ))}
            </ul>
          </section>
        ))
      )}
    </Card>
  );
}
