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
import { TEMPORAL_LEVEL, TEMPORAL_STATUS_LABELS } from "@/lib/labels";
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
  isNewProfile,
  listNames,
  profileContext,
} from "./areas";

/**
 * El nostre perfil (handoff L8, mockup 08): les onze àrees de
 * `completeness.sections` agrupades pel seu estat real, amb què hi ha, què en
 * depèn en genèric (C-5: mai un recompte de licitacions) i on es va a
 * omplir-ho. Acull la completitud i les caducitats que van sortir del
 * dashboard a L5. El percentatge només surt al peu de la taula i al recompte
 * de la barra lateral.
 */

const FIRST_STEPS = [
  {
    title: "Què feu · codis CPV",
    description: "Els codis amb què l'Administració classifica els contractes.",
    action: "Comença",
    href: "/empresa/cpv",
  },
  {
    title: "On treballeu · territoris",
    description: "Comarques o províncies on us podeu presentar. Es pot canviar sempre.",
    action: "Tria territoris",
    href: "/empresa/territoris",
  },
  {
    title: "Quina mida · franja d'import",
    description: "El mínim i el màxim que us interessa: pesa en l'encaix de cada oportunitat.",
    action: "Posa la franja d'import",
    href: "/empresa/solvencia",
  },
];

const ACTION_CLASS = "min-h-11 w-full justify-center md:min-h-0 md:w-auto";

function sentence(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

function WelcomeCard() {
  return (
    <Card frame className="bg-surface">
      <div className="px-5 py-5 sm:px-7 sm:py-6">
        <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-brand-strong uppercase">
          Per començar · uns 10 minuts
        </p>
        <h2 className="mt-1.5 font-heading text-[22px] leading-tight font-semibold text-ink">
          Tres coses del perfil, i l&apos;encaix es calcula sol
        </h2>
        <ol className="mt-5 divide-y divide-border border-t border-border">
          {FIRST_STEPS.map((step, index) => (
            <li key={step.href} className="flex flex-col gap-3 py-3.5 sm:flex-row sm:items-center sm:gap-4">
              <span
                className={
                  "flex h-7 w-7 shrink-0 items-center justify-center font-heading text-[13px] font-semibold " +
                  (index === 0 ? "bg-brand text-white" : "border border-border-strong text-muted")
                }
              >
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-medium text-ink">{step.title}</p>
                <p className="text-caption text-muted">{step.description}</p>
              </div>
              <LinkButton
                href={step.href}
                variant={index === 0 ? "primary" : "secondary"}
                size="md"
                className={ACTION_CLASS}
              >
                {step.action}
              </LinkButton>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-[13px] leading-5 text-muted">
          Després, quan obriu una licitació, us demanarem la resta (solvència, assegurances,
          certificats) <strong className="font-medium text-ink">només quan un plec ho exigeixi</strong>.
          No cal omplir-ho tot avui.
        </p>
      </div>
    </Card>
  );
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
  const sections = completeness.sections;
  const fresh = isNewProfile(completeness);
  const empties = emptyAreas(sections);
  const groups = groupAreas(sections).map((group) =>
    fresh && group.key === "empty" ? { ...group, collapsed: true } : group,
  );
  const firstEmptyKey = fresh ? null : empties.first?.key;
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
            {section.requires_evidence && " · es completa amb evidències"}
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
          !fresh && empties.first ? (
            <LinkButton href={areaLink(empties.first.key)} variant="primary" size="md">
              {areaActionLabel(empties.first)}
            </LinkButton>
          ) : undefined
        }
      />

      {fresh ? (
        <div className="mb-4">
          <WelcomeCard />
        </div>
      ) : (
        empties.first && (
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
            no.
          </Banner>
        )
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Card className="min-w-0">
          <DataTable<CompletenessSection>
            columns={columns}
            rows={sections}
            rowKey={(section) => section.key}
            groups={groups}
            stackBelow="md"
            footer={<span>{footerLine(sections, completeness.score)}</span>}
          />
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardHeader
              title="Caducitats que t'afecten"
              description="Acreditacions caducades o que caduquen en els propers 90 dies."
            />
            <ExpiryList alerts={alerts} />
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
