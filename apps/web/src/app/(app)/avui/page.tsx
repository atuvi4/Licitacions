"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { ScoreValue } from "@/components/PreFit";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, CardHeader, ErrorState, LinkButton, PageHeader, PageSkeleton, Skeleton, StatusPill } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import {
  ELIGIBILITY_LABELS,
  RECOMMENDATION_LABELS,
  deadlineCountdownView,
  shortSummary,
} from "@/lib/labels";
import type { MatchSummary, RecalculationResult, TenderMatchListItem } from "@/lib/matching";
import { blockerCountLabel, type PreparationCounts } from "@/lib/readiness";
import { buildQuery, type Page } from "@/lib/tenders";
import type { WorkspaceSummary } from "@/lib/workspace";

import {
  RADAR_NOTE,
  assignQueue,
  blockerReasons,
  candidatesCause,
  candidatesEmptyLine,
  candidatesLine,
  decisionsAboveLine,
  formatCount,
  preparationAboveLine,
  preparationScope,
  todayLine,
  uncountedLine,
  withWorkspaceLine,
  type CandidatesCause,
  type QueueRow,
  type QueueTag,
} from "./queue";

/**
 * Pendents de preparació de cada candidatura oberta: els bloquejos de
 * `final-readiness` (la mateixa lectura que Tancar), comptats al servidor en UNA
 * crida (`…/workspaces/preparation-counts`, P2-09). `null` mentre es compta; un
 * valor `null` per candidatura vol dir que no s'ha pogut saber, mai zero: si la
 * crida falla, totes les de l'abast; si en falta una a la resposta, aquella.
 */
function usePreparationPending(
  base: string,
  ids: string[] | null,
  version: number,
): Record<string, number | null> | null {
  const idsKey = ids === null ? null : ids.join(",");
  // La versió fa que «Torna-ho a provar» i «Torna-ho a comptar» tornin a llegir encara que els ids siguin els mateixos.
  const key = idsKey === null ? null : `${base}|${version}|${idsKey}`;
  const [state, setState] = useState<{ key: string; counts: Record<string, number | null> } | null>(null);
  useEffect(() => {
    if (key === null || idsKey === null) return;
    let cancelled = false;
    const list = idsKey ? idsKey.split(",") : [];
    void (async () => {
      let pending: Map<string, number | null> | null = null;
      // Cap candidatura a l'abast: res a demanar.
      if (list.length > 0) {
        try {
          const response = await api.get<PreparationCounts>(`${base}/workspaces/preparation-counts`);
          if (Array.isArray(response?.items)) {
            pending = new Map(response.items.map((item) => [item.workspace_id, item.pending]));
          }
        } catch {
          pending = null;
        }
      }
      const counts = Object.fromEntries(
        list.map((id) => {
          const value = pending?.get(id);
          return [id, typeof value === "number" ? value : null];
        }),
      );
      if (!cancelled) setState({ key, counts });
    })();
    return () => {
      cancelled = true;
    };
  }, [base, key, idsKey]);
  if (key === null || !state || state.key !== key) return null;
  return state.counts;
}

function SubHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="border-b border-border bg-subtle px-3.5 py-1.5 text-caption font-medium uppercase tracking-wide text-muted">
      {children}
    </h3>
  );
}

/**
 * Avui: la cua de treball (handoff L5, mockup 02). Tres blocs composts al
 * client amb el que ja hi ha: /workspaces per a decisions i avisos, i el feed
 * de candidates ordenat per termini per a les noves. Cap recompte apareix
 * abans de ser real; si una crida falla, el seu bloc ho diu i ofereix
 * tornar-ho a provar. «Només les meves» és el cas C-4 i no es renderitza.
 */

const FRESH_LIMIT = 5;
const ACTION_CLASS = "min-h-12 w-full sm:min-h-0 sm:w-auto";

/** El comptador de `lib/deadline` (A11), a partir de la data: el mateix que a la fitxa. */
function DeadlinePill({ deadline }: { deadline: string | null }) {
  const countdown = deadlineCountdownView(deadline);
  return <StatusPill level={countdown.level}>{countdown.text}</StatusPill>;
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

/**
 * Les altres condicions d'una fila (P2-05): cada candidatura surt una sola vegada,
 * a la secció més urgent, i la resta es diu aquí, unida amb « · ».
 */
function QueueTags({ tags }: { tags: QueueTag[] }) {
  if (tags.length === 0) return null;
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-1 text-[12px] text-muted" data-testid="queue-tags">
      {tags.map((tag, index) => (
        <span key={tag.kind} className="inline-flex items-center gap-1">
          {index > 0 && <span aria-hidden>{" · "}</span>}
          <StatusPill level="attention" size="sm">
            {tag.label}
          </StatusPill>
        </span>
      ))}
    </p>
  );
}

/**
 * Candidatures de més amunt amb els pendents sense comptar (I-1): no es diu que en
 * tinguin, i es pot tornar a demanar el recompte des d'aquí.
 */
function UncountedAbove({ count, onRecount }: { count: number; onRecount: () => void }) {
  return (
    <Row>
      <p className="min-w-0 flex-1 text-[13px] text-muted">{uncountedLine(count)}</p>
      <Button variant="secondary" size="md" className={ACTION_CLASS} onClick={onRecount}>
        Torna-ho a comptar
      </Button>
    </Row>
  );
}

/** «N candidates ja tenen candidatura»: les que Avui treu de la llista, amb on són. */
function WithWorkspaceNote({ count }: { count: number }) {
  return (
    <Link href="/candidatures" className="text-brand-strong hover:text-accent-800">
      {withWorkspaceLine(count)}
    </Link>
  );
}

/** El recàlcul del pre-fit que ja existeix (`POST …/matches/recalculate`). */
interface RadarCalculation {
  run: () => void;
  saving: boolean;
  error: string | null;
}

/**
 * El bloc del Radar sense cap candidata oberta (A17): el 0 es diu sobre el
 * total de licitacions i amb la causa, i la sortida és on es resol. Sense cap
 * licitació avaluada, la sortida és calcular-lo (F1).
 */
function RadarEmpty({
  radar,
  cause,
  calculate,
}: {
  radar: MatchSummary | null;
  cause: CandidatesCause | null;
  calculate: RadarCalculation;
}) {
  // P2-05: la llista no porta les que ja tenen candidatura. Si totes en tenen, es diu així.
  const withWorkspace = radar?.open_candidates_with_workspace ?? 0;
  if (withWorkspace > 0) {
    return (
      <Nothing>
        {withWorkspace === 1
          ? "L'única candidata oberta ja té "
          : `Totes les ${formatCount(withWorkspace)} candidates obertes ja tenen `}
        <Link href="/candidatures" className="text-brand-strong hover:text-accent-800">
          candidatura
        </Link>
        .
      </Nothing>
    );
  }
  if (radar && radar.candidates > 0) {
    return <Nothing>Cap candidata amb termini obert · {candidatesLine(radar.candidates, radar.total_tenders)}.</Nothing>;
  }
  return (
    <div className="px-3.5 py-4 text-[13px] text-muted">
      <p>{radar ? candidatesEmptyLine(radar.total_tenders, radar.evaluated_count, cause) : "Cap candidata"}.</p>
      {calculate.error && <p className="mt-1 text-danger">{calculate.error}</p>}
      <p className="mt-1">
        Són {RADAR_NOTE}.{" "}
        {cause === "not_evaluated" ? (
          <Button size="sm" variant="secondary" loading={calculate.saving} onClick={calculate.run}>
            Calcula el pre-fit
          </Button>
        ) : cause === "profile" ? (
          <Link href="/empresa/radar" className="text-brand-strong hover:text-accent-800">
            Defineix les preferències del Radar →
          </Link>
        ) : cause === "no_cpv" ? (
          <Link href="/empresa/radar" className="text-brand-strong hover:text-accent-800">
            Afegeix els CPV al perfil →
          </Link>
        ) : (
          <Link href="/licitacions" className="text-brand-strong hover:text-accent-800">
            Mira totes les licitacions →
          </Link>
        )}
      </p>
    </div>
  );
}

export default function AvuiPage() {
  const { companyId } = useSession();
  const base = `/companies/${companyId}`;
  const workspaces = useResource<WorkspaceSummary[]>(`${base}/workspaces`);
  // Només les candidates NOVES (sense candidatura oberta): les que ja en tenen
  // surten a les seccions de candidatures i aquí es diuen a part (P2-05).
  const fresh = useResource<Page<TenderMatchListItem>>(
    `${base}/matches?${buildQuery({ match_status: "candidate", sort: "deadline", only_open: true, exclude_open_workspaces: true, limit: FRESH_LIMIT })}`,
  );
  // El resum del Radar: candidates prioritzades i el total de licitacions
  // visibles (el mateix univers que «Totes» d'Oportunitats), A17. Porta també
  // els recomptes del perfil per a la causa del 0 (P2-09: cap crida a `/summary`).
  const radar = useResource<MatchSummary>(`${base}/matches/summary`);

  const recalculation = useMutation();
  // Tornar a llegir els pendents de preparació (reintent o recàrrega), encara que les candidatures siguin les mateixes.
  const [readinessVersion, setReadinessVersion] = useState(0);
  const recountPreparation = useCallback(() => setReadinessVersion((version) => version + 1), []);

  const reloadAll = useCallback(() => {
    void workspaces.reload();
    void fresh.reload();
    void radar.reload();
    recountPreparation();
  }, [workspaces, fresh, radar, recountPreparation]);

  const calculate: RadarCalculation = {
    saving: recalculation.saving,
    error: recalculation.error,
    run: () => {
      void (async () => {
        const done = await recalculation.run(async () => {
          await api.post<RecalculationResult>(`${base}/matches/recalculate`, {});
        });
        if (done) {
          void fresh.reload();
          void radar.reload();
        }
      })();
    },
  };

  // Només les de l'empresa activa: en canviar d'empresa, la llista anterior no es compta ni es demana amb la ruta nova.
  const companyWorkspaces = workspaces.data
    ? workspaces.data.filter((workspace) => workspace.company_id === companyId)
    : null;
  const preparation = usePreparationPending(
    base,
    companyWorkspaces ? preparationScope(companyWorkspaces).map((workspace) => workspace.id) : null,
    readinessVersion,
  );
  // P2-05: cada candidatura a UNA sola secció (la més urgent), amb la resta d'etiquetes.
  // Auditoria UI P1-04: «cap bloqueig» no pot amagar el que no s'ha avaluat ni el que falta preparar.
  const queue = companyWorkspaces ? assignQueue(companyWorkspaces, preparation) : null;
  const decisions = queue?.decisions ?? null;
  const blockers = queue?.blockers ?? null;
  const unevaluated = queue?.unevaluated ?? null;
  const preparationList = queue?.preparation ?? null;
  const freshTotal = fresh.data ? fresh.data.total : undefined;
  const withWorkspace = radar.data?.open_candidates_with_workspace ?? 0;
  // «Veure les N»: totes les candidates obertes, també les que ja tenen candidatura.
  const allOpenCandidates = freshTotal !== undefined ? freshTotal + withWorkspace : undefined;
  // La causa del 0 surt del resum del Radar (avaluades), mai només del perfil (F1).
  const cause = candidatesCause(radar.data, radar.data?.profile_counts);
  const subtitle = todayLine(new Date(), {
    decisions: decisions?.length,
    warnings: blockers?.length,
    unevaluated: unevaluated?.length,
    candidates: radar.data?.candidates,
    totalTenders: radar.data?.total_tenders,
    evaluated: radar.data?.evaluated_count,
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

  // Buit total: cap candidatura i cap candidata. Amb sortida sempre, i el 0
  // explicat: candidates prioritzades pel Radar sobre el total de licitacions,
  // amb la causa (A17). Cap mesurador de completitud ni «Completa el perfil»
  // (B1): la sortida són les Preferències del Radar.
  if (workspaces.data && workspaces.data.length === 0 && fresh.data && fresh.data.total === 0) {
    const zero = radar.data
      ? candidatesEmptyLine(radar.data.total_tenders, radar.data.evaluated_count, cause)
      : "0 candidates";
    const reading =
      cause === "no_match"
        ? ": ara mateix cap licitació oberta avaluada encaixa amb el perfil"
        : cause === "insufficient_data"
          ? ": no hi ha prou dades (del perfil o de les licitacions avaluades) per opinar"
          : "";
    return (
      <>
        <PageHeader title="Avui" description={subtitle} />
        <Card>
          {cause === "not_evaluated" ? (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="El Radar encara no s'ha calculat"
              description={`${zero}. Són ${RADAR_NOTE}: el pre-fit encara no s'ha calculat per a cap licitació, i per tant no se sap quines encaixen.`}
              action={
                <>
                  {calculate.error && <p className="mb-2 text-[13px] text-danger">{calculate.error}</p>}
                  <Button variant="primary" loading={calculate.saving} onClick={calculate.run}>
                    Calcula el pre-fit
                  </Button>
                </>
              }
              secondary={
                <LinkButton href="/licitacions" variant="secondary" size="md">
                  Mira les oportunitats
                </LinkButton>
              }
            />
          ) : cause === "profile" ? (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="Encara no sabem què busqueu"
              description={`${zero}. Són ${RADAR_NOTE}: el Radar prioritza amb els codis CPV, els territoris i la franja d'import del perfil, i sense això només pot llistar les licitacions.`}
              action={
                <LinkButton href="/empresa/radar" variant="primary" size="md">
                  Defineix les preferències del Radar
                </LinkButton>
              }
              secondary={
                <LinkButton href="/licitacions" variant="secondary" size="md">
                  Mira les oportunitats sense encaix
                </LinkButton>
              }
            />
          ) : cause === "no_cpv" ? (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="Sense CPV al perfil, cap licitació no pot ser candidata"
              description={`${zero}. Són ${RADAR_NOTE}: el Radar ja avalua les licitacions, però una licitació només és candidata si encaixa amb l'activitat (codis CPV) de l'empresa, i el perfil encara no en té cap. El territori i la franja d'import sols no hi arriben.`}
              action={
                <LinkButton href="/empresa/radar" variant="primary" size="md">
                  Afegeix els CPV al perfil
                </LinkButton>
              }
              secondary={
                <LinkButton href="/licitacions" variant="secondary" size="md">
                  Mira les oportunitats
                </LinkButton>
              }
            />
          ) : (
            <EmptyState
              icon={<Search aria-hidden strokeWidth={1.5} />}
              title="Encara no hi ha res a la cua"
              description={`${zero}. Són ${RADAR_NOTE}${reading}. Una candidatura es crea des d'una oportunitat, quan decidiu que val la pena mirar-la de prop.`}
              action={
                <LinkButton href="/licitacions" variant="primary" size="md">
                  Mira les oportunitats
                </LinkButton>
              }
              secondary={
                <LinkButton href="/empresa/radar" variant="secondary" size="md">
                  Preferències del Radar
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
          <Nothing>{queue && queue.decisionsAbove > 0 ? decisionsAboveLine(queue.decisionsAbove) : "Cap decisió pendent."}</Nothing>
        ) : (
          decisions.map(({ workspace, tags }, index) => (
            <Row key={workspace.id}>
              <div className="min-w-0 flex-1">
                <p className="font-medium text-ink">{workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}</p>
                <p className="text-[13px] text-muted">
                  {[workspace.buyer_name, workspace.expediente_number].filter(Boolean).join(" · ")}
                  {workspace.recommendation && (
                    <>
                      {" · "}Resultat de l&apos;anàlisi: {RECOMMENDATION_LABELS[workspace.recommendation]}
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
                <QueueTags tags={tags} />
              </div>
              <DeadlinePill deadline={workspace.deadline_at} />
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
          title="Bloquejos i pendents"
          description="Separats: el que s'ha detectat, el que encara no s'ha avaluat i el que falta preparar"
          action={blockers ? <CountPill count={blockers.length} /> : undefined}
        />
        {workspaces.error ? (
          <BlockError
            what="els avisos"
            onRetry={() => {
              void workspaces.reload();
              recountPreparation();
            }}
          />
        ) : !queue || !blockers || !unevaluated ? (
          <GhostRows rows={2} />
        ) : (
          <>
            <SubHeading>Bloquejos detectats</SubHeading>
            {blockers.length === 0 ? (
              <Nothing>
                Cap bloqueig detectat.
                {/* Una advertència, no un comptador: totes les sense avaluar, siguin a la secció que siguin. */}
                {queue.unevaluatedTotal > 0 &&
                  ` Això no vol dir que no n'hi hagi: ${queue.unevaluatedTotal === 1 ? "1 candidatura encara no s'ha avaluat" : `${queue.unevaluatedTotal} candidatures encara no s'han avaluat`}.`}
              </Nothing>
            ) : (
              <BlockerRows blockers={blockers} />
            )}

            {unevaluated.length > 0 && (
              <>
                <SubHeading>Sense avaluar</SubHeading>
                {unevaluated.map(({ workspace, tags }) => (
                  <Row key={workspace.id}>
                    <StatusPill level="attention" className="shrink-0 self-start sm:self-center">
                      Sense anàlisi del plec
                    </StatusPill>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-ink">{workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}</p>
                      <p className="text-[13px] text-muted">
                        {[
                          workspace.buyer_name,
                          "No s'ha avaluat cap requisit del plec: «cap bloqueig» aquí vol dir que no s'ha mirat, no que no n'hi hagi",
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      <QueueTags tags={tags} />
                    </div>
                    <LinkButton href={`/candidatures/${workspace.id}`} variant="secondary" size="md" className={ACTION_CLASS}>
                      Obre la candidatura
                    </LinkButton>
                  </Row>
                ))}
              </>
            )}

            <SubHeading>Pendents de preparació</SubHeading>
            {!preparationList ? (
              <GhostRows rows={1} />
            ) : preparationList.length === 0 ? (
              // Cap afirmació falsa: les comptades de més amunt es diuen «amb pendents»; les
              // que no s'han pogut comptar, a part (`UncountedAbove`). Zero només si no n'hi ha cap.
              queue.preparationAbove > 0 ? (
                <Nothing>{preparationAboveLine(queue.preparationAbove)}</Nothing>
              ) : queue.uncountedAbove === 0 ? (
                <Nothing>Cap pendent de preparació a les candidatures obertes.</Nothing>
              ) : null
            ) : (
              preparationList.map(({ workspace, pending = null }) => (
                <Row key={workspace.id}>
                  <StatusPill level={pending === null ? "neutral" : "attention"} className="shrink-0 self-start sm:self-center">
                    {pending === null ? "Sense comptar" : blockerCountLabel(pending)}
                  </StatusPill>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink">{workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}</p>
                    <p className="text-[13px] text-muted">
                      {pending === null
                        ? "No s'han pogut comptar els pendents d'aquesta candidatura"
                        : "El que encara impedeix tancar la candidatura"}
                    </p>
                  </div>
                  {pending === null ? (
                    <Button
                      variant="secondary"
                      size="md"
                      className={ACTION_CLASS}
                      onClick={recountPreparation}
                      aria-label={`Torna-ho a comptar: ${workspace.tender_title ?? workspace.expediente_number ?? "Licitació"}`}
                    >
                      Torna-ho a comptar
                    </Button>
                  ) : (
                    <LinkButton href={`/candidatures/${workspace.id}?pas=tancar`} variant="secondary" size="md" className={ACTION_CLASS}>
                      Mira els pendents
                    </LinkButton>
                  )}
                </Row>
              ))
            )}
            {preparationList && queue.uncountedAbove > 0 && (
              <UncountedAbove count={queue.uncountedAbove} onRecount={recountPreparation} />
            )}
          </>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Candidates del Radar"
          description={`Candidates ${RADAR_NOTE.replace(/^candidates /, "")} · obertes, ordenades per termini`}
          action={
            radar.data ? (
              <StatusPill level="neutral" className="tabular-nums">
                {candidatesLine(radar.data.candidates, radar.data.total_tenders)}
              </StatusPill>
            ) : freshTotal !== undefined ? (
              <CountPill count={freshTotal} />
            ) : undefined
          }
        />
        {fresh.error ? (
          <BlockError what="les oportunitats" onRetry={() => void fresh.reload()} />
        ) : !fresh.data ? (
          <GhostRows />
        ) : fresh.data.items.length === 0 ? (
          <RadarEmpty radar={radar.data} cause={cause} calculate={calculate} />
        ) : (
          <>
            {fresh.data.items.map(({ tender, match }, index) => {
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
                  <DeadlinePill deadline={tender.deadline_at} />
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
            {withWorkspace > 0 && (
              <div className="border-t border-border px-3.5 py-2.5 text-[13px]">
                <WithWorkspaceNote count={withWorkspace} />
              </div>
            )}
            {allOpenCandidates !== undefined && allOpenCandidates > fresh.data.items.length && (
              <div className="border-t border-border px-3.5 py-2.5 text-[13px]">
                <Link href="/licitacions?match_status=candidate" className="text-brand-strong hover:text-accent-800">
                  Veure les {allOpenCandidates} a Oportunitats →
                </Link>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

/** Bloquejos detectats: cada un amb el motiu i on es resol (el que ja feia «Avui»). */
function BlockerRows({ blockers }: { blockers: QueueRow[] }) {
  return (
    <>
      {blockers.map(({ workspace, tags }, index) => {
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
              <QueueTags tags={tags} />
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
      })}
    </>
  );
}
