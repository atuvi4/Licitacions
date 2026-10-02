"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useSession } from "@/components/SessionProvider";
import { Banner, Button, Card, CardHeader, EmptyState, ErrorNotice, LoadingRows, PermissionNote, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  MISSING_DESTINATION,
  MISSING_TEXT,
  analysisPath,
  type AnalysisRun,
  type AnalysisWriteResult,
  type TenderStructureView,
} from "@/lib/analysis";
import { formatDateTime } from "@/lib/labels";
import { useResource } from "@/lib/hooks";
import type { KnowledgeRecords, Vocabulary } from "@/lib/knowledge";
import type { AwardCriterion, Requirement, RequirementAnalysis } from "@/lib/requirements";
import { canReview, whoCanReview } from "@/lib/review";
import type { Service } from "@/lib/types";

import { AnalysisSummary } from "./AnalysisSummary";
import { CriteriaPoints } from "./CriteriaPoints";
import { DecisionFooter } from "./DecisionFooter";
import { Delivery } from "./Delivery";
import { Questions } from "./Questions";
import { RequirementsFit } from "./RequirementsFit";
import { ResolveBeforeDeciding } from "./ResolveBeforeDeciding";

export interface AnalysisLookup {
  requirements: Map<string, Requirement>;
  criteria: Map<string, AwardCriterion>;
  structure: TenderStructureView | null;
  /** Error real de la lectura de l'estructura (no «encara es carrega»). */
  structureFailed: boolean;
  vocabulary: Vocabulary | null;
  services: Map<string, Service>;
  partnerRoles: Map<string, { role: string; partner: string | null }>;
}

function code(error: unknown): string | null {
  return error && typeof error === "object" && "code" in error ? String((error as { code: unknown }).code) : null;
}

function missingOf(error: unknown): string[] {
  const details = error instanceof ApiError ? error.details : (error as { details?: Record<string, unknown> })?.details;
  const missing = details?.missing;
  return Array.isArray(missing) ? missing.map(String) : [];
}

function writeErrorText(error: unknown): string | null {
  switch (code(error)) {
    case "analysis_prerequisites_missing":
      return null; // es pinta amb `missingOf`
    case "analysis_run_conflict":
      return "S'ha recalculat alhora des d'un altre lloc: torna-ho a provar.";
    case "permission_denied":
      return `El teu rol no pot recalcular l'anàlisi: ho fa ${whoCanReview()}.`;
    default:
      return "No s'ha pogut recalcular l'anàlisi. Torna-ho a provar.";
  }
}

export function AnalysisTab({
  companyId,
  tenderId,
  requirements,
  requirementsFailed = false,
  workspaceId,
  candidatureChecked,
}: {
  companyId: string | null;
  tenderId: string;
  requirements: RequirementAnalysis | null;
  /** La lectura de requisits de la pàgina ha fallat de veritat. `requirements` nul i sense error = encara es carrega. */
  requirementsFailed?: boolean;
  workspaceId: string | null;
  candidatureChecked: boolean;
}) {
  const session = useSession() as { membership?: { role?: string } };
  const mayRecalculate = canReview(session.membership?.role);
  const base = companyId ? analysisPath(companyId, tenderId) : null;
  const current = useResource<AnalysisRun>(base ? `${base}/current` : null);
  const structure = useResource<TenderStructureView>(companyId ? `/companies/${companyId}/tenders/${tenderId}/structure` : null);
  const vocabulary = useResource<Vocabulary>(companyId ? `/companies/${companyId}/knowledge/vocabulary` : null);
  const services = useResource<Service[]>(companyId ? `/companies/${companyId}/services` : null);
  const records = useResource<KnowledgeRecords>(companyId ? `/companies/${companyId}/knowledge/records` : null);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [writeError, setWriteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState("");
  const root = useRef<HTMLDivElement>(null);
  // El botó d'acció es torna a muntar (estat buit → vista completa, o nota de permís): el focus hi torna.
  const focusAction = useRef(false);

  const shown = run ?? current.data ?? null;
  const notFound = current.status === 404;

  const lookup = useMemo<AnalysisLookup>(() => {
    const roles = new Map<string, { role: string; partner: string | null }>();
    const partners = new Map((records.data?.partners ?? []).map((p) => [p.id, p.name]));
    for (const role of records.data?.partner_roles ?? []) roles.set(role.id, { role: role.role, partner: partners.get(role.partner_id) ?? null });
    return {
      requirements: new Map((requirements?.requirements ?? []).map((r) => [r.id, r])),
      criteria: new Map([...(requirements?.award_criteria ?? []), ...(requirements?.award_candidates ?? [])].map((c) => [c.id, c])),
      structure: structure.data ?? null,
      structureFailed: Boolean(structure.error),
      vocabulary: vocabulary.data ?? null,
      services: new Map((services.data ?? []).map((s) => [s.id, s])),
      partnerRoles: roles,
    };
  }, [requirements, structure.data, structure.error, vocabulary.data, services.data, records.data]);

  useEffect(() => {
    if (!focusAction.current) return;
    focusAction.current = false;
    root.current?.querySelector<HTMLElement>("[data-analysis-action]")?.focus();
  }, [run, writeError]);

  async function recalculate() {
    if (!base || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setWriteError(null);
    setAnnouncement("");
    try {
      const result = await api.post<AnalysisWriteResult>(`${base}/runs`, {});
      // `reused` vol dir «cap anàlisi nova escrita», no «la mateixa que hi havia a la pantalla».
      const same = result.reused && result.id === shown?.id;
      // El primer «Analitza» desmunta l'estat buit: el focus passa a «Recalcula».
      if (!shown) focusAction.current = true;
      setRun(result);
      setAnnouncement(same ? "Les dades no han canviat: és la mateixa anàlisi." : "Anàlisi actualitzada.");
    } catch (error) {
      const denied = code(error) === "permission_denied";
      // Un 403 embolcalla el botó amb la nota de permís: el focus hi torna.
      if (denied) focusAction.current = true;
      setWriteError(error);
      setAnnouncement(denied ? (writeErrorText(error) ?? "") : "No s'ha pogut recalcular l'anàlisi.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const denied = code(writeError) === "permission_denied";
  const missing = code(writeError) === "analysis_prerequisites_missing" ? missingOf(writeError) : [];
  const otherError = writeError && !denied && missing.length === 0 ? writeErrorText(writeError) : null;
  // Només errors reals: mentre una lectura encara es carrega no es diu que ha fallat.
  const secondaryFailed = Boolean(structure.error || services.error || records.error || vocabulary.error || requirementsFailed);

  // Mai `disabled` natiu mentre treballa: un botó amb el focus que es desactiva el perd (spec §5.6).
  // `inFlight` impedeix el doble POST; `aria-disabled` i `aria-busy` ho diuen.
  const actionButton = (text: string) => (
    <Button
      variant={shown ? "secondary" : "primary"}
      aria-busy={busy}
      aria-disabled={busy || denied}
      data-analysis-action=""
      className="aria-disabled:cursor-not-allowed aria-disabled:opacity-45"
      onClick={() => {
        if (!denied) void recalculate();
      }}
    >
      {busy && (
        <span aria-hidden="true" className="inline-flex">
          <Spinner className="h-3.5 w-3.5" />
        </span>
      )}
      {text}
    </Button>
  );
  const permission = (children: ReactNode) => (
    <PermissionNote who={whoCanReview()} what="analitzar o recalcular l'anàlisi">
      {children}
    </PermissionNote>
  );
  const action = (text: string) =>
    !mayRecalculate
      ? permission(
          <Button variant={shown ? "secondary" : "primary"} disabled>
            {text}
          </Button>,
        )
      : denied
        ? permission(actionButton(text))
        : actionButton(text);

  const live = (
    <p role="status" aria-live="polite" aria-label="Estat del recàlcul" className="sr-only">
      {announcement}
    </p>
  );
  const missingBlock = missing.length > 0 && (
    <div role="alert" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
      {shown && <p className="font-medium">No es pot recalcular fins que es resolgui:</p>}
      <ul className="mt-1 list-disc pl-5">
        {missing.map((m) => {
          const destination = MISSING_DESTINATION[m]?.(tenderId);
          return (
            <li key={m}>
              {MISSING_TEXT[m] ?? "Falta una dada necessària per analitzar."}
              {destination && (
                <>
                  {" "}
                  <Link href={destination.href} className="inline-flex min-h-11 items-center font-medium text-brand underline md:min-h-0">
                    {destination.label}
                  </Link>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
  const errorBlock = otherError && (
    <div role="alert">
      <ErrorNotice message={otherError} />
    </div>
  );

  if (!companyId) return null;
  return (
    <div ref={root} className="flex min-w-0 flex-col gap-4">
      {live}
      {renderBody(companyId)}
    </div>
  );

  function renderBody(companyId: string) {
  if (current.loading && !shown && !notFound && !current.error) {
    return (
      <Card>
        <LoadingRows rows={4} />
      </Card>
    );
  }
  if (!shown && current.error && !notFound) {
    return <ErrorNotice message="No s'ha pogut carregar l'anàlisi." onRetry={() => void current.reload()} />;
  }
  if (!shown) {
    return (
      <Card>
        <EmptyState
          title="Encara no hi ha cap anàlisi d'aquesta licitació"
          description="L'anàlisi contrasta els requisits del plec amb el que se sap de l'empresa i explica què es compleix, què no i què no se sap. No decideix per tu: la decisió de presentar-s'hi és vostra."
          action={action("Analitza")}
        />
        <div className="px-5 pb-5 space-y-3">
          {missingBlock}
          {errorBlock}
        </div>
      </Card>
    );
  }

  const stale = run ? false : Boolean(shown.stale);
  return (
    <>
      <Card>
        <CardHeader
          title="Anàlisi del plec"
          description={`Feta el ${formatDateTime(shown.created_at)}. Tuvi explica què sap i què no: la decisió és vostra.`}
          action={action("Recalcula")}
        />
        <div className="space-y-3 px-5 pb-5">
          {stale && (
            <Banner level="attention" kicker="Desactualitzada" action={null}>
              Alguna dada ha canviat des d&apos;aquesta anàlisi (el plec, la revisió o el coneixement de l&apos;empresa). Recalcula-la per veure-la al dia.
            </Banner>
          )}
          {missingBlock}
          {errorBlock}
          {secondaryFailed && (
            <p className="text-caption text-muted">Algunes dades complementàries no s&apos;han pogut carregar: alguns noms poden no aparèixer.</p>
          )}
          <AnalysisSummary run={shown} />
        </div>
      </Card>
      <ResolveBeforeDeciding run={shown} lookup={lookup} tenderId={tenderId} />
      <RequirementsFit run={shown} lookup={lookup} tenderId={tenderId} />
      <CriteriaPoints run={shown} lookup={lookup} tenderId={tenderId} />
      <Delivery run={shown} lookup={lookup} tenderId={tenderId} />
      <Questions run={shown} lookup={lookup} tenderId={tenderId} />
      <DecisionFooter companyId={companyId} tenderId={tenderId} workspaceId={workspaceId} checked={candidatureChecked} />
    </>
  );
  }
}
