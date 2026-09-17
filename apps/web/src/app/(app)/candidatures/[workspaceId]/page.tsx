"use client";

import { Check } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { SubmissionPackageSection } from "@/components/SubmissionPackage";
import { DecisionHistory, DecisionPanel } from "@/components/WorkspaceDecision";
import { DossierSection } from "@/components/WorkspaceDossier";
import { BlockerCounters, ChecklistSection, NotesSection, TasksSection } from "@/components/WorkspaceWork";
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
  cn,
} from "@/components/ui";
import { ANALYSIS_BASIS_LABELS, ELIGIBILITY_LABELS, RECOMMENDATION_LABELS } from "@/lib/compliance";
import { useResource } from "@/lib/hooks";
import {
  ANALYSIS_BASIS_LEVEL,
  ELIGIBILITY_LEVEL,
  RECOMMENDATION_LEVEL,
  WORKSPACE_STATUS_LEVEL,
  deadlineLevel,
} from "@/lib/labels";
import { formatDateTime } from "@/lib/tenders";
import {
  WORKSPACE_STATUS_LABELS,
  deadlineLabel,
  type WorkspaceDecisionType,
  type WorkspaceDetail,
} from "@/lib/workspace";

import { STEPS, defaultStep, primaryBanner, stepperItems, type CandidatureStep, type StepItem } from "./steps";

/**
 * Decidir → Preparar → Tancar. El progrés surt de l'estat real; el pas que
 * es mira, de la URL (`?pas=`). Enllaços de debò: un enllaç directe, un
 * refresc i el botó enrere funcionen sense cap estat local.
 */
function PhaseStepper({
  items,
  current,
  hrefFor,
}: {
  items: StepItem[];
  current: CandidatureStep;
  hrefFor: (step: CandidatureStep) => string;
}) {
  const active = items.find((item) => item.key === current) ?? items[0];
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

/** Context de la decisió: les quatre coses que no es fusionen, una per fila. */
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
    {
      label: "Elegibilitat",
      value: workspace.eligibility ? (
        <>
          <StatusPill level={ELIGIBILITY_LEVEL[workspace.eligibility]} size="sm">
            {ELIGIBILITY_LABELS[workspace.eligibility]}
          </StatusPill>
          <span className="ml-1.5 text-muted">podem acreditar que complim?</span>
        </>
      ) : (
        <span className="text-muted">Sense càlcul</span>
      ),
    },
    {
      label: "Recomanació",
      value: workspace.recommendation ? (
        <>
          <StatusPill level={RECOMMENDATION_LEVEL[workspace.recommendation]} size="sm">
            {RECOMMENDATION_LABELS[workspace.recommendation]}
          </StatusPill>
          <span className="ml-1.5 text-muted">automàtica · no és una decisió</span>
        </>
      ) : (
        <span className="text-muted">Sense càlcul</span>
      ),
    },
    {
      label: "Anàlisi",
      value: (
        <>
          <StatusPill level={workspace.analysis_outdated ? "attention" : ANALYSIS_BASIS_LEVEL[workspace.analysis_basis]} size="sm">
            {ANALYSIS_BASIS_LABELS[workspace.analysis_basis]}
            {workspace.analysis_outdated && " · endarrerida"}
          </StatusPill>
        </>
      ),
    },
  ];
  if (workspace.lot_number !== null) {
    rows.splice(1, 0, {
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

export default function WorkspacePage() {
  const params = useParams<{ workspaceId: string }>();
  const { companyId, user } = useSession();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { data, error, status, reload } = useResource<WorkspaceDetail>(
    companyId ? `/companies/${companyId}/workspaces/${params.workspaceId}` : null,
  );

  // El pas que es mira surt de la URL. Si la URL no ho diu, la primera càrrega
  // el fixa (a la URL i aquí) a partir de l'estat real. Després només el mou
  // la persona: amb els enllaços del stepper o amb un GO humà registrat
  // (`onDecided`). Una recomanació automàtica, un NO-GO, un ajornament o un
  // POST fallit no canvien de pas. Un refresc o un enllaç directe cauen sempre
  // al mateix lloc.
  const param = searchParams?.get("pas") ?? null;
  const paramStep = param && (STEPS as readonly string[]).includes(param) ? (param as CandidatureStep) : null;
  const [pinned, setPinned] = useState<CandidatureStep | null>(null);
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
  const items = stepperItems(data);
  const banner = primaryBanner(data);
  const signerName = user?.full_name || user?.email || "tu";
  const refresh = async () => {
    await reload();
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
    await reload();
    if (decision !== "go") return;
    setPinned("preparar");
    router.replace(hrefFor("preparar"), { scroll: false });
  };
  const days = deadlineLabel(data.days_remaining);

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
            <StatusPill level={WORKSPACE_STATUS_LEVEL[data.status]}>{WORKSPACE_STATUS_LABELS[data.status]}</StatusPill>
            <LinkButton href={`/licitacions/${data.tender_id}`} variant="secondary" size="sm">
              Obre la fitxa de la licitació →
            </LinkButton>
          </>
        }
      />

      <div className="space-y-5">
        <PhaseStepper items={items} current={step} hrefFor={hrefFor} />

        <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-ink">
          <span className="text-muted">Termini:</span>
          {data.deadline_at ? (
            <>
              <StatusPill level={deadlineLevel(data.days_remaining)} size="sm">
                {days ?? formatDateTime(data.deadline_at)}
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
          <div className="grid gap-5 lg:grid-cols-12">
            <div className="lg:col-span-7">
              <DecisionPanel workspace={data} onDecided={onDecided} signerName={signerName} />
            </div>
            <div className="flex flex-col gap-4 lg:col-span-5">
              <BlockerCounters workspace={data} />
              <ContextPanel workspace={data} />
              <DecisionHistory workspace={data} />
            </div>
          </div>
        )}

        {step === "preparar" && (
          <>
            <DossierSection workspace={data} onChange={refresh} />
            <ChecklistSection workspace={data} onChange={refresh} />
            <TasksSection workspace={data} onChange={refresh} />
            <NotesSection workspace={data} onChange={refresh} />
          </>
        )}

        {step === "tancar" && <SubmissionPackageSection workspace={data} onChange={refresh} />}

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
