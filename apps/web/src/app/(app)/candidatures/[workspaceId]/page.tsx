"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { DecisionHistory, DecisionPanel } from "@/components/WorkspaceDecision";
import {
  BlockerCounters,
  ChecklistSection,
  NotesSection,
  TasksSection,
} from "@/components/WorkspaceWork";
import { useSession } from "@/components/SessionProvider";
import { Badge, Card, ErrorNotice, PageHeader, Skeleton } from "@/components/ui";
import { ELIGIBILITY_LABELS, ELIGIBILITY_TONES, RECOMMENDATION_LABELS } from "@/lib/compliance";
import { useResource } from "@/lib/hooks";
import { formatDateTime } from "@/lib/tenders";
import {
  DECISION_LABELS,
  DECISION_TONES,
  OUTDATED_LABELS,
  WORKSPACE_STATUS_LABELS,
  WORKSPACE_STATUS_TONES,
  deadlineLabel,
  deadlineTone,
  type WorkspaceDetail,
} from "@/lib/workspace";

function Metric({
  label,
  value,
  hint,
  children,
}: {
  label: string;
  value?: string | number | null;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-[12px] uppercase tracking-wide text-muted">{label}</p>
      <div className="mt-1 text-lg font-semibold text-ink">{children ?? value ?? "—"}</div>
      {hint && <p className="mt-0.5 text-[12px] text-faint">{hint}</p>}
    </div>
  );
}

export default function WorkspacePage() {
  const params = useParams<{ workspaceId: string }>();
  const { companyId } = useSession();
  const { data, loading, error, reload } = useResource<WorkspaceDetail>(
    companyId ? `/companies/${companyId}/workspaces/${params.workspaceId}` : null,
  );

  if (error) {
    return (
      <>
        <PageHeader title="Candidatura" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  if (loading || !data) {
    return (
      <>
        <PageHeader title="Candidatura" />
        <Skeleton className="h-96 w-full" />
      </>
    );
  }

  const refresh = async () => {
    await reload();
  };
  const days = deadlineLabel(data.days_remaining);

  return (
    <>
      <Link
        href="/candidatures"
        className="mb-3 inline-block text-[13px] text-muted hover:text-ink"
      >
        ← Torna a Candidatures
      </Link>

      <PageHeader
        title={data.tender_title ?? "Candidatura"}
        description={
          [data.expediente_number, data.buyer_name].filter(Boolean).join(" · ") || undefined
        }
        actions={
          <div className="flex items-center gap-2">
            {data.lot_number !== null && <Badge tone="neutral">Lot {data.lot_number}</Badge>}
            <Badge tone={WORKSPACE_STATUS_TONES[data.status]}>
              {WORKSPACE_STATUS_LABELS[data.status]}
            </Badge>
          </div>
        }
      />

      <div className="space-y-4">
        {/* El termini mai s'amaga, ni quan ja ha passat. */}
        {data.deadline_passed && (
          <div className="rounded-[var(--radius-card)] border border-danger/40 bg-danger-soft px-5 py-4">
            <p className="text-sm font-semibold text-danger">
              El termini de presentació ja ha passat
            </p>
            <p className="mt-1 text-[13px] text-ink">
              Va vèncer el {formatDateTime(data.deadline_at)}. El workspace segueix consultable i
              es pot deixar constància del que es va decidir, però qualsevol GO a partir d&apos;ara
              exigeix motiu escrit.
            </p>
          </div>
        )}

        {data.analysis_outdated && (
          <div className="rounded-[var(--radius-card)] border border-warning/40 bg-warning-soft px-5 py-4">
            <p className="text-sm font-semibold text-warning">L&apos;anàlisi ha quedat endarrerida</p>
            <ul className="mt-1 space-y-0.5">
              {data.outdated_reasons.map((code) => (
                <li key={code} className="text-[13px] text-ink">
                  {OUTDATED_LABELS[code] ?? code}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-[12px] text-muted">
              No s&apos;ha substituït res: la decisió i la feina fetes es conserven. Prem
              «Reconcilia amb l&apos;anàlisi» a la checklist quan vulguis incorporar-ho.
            </p>
          </div>
        )}

        {/* ------------------------------------------------- Capçalera --- */}
        <Card>
          <div className="grid gap-4 px-5 py-5 sm:grid-cols-2 lg:grid-cols-5">
            <Metric label="Termini">
              <span className="text-base">
                {data.deadline_at ? formatDateTime(data.deadline_at) : "—"}
              </span>
              {days && (
                <Badge tone={deadlineTone(data.days_remaining)} className="ml-2">
                  {days}
                </Badge>
              )}
            </Metric>
            <Metric
              label="Pre-fit"
              value={data.prefit_score}
              hint="Ens interessa? (metadades)"
            />
            <Metric label="Elegibilitat" hint="Podem acreditar que complim?">
              {data.eligibility ? (
                <Badge tone={ELIGIBILITY_TONES[data.eligibility]}>
                  {ELIGIBILITY_LABELS[data.eligibility]}
                </Badge>
              ) : (
                "Sense càlcul"
              )}
            </Metric>
            <Metric
              label="Recomanació"
              hint="El que suggereix el sistema"
              value={
                data.recommendation ? RECOMMENDATION_LABELS[data.recommendation] : "Sense càlcul"
              }
            />
            <Metric label="Decisió humana" hint="L&apos;única oficial">
              {data.human_decision ? (
                <Badge tone={DECISION_TONES[data.human_decision]}>
                  {DECISION_LABELS[data.human_decision]}
                </Badge>
              ) : (
                <span className="text-warning">PENDENT</span>
              )}
            </Metric>
          </div>
        </Card>

        <DecisionPanel workspace={data} onDecided={refresh} />
        <BlockerCounters workspace={data} />
        <ChecklistSection workspace={data} onChange={refresh} />
        <TasksSection workspace={data} onChange={refresh} />
        <NotesSection workspace={data} onChange={refresh} />
        <DecisionHistory workspace={data} />

        {/* ----------------------------------------------------- Enllaços --- */}
        <Card>
          <div className="flex flex-wrap gap-4 px-5 py-4 text-[13px]">
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
              <a
                href={data.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand hover:underline"
              >
                Expedient a la plataforma oficial ↗
              </a>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
