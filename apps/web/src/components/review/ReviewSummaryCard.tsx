"use client";

import { Card, CardHeader, LinkButton } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import { type AwardCriteriaView, type ReviewOverview, formatPoints, reviewPath } from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const GENERIC =
  "Requisits i criteris d'adjudicació tal com els revisa la vostra empresa: què puntua, què cal revisar i d'on surt cada cosa.";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className={KICKER}>{label}</dt>
      <dd className="mt-0.5 font-heading text-[20px] leading-tight font-semibold text-ink tabular-nums">{value}</dd>
    </div>
  );
}

/**
 * La revisió de l'empresa a la fitxa: la ÚNICA crida principal cap a `/revisio`,
 * amb les xifres vives de la revisió canònica (només lectura). No l'inicia mai:
 * sense revisió, o si la lectura falla, es queda el text genèric i l'enllaç.
 * Les xifres són les de la revisió, no les de l'anàlisi automàtica (que en té
 * les seves, dites «extrets» i «detectats»).
 */
export function ReviewSummaryCard({ companyId, tenderId }: { companyId: string | null; tenderId: string }) {
  const overview = useResource<ReviewOverview>(companyId ? reviewPath(companyId, tenderId, "/overview") : null);
  const started = overview.data?.review_started === true;
  const scoring = useResource<AwardCriteriaView>(
    companyId && started ? reviewPath(companyId, tenderId, "/award-criteria?view=scoring") : null,
  );
  const data = started ? overview.data : null;

  return (
    <Card>
      <CardHeader
        title="Revisió de la licitació"
        description={data ? "El que la vostra empresa té revisat d'aquesta licitació." : GENERIC}
        action={
          <LinkButton href={`/licitacions/${tenderId}/revisio`} variant="primary" size="sm">
            Obre la revisió
          </LinkButton>
        }
      />
      {data && (
        <dl aria-label="Resum de la revisió" className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-4 md:grid-cols-4">
          <Metric label="Requisits" value={String(data.total_requirements)} />
          <Metric label="Cal revisar" value={String(data.needs_review_count)} />
          <Metric label="Criteris que puntuen" value={scoring.data ? String(scoring.data.scored_count) : "…"} />
          <Metric label="Punts" value={scoring.data ? formatPoints(scoring.data.total_points) : "…"} />
        </dl>
      )}
    </Card>
  );
}
