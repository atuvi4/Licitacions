"use client";

import Link from "next/link";

import { useCompanyPath, useSession } from "@/components/SessionProvider";
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
} from "@/components/ui";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { useResource } from "@/lib/hooks";
import { COMPLETENESS_LEVEL, TEMPORAL_LEVEL, TEMPORAL_STATUS_LABELS } from "@/lib/labels";
import type { Company, CompletenessSection, DashboardSummary, ExpiryAlert } from "@/lib/types";

import {
  areaActionLabel,
  areaLink,
  areaMeta,
  areaTitle,
  emptyAreas,
  expiryLine,
  expiryRoute,
  footerLine,
  groupAreas,
  listNames,
  profileContext,
  splitRadarSections,
} from "./areas";

/**
 * El nostre perfil (handoff L8, mockup 08): les àrees de coneixement de
 * `completeness.sections` agrupades pel seu estat real, amb què hi ha, què en
 * depèn en genèric (C-5: mai un recompte de licitacions) i on es va a
 * omplir-ho. Les preferències del Radar (CPV, territoris) van a part (B4) i
 * cap percentatge de completitud es mostra enlloc (B1): el perfil és el que
 * l'empresa declara, no un formulari que es completa.
 */

const ACTION_CLASS = "min-h-11 w-full justify-center md:min-h-0 md:w-auto";

function sentence(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

function ExpiryList({ alerts }: { alerts: ExpiryAlert[] }) {
  if (alerts.length === 0) {
    return (
      <p className="px-3.5 py-3 text-body text-muted">
        Cap acreditació caducada ni a punt de caducar en els propers 90 dies.
      </p>
    );
  }
  const ordered = [...alerts].sort((a, b) => a.days_left - b.days_left);
  return (
    <ul className="divide-y divide-border">
      {ordered.map((alert) => (
        <li
          key={`${alert.entity_type}-${alert.entity_id}`}
          className="flex items-start justify-between gap-3 px-3.5 py-2.5"
        >
          <div className="min-w-0">
            <Link
              href={expiryRoute(alert.entity_type)}
              className="block truncate text-body font-medium text-ink hover:text-brand-strong"
            >
              {alert.label}
            </Link>
            <p className="text-caption text-muted">{expiryLine(alert)}</p>
          </div>
          <StatusPill level={TEMPORAL_LEVEL[alert.temporal_status]} size="sm" className="shrink-0">
            {TEMPORAL_STATUS_LABELS[alert.temporal_status]}
          </StatusPill>
        </li>
      ))}
    </ul>
  );
}

/** Les preferències del Radar amb el seu estat real, i la sortida a la seva pantalla (B4). */
function RadarCard({ sections }: { sections: CompletenessSection[] }) {
  return (
    <Card>
      <CardHeader
        title="Preferències del Radar"
        description="El que el Radar fa servir per prioritzar licitacions. No és coneixement de l'empresa: és el que voleu que us ensenyi."
        action={
          <LinkButton href="/empresa/radar" variant="secondary" size="sm">
            Obre les preferències
          </LinkButton>
        }
      />
      <ul className="divide-y divide-border">
        {sections.map((section) => (
          <li key={section.key} className="flex items-start justify-between gap-3 px-3.5 py-2.5">
            <div className="min-w-0">
              <Link href={areaLink(section.key)} className="block text-body font-medium text-ink hover:text-brand-strong">
                {areaTitle(section)}
              </Link>
              <p className="text-caption text-muted">{sentence(section.detail)}</p>
            </div>
            <StatusPill level={COMPLETENESS_LEVEL[section.status]} size="sm" className="shrink-0">
              {section.status === "empty" ? "Sense dada" : section.status === "partial" ? "Parcial" : "Definida"}
            </StatusPill>
          </li>
        ))}
        <li className="px-3.5 py-2.5">
          <Link href="/empresa/solvencia" className="text-caption text-muted hover:text-brand-strong">
            Franja d&apos;import: a «Solvència econòmica» →
          </Link>
        </li>
      </ul>
    </Card>
  );
}

export default function ProfileHubPage() {
  const path = useCompanyPath();
  const { membership } = useSession();
  const summary = useResource<DashboardSummary>(path("/summary"));
  const company = useResource<Company>(path(""));

  const sessionCompany = membership.company;
  const companyName =
    summary.data?.company.trade_name ??
    summary.data?.company.legal_name ??
    sessionCompany.trade_name ??
    sessionCompany.legal_name;

  function reloadAll() {
    void summary.reload();
    void company.reload();
  }

  if (summary.error && !summary.data) {
    return (
      <>
        <PageHeader title="El nostre perfil" description={companyName} />
        <ErrorState
          title="No hem pogut carregar el perfil"
          description="El resum de les àrees no ha respost. Les dades no s'han perdut: torna-ho a provar."
          action={
            <Button variant="primary" onClick={reloadAll}>
              Torna-ho a provar
            </Button>
          }
        />
      </>
    );
  }

  if (!summary.data) {
    return (
      <PageSkeleton
        variant="detail"
        title="El nostre perfil"
        context={`${companyName} · carregant les àrees del perfil…`}
      />
    );
  }

  const { completeness, expiry_alerts: alerts } = summary.data;
  // El Radar (CPV, territoris) va a part: la taula és només el coneixement de l'empresa.
  const { radar, knowledge: sections } = splitRadarSections(completeness.sections);
  const empties = emptyAreas(sections);
  const groups = groupAreas(sections);
  const firstEmptyKey = empties.first?.key;
  const updatedAt = company.data?.updated_at ?? null;

  const columns: Array<DataTableColumn<CompletenessSection>> = [
    {
      key: "area",
      header: "Àrea",
      width: "28%",
      cell: (section) => (
        <div className="min-w-0">
          <p className="font-medium text-ink">{areaTitle(section)}</p>
          <p className="text-caption text-muted">
            {areaMeta(section.key).subtitle}
            {section.requires_evidence && " · es pot acreditar amb evidències"}
          </p>
        </div>
      ),
    },
    {
      key: "have",
      header: "Què hi tenim",
      width: "28%",
      cell: (section) => (
        <span className={section.status === "empty" ? "font-medium text-danger" : undefined}>
          {sentence(section.detail)}
        </span>
      ),
    },
    {
      key: "depends",
      header: "Què en depèn",
      width: "26%",
      cell: (section) => <span data-depends>{areaMeta(section.key).dependsOn}</span>,
    },
    {
      key: "action",
      header: "Acció",
      width: "18%",
      align: "right",
      cell: (section) => (
        <LinkButton
          href={areaLink(section.key)}
          variant={section.key === firstEmptyKey ? "primary" : "secondary"}
          size="sm"
          className={ACTION_CLASS}
        >
          {areaActionLabel(section)}
        </LinkButton>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="El nostre perfil"
        description={profileContext(companyName, sections.length, updatedAt)}
        actions={
          empties.first ? (
            <LinkButton href={areaLink(empties.first.key)} variant="primary" size="md">
              {areaActionLabel(empties.first)}
            </LinkButton>
          ) : undefined
        }
      />

      {empties.first && (
        <Banner
          level="attention"
          kicker={`${empties.count} ${empties.count === 1 ? "àrea" : "àrees"} sense dada`}
          className="mb-4"
          action={
            <LinkButton href={areaLink(empties.first.key)} variant="secondary" size="sm">
              Comença per {areaTitle(empties.first)}
            </LinkButton>
          }
        >
          <strong className="font-medium">{listNames(empties.names)}</strong>. Mentre no hi hagi
          dada, els requisits que en depenen surten com a «sense dada», que no és ni un sí ni un
          no. No cal omplir-ho tot: l&apos;evidència només es demana quan un plec l&apos;exigeix.
        </Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Card className="min-w-0">
          <DataTable<CompletenessSection>
            columns={columns}
            rows={sections}
            rowKey={(section) => section.key}
            groups={groups}
            stackBelow="md"
            footer={<span>{footerLine(sections)}</span>}
          />
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <RadarCard sections={radar} />
          <Card>
            <CardHeader
              title="Caducitats que t'afecten"
              description="Acreditacions caducades o que caduquen en els propers 90 dies."
            />
            <ExpiryList alerts={alerts} />
          </Card>
          <Card>
            <CardHeader
              title="Coneixement de l'empresa"
              description="Què és actual i amb quin estat: declarat, verificat amb evidència o detectat a l'històric."
              action={
                <LinkButton href="/empresa/coneixement" variant="secondary" size="sm">
                  Obre el coneixement
                </LinkButton>
              }
            />
          </Card>
          <Card className="bg-subtle">
            <p className="px-3.5 py-3 text-caption leading-5 text-muted">
              Cap dada del perfil la canvia l&apos;anàlisi: totes les escriu una persona i cada camp
              recorda qui i quan. El que llegeix del plec es compara contra això, mai a
              l&apos;inrevés.
            </p>
          </Card>
        </div>
      </div>
    </>
  );
}
