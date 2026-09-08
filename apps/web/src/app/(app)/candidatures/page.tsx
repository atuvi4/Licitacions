"use client";

import Link from "next/link";

import { useSession } from "@/components/SessionProvider";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Skeleton,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { RECOMMENDATION_LABELS } from "@/lib/compliance";
import { useResource } from "@/lib/hooks";
import {
  DECISION_LABELS,
  DECISION_TONES,
  WORKSPACE_STATUS_LABELS,
  WORKSPACE_STATUS_TONES,
  deadlineLabel,
  deadlineTone,
  type WorkspaceSummary,
} from "@/lib/workspace";

/**
 * Llistat de candidatures.
 *
 * Les columnes «Recomanació» i «Decisió» són dues, no una: es pot veure d'un
 * cop d'ull on el sistema deia una cosa i la persona n'ha decidit una altra.
 */
export default function WorkspacesPage() {
  const { companyId } = useSession();
  const { data, loading, error, reload } = useResource<WorkspaceSummary[]>(
    companyId ? `/companies/${companyId}/workspaces` : null,
  );

  if (error) {
    return (
      <>
        <PageHeader title="Candidatures" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Candidatures"
        description="Les licitacions a què heu obert workspace. La decisió final sempre és humana."
      />

      {loading || !data ? (
        <Skeleton className="h-64 w-full" />
      ) : data.length === 0 ? (
        <EmptyState
          title="Cap candidatura oberta"
          description="Obre un workspace des de la fitxa d'una licitació per començar a treballar-hi."
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <Tr>
                <Th>Licitació</Th>
                <Th className="w-32">Termini</Th>
                <Th className="w-20">Pre-fit</Th>
                <Th className="w-36">Recomanació</Th>
                <Th className="w-32">Decisió</Th>
                <Th className="w-40">Estat</Th>
              </Tr>
            </thead>
            <tbody>
              {data.map((workspace) => (
                <Tr key={workspace.id}>
                  <Td>
                    <Link
                      href={`/candidatures/${workspace.id}`}
                      className="text-ink hover:text-brand hover:underline"
                    >
                      {workspace.tender_title ?? "Sense títol"}
                    </Link>
                    <p className="mt-0.5 text-[12px] text-faint">
                      {[workspace.expediente_number, workspace.buyer_name]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </p>
                  </Td>
                  <Td>
                    <Badge tone={deadlineTone(workspace.days_remaining)}>
                      {deadlineLabel(workspace.days_remaining) ?? "Sense termini"}
                    </Badge>
                  </Td>
                  <Td>
                    <span className="tabular-nums">{workspace.prefit_score ?? "—"}</span>
                  </Td>
                  <Td>
                    <span className="text-[13px] text-muted">
                      {workspace.recommendation
                        ? RECOMMENDATION_LABELS[workspace.recommendation]
                        : "—"}
                    </span>
                  </Td>
                  <Td>
                    {workspace.human_decision ? (
                      <Badge tone={DECISION_TONES[workspace.human_decision]}>
                        {DECISION_LABELS[workspace.human_decision]}
                      </Badge>
                    ) : (
                      <span className="text-[13px] text-warning">PENDENT</span>
                    )}
                  </Td>
                  <Td>
                    <Badge tone={WORKSPACE_STATUS_TONES[workspace.status]}>
                      {WORKSPACE_STATUS_LABELS[workspace.status]}
                    </Badge>
                    {workspace.analysis_outdated && (
                      <Badge tone="warning" className="ml-1">
                        Anàlisi endarrerida
                      </Badge>
                    )}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
