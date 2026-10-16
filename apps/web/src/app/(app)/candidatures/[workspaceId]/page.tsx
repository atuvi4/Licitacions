"use client";

import { Check, Lock } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";

import { useSession } from "@/components/SessionProvider";
import { EconomicOfferSection } from "@/components/EconomicOfferSection";
import { SubmissionPackageSection } from "@/components/SubmissionPackage";
import { TechnicalSolutionSection } from "@/components/TechnicalSolutionSection";
import { WorkPlanDeadlines } from "@/components/WorkPlanDeadlines";
import { DecisionHistory, DecisionPanel } from "@/components/WorkspaceDecision";
import { DossierSection } from "@/components/WorkspaceDossier";
import { DecisionAnalysisCard } from "@/components/tender-analysis/DecisionAnalysisCard";
import { ChecklistSection, NotesSection, TasksSection } from "@/components/WorkspaceWork";
import {
  Banner,
  Button,
  Card,
  CardHeader,
  ErrorState,
  LinkButton,
  PageHeader,
  PageSkeleton,
  StatusPill,
  Tabs,
  cn,
} from "@/components/ui";
import { ReadinessControl } from "@/components/ReadinessControl";
import { ReadinessProvider, useInvalidateReadiness, useReadiness } from "@/components/ReadinessProvider";
import { decisionAnalysisState, analysisPath, type AnalysisRun } from "@/lib/analysis";
import { useResource } from "@/lib/hooks";
import { IssuesDrawer } from "@/components/IssuesDrawer";
import { PREPARE_AREAS, PREPARE_AREA_LABELS, blockerCount, blockersByArea, isPrepareArea, pendingCountLabel, type PrepareArea } from "@/lib/readiness";
import {
  WORKSPACE_STATUS_LEVEL,
  deadlineCountdownView,
} from "@/lib/labels";
import { formatDateTime } from "@/lib/tenders";
import {
  workspaceStatusLabel,
  type WorkspaceDecisionType,
  type WorkspaceDetail,
} from "@/lib/workspace";

import {
  PREPARE_READ_ONLY_TEXT,
  STEPS,
  defaultStep,
  prepareMode,
  primaryBanner,
  stepperItems,
  type CandidatureStep,
  type StepItem,
} from "./steps";

/**
 * Preparar i Tancar sense GO (3B-2 §7.1.6, decisió 4): es llegeixen, no
 * s'editen. Una nota i no un bàner: la pàgina només en té un (handoff §2.4).
 */
function ReadOnlyNote({ decideHref }: { decideHref: string }) {
  return (
    <p role="note" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] text-muted">
      <Lock size={14} strokeWidth={1.5} aria-hidden />
      <span>{PREPARE_READ_ONLY_TEXT}</span>
      <span aria-hidden>·</span>
      <Link href={decideHref} className="font-medium text-brand hover:underline">
        Ves a Decidir
      </Link>
    </p>
  );
}

/**
 * Sense GO, l'àrea es renderitza igual però dins d'un `fieldset` desactivat: el
 * navegador desactiva tots els controls de dins (botons, camps, selectors) i
 * cap crida mutadora pot sortir de la interfície. Els enllaços continuen
 * funcionant: es pot consultar. El backend ho imposa igual (403
 * `decision_required`), aquí només s'evita oferir el que no es pot fer.
 */
function PrepareArea({ editable, children }: { editable: boolean; children: ReactNode }) {
  return (
    <fieldset
      disabled={!editable}
      data-prepare-mode={editable ? "edit" : "read-only"}
      aria-label="Preparació de la candidatura"
      className="m-0 min-w-0 space-y-5 border-0 p-0"
    >
      {children}
    </fieldset>
  );
}

/**
 * Decidir → Preparar → Tancar. El progrés surt de l'estat real; el pas que
 * es mira, de la URL (`?pas=`). Enllaços de debò: un enllaç directe, un
 * refresc i el botó enrere funcionen sense cap estat local.
 */
function PhaseStepper({
  items,
  current,
  hrefFor,
  beforeNavigate,
}: {
  items: StepItem[];
  current: CandidatureStep;
  hrefFor: (step: CandidatureStep) => string;
  /**
   * Pot aturar el canvi de pas. Si torna `false`, no es navega: és el que
   * permet que una àrea amb feina sense desar pregunti abans de perdre-la.
   */
  beforeNavigate?: (step: CandidatureStep) => boolean | Promise<boolean>;
}) {
  const router = useRouter();
  const active = items.find((item) => item.key === current) ?? items[0];
  // Continuen sent enllaços de debò —obrir en una pestanya nova, copiar
  // l'adreça i el botó del mig funcionen igual—; només el clic normal passa
  // per la pregunta, i només quan n'hi ha una de registrada.
  async function onClick(event: MouseEvent<HTMLAnchorElement>, step: CandidatureStep) {
    if (!beforeNavigate || step === current) return;
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const verdict = beforeNavigate(step);
    // Sense res a preguntar, el clic segueix sent el del `Link` de sempre.
    if (verdict === true) return;
    event.preventDefault();
    if (verdict !== false && (await verdict)) router.push(hrefFor(step));
  }
  return (
    <nav aria-label="Passos de la candidatura" className="border-y border-border">
      <p className="px-1 pt-2 text-[12.5px] text-muted md:hidden">
        Pas {active.number} de 3 · <strong className="text-ink">{active.label}</strong>
      </p>
      <ol className="flex items-stretch">
        {items.map((item, index) => {
          const selected = item.key === current;
          return (
            <li key={item.key} data-state={item.state} className="flex min-w-0 flex-1 items-center">
              <Link
                href={hrefFor(item.key)}
                onClick={(event) => void onClick(event, item.key)}
                aria-current={selected ? "step" : undefined}
                className={cn(
                  "-mb-px flex min-h-11 min-w-0 flex-1 items-center gap-2.5 border-b-2 px-2 py-2.5 md:px-3",
                  selected ? "border-accent-500" : "border-transparent hover:bg-subtle",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "grid h-6 w-6 shrink-0 place-items-center border font-heading text-[12px] font-semibold",
                    item.state === "done" && "border-brand bg-brand text-white",
                    item.state === "on" && "border-brand-strong text-brand-strong",
                    item.state === "todo" && "border-border-strong text-muted",
                  )}
                >
                  {item.state === "done" ? <Check size={13} strokeWidth={2.5} /> : item.number}
                </span>
                <span className="min-w-0">
                  <span
                    className={cn(
                      "font-heading text-[13.5px] font-semibold",
                      item.state === "todo" && !selected ? "text-muted" : "text-ink",
                    )}
                  >
                    {item.label}
                  </span>
                  {item.note && (
                    <span className="hidden text-[12.5px] text-muted md:inline"> · {item.note}</span>
                  )}
                  {item.pill && (
                    <StatusPill level={item.pill.level} size="sm" className="ml-2 hidden md:inline-flex">
                      {item.pill.text}
                    </StatusPill>
                  )}
                </span>
              </Link>
              {index < items.length - 1 && (
                <span aria-hidden className="mx-1 hidden h-px w-6 shrink-0 bg-border-strong md:block" />
              )}
            </li>
          );
        })}
      </ol>
      {active.pill && (
        <p className="px-1 pb-2 md:hidden">
          <StatusPill level={active.pill.level} size="sm">
            {active.pill.text}
          </StatusPill>
        </p>
      )}
    </nav>
  );
}

/**
 * Context de la decisió: només Encaix (de `prefit_score`) i Lot. L'elegibilitat
 * i la base de l'anàlisi del compliment anterior no hi són: no es presenten com
 * a estat vigent (F-4).
 */
function ContextPanel({ workspace }: { workspace: WorkspaceDetail }) {
  const rows: Array<{ label: string; value: React.ReactNode }> = [
    {
      label: "Encaix",
      value:
        workspace.prefit_score !== null ? (
          <>
            <strong className="text-ink">{workspace.prefit_score}</strong>
            <span className="text-muted"> sobre 100 · ens interessa?</span>
          </>
        ) : (
          <span className="text-muted">Sense càlcul</span>
        ),
    },
  ];
  if (workspace.lot_number !== null) {
    rows.push({
      label: "Lot",
      value: (
        <span className="text-ink">
          {workspace.lot_number}
          {workspace.lot_title && <span className="text-muted"> · {workspace.lot_title}</span>}
        </span>
      ),
    });
  }
  return (
    <Card>
      <dl className="px-3.5 py-1 text-[13.5px]">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-b-0">
            <dt className="shrink-0 text-muted">{row.label}</dt>
            <dd className="min-w-0 text-right">{row.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

/**
 * Únic propietari de `/analysis/current` (spec §4): només es munta al pas
 * Decidir, llegeix un sol cop i passa el mateix estat a la targeta i al panell.
 */
function DecidirStep({
  companyId,
  data,
  onDecided,
  onRefreshWorkspace,
  signerName,
  children,
}: {
  companyId: string | null;
  data: WorkspaceDetail;
  onDecided: (decision: WorkspaceDecisionType) => Promise<void>;
  onRefreshWorkspace: () => Promise<void>;
  signerName: string;
  children: ReactNode;
}) {
  const resource = useResource<AnalysisRun>(
    companyId ? `${analysisPath(companyId, data.tender_id)}/current` : null,
  );
  const analysis = decisionAnalysisState(resource);
  // Conflicte `decision_reason_required`: workspace i anàlisi vigent, les dues
  // lectures alhora; el modal continua obert amb l'estat nou.
  const onDecisionConflict = async () => {
    await Promise.all([onRefreshWorkspace(), resource.reload()]);
  };
  return (
    <div className="grid gap-5 lg:grid-cols-12">
      <div className="lg:col-span-7">
        <DecisionPanel
          workspace={data}
          onDecided={onDecided}
          onDecisionConflict={onDecisionConflict}
          signerName={signerName}
          analysis={analysis}
        />
      </div>
      <div className="flex flex-col gap-4 lg:col-span-5">
        {companyId && <DecisionAnalysisCard state={analysis} tenderId={data.tender_id} />}
        {children}
      </div>
    </div>
  );
}

export default function WorkspacePage() {
  const params = useParams<{ workspaceId: string }>();
  const { companyId } = useSession();
  return (
    <ReadinessProvider companyId={companyId} workspaceId={params.workspaceId}>
      <WorkspaceView />
    </ReadinessProvider>
  );
}

function WorkspaceView() {
  const params = useParams<{ workspaceId: string }>();
  const { companyId, user } = useSession();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { data, error, status, reload } = useResource<WorkspaceDetail>(
    companyId ? `/companies/${companyId}/workspaces/${params.workspaceId}` : null,
  );
  const invalidateReadiness = useInvalidateReadiness();
  const readiness = useReadiness();
  // Una àrea amb feina sense desar pot aturar el canvi de pestanya. La secció
  // registra la pregunta; les pestanyes la fan abans de navegar.
  const leaveGuard = useRef<(() => Promise<boolean>) | null>(null);
  const registerLeaveGuard = useCallback((guard: (() => Promise<boolean>) | null) => {
    leaveGuard.current = guard;
  }, []);

  // El pas que es mira surt de la URL. Si la URL no ho diu, la primera càrrega
  // el fixa (a la URL i aquí) a partir de l'estat real. Després només el mou
  // la persona: amb els enllaços del stepper o amb un GO humà registrat
  // (`onDecided`). Una recomanació automàtica, un NO-GO, un ajornament o un
  // POST fallit no canvien de pas. Un refresc o un enllaç directe cauen sempre
  // al mateix lloc.
  const param = searchParams?.get("pas") ?? null;
  const paramStep = param && (STEPS as readonly string[]).includes(param) ? (param as CandidatureStep) : null;
  // L'àrea de treball dins de Preparar. Sense `area`, el dossier: és el que
  // ensenyava fins ara un enllaç `?pas=preparar`, i cap enllaç antic no ha de
  // caure en una pantalla diferent de la que algú va desar.
  const paramArea = searchParams?.get("area") ?? null;
  const area: PrepareArea = isPrepareArea(paramArea) ? paramArea : "dossier";
  // Una guarda pertany a l'àrea que la va registrar. Es descarta en canviar
  // d'àrea i no quan la secció es desmunta: si depengués de la neteja de cada
  // secció, oblidar-se'n en una deixaria l'anterior preguntant per feina que ja
  // no hi és. Va al render i no a un efecte perquè els efectes dels fills
  // s'executen abans que els del pare, i el pare esborraria la guarda que el
  // fill acaba de registrar.
  const guardArea = useRef<PrepareArea | null>(null);
  if (guardArea.current !== area) {
    guardArea.current = area;
    leaveGuard.current = null;
  }
  const [pinned, setPinned] = useState<CandidatureStep | null>(null);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const initialStatus = data?.status ?? null;
  useEffect(() => {
    if (!initialStatus || paramStep || pinned) return;
    const step = defaultStep(initialStatus);
    setPinned(step);
    const next = new URLSearchParams(searchParams?.toString() ?? "");
    next.set("pas", step);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }, [initialStatus, paramStep, pinned, pathname, router, searchParams]);

  if (error && !data) {
    if (status === 404) {
      return (
        <ErrorState
          title="Aquesta candidatura ja no hi és"
          description="L'enllaç és antic o la candidatura s'ha eliminat. La licitació, si encara existeix, continua a Oportunitats."
          action={
            <LinkButton href="/candidatures" variant="primary" size="md">
              Ves a Candidatures
            </LinkButton>
          }
          secondary={
            <LinkButton href="/licitacions" variant="secondary" size="md">
              Obre les oportunitats
            </LinkButton>
          }
        />
      );
    }
    return (
      <ErrorState
        title="No s'ha pogut carregar la candidatura"
        description={error}
        action={
          <Button variant="primary" size="md" onClick={() => void reload()}>
            Torna-ho a provar
          </Button>
        }
        technical={{ code: status !== null ? String(status) : null, method: "GET" }}
      />
    );
  }

  // Només la PRIMERA càrrega ensenya l'esquelet. Si ja hi ha dades, refrescar
  // no pot desmuntar la pàgina: destruiria l'estat local de cada secció i
  // faria parpellejar tota la pantalla a cada clic.
  if (!data) {
    return <PageSkeleton variant="decision" title="Candidatura" context="carregant la candidatura…" />;
  }

  const step = paramStep ?? pinned ?? defaultStep(data.status);
  // Un sol comptador de pendents (A15): el de la preparació final, el mateix que
  // el distintiu del capçal i que Tancar; `null` mentre no se sap.
  const readinessBlockers =
    readiness.data && Array.isArray(readiness.data.findings) ? blockerCount(readiness.data.findings) : null;
  // Recompte per àrea (B6): el comptador del capçal és de les quatre àrees; cada pestanya diu el seu.
  const areaBlockers =
    readiness.data && Array.isArray(readiness.data.findings) ? blockersByArea(readiness.data.findings) : null;
  const items = stepperItems(data, readinessBlockers);
  const banner = primaryBanner(data);
  // La porta d'edició de Preparar i Tancar: només un GO vigent (3B-2 §7.1.6).
  const editable = prepareMode(data) === "edit";
  const signerName = user?.full_name || user?.email || "tu";
  // Qualsevol desat d'una secció ja passava per aquí; ara també torna a
  // preguntar la preparació final, perquè el capçal no quedi dient el que era
  // cert abans de la mutació. Les dues lectures van alhora: són independents.
  const refresh = async () => {
    await Promise.all([reload(), invalidateReadiness()]);
  };
  const hrefFor = (key: CandidatureStep) => {
    const next = new URLSearchParams(searchParams?.toString() ?? "");
    next.set("pas", key);
    return `${pathname}?${next.toString()}`;
  };
  // La porta humana: només una decisió GO desada pel backend obre Preparar.
  // El progrés del stepper continua sortint de l'estat real que torna el
  // refresc; aquí només es canvia el pas que es mira.
  const onDecided = async (decision: WorkspaceDecisionType) => {
    await Promise.all([reload(), invalidateReadiness()]);
    if (decision !== "go") return;
    setPinned("preparar");
    router.replace(hrefFor("preparar"), { scroll: false });
  };
  // El backend ha rebutjat la decisió amb `decision_reason_required`: només es
  // rellegeix el workspace (go_requires_reason i els avisos), sense preparació
  // final, sense navegar i sense canviar el pas que es mira.
  const onRefreshWorkspace = async () => {
    await reload();
  };
  // «Passa a revisió final» acceptat pel backend: es rellegeix el workspace i,
  // només si l'estat real reflecteix la transició, s'obre Tancar. Un advance
  // fallit no arriba aquí.
  const onAdvanced = async () => {
    const [next] = await Promise.all([reload(), invalidateReadiness()]);
    if (next?.status !== "ready_for_final_review") return;
    setPinned("tancar");
    router.replace(hrefFor("tancar"), { scroll: false });
  };
  // El comptador de `lib/deadline` sobre `deadline_at` (A11): el mateix nombre que a la fitxa.
  const countdown = deadlineCountdownView(data.deadline_at);

  return (
    <>
      <nav aria-label="Camí" className="mb-3 text-[13px] text-muted">
        <Link href="/candidatures" className="hover:text-ink">
          Candidatures
        </Link>
        <span aria-hidden> › </span>
        <span>{[data.buyer_name, data.expediente_number].filter(Boolean).join(" · ") || "Candidatura"}</span>
      </nav>

      <PageHeader
        title={data.tender_title ?? "Candidatura"}
        description={
          data.lot_number !== null
            ? `Lot ${data.lot_number}${data.lot_title ? ` · ${data.lot_title}` : ""}`
            : undefined
        }
        actions={
          <>
            {/* L'estat `analysis` s'etiqueta segons l'anàlisi real del plec (A13). */}
            <StatusPill level={WORKSPACE_STATUS_LEVEL[data.status]}>{workspaceStatusLabel(data)}</StatusPill>
            <ReadinessControl />
          </>
        }
      />

      <div className="space-y-5">
        {/* Sempre a la capçalera, fora dels badges, a Decidir, Preparar i Tancar (A16). */}
        <p className="-mt-2 text-[13.5px]">
          <Link href={`/licitacions/${data.tender_id}`} className="font-medium text-brand hover:underline">
            Veure la licitació
          </Link>
          <span className="text-muted"> · la fitxa, els plecs i l&apos;anàlisi del plec</span>
        </p>

        <PhaseStepper items={items} current={step} hrefFor={hrefFor} beforeNavigate={() => leaveGuard.current?.() ?? true} />

        <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-ink">
          <span className="text-muted">Termini:</span>
          {data.deadline_at ? (
            <>
              <StatusPill level={countdown.level} size="sm">
                {countdown.text}
              </StatusPill>
              <span>{formatDateTime(data.deadline_at)}</span>
            </>
          ) : (
            <StatusPill level="neutral" size="sm">
              Termini no consta
            </StatusPill>
          )}
        </p>

        {banner && (
          <Banner
            level={banner.level}
            kicker={banner.kicker}
            action={
              <LinkButton href={banner.action.href} variant="secondary" size="sm">
                {banner.action.label}
              </LinkButton>
            }
          >
            <p>
              <strong>{banner.title}</strong>
              {banner.detail && <span className="text-muted"> {banner.detail}</span>}
            </p>
            {banner.reasons.length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px] leading-5">
                {banner.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </Banner>
        )}

        {step === "decidir" && (
          <DecidirStep
            companyId={companyId}
            data={data}
            onDecided={onDecided}
            onRefreshWorkspace={onRefreshWorkspace}
            signerName={signerName}
          >
            <ContextPanel workspace={data} />
            <DecisionHistory workspace={data} />
          </DecidirStep>
        )}

        {step === "preparar" && (
          <>
            {/* Subordinada als tres passos: la mateixa barra que la fitxa de
                licitació, no una segona navegació principal. */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Tabs
                items={PREPARE_AREAS.map((key) => ({
                  key,
                  label: PREPARE_AREA_LABELS[key],
                  ...(areaBlockers && areaBlockers[key] > 0
                    ? { count: areaBlockers[key], countLabel: pendingCountLabel(areaBlockers[key]), countLevel: "blocking" as const }
                    : {}),
                }))}
                value={area}
                param="area"
                history="push"
                beforeChange={() => leaveGuard.current?.() ?? true}
                onChange={() => undefined}
              />
              {/* A la capçalera i no dins de cap àrea: un dubte és transversal
                  per definició —pot ser d'un requisit, d'una magnitud o d'un
                  preu— i posar-lo a una de les quatre obligaria a triar-ne una
                  d'arbitrària. Les quatre àrees es queden com estaven. */}
              <Button variant="secondary" size="sm" onClick={() => setIssuesOpen(true)}>
                Dubtes i discrepàncies
              </Button>
            </div>

            {!editable && <ReadOnlyNote decideHref={hrefFor("decidir")} />}

            <IssuesDrawer
              workspace={data}
              open={issuesOpen}
              onClose={() => setIssuesOpen(false)}
              readOnly={!editable}
            />

            <PrepareArea editable={editable}>
              {area === "solucio" && <TechnicalSolutionSection workspace={data} registerLeaveGuard={registerLeaveGuard} />}
              {area === "oferta" && <EconomicOfferSection workspace={data} registerLeaveGuard={registerLeaveGuard} />}
              {area === "dossier" && <DossierSection workspace={data} onChange={refresh} onAdvanced={onAdvanced} />}
              {area === "pla" && (
                <>
                  <WorkPlanDeadlines workspace={data} />
                  <ChecklistSection workspace={data} onChange={refresh} />
                  <TasksSection workspace={data} onChange={refresh} />
                  <NotesSection workspace={data} onChange={refresh} />
                </>
              )}
            </PrepareArea>
          </>
        )}

        {step === "tancar" && (
          <>
            {/* El paquet de presentació també és una generació del pas Preparar (§7.1.6). */}
            {!editable && <ReadOnlyNote decideHref={hrefFor("decidir")} />}
            <PrepareArea editable={editable}>
              <SubmissionPackageSection workspace={data} onChange={refresh} signerName={signerName} />
            </PrepareArea>
          </>
        )}

        <Card>
          <CardHeader title="Enllaços" />
          <div className="flex flex-wrap gap-4 px-3.5 py-3 text-[13px]">
            <Link href={`/licitacions/${data.tender_id}`} className="text-brand hover:underline">
              Veure la licitació i el plec
            </Link>
            <Link href="/empresa" className="text-brand hover:underline">
              Editar el perfil d&apos;empresa
            </Link>
            <Link href="/documents" className="text-brand hover:underline">
              Documents de l&apos;empresa
            </Link>
            {data.source_url && (
              <a href={data.source_url} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">
                Expedient a la plataforma oficial ↗
              </a>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
