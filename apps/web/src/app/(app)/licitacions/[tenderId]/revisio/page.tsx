"use client";

import { ArrowLeft, ClipboardList } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { AwardCriteriaPanel } from "@/components/review/AwardCriteriaPanel";
import { RequirementReviewList } from "@/components/review/RequirementReviewList";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, EmptyState, ErrorState, LinkButton, LoadingRows, PageHeader, PageSkeleton } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import { type AwardCriteriaView, type CanonicalRequirement, type ReviewOverview, reviewPath } from "@/lib/review";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="border border-border px-3.5 py-3">
      <dt className={KICKER}>{label}</dt>
      <dd className="mt-1 font-heading text-[28px] leading-none font-semibold text-ink tabular-nums">{value}</dd>
      {note && <dd className="mt-1 text-caption text-muted">{note}</dd>}
    </div>
  );
}

/**
 * Revisió de la licitació (primera pantalla de producte): el que AQUESTA
 * empresa té acceptat de la licitació, llegit de la capa de revisió canònica.
 * Només lectura: aquesta pantalla no inicia la revisió ni aplica cap acció.
 */
export default function TenderReviewPage() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const { companyId } = useSession();
  const back = `/licitacions/${tenderId}?tab=requisits`;

  const tender = useResource<{ title: string }>(`/tenders/${tenderId}`);
  const overview = useResource<ReviewOverview>(companyId ? reviewPath(companyId, tenderId, "/overview") : null);
  const started = overview.data?.review_started === true;
  const requirements = useResource<CanonicalRequirement[]>(
    companyId && started ? reviewPath(companyId, tenderId, "/requirements") : null,
  );
  const scoring = useResource<AwardCriteriaView>(
    companyId && started ? reviewPath(companyId, tenderId, "/award-criteria?view=scoring") : null,
  );

  const title = "Revisió de la licitació";
  const description = tender.data?.title;
  const backLink = (
    <Link href={back} className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-brand-strong hover:text-accent-800">
      <ArrowLeft size={14} strokeWidth={1.5} aria-hidden />
      Torna a la fitxa
    </Link>
  );

  if (!companyId || (overview.loading && !overview.data)) {
    return <PageSkeleton variant="list" title={title} context="Carregant la revisió…" />;
  }

  if (overview.error || !overview.data) {
    const missing = overview.status === 404;
    return (
      <div>
        {backLink}
        <ErrorState
          title={missing ? "No trobem aquesta licitació" : "No s'ha pogut carregar la revisió"}
          description={
            missing
              ? "Pot ser que no existeixi o que la vostra empresa no hi tingui accés."
              : "No s'ha perdut res: torneu-ho a provar d'aquí a un moment."
          }
          action={
            <Button variant="secondary" onClick={() => void overview.reload()}>
              Torna-ho a provar
            </Button>
          }
          technical={overview.status ? { code: String(overview.status) } : undefined}
        />
      </div>
    );
  }

  if (!started) {
    return (
      <div>
        {backLink}
        <PageHeader title={title} description={description} />
        <Card>
          <EmptyState
            icon={<ClipboardList aria-hidden />}
            title="La revisió encara no s'ha iniciat"
            description="Quan s'iniciï, aquí hi haurà la llista de requisits i criteris d'adjudicació que la vostra empresa revisa. Aquesta pantalla només consulta: no inicia res."
            action={
              <LinkButton href={back} variant="secondary" size="md">
                Veure els requisits de la fitxa
              </LinkButton>
            }
          />
        </Card>
      </div>
    );
  }

  const data = overview.data;
  const award = scoring.data;
  return (
    <div>
      {backLink}
      <PageHeader title={title} description={description} />

      <section aria-labelledby="resum-revisio" className="mb-5">
        <h2 id="resum-revisio" className="sr-only">
          Resum
        </h2>
        <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Requisits" value={String(data.total_requirements)} />
          <Stat label="Cal revisar" value={String(data.needs_review_count)} />
          <Stat label="Criteris puntuats" value={award ? String(award.scored_count) : "…"} />
          <Stat label="Puntuació" value={award ? String(award.total_points) : "…"} note={award ? "punts en total" : undefined} />
        </dl>
      </section>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 xl:order-2">
          <AwardCriteriaPanel companyId={companyId} tenderId={tenderId} scoring={scoring} />
        </div>
        <div className="min-w-0 xl:order-1">
          {requirements.loading && !requirements.data && (
            <Card>
              <div aria-busy="true" aria-label="Carregant els requisits">
                <LoadingRows rows={6} />
              </div>
            </Card>
          )}
          {requirements.error && !requirements.loading && (
            <Card>
              <ErrorState
                title="No s'han pogut llegir els requisits"
                description="El resum i els criteris es poden consultar igualment."
                action={
                  <Button variant="secondary" onClick={() => void requirements.reload()}>
                    Torna-ho a provar
                  </Button>
                }
              />
            </Card>
          )}
          {requirements.data && <RequirementReviewList items={requirements.data} />}
        </div>
      </div>
    </div>
  );
}
