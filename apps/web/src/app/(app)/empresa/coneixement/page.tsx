"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { CurrentTab } from "@/components/knowledge/CurrentTab";
import { DeclareDialog, type DeclareTarget } from "@/components/knowledge/DeclareDialog";
import { HistoricTab } from "@/components/knowledge/HistoricTab";
import { ScopeDialog } from "@/components/knowledge/ScopeDialog";
import { QualificationTab } from "@/components/knowledge/qualification/QualificationTab";
import { EMPTY_RECORDS, type KnowledgeView } from "@/components/knowledge/view";
import { useCompanyPath, useSession } from "@/components/SessionProvider";
import { Button, ErrorNotice, ErrorState, Input, PageHeader, PageSkeleton, Tabs } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import {
  WHO_CAN_DECIDE_KNOWLEDGE,
  WHO_CAN_EDIT_KNOWLEDGE,
  WHO_CAN_VERIFY_KNOWLEDGE,
  canDecideKnowledge,
  canDeleteKnowledge,
  canEditKnowledge,
  canVerifyKnowledge,
  indexSnapshot,
  type KnowledgeRecords,
  type KnowledgeSnapshot,
  type Observation,
  type Vocabulary,
} from "@/lib/knowledge";
import { searchMatcher } from "@/lib/text";
import type { Certification, FinancialYear, Insurance, Person, Project, Registration, Service } from "@/lib/types";

/**
 * Coneixement de l'empresa (fase 2B del motor): què sap Tuvi de l'empresa i
 * amb quin estat. Tres vistes sobre les mateixes dades: el que és actual per
 * categoria, «Qualificació i mitjans» (solvència, experiència, assegurances,
 * registres, persones, equips i recursos quantitatius, 0052) i el que diu
 * l'històric.
 *
 * Tot l'estat que es pinta surt de la fotografia (`/knowledge/snapshot`): una
 * dada només surt «Verificada» si la fotografia diu que ho és, és a dir, si té
 * evidència enllaçada. «Declarat» és un estat vàlid, no una tasca pendent:
 * l'evidència només es demana quan una licitació la requereix. Per això no hi
 * ha cap mesurador global ni cap llista de tasques (addendum 3B-2, B1–B3, B5):
 * cada ítem porta el seu estat i les seves accions.
 */

type TabKey = "actual" | "qualificacio" | "historic";
const TABS: TabKey[] = ["actual", "qualificacio", "historic"];

function isTab(value: string | null): value is TabKey {
  return value !== null && (TABS as string[]).includes(value);
}

export default function CompanyKnowledgePage() {
  const path = useCompanyPath();
  const { membership } = useSession();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const snapshot = useResource<KnowledgeSnapshot>(path("/knowledge/snapshot"));
  const observations = useResource<Observation[]>(path("/knowledge/observations?status=candidate"));
  const records = useResource<KnowledgeRecords>(path("/knowledge/records"));
  const certifications = useResource<Certification[]>(path("/certifications"));
  const services = useResource<Service[]>(path("/services"));
  const vocabulary = useResource<Vocabulary>(path("/knowledge/vocabulary"));
  const financialYears = useResource<FinancialYear[]>(path("/financial-years"));
  const projects = useResource<Project[]>(path("/projects"));
  const insurances = useResource<Insurance[]>(path("/insurances"));
  const registrations = useResource<Registration[]>(path("/registrations"));
  const people = useResource<Person[]>(path("/people"));

  const [picked, setPicked] = useState<TabKey | null>(null);
  const [declaring, setDeclaring] = useState<DeclareTarget | null>(null);
  const [scoping, setScoping] = useState<Certification | null>(null);
  // Cerca sobre el que ja és a la pantalla: cap crida nova per tecla.
  const [search, setSearch] = useState("");
  const matches = useMemo(() => searchMatcher(search), [search]);

  const role = membership.role;
  const canEdit = canEditKnowledge(role);
  const canDelete = canDeleteKnowledge(role);
  const canDecide = canDecideKnowledge(role);
  const canVerify = canVerifyKnowledge(role);
  const companyName = membership.company.trade_name ?? membership.company.legal_name;

  /** Com `Tabs`: la pestanya viu a la URL, perquè es pugui enllaçar i Enrere hi torni. */
  function showTab(next: TabKey) {
    setPicked(next);
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set("tab", next);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  function reload() {
    void snapshot.reload();
    void observations.reload();
    void records.reload();
    void certifications.reload();
    void services.reload();
    void financialYears.reload();
    void projects.reload();
    void insurances.reload();
    void registrations.reload();
    void people.reload();
  }

  const index = useMemo(() => indexSnapshot(snapshot.data), [snapshot.data]);

  const title = "Coneixement de l'empresa";
  if (snapshot.error && !snapshot.data) {
    return (
      <>
        <PageHeader title={title} description={companyName} />
        <ErrorState
          title="No hem pogut carregar el coneixement de l'empresa"
          description="La fotografia del que se sap de l'empresa no ha respost. Les dades no s'han perdut: torna-ho a provar."
          action={
            <Button variant="primary" onClick={reload}>
              Torna-ho a provar
            </Button>
          }
        />
      </>
    );
  }
  if (!snapshot.data) {
    return <PageSkeleton variant="detail" title={title} context={`${companyName} · carregant el que se sap de l'empresa…`} />;
  }

  const view: KnowledgeView = {
    snapshot: snapshot.data,
    index,
    observations: new Map((observations.data ?? []).map((item) => [item.id, item])),
    superseded: new Set(
      snapshot.data.pending_validation
        .filter((item) => item.kind === "historic_candidate" && item.superseded_by_current && item.observation_id)
        .map((item) => item.observation_id as string),
    ),
    records: records.data ?? EMPTY_RECORDS,
    certifications: certifications.data ?? [],
    services: services.data ?? [],
    qualification: {
      financialYears: financialYears.data ?? [],
      projects: projects.data ?? [],
      insurances: insurances.data ?? [],
      registrations: registrations.data ?? [],
      people: people.data ?? [],
    },
    vocabulary: vocabulary.data,
    canEdit,
    canDecide,
    canVerify,
    canDelete,
    reload,
    declare: setDeclaring,
    editScope: setScoping,
    search,
    matches,
  };

  /** Verificada segons la fotografia: editar-la n'invalidarà la verificació. */
  const isVerified = (recordId: string) => index.claims.get(recordId)?.source === "current_verified";

  // Sempre s'obre al coneixement actual: cap vista de «feina pendent» (B5).
  const urlTab = searchParams?.get("tab") ?? null;
  const tab: TabKey = isTab(urlTab) ? urlTab : (picked ?? "actual");
  const secondaryError =
    observations.error ??
    records.error ??
    certifications.error ??
    services.error ??
    vocabulary.error ??
    financialYears.error ??
    projects.error ??
    insurances.error ??
    registrations.error ??
    people.error;

  return (
    <>
      <PageHeader
        title={title}
        description={`Què sap Tuvi de ${companyName} i amb quin estat: declarat, verificat amb evidència o detectat a l'històric. L'evidència només es demana quan una licitació la requereix.`}
      />

      {secondaryError && (
        <div className="mb-4">
          <ErrorNotice message={`Una part de les dades no s'ha pogut carregar: ${secondaryError}`} onRetry={reload} />
        </div>
      )}

      {(!canEdit || !canDecide || !canVerify) && (
        <p className="mb-4 text-[13px] leading-5 text-warning">
          {!canEdit && `Amb el teu rol pots consultar el coneixement, però no declarar-lo ni modificar-lo: ho fa ${WHO_CAN_EDIT_KNOWLEDGE}. `}
          {!canDecide && `Només ${WHO_CAN_DECIDE_KNOWLEDGE} pot confirmar o descartar el que diu l'històric. `}
          {!canVerify && `Només ${WHO_CAN_VERIFY_KNOWLEDGE} pot verificar una dada.`}
        </p>
      )}

      <div className="mb-4">
        <Input
          type="search"
          aria-label="Cerca al coneixement"
          placeholder="Cerca al coneixement..."
          className="w-full md:max-w-md"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <Tabs
        className="mb-5"
        items={[
          { key: "actual", label: "Coneixement actual" },
          { key: "qualificacio", label: "Qualificació i mitjans" },
          { key: "historic", label: "Històric detectat" },
        ]}
        value={tab}
        onChange={(key) => setPicked(key as TabKey)}
      />

      {tab === "actual" && <CurrentTab view={view} />}
      {tab === "qualificacio" && <QualificationTab view={view} />}
      {tab === "historic" && <HistoricTab view={view} onShowCurrent={() => showTab("actual")} />}

      {declaring && (
        <DeclareDialog
          target={declaring}
          vocabulary={vocabulary.data}
          records={records.data}
          canDelete={canDelete}
          verified={"record" in declaring && declaring.record ? isVerified(declaring.record.id) : false}
          onClose={() => setDeclaring(null)}
          onSaved={reload}
        />
      )}
      {scoping && (
        <ScopeDialog
          certification={scoping}
          vocabulary={vocabulary.data}
          verified={isVerified(scoping.id)}
          onClose={() => setScoping(null)}
          onSaved={reload}
        />
      )}
    </>
  );
}
