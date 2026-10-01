"use client";

import type { ReactNode } from "react";

import { Button, Card, CardHeader, LinkButton, StatusPill } from "@/components/ui";
import { formatDate } from "@/lib/format";
import {
  CAPABILITY_STATUS_LABELS,
  DELIVERY_MODE_LABELS,
  FACT_KEYS,
  FACT_LABELS,
  PARTNER_ROLE_LABELS,
  STANDARD_DESCRIPTIONS,
  candidateSearchText,
  capabilityLabel,
  capabilityValue,
  certificationTitle,
  coverageSummary,
  entryState,
  findEntry,
  recordState,
  serviceLineLabel,
  standardLabel,
  type KnowledgeEntry,
  type SnapshotSection,
} from "@/lib/knowledge";
import type { Certification } from "@/lib/types";

import { HintList } from "./CandidateRow";
import { KnowledgeGap, KnowledgeItem } from "./KnowledgeItem";
import { NoResults } from "./NoResults";
import type { KnowledgeView } from "./view";

const ACTION = "min-h-11 md:min-h-0";
const UNKNOWN = { state: "unknown" as const, flags: [] };

function Section({ id, title, description, action, children }: { id: string; title: string; description: string; action?: ReactNode; children: ReactNode }) {
  return (
    <Card id={id}>
      <CardHeader title={title} description={description} action={action} />
      <ul>{children}</ul>
    </Card>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <li className="px-5 py-3.5 text-body text-muted">{children}</li>;
}

/** Les claus d'una llista tancada de les quals no se sap res, en una sola fila. */
function UnknownRow({ names, onDeclare, disabled }: { names: string[]; onDeclare: () => void; disabled: boolean }) {
  if (names.length === 0) return null;
  return (
    <KnowledgeGap
      title={names.length === 1 ? "Sense cap dada" : `Sense cap dada (${names.length})`}
      state={UNKNOWN}
      subtitle={`${names.join(" · ")}. No saber-ho no vol dir que no: vol dir que ningú no ho ha declarat.`}
      actions={
        <Button size="sm" variant="ghost" className={ACTION} disabled={disabled} onClick={onDeclare}>
          Declara
        </Button>
      }
    />
  );
}

/** Claus que només surten a l'històric: una fila per clau, amb el que s'hi pot fer. */
function HistoricRows({ entries, title, view }: { entries: KnowledgeEntry[]; title: (key: string) => string; view: KnowledgeView }) {
  return (
    <>
      {entries.map((entry) => (
        <KnowledgeGap key={entry.key} title={title(entry.key)} state={entryState(entry)}>
          <HintList hints={entry.hints} view={view} />
        </KnowledgeGap>
      ))}
    </>
  );
}

function historicOnly(view: KnowledgeView, section: SnapshotSection, taken: Set<string>): KnowledgeEntry[] {
  return view.snapshot[section].filter((entry) => entry.claims.length === 0 && entry.hints.length > 0 && !taken.has(entry.key));
}

function ScopeLine({ certification, view }: { certification: Certification; view: KnowledgeView }) {
  const lines = certification.scope_service_lines ?? [];
  return (
    <div className="mt-1.5 text-caption">
      {lines.length > 0 ? (
        <p className="flex flex-wrap items-center gap-1.5 text-muted">
          Abast:
          {lines.map((code) => (
            <StatusPill key={code} level="neutral" size="sm" outline>
              {serviceLineLabel(code, view.vocabulary)}
            </StatusPill>
          ))}
        </p>
      ) : (
        <p className="font-medium text-warning">Abast pendent d&apos;estructurar</p>
      )}
      {certification.scope && (
        <p className="mt-0.5 text-muted">
          Nota d&apos;abast: «{certification.scope}»{lines.length === 0 && " (no es fa servir per deduir què cobreix)"}
        </p>
      )}
    </div>
  );
}

function validity(expiresAt: string | null, expired: boolean): string {
  if (!expiresAt) return "Sense data de caducitat";
  return expired ? `Va caducar el ${formatDate(expiresAt)}` : `Vigent fins al ${formatDate(expiresAt)}`;
}

/**
 * «Coneixement actual»: què se sap de l'empresa, categoria per categoria. Cada
 * fila diu el seu estat efectiu; el que només surt a l'històric i el que no se
 * sap hi són al costat, perquè es vegi què falta.
 */
export function CurrentTab({ view }: { view: KnowledgeView }) {
  const { snapshot, index, records, vocabulary, canEdit, canVerify, reload, declare, matches } = view;
  const common = { canEdit, canVerify, onChanged: reload };
  const searching = view.search.trim().length > 0;
  /** Una fila de l'històric encaixa pel seu títol o pel que hi diu l'històric. */
  const entryMatches = (entry: KnowledgeEntry | undefined, ...labels: Array<string | null | undefined>) =>
    matches(
      ...labels,
      ...(entry?.hints ?? []).flatMap((hint) => {
        const observation = view.observations.get(hint.observation_id);
        return observation ? candidateSearchText(observation, vocabulary) : [hint.statement];
      }),
    );
  const lineLabel = (code: string | null | undefined) => (code ? serviceLineLabel(code, vocabulary) : null);

  /* Certificacions */
  const standards = vocabulary?.standards ?? [];
  const certifications = view.certifications.filter((item) =>
    matches(certificationTitle(item, vocabulary), item.name, item.scope, ...(item.scope_service_lines ?? []).map(lineLabel)),
  );
  const certified = new Set(view.certifications.map((item) => item.standard_code).filter(Boolean) as string[]);
  const historicStandards = standards.filter(
    (item) =>
      !certified.has(item.code) &&
      entryState(findEntry(snapshot, "certifications", item.code)).state === "historic" &&
      entryMatches(findEntry(snapshot, "certifications", item.code), standardLabel(item.code, vocabulary), STANDARD_DESCRIPTIONS[item.code]),
  );
  const unknownStandards = standards.filter(
    (item) => !certified.has(item.code) && !findEntry(snapshot, "certifications", item.code) && matches(standardLabel(item.code, vocabulary)),
  );

  /* Serveis */
  const lines = vocabulary?.service_lines ?? [];
  const services = view.services.filter((item) =>
    matches(item.name, lineLabel(item.service_line_code), item.delivery_mode ? DELIVERY_MODE_LABELS[item.delivery_mode] : null, coverageSummary(item.coverage)),
  );
  const served = new Set(view.services.map((item) => item.service_line_code).filter(Boolean) as string[]);
  const historicLines = lines.filter(
    (item) =>
      !served.has(item.code) &&
      entryState(findEntry(snapshot, "services", item.code)).state === "historic" &&
      entryMatches(findEntry(snapshot, "services", item.code), lineLabel(item.code)),
  );
  const unknownLines = lines.filter((item) => !served.has(item.code) && !findEntry(snapshot, "services", item.code) && matches(lineLabel(item.code)));

  /* Fets */
  const facts = records.operating_facts.filter((item) =>
    matches(FACT_LABELS[item.key] ?? item.key, item.detail, item.value ? "Sí" : "No", coverageSummary(item.coverage)),
  );
  const factByKey = new Map(records.operating_facts.map((item) => [item.key, item]));
  const historicFacts = FACT_KEYS.filter(
    (key) =>
      !factByKey.has(key) &&
      entryState(findEntry(snapshot, "facts", key)).state === "historic" &&
      entryMatches(findEntry(snapshot, "facts", key), FACT_LABELS[key]),
  );
  const unknownFacts = FACT_KEYS.filter((key) => !factByKey.has(key) && !findEntry(snapshot, "facts", key) && matches(FACT_LABELS[key]));

  /* Solucions, capacitats, productes, partners */
  const solutionMatches = (solution: (typeof records.solutions)[number]) =>
    matches(solution.name, lineLabel(solution.service_line_code), solution.vendor, solution.edition);
  const solutions = records.solutions.filter(solutionMatches);
  const historicSolutions = historicOnly(view, "solutions", new Set(records.solutions.map((item) => item.name))).filter((entry) =>
    entryMatches(entry, entry.key),
  );
  /** Les capacitats d'una solució: totes si la solució encaixa; si no, només les que encaixen. */
  const capabilitiesOf = (solution: (typeof records.solutions)[number]) =>
    records.solution_capabilities
      .filter((item) => item.solution_id === solution.id)
      .filter((item) => solutionMatches(solution) || matches(capabilityLabel(item.capability_code, vocabulary), CAPABILITY_STATUS_LABELS[item.status], item.limits_note));
  const solutionsWithCapabilities = records.solutions.filter((solution) => capabilitiesOf(solution).length > 0);
  const products = records.products.filter((item) => matches(`${item.manufacturer} ${item.model}`, lineLabel(item.service_line_code)));
  const historicProducts = historicOnly(view, "products", new Set(records.products.map((item) => `${item.manufacturer} ${item.model}`))).filter((entry) =>
    entryMatches(entry, entry.key),
  );
  const roleText = (role: (typeof records.partner_roles)[number]) =>
    [PARTNER_ROLE_LABELS[role.role] ?? role.role, lineLabel(role.service_line_code), role.territory, coverageSummary(role.coverage)]
      .filter(Boolean)
      .join(" · ");
  const partners = records.partners.filter((partner) =>
    matches(partner.name, partner.tax_id, ...records.partner_roles.filter((role) => role.partner_id === partner.id).map(roleText)),
  );
  const historicPartners = historicOnly(view, "partners", new Set(records.partners.map((item) => item.name))).filter((entry) =>
    entryMatches(entry, entry.key),
  );

  /* Quan es cerca, les categories sense cap resultat no es pinten. */
  const counts = {
    certificacions: certifications.length + historicStandards.length + unknownStandards.length,
    serveis: services.length + historicLines.length + unknownLines.length,
    fets: facts.length + historicFacts.length + unknownFacts.length,
    solucions: solutions.length + historicSolutions.length,
    capacitats: solutionsWithCapabilities.length,
    productes: products.length + historicProducts.length,
    partners: partners.length + historicPartners.length,
  };
  const show = (section: keyof typeof counts) => !searching || counts[section] > 0;
  if (searching && Object.values(counts).every((count) => count === 0)) return <NoResults search={view.search} />;

  return (
    <div className="flex flex-col gap-4">
      {show("certificacions") && (
        <Section
          id="certificacions"
          title="Certificacions"
          description="Norma, nivell, vigència, evidència i abast. Una certificació només acredita el que cobreix el seu abast."
          action={
            <Button size="sm" variant="secondary" disabled={!canEdit} onClick={() => declare({ kind: "certification" })}>
              Declara una certificació
            </Button>
          }
        >
          {certifications.map((certification) => {
            const state = recordState(certification.id, certification.lifecycle ?? "current", index);
            return (
              <KnowledgeItem
                key={certification.id}
                kind="certification"
                recordId={certification.id}
                title={certificationTitle(certification, vocabulary)}
                subtitle={validity(certification.expires_at, state.state === "expired")}
                state={state}
                actions={
                  <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => view.editScope(certification)}>
                    Edita l&apos;abast
                  </Button>
                }
                {...common}
              >
                <ScopeLine certification={certification} view={view} />
              </KnowledgeItem>
            );
          })}
          {historicStandards.map((standard) => (
            <KnowledgeGap
              key={standard.code}
              title={standardLabel(standard.code, vocabulary)}
              subtitle={STANDARD_DESCRIPTIONS[standard.code]}
              state={entryState(findEntry(snapshot, "certifications", standard.code))}
              actions={
                <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "certification", standardCode: standard.code })}>
                  Declara-la
                </Button>
              }
            >
              <HintList hints={findEntry(snapshot, "certifications", standard.code)?.hints ?? []} view={view} />
            </KnowledgeGap>
          ))}
          <UnknownRow names={unknownStandards.map((item) => standardLabel(item.code, vocabulary))} onDeclare={() => declare({ kind: "certification" })} disabled={!canEdit} />
        </Section>
      )}

      {show("serveis") && (
        <Section
          id="serveis"
          title="Serveis"
          description="Què presta l'empresa, per línia de servei. La línia és el que permet comparar-ho amb un plec."
          action={
            <Button size="sm" variant="secondary" disabled={!canEdit} onClick={() => declare({ kind: "service" })}>
              Declara un servei
            </Button>
          }
        >
          {services.map((service) =>
            service.service_line_code ? (
              <KnowledgeItem
                key={service.id}
                kind="service"
                recordId={service.id}
                title={service.name}
                subtitle={[
                  serviceLineLabel(service.service_line_code, vocabulary),
                  service.delivery_mode ? DELIVERY_MODE_LABELS[service.delivery_mode] : null,
                  coverageSummary(service.coverage),
                ]
                  .filter(Boolean)
                  .join(" · ")}
                state={recordState(service.id, service.active ? "current" : "retired", index)}
                {...common}
              />
            ) : (
              <KnowledgeGap
                key={service.id}
                title={service.name}
                subtitle="Sense línia de servei: no es pot comparar amb cap plec."
                state={{ state: "inactive", flags: [] }}
                actions={
                  <LinkButton href="/empresa/serveis" className={ACTION}>
                    Assigna-li una línia
                  </LinkButton>
                }
              />
            ),
          )}
          {historicLines.map((line) => (
            <KnowledgeGap key={line.code} title={serviceLineLabel(line.code, vocabulary)} state={entryState(findEntry(snapshot, "services", line.code))}>
              <HintList hints={findEntry(snapshot, "services", line.code)?.hints ?? []} view={view} />
            </KnowledgeGap>
          ))}
          <UnknownRow names={unknownLines.map((item) => serviceLineLabel(item.code, vocabulary))} onDeclare={() => declare({ kind: "service" })} disabled={!canEdit} />
        </Section>
      )}

      {show("fets") && (
        <Section
          id="fets"
          title="Fets operatius"
          description="Condicions de l'empresa que els plecs solen exigir i que no tenen lloc a cap altra àrea del perfil."
          action={
            <Button size="sm" variant="secondary" onClick={() => declare({ kind: "operating_fact" })} disabled={!canEdit || factByKey.size === FACT_KEYS.length}>
              Declara un fet
            </Button>
          }
        >
          {facts.map((fact) => (
            <KnowledgeItem
              key={fact.id}
              kind="operating_fact"
              recordId={fact.id}
              title={FACT_LABELS[fact.key] ?? fact.key}
              subtitle={[fact.value ? "Sí" : "No", fact.detail, coverageSummary(fact.coverage)].filter(Boolean).join(" · ")}
              state={recordState(fact.id, fact.lifecycle, index)}
              actions={
                <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "operating_fact", record: fact })}>
                  Edita
                </Button>
              }
              {...common}
            />
          ))}
          {historicFacts.map((key) => (
            <KnowledgeGap key={key} title={FACT_LABELS[key]} state={entryState(findEntry(snapshot, "facts", key))}>
              <HintList hints={findEntry(snapshot, "facts", key)?.hints ?? []} view={view} />
            </KnowledgeGap>
          ))}
          <UnknownRow names={unknownFacts.map((key) => FACT_LABELS[key])} onDeclare={() => declare({ kind: "operating_fact" })} disabled={!canEdit} />
        </Section>
      )}

      {show("solucions") && (
        <Section
          id="solucions"
          title="Solucions"
          description="Amb què es presta cada servei: la plataforma, el fabricant i l'edició."
          action={
            <Button size="sm" variant="secondary" disabled={!canEdit} onClick={() => declare({ kind: "solution" })}>
              Declara una solució
            </Button>
          }
        >
          {solutions.map((solution) => (
            <KnowledgeItem
              key={solution.id}
              kind="solution"
              recordId={solution.id}
              title={solution.name}
              subtitle={[serviceLineLabel(solution.service_line_code, vocabulary), solution.vendor, solution.edition].filter(Boolean).join(" · ")}
              state={recordState(solution.id, solution.lifecycle, index)}
              actions={
                <>
                  <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "solution_capability", solutionId: solution.id })}>
                    Afegeix una capacitat
                  </Button>
                  <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "solution", record: solution })}>
                    Edita
                  </Button>
                </>
              }
              {...common}
            />
          ))}
          <HistoricRows entries={historicSolutions} title={(key) => key} view={view} />
          {records.solutions.length === 0 && historicSolutions.length === 0 && <Note>Cap solució declarada.</Note>}
        </Section>
      )}

      {show("capacitats") && (
        <Section
          id="capacitats"
          title="Capacitats"
          description="Què fa cada solució. Una capacitat que no s'ha declarat és desconeguda: no vol dir que la solució no la tingui."
          action={
            <Button size="sm" variant="secondary" onClick={() => declare({ kind: "solution_capability" })} disabled={!canEdit || records.solutions.length === 0}>
              Declara una capacitat
            </Button>
          }
        >
          {solutionsWithCapabilities.map((solution) => (
            <li key={solution.id} className="border-b border-border last:border-b-0">
              <p className="bg-subtle px-5 py-2 font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">{solution.name}</p>
              <ul>
                {capabilitiesOf(solution)
                  .map((item) => (
                    <KnowledgeItem
                      key={item.id}
                      kind="solution_capability"
                      recordId={item.id}
                      title={capabilityLabel(item.capability_code, vocabulary)}
                      subtitle={[CAPABILITY_STATUS_LABELS[item.status], capabilityValue(item, vocabulary), item.limits_note].filter(Boolean).join(" · ")}
                      state={recordState(item.id, item.lifecycle, index)}
                      actions={
                        <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "solution_capability", record: item })}>
                          Edita
                        </Button>
                      }
                      {...common}
                    />
                  ))}
              </ul>
            </li>
          ))}
          {solutionsWithCapabilities.length === 0 && (
            <Note>
              {records.solutions.length === 0
                ? "Primer cal declarar una solució: les capacitats són de cada solució."
                : "Cap capacitat declarada encara."}
            </Note>
          )}
        </Section>
      )}

      {show("productes") && (
        <Section
          id="productes"
          title="Productes"
          description="Equips que l'empresa fa servir o homologa. Sense preus."
          action={
            <Button size="sm" variant="secondary" disabled={!canEdit} onClick={() => declare({ kind: "product" })}>
              Declara un producte
            </Button>
          }
        >
          {products.map((product) => (
            <KnowledgeItem
              key={product.id}
              kind="product"
              recordId={product.id}
              title={`${product.manufacturer} ${product.model}`}
              subtitle={product.service_line_code ? serviceLineLabel(product.service_line_code, vocabulary) : undefined}
              state={recordState(product.id, product.lifecycle, index)}
              actions={
                <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "product", record: product })}>
                  Edita
                </Button>
              }
              {...common}
            />
          ))}
          <HistoricRows entries={historicProducts} title={(key) => key} view={view} />
          {records.products.length === 0 && historicProducts.length === 0 && <Note>Cap producte declarat.</Note>}
        </Section>
      )}

      {show("partners") && (
        <Section
          id="partners"
          title="Partners"
          description="Proveïdors, operadors majoristes, subcontractistes i socis, i quin paper fa cadascun. Sense condicions econòmiques."
          action={
            <Button size="sm" variant="secondary" disabled={!canEdit} onClick={() => declare({ kind: "partner" })}>
              Declara un partner
            </Button>
          }
        >
          {partners.map((partner) => {
            const roles = records.partner_roles.filter((role) => role.partner_id === partner.id);
            return (
              <KnowledgeItem
                key={partner.id}
                kind="partner"
                recordId={partner.id}
                title={partner.name}
                subtitle={partner.tax_id ?? undefined}
                state={recordState(partner.id, partner.lifecycle, index)}
                actions={
                  <>
                    <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "partner_role", partnerId: partner.id })}>
                      Afegeix un paper
                    </Button>
                    <Button size="sm" variant="ghost" className={ACTION} disabled={!canEdit} onClick={() => declare({ kind: "partner", record: partner })}>
                      Edita
                    </Button>
                  </>
                }
                {...common}
              >
                {roles.length > 0 ? (
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {roles.map((role) => (
                      <li key={role.id}>
                        <button
                          type="button"
                          disabled={!canEdit} onClick={() => declare({ kind: "partner_role", partnerId: partner.id, record: role })}
                          className="rounded-pill border border-border-strong px-2 py-px text-[11.5px] leading-[1.5] text-neutral-600 hover:bg-subtle"
                          aria-label={`Edita el paper ${PARTNER_ROLE_LABELS[role.role] ?? role.role} de ${partner.name}`}
                        >
                          {[
                            PARTNER_ROLE_LABELS[role.role] ?? role.role,
                            role.service_line_code ? serviceLineLabel(role.service_line_code, vocabulary) : null,
                            role.territory,
                            coverageSummary(role.coverage),
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-caption text-muted">Sense cap paper declarat.</p>
                )}
              </KnowledgeItem>
            );
          })}
          <HistoricRows entries={historicPartners} title={(key) => key} view={view} />
          {records.partners.length === 0 && historicPartners.length === 0 && <Note>Cap partner declarat.</Note>}
        </Section>
      )}
    </div>
  );
}
