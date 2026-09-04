"use client";

import Link from "next/link";

import { useCompanyPath, useSession } from "@/components/SessionProvider";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Skeleton,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { PreFitDisclaimer } from "@/components/PreFit";
import { formatDate, TEMPORAL_STATUS_LABELS } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import { formatDateTime } from "@/lib/tenders";
import type { MatchSummary } from "@/lib/matching";
import type { CompletenessSection, DashboardSummary } from "@/lib/types";

const SECTION_LINKS: Record<string, string> = {
  general: "/empresa",
  services: "/empresa/serveis",
  territories: "/empresa/territoris",
  cpv: "/empresa/cpv",
  solvency: "/empresa/solvencia",
  certifications: "/empresa/certificacions",
  registrations: "/empresa/registres",
  insurances: "/empresa/assegurances",
  team: "/empresa/equip",
  experience: "/experiencia",
  documents: "/documents",
};

const STATUS_MARK: Record<CompletenessSection["status"], { label: string; tone: "success" | "warning" | "danger" }> = {
  complete: { label: "Complet", tone: "success" },
  partial: { label: "Parcial", tone: "warning" },
  empty: { label: "Buit", tone: "danger" },
};

function CountCard({ label, value, href }: { label: string; value: number; href: string }) {
  return (
    <Link
      href={href}
      className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3 transition-colors hover:border-border-strong"
    >
      <p className="text-[12px] text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{value}</p>
    </Link>
  );
}

/** Xifra del pre-fit. Sense enllaç si no hi ha res a mirar. */
function PreFitCount({
  label,
  value,
  hint,
  href,
  tone,
}: {
  label: string;
  value: number;
  hint: string;
  href?: string;
  tone?: "success" | "warning";
}) {
  const body = (
    <>
      <p className="text-[12px] text-muted">{label}</p>
      <p
        className={
          "mt-1 text-2xl font-semibold tabular-nums " +
          (tone === "success" && value > 0
            ? "text-success"
            : tone === "warning" && value > 0
              ? "text-warning"
              : "text-ink")
        }
      >
        {value}
      </p>
      <p className="mt-0.5 text-[11px] text-faint">{hint}</p>
    </>
  );
  const className =
    "block rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3";
  return href ? (
    <Link href={href} className={`${className} transition-colors hover:border-border-strong`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

export default function DashboardPage() {
  const { membership } = useSession();
  const path = useCompanyPath();
  const { data, loading, error, reload } = useResource<DashboardSummary>(path("/summary"));
  const prefit = useResource<MatchSummary>(path("/matches/summary"));

  if (error) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <ErrorNotice message={error} onRetry={() => void reload()} />
      </>
    );
  }

  if (loading || !data) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-44 lg:col-span-2" />
          <Skeleton className="h-44" />
        </div>
      </>
    );
  }

  const { completeness, counts, expiry_alerts: alerts } = data;

  return (
    <>
      <PageHeader
        title={membership.company.trade_name ?? membership.company.legal_name}
        description="Estat del perfil acreditable de l'empresa."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Completitud del perfil"
            description="Càlcul determinista a partir de les dades introduïdes. No hi intervé cap IA."
          />
          <div className="px-5 py-4">
            <div className="mb-4 flex items-end gap-3">
              <span className="text-3xl font-semibold tabular-nums text-ink">
                {completeness.score}%
              </span>
              <div className="mb-1.5 h-2 flex-1 overflow-hidden rounded-full bg-subtle">
                <div
                  className="h-full rounded-full bg-brand transition-all"
                  style={{ width: `${completeness.score}%` }}
                />
              </div>
            </div>

            <ul className="divide-y divide-border">
              {completeness.sections.map((section) => {
                const mark = STATUS_MARK[section.status];
                return (
                  <li key={section.key} className="flex items-center justify-between gap-4 py-2.5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <Link
                          href={SECTION_LINKS[section.key] ?? "/dashboard"}
                          className="text-[13px] font-medium text-ink hover:text-brand"
                        >
                          {section.label}
                        </Link>
                        {section.requires_evidence && (
                          <span
                            title="Aquesta àrea només es completa amb evidència documental"
                            className="rounded bg-subtle px-1.5 py-0.5 text-[10px] font-medium text-faint"
                          >
                            evidència
                          </span>
                        )}
                      </div>
                      <p className="truncate text-[12px] text-muted">{section.detail}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className="text-[12px] tabular-nums text-faint">
                        {section.points} / {section.weight}
                      </span>
                      <Badge tone={mark.tone}>{mark.label}</Badge>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </Card>

        <div className="grid grid-cols-2 gap-3 self-start">
          <CountCard label="Serveis" value={counts.services} href="/empresa/serveis" />
          <CountCard label="CPV" value={counts.cpv_preferences} href="/empresa/cpv" />
          <CountCard label="Certificacions" value={counts.certifications} href="/empresa/certificacions" />
          <CountCard label="Registres" value={counts.registrations} href="/empresa/registres" />
          <CountCard label="Pòlisses" value={counts.insurances} href="/empresa/assegurances" />
          <CountCard label="Equip" value={counts.people} href="/empresa/equip" />
          <CountCard label="Projectes" value={counts.projects} href="/experiencia" />
          <CountCard label="Documents" value={counts.documents} href="/documents" />
        </div>
      </div>

      {/* -------------------------------------------------------- Pre-fit --- */}
      <Card className="mt-4">
        <CardHeader
          title="Pre-fit sobre les licitacions ingerides"
          description="Encaix calculat amb metadades públiques: CPV, territori, import i terminis."
          action={
            <Link href="/licitacions" className="text-[13px] text-brand hover:underline">
              Obre el feed
            </Link>
          }
        />
        {prefit.data ? (
          <div className="px-5 py-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <PreFitCount
                label="Candidates"
                value={prefit.data.candidates}
                hint="Val la pena mirar-se els plecs"
                href="/licitacions"
                tone="success"
              />
              <PreFitCount
                label="Encaix alt (80+)"
                value={prefit.data.high_fit}
                hint="Dins de les candidates"
                tone="success"
              />
              <PreFitCount
                label="Encaix baix"
                value={prefit.data.low_fit}
                hint="Obertes però sense prou coincidència"
                href="/licitacions"
              />
              <PreFitCount
                label="Sense dades suficients"
                value={prefit.data.insufficient_data}
                hint="La font no publica prou metadades"
                tone="warning"
              />
            </div>
            <p className="mt-3 text-[12px] text-muted">
              {prefit.data.actionable} de {prefit.data.total_tenders} licitacions ingerides encara
              admeten ofertes; {prefit.data.closed} estan fora de termini i {prefit.data.excluded}{" "}
              descartades per preferències vostres.
              {prefit.data.stale > 0 && (
                <span className="text-warning">
                  {" "}
                  {prefit.data.stale} pre-fits estan desactualitzats.
                </span>
              )}
            </p>
            <PreFitDisclaimer className="mt-3" />
            <p className="mt-2 text-[11px] text-faint">
              Últim càlcul: {formatDateTime(prefit.data.last_calculated_at)} · regles{" "}
              <span className="font-mono">{prefit.data.rules_version}</span>
            </p>
          </div>
        ) : (
          <EmptyState
            title="Pre-fit encara no calculat"
            description="Quan hi hagi licitacions ingerides i el perfil tingui CPV i territoris, aquí veureu l'encaix."
          />
        )}
      </Card>

      <Card className="mt-4">
        <CardHeader
          title="Avisos de caducitat"
          description="Acreditacions caducades o que caduquen en els propers 90 dies."
        />
        {alerts.length === 0 ? (
          <EmptyState
            title="Cap acreditació a punt de caducar"
            description="Quan una certificació, assegurança, registre o document tingui data de caducitat propera, apareixerà aquí."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Element</Th>
                <Th>Tipus</Th>
                <Th>Caduca</Th>
                <Th>Estat</Th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <Tr key={`${alert.entity_type}-${alert.entity_id}`}>
                  <Td className="font-medium">{alert.label}</Td>
                  <Td className="text-muted">{alert.entity_type.replace(/_/g, " ")}</Td>
                  <Td className="tabular-nums">
                    {formatDate(alert.expires_at)}
                    <span className="ml-2 text-[12px] text-faint">
                      {alert.days_left < 0
                        ? `fa ${Math.abs(alert.days_left)} dies`
                        : `en ${alert.days_left} dies`}
                    </span>
                  </Td>
                  <Td>
                    <Badge tone={alert.temporal_status === "expired" ? "danger" : "warning"}>
                      {TEMPORAL_STATUS_LABELS[alert.temporal_status]}
                    </Badge>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
