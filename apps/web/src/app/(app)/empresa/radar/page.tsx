"use client";

import { useCompanyPath, useSession } from "@/components/SessionProvider";
import { Button, Card, CardHeader, ErrorNotice, LinkButton, LoadingRows, PageHeader, StatusPill } from "@/components/ui";
import { formatMoney, TERRITORY_TYPE_LABELS } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import { activeLevel, cpvPreferenceLevel } from "@/lib/labels";
import { cpvDescription } from "@/lib/tenders";
import type { ContractPreferences, CPVPreference, Territory } from "@/lib/types";

/**
 * Preferències del Radar (addendum 3B-2, B4): el que el Radar fa servir per
 * prioritzar licitacions —codis CPV, territoris i franja d'import— en una
 * pantalla pròpia, separada del coneixement corporatiu. Les dades i les
 * pantalles d'edició són les de sempre; aquí només es llegeixen.
 */

const ACTION = "min-h-11 md:min-h-0";

const AMOUNT_FIELDS: Array<{ key: keyof ContractPreferences; label: string }> = [
  { key: "minimum_interesting_contract", label: "Mínim que interessa" },
  { key: "preferred_contract_min", label: "Franja preferida · des de" },
  { key: "preferred_contract_max", label: "Franja preferida · fins a" },
  { key: "maximum_recommended_contract", label: "Màxim recomanat" },
  { key: "maximum_assumable_contract", label: "Màxim assumible" },
];

function Section({ title, description, action, error, onRetry, loading, children }: {
  title: string;
  description: string;
  action: React.ReactNode;
  error: string | null;
  onRetry: () => void;
  loading: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader title={title} description={description} action={action} />
      {error ? (
        <div className="px-5 py-4">
          <ErrorNotice message={`No s'ha pogut carregar: ${error}`} onRetry={onRetry} />
        </div>
      ) : loading ? (
        <LoadingRows rows={2} />
      ) : (
        children
      )}
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-5 py-4 text-body text-muted">{children}</p>;
}

export default function RadarPreferencesPage() {
  const path = useCompanyPath();
  const { membership } = useSession();
  const cpvs = useResource<CPVPreference[]>(path("/cpv-preferences"));
  const territories = useResource<Territory[]>(path("/territories"));
  const amounts = useResource<ContractPreferences>(path("/contract-preferences"));
  const companyName = membership.company.trade_name ?? membership.company.legal_name;

  const cpvList = cpvs.data ?? [];
  const activeCpvs = cpvList.filter((item) => item.active && !item.excluded).length;
  const excludedCpvs = cpvList.filter((item) => item.excluded).length;
  const territoryList = territories.data ?? [];
  const amountRows = AMOUNT_FIELDS.map((field) => ({ ...field, value: amounts.data?.[field.key] as string | null | undefined }));
  const hasAmounts = amountRows.some((row) => row.value);
  const nothing =
    cpvs.data !== null && territories.data !== null && amounts.data !== null && cpvList.length === 0 && territoryList.length === 0 && !hasAmounts;

  return (
    <>
      <PageHeader
        title="Preferències del Radar"
        description={`${companyName} · El Radar només fa servir aquestes preferències per prioritzar licitacions: codis CPV, territoris i franja d'import. Cap d'elles és coneixement de l'empresa, i cap no amaga cap licitació de «Totes».`}
      />

      {nothing && (
        <p className="mb-4 text-[13px] leading-5 text-warning">
          Sense cap preferència, el Radar no pot prioritzar: només llista les licitacions ingerides.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="Codis CPV"
          description="Els codis amb què l'Administració classifica els contractes que us interessen, i els que voleu descartar d'entrada."
          action={
            <LinkButton href="/empresa/cpv" variant="secondary" size="sm" className={ACTION}>
              {cpvList.length === 0 ? "Tria codis CPV" : "Gestiona els CPV"}
            </LinkButton>
          }
          error={cpvs.error && !cpvs.data ? cpvs.error : null}
          onRetry={() => void cpvs.reload()}
          loading={cpvs.loading && !cpvs.data}
        >
          {cpvList.length === 0 ? (
            <Empty>Cap codi CPV. Sense codis, el Radar no sap de quina activitat són les licitacions que busqueu.</Empty>
          ) : (
            <>
              <p className="border-b border-border px-5 py-2 text-caption text-muted">
                {activeCpvs} {activeCpvs === 1 ? "actiu" : "actius"} · {excludedCpvs} {excludedCpvs === 1 ? "exclòs" : "exclosos"}
              </p>
              <ul className="divide-y divide-border">
                {cpvList.map((item) => {
                  const description = cpvDescription(item);
                  return (
                    <li key={item.id} className="flex items-start justify-between gap-3 px-5 py-2.5 text-[13px]">
                      <span className="min-w-0">
                        <span className="font-mono font-medium text-ink">{item.cpv_code}</span>
                        {description ? <span className="text-muted"> · {description}</span> : <span className="text-faint"> · sense descripció al catàleg</span>}
                      </span>
                      <StatusPill level={cpvPreferenceLevel(item)} size="sm" className="shrink-0">
                        {item.excluded ? "Exclòs" : item.active ? "Actiu" : "Inactiu"}
                      </StatusPill>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </Section>

        <Section
          title="Territoris"
          description="On us podeu presentar. Es compara amb el codi NUTS o el nom del territori que publica la font."
          action={
            <LinkButton href="/empresa/territoris" variant="secondary" size="sm" className={ACTION}>
              {territoryList.length === 0 ? "Tria territoris" : "Gestiona els territoris"}
            </LinkButton>
          }
          error={territories.error && !territories.data ? territories.error : null}
          onRetry={() => void territories.reload()}
          loading={territories.loading && !territories.data}
        >
          {territoryList.length === 0 ? (
            <Empty>Cap territori. Sense territoris, l&apos;encaix territorial de cada licitació queda com a desconegut.</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {territoryList.map((item) => {
                // El codi del territori (NUTS o INE, com l'ha escrit l'empresa): es mostra tal qual, cap nom inventat (C2).
                const territoryCode = item.code;
                return (
                <li key={item.id} className="flex items-start justify-between gap-3 px-5 py-2.5 text-[13px]">
                  <span className="min-w-0">
                    <span className="font-medium text-ink">{item.name}</span>
                    <span className="text-muted"> · {TERRITORY_TYPE_LABELS[item.territory_type]}</span>
                    {territoryCode && <span className="font-mono text-muted"> · {territoryCode}</span>}
                  </span>
                  <StatusPill level={activeLevel(item.willing_to_work)} size="sm" className="shrink-0">
                    {item.willing_to_work ? "Hi treballem" : "Descartat"}
                  </StatusPill>
                </li>
                );
              })}
            </ul>
          )}
        </Section>

        <Section
          title="Franja d'import"
          description="Els llindars d'import amb què l'empresa se sent còmoda. S'editen a «Solvència econòmica», amb els exercicis."
          action={
            <LinkButton href="/empresa/solvencia" variant="secondary" size="sm" className={ACTION}>
              {hasAmounts ? "Edita la franja d'import" : "Posa la franja d'import"}
            </LinkButton>
          }
          error={amounts.error && !amounts.data ? amounts.error : null}
          onRetry={() => void amounts.reload()}
          loading={amounts.loading && !amounts.data}
        >
          {!hasAmounts ? (
            <Empty>Cap franja d&apos;import. Sense franja, l&apos;import de cada licitació no pesa en la prioritat.</Empty>
          ) : (
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 px-5 py-4 text-[13px] sm:grid-cols-2">
              {amountRows.map((row) => (
                <div key={row.key}>
                  <dt className="text-caption text-muted">{row.label}</dt>
                  <dd className="mt-0.5 tabular-nums text-ink">
                    {row.value ? formatMoney(row.value, amounts.data?.currency) : <span className="text-muted">Sense especificar</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Section>
      </div>

      {(cpvs.error || territories.error || amounts.error) && (
        <div className="mt-4">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void cpvs.reload();
              void territories.reload();
              void amounts.reload();
            }}
          >
            Torna-ho a provar tot
          </Button>
        </div>
      )}
    </>
  );
}
