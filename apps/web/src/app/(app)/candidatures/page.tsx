"use client";

import { FileCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ScoreValue } from "@/components/PreFit";
import { useSession } from "@/components/SessionProvider";
import { Button, Card, ErrorState, LinkButton, PageHeader, PageSkeleton, StatusPill } from "@/components/ui";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { useResource } from "@/lib/hooks";
import {
  RECOMMENDATION_LABELS,
  WORKSPACE_STATUS_LEVEL,
  deadlineCountdownView,
} from "@/lib/labels";
import type { MatchSummary } from "@/lib/matching";
import { workspaceStatusLabel, type WorkspaceSummary } from "@/lib/workspace";

import { attentionPills, decisionPill, groupWorkspaces, nextStep, summaryLine } from "./groups";

/**
 * Llistat de candidatures (handoff L7): en quin pas és cada una i què li
 * falta, sense fusionar el resultat del motor i la decisió. Les columnes
 * «Resultat de l'anàlisi» i «Decisió» són dues, no una: es pot veure d'un cop
 * d'ull on el motor deia una cosa i la persona n'ha decidit una altra (A14: el
 * motor no recomana ni diu GO/NO-GO; això només ho diu una persona).
 */

// A14: el motor dona un «Resultat de l'anàlisi», no una recomanació; la decisió és humana.
const COLUMN_HEADERS = ["Licitació", "Termini", "Encaix", "Resultat de l'anàlisi", "Decisió", "Estat", "Acció"];
const ACTION_CLASS = "min-h-11 w-full justify-center md:min-h-0 md:w-auto";

/** El comptador de `lib/deadline` sobre `deadline_at` (A11): el mateix nombre que a la fitxa. */
function DeadlineCell({ workspace }: { workspace: WorkspaceSummary }) {
  const countdown = deadlineCountdownView(workspace.deadline_at);
  return (
    <StatusPill level={countdown.level} size="sm">
      {countdown.text}
    </StatusPill>
  );
}

export default function WorkspacesPage() {
  const { companyId } = useSession();
  const router = useRouter();
  const list = useResource<WorkspaceSummary[]>(companyId ? `/companies/${companyId}/workspaces` : null);
  // Només per al buit B2: quantes candidates hi ha per obrir.
  const summary = useResource<MatchSummary>(companyId ? `/companies/${companyId}/matches/summary` : null);

  if (list.error && !list.data) {
    return (
      <>
        <PageHeader title="Candidatures" />
        <ErrorState
          title="No hem pogut carregar les candidatures"
          description="La connexió amb el servei ha fallat. No s'ha perdut res: les decisions i els dossiers continuen desats."
          action={
            <Button variant="primary" onClick={() => void list.reload()}>
              Torna-ho a provar
            </Button>
          }
          technical={{ method: "GET /workspaces", code: list.error }}
        />
      </>
    );
  }

  if (!list.data) {
    return (
      <PageSkeleton
        variant="table"
        title="Candidatures"
        context="carregant les candidatures…"
        columns={COLUMN_HEADERS}
      />
    );
  }

  const workspaces = list.data;
  const groups = groupWorkspaces(workspaces);
  const firstOpenId = groups[0]?.rows[0]?.id;
  const candidates = summary.data?.candidates ?? null;

  const columns: Array<DataTableColumn<WorkspaceSummary>> = [
    {
      key: "tender",
      header: "Licitació",
      cell: (workspace) => (
        <div className="min-w-0">
          <p className="font-medium text-ink">
            <Link
              href={`/candidatures/${workspace.id}`}
              onClick={(event) => event.stopPropagation()}
              className="hover:text-brand-strong"
            >
              {workspace.tender_title ?? workspace.expediente_number ?? "Sense títol"}
            </Link>
          </p>
          <p className="text-caption text-muted">
            {[workspace.expediente_number, workspace.buyer_name, workspace.tender_lot_id ? "Per lot" : null]
              .filter(Boolean)
              .join(" · ") || "—"}
          </p>
        </div>
      ),
    },
    { key: "deadline", header: "Termini", width: "14%", cell: (workspace) => <DeadlineCell workspace={workspace} /> },
    {
      key: "fit",
      hideBelow: "lg",
      header: "Encaix",
      width: "8%",
      cell: (workspace) => <ScoreValue score={workspace.prefit_score} size="sm" />,
    },
    {
      key: "recommendation",
      header: "Resultat de l'anàlisi",
      width: "13%",
      cell: (workspace) => (
        <span className="text-[13px] text-muted">
          {workspace.recommendation ? RECOMMENDATION_LABELS[workspace.recommendation] : "—"}
        </span>
      ),
    },
    {
      key: "decision",
      header: "Decisió",
      width: "10%",
      cell: (workspace) => {
        const pill = decisionPill(workspace);
        return (
          <StatusPill level={pill.level} size="sm">
            {pill.label}
          </StatusPill>
        );
      },
    },
    {
      key: "status",
      header: "Estat",
      width: "17%",
      cell: (workspace) => (
        <div className="flex flex-wrap items-center gap-1">
          <StatusPill level={WORKSPACE_STATUS_LEVEL[workspace.status]} size="sm">
            {workspaceStatusLabel(workspace)}
          </StatusPill>
          {attentionPills(workspace).map((pill) => (
            <StatusPill key={pill.label} level={pill.level} size="sm">
              {pill.label}
            </StatusPill>
          ))}
        </div>
      ),
    },
    {
      key: "action",
      header: "Acció",
      width: "12%",
      align: "right",
      cell: (workspace) => (
        <LinkButton
          href={`/candidatures/${workspace.id}`}
          variant={workspace.id === firstOpenId ? "primary" : "secondary"}
          size="sm"
          className={ACTION_CLASS}
        >
          {nextStep(workspace)}
        </LinkButton>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Candidatures"
        description="Les licitacions que heu decidit mirar de prop. El resultat de l'anàlisi és automàtic; la decisió sempre és d'una persona."
      />

      {workspaces.length === 0 ? (
        <Card>
          <EmptyState
            icon={<FileCheck />}
            title="Cap candidatura oberta"
            description={
              "Una candidatura es crea des d'una oportunitat, quan decidiu que val la pena mirar-la de prop." +
              (candidates ? ` Ara mateix n'hi ha ${candidates} amb encaix que no heu obert.` : "")
            }
            action={
              candidates ? (
                <LinkButton href="/licitacions?match_status=candidate" variant="primary" size="md">
                  Veure les {candidates} amb més encaix
                </LinkButton>
              ) : (
                <LinkButton href="/licitacions" variant="primary" size="md">
                  Veure Oportunitats
                </LinkButton>
              )
            }
          />
        </Card>
      ) : (
        <Card>
          <DataTable<WorkspaceSummary>
            columns={columns}
            rows={workspaces}
            rowKey={(workspace) => workspace.id}
            groups={groups}
            onRowClick={(workspace) => router.push(`/candidatures/${workspace.id}`)}
            stackBelow="md"
            footer={<span>{summaryLine(workspaces)}</span>}
          />
        </Card>
      )}
    </>
  );
}
