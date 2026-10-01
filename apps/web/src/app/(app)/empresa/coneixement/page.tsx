"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { CurrentTab } from "@/components/knowledge/CurrentTab";
import { DeclareDialog, type DeclareTarget } from "@/components/knowledge/DeclareDialog";
import { HistoricTab } from "@/components/knowledge/HistoricTab";
import { PendingTab } from "@/components/knowledge/PendingTab";
import { ScopeDialog } from "@/components/knowledge/ScopeDialog";
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
  summarize,
  type KnowledgeRecords,
  type KnowledgeSnapshot,
  type Observation,
  type Vocabulary,
} from "@/lib/knowledge";
import { searchMatcher } from "@/lib/text";
import type { Certification, Service } from "@/lib/types";

/**
 * Coneixement de l'empresa (fase 2B del motor): què sap Tuvi de l'empresa, amb
 * quina garantia, i què falta validar. Tres vistes sobre les mateixes dades:
 * la feina pendent, el que és actual per categoria, i el que diu l'històric.
 *
 * Tot l'estat que es pinta surt de la fotografia (`/knowledge/snapshot`): una
 * dada només surt «Verificada» si la fotografia diu que ho és, és a dir, si té
 * evidència enllaçada.
 */

type TabKey = "pendents" | "actual" | "historic";
const TABS: TabKey[] = ["pendents", "actual", "historic"];

function isTab(value: string | null): value is TabKey {
  return value !== null && (TABS as string[]).includes(value);
}

const TILES: Array<{ key: "verified" | "declared" | "pending" | "historic" | "unknown"; label: string; hint: string }> = [
  { key: "verified", label: "Verificat", hint: "amb evidència" },
  { key: "declared", label: "Declarat", hint: "sense acreditar encara" },
  { key: "pending", label: "Per acreditar", hint: "dades amb feina pendent" },
  { key: "historic", label: "Històric detectat", hint: "suggeriments per revisar" },
  { key: "unknown", label: "Desconegut", hint: "normes, fets i línies sense dada" },
];

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
  }

  const index = useMemo(() => indexSnapshot(snapshot.data), [snapshot.data]);
  const summary = useMemo(() => summarize(snapshot.data, vocabulary.data), [snapshot.data, vocabulary.data]);

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
    vocabulary: vocabulary.data,
    canEdit,
    canDecide,
    canVerify,
    reload,
    declare: setDeclaring,
    editScope: setScoping,
    search,
    matches,
  };

  /** Verificada segons la fotografia: editar-la n'invalidarà la verificació. */
  const isVerified = (recordId: string) => index.claims.get(recordId)?.source === "current_verified";

  const todo = summary.pending + summary.historic;
  const urlTab = searchParams?.get("tab") ?? null;
  const tab: TabKey = isTab(urlTab) ? urlTab : (picked ?? (todo > 0 ? "pendents" : "actual"));
  const secondaryError = observations.error ?? records.error ?? certifications.error ?? services.error ?? vocabulary.error;

  return (
    <>
      <PageHeader
        title={title}
        description={`Què sap Tuvi de ${companyName}: què és actual, què es pot acreditar i què falta validar.`}
      />

      {secondaryError && (
        <div className="mb-4">
          <ErrorNotice message={`Una part de les dades no s'ha pogut carregar: ${secondaryError}`} onRetry={reload} />
        </div>
      )}

      <dl className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5" aria-label="Resum del coneixement">
        {TILES.map((tile) => (
          <div key={tile.key} data-tile={tile.key} className="rounded-card border border-border bg-surface px-4 py-3 shadow-card">
            <dt className="text-caption font-medium text-muted">{tile.label}</dt>
            <dd className="mt-0.5 font-heading text-[24px] leading-tight font-semibold text-ink tabular-nums">{summary[tile.key]}</dd>
            <p className="text-caption text-muted">{tile.hint}</p>
          </div>
        ))}
      </dl>

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
          { key: "pendents", label: "Pendents de validar", count: todo > 0 ? todo : undefined, countLevel: "attention" },
          { key: "actual", label: "Coneixement actual" },
          { key: "historic", label: "Històric detectat", count: summary.historic > 0 ? summary.historic : undefined },
        ]}
        value={tab}
        onChange={(key) => setPicked(key as TabKey)}
      />

      {tab === "pendents" && <PendingTab view={view} onShowCurrent={() => showTab("actual")} />}
      {tab === "actual" && <CurrentTab view={view} />}
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
