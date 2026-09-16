"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useCallback, type ReactNode } from "react";

import { ScoreValue } from "@/components/PreFit";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, CardHeader, ErrorState, LinkButton, PageHeader, PageSkeleton, Skeleton, StatusPill } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { useResource } from "@/lib/hooks";
import {
  ELIGIBILITY_LABELS,
  RECOMMENDATION_LABELS,
  daysUntilDeadline,
  deadlineLabel,
  deadlineLevel,
  shortSummary,
} from "@/lib/labels";
import type { TenderMatchListItem } from "@/lib/matching";
import { buildQuery, type Page } from "@/lib/tenders";
import type { DashboardSummary } from "@/lib/types";
import type { WorkspaceSummary } from "@/lib/workspace";

import { blockerReasons, emptyKind, openBlockers, pendingDecisions, todayLine } from "./queue";

/**
 * Avui: la cua de treball (handoff L5, mockup 02). Tres blocs composts al
 * client amb el que ja hi ha: /workspaces per a decisions i avisos, i el feed
 * de candidates ordenat per termini per a les noves. Cap recompte apareix
 * abans de ser real; si una crida falla, el seu bloc ho diu i ofereix
 * tornar-ho a provar. «Només les meves» és el cas C-4 i no es renderitza.
 */

const FRESH_LIMIT = 5;
const ACTION_CLASS = "min-h-12 w-full sm:min-h-0 sm:w-auto";

function DeadlinePill({ days }: { days: number | null }) {
  if (days === null) return <StatusPill level="neutral">Sense termini</StatusPill>;
  return <StatusPill level={deadlineLevel(days)}>{deadlineLabel(days)}</StatusPill>;
}

function Row({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 border-b border-border px-3.5 py-3 last:border-b-0 sm:flex-row sm:items-center sm:gap-3.5">
      {children}
    </div>
  );
}

function GhostRows({ rows = 3 }: { rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, index) => (
        <Row key={index}>
          <div className="min-w-0 flex-1">
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="mt-1.5 h-2.5 w-2/5" />
          </div>
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-9 w-24" />
        </Row>
      ))}
    </>
  );
}

function BlockError({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <ErrorState
      className="py-8"
      title={`No hem pogut carregar ${what}`}
      description="La connexió amb el servei ha fallat. La resta de la pàgina no en depèn."
      action={
        <Button variant="secondary" onClick={onRetry}>
          Torna-ho a provar
        </Button>
      }
    />
  );
}

function Nothing({ children }: { children: ReactNode }) {
  return <p className="px-3.5 py-4 text-[13px] text-muted">{children}</p>;
}

function CountPill({ count }: { count: number }) {
  return (
    <StatusPill level="neutral" className="tabular-nums">
      {count}
    </StatusPill>
  );
}

export default function AvuiPage() {
  const { companyId } = useSession();
  const base = `/companies/${companyId}`;
  const workspaces = useResource<WorkspaceSummary[]>(`${base}/workspaces`);
  const fresh = useResource<Page<TenderMatchListItem>>(
    `${base}/matches?${buildQuery({ match_status: "candidate", sort: "deadline", only_open: true, limit: FRESH_LIMIT })}`,
  );
  const profile = useResource<DashboardSummary>(`${base}/summary`);

  const reloadAll = useCallback(() => {
    void workspaces.reload();
    void fresh.reload();
    void profile.reload();
  }, [workspaces, fresh, profile]);

  const decisions = workspaces.data ? pendingDecisions(workspaces.data) : null;
  const blockers = workspaces.data ? openBlockers(workspaces.data) : null;
  const freshTotal = fresh.data ? fresh.data.total : undefined;
  const subtitle = todayLine(new Date(), {
    decisions: decisions?.length,
    warnings: blockers?.length,
    fresh: freshTotal,
  });

  // Primera càrrega: l'esquelet amb el títol i la data reals, cap recompte.
  if (!workspaces.data && !fresh.data && !workspaces.error && !fresh.error) {
    return <PageSkeleton variant="list" title="Avui" context={subtitle} />;
  }

  if (workspaces.error && fresh.error) {
    return (
      <>
        <PageHeader title="Avui" description={subtitle} />
        <ErrorState
          title="No hem pogut carregar la feina d'avui"
          description="La connexió amb el servei ha fallat. No s'ha perdut res: les candidatures i les oportunitats continuen on eren."
          action={
            <Button variant="primary" onClick={reloadAll}>
              Torna-ho a provar
            </Button>
          }
        />
      </>
    );
  }

  // Buit total: cap candidatura i cap candidata. Amb sortida sempre.
  if (workspaces.data && workspaces.data.length === 0 && fresh.data && fresh.data.total === 0) {
    const kind = emptyKind(fresh.data.total, profile.data?.counts.cpv_preferences);
    const openTenders = profile.data ? null : null;
    void openTenders;
    return (
      <>
        <PageHeader title="Avui" description={subtitle} />
        <Card>
          {kind === "profile" ? (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="Encara no sabem què busqueu"
              description="L'encaix es calcula amb els codis CPV, els territoris i la franja d'import del perfil. Sense això no podem ordenar les licitacions obertes: només llistar-les."
              action={
                <LinkButton href="/empresa" variant="primary" size="md">
                  Completa el perfil · 3 passos
                </LinkButton>
              }
              secondary={
                <LinkButton href="/licitacions" variant="secondary" size="md">
                  Mira les oportunitats sense encaix
                </LinkButton>
              }
            />
          ) : (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="Encara no hi ha res a la cua"
              description="Una candidatura es crea des d'una oportunitat, quan decidiu que val la pena mirar-la de prop. Ara mateix cap oportunitat oberta encaixa amb el perfil."
              action={
                <LinkButton href="/licitacions" variant="primary" size="md">
                  Mira les oportunitats
                </LinkButton>
              }
            />
          )}
        </Card>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Avui" description={subtitle} />

      <Card>
        <CardHeader
          title="Decisions pendents"
          description="Ordenades per termini"
          action={decisions ? <CountPill count={decisions.length} /> : undefined}
        />
        {workspaces.error ? (
          <BlockError what="les candidatures" onRetry={() => void workspaces.reload()} />
        ) : !decisions ? (
          <GhostRows />
        ) : decisions.length === 0 ? (
          <Nothing>Cap decisió pendent.</Nothing>
        ) : (
          decisions.map((workspace, index) => (
            <Row key={workspace.id}>
              <div className="min-w-0 flex-1">
                <p className="font-medium text-ink">{workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}</p>
                <p className="text-[13px] text-muted">
                  {[workspace.buyer_name, workspace.expediente_number].filter(Boolean).join(" · ")}
                  {workspace.recommendation && (
                    <>
                      {" · "}Recomanació automàtica: {RECOMMENDATION_LABELS[workspace.recommendation]}
                    </>
                  )}
                  {workspace.eligibility && (
                    <>
                      {" · "}Elegibilitat: {ELIGIBILITY_LABELS[workspace.eligibility]}
                    </>
                  )}
                  {workspace.analysis_outdated && <span className="text-warning">{" · "}el plec ha canviat després de l&apos;anàlisi</span>}
                </p>
                {workspace.human_decision === "defer" && (
                  <StatusPill level="attention" size="sm" className="mt-1.5">
                    Ajornada · torna a decidir
                  </StatusPill>
                )}
              </div>
              <DeadlinePill days={workspace.days_remaining} />
              <LinkButton
                href={`/candidatures/${workspace.id}?pas=decidir`}
                variant={index === 0 ? "primary" : "secondary"}
                size="md"
                className={ACTION_CLASS}
              >
                Decideix
              </LinkButton>
            </Row>
          ))
        )}
      </Card>

      <Card>
        <CardHeader
          title="Bloquejos i avisos"
          description="Cada un amb on es resol"
          action={blockers ? <CountPill count={blockers.length} /> : undefined}
        />
        {workspaces.error ? (
          <BlockError what="els avisos" onRetry={() => void workspaces.reload()} />
        ) : !blockers ? (
          <GhostRows rows={2} />
        ) : blockers.length === 0 ? (
          <Nothing>Cap bloqueig obert.</Nothing>
        ) : (
          blockers.map((workspace, index) => {
            const reasons = blockerReasons(workspace);
            const first = reasons[0];
            return (
              <Row key={workspace.id}>
                <StatusPill level={first.level} className="shrink-0 self-start sm:self-center">
                  {first.label}
                </StatusPill>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink">{workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}</p>
                  <p className="text-[13px] text-muted">
                    {[workspace.buyer_name, ...reasons.map((reason) => reason.detail)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <LinkButton
                  href={`/candidatures/${workspace.id}`}
                  variant={index === 0 ? "primary" : "secondary"}
                  size="md"
                  className={ACTION_CLASS}
                >
                  Obre la candidatura
                </LinkButton>
              </Row>
            );
          })
        )}
      </Card>

      <Card>
        <CardHeader
          title="Noves per revisar"
          description="Candidates obertes, ordenades per termini"
          action={freshTotal !== undefined ? <CountPill count={freshTotal} /> : undefined}
        />
        {fresh.error ? (
          <BlockError what="les oportunitats" onRetry={() => void fresh.reload()} />
        ) : !fresh.data ? (
          <GhostRows />
        ) : fresh.data.items.length === 0 ? (
          <Nothing>Cap oportunitat candidata oberta.</Nothing>
        ) : (
          <>
            {fresh.data.items.map(({ tender, match }, index) => {
              const days = daysUntilDeadline(tender.deadline_at);
              return (
                <Row key={tender.id}>
                  <span className="grid h-8 min-w-11 shrink-0 place-items-center self-start px-1.5 sm:self-center">
                    <ScoreValue score={match?.score ?? null} size="sm" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink">{tender.title}</p>
                    <p className="text-[13px] text-muted">
                      {[tender.buyer?.name, match ? shortSummary(match) : "Encaix sense calcular"].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                  <DeadlinePill days={days} />
                  <LinkButton
                    href={`/licitacions/${tender.id}`}
                    variant={index === 0 ? "primary" : "secondary"}
                    size="md"
                    className={ACTION_CLASS}
                  >
                    Obre
                  </LinkButton>
                </Row>
              );
            })}
            {freshTotal !== undefined && freshTotal > fresh.data.items.length && (
              <div className="border-t border-border px-3.5 py-2.5 text-[13px]">
                <Link href="/licitacions?match_status=candidate" className="text-brand-strong hover:text-accent-800">
                  Veure les {freshTotal} a Oportunitats →
                </Link>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
