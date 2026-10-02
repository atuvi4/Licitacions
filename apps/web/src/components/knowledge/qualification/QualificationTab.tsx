"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button, Card, CardHeader, EmptyState, PermissionNote, cn } from "@/components/ui";
import {
  INSURANCE_LIMIT_KIND_LABELS,
  INSURANCE_TYPE_LABELS,
  PROJECT_STATUS_LABELS,
  REGISTRATION_TYPE_LABELS,
  formatAmount,
  formatDay,
} from "@/lib/format";
import {
  EQUIPMENT_ACCESS_LABELS,
  FACT_LABELS,
  NUMERIC_FACT_KEYS,
  NUMERIC_FACT_UNITS,
  WHO_CAN_EDIT_KNOWLEDGE,
  recordState,
  serviceLineLabel,
  type EffectiveState,
  type EquipmentRecord,
  type KnowledgeLifecycle,
  type OperatingFactRecord,
} from "@/lib/knowledge";
import type { FinancialYear, Insurance, Person, Project, Registration } from "@/lib/types";

import { KnowledgeGap, KnowledgeItem } from "../KnowledgeItem";
import { NoResults } from "../NoResults";
import type { KnowledgeView } from "../view";
import { EquipmentDrawer } from "./EquipmentDrawer";
import { FinancialYearDrawer } from "./FinancialYearDrawer";
import { InsuranceDrawer } from "./InsuranceDrawer";
import { equipmentTypeLabel, profileLabel } from "./labels";
import { NumericFactDrawer } from "./NumericFactDrawer";
import { PersonDrawer } from "./PersonDrawer";
import { ProjectDrawer } from "./ProjectDrawer";
import { RegistrationDrawer } from "./RegistrationDrawer";
import { ACTION, Chips } from "./shared";

export type SectionKey = "solvencia" | "experiencia" | "assegurances" | "registres" | "persones" | "equips" | "recursos";

interface SectionInfo {
  key: SectionKey;
  title: string;
  description: string;
  /** El buit, sense cap judici: no saber-ho no vol dir que no. */
  empty: string;
  add: string;
}

export const QUALIFICATION_SECTIONS: SectionInfo[] = [
  {
    key: "solvencia",
    title: "Solvència econòmica",
    description: "Exercicis econòmics: any, xifra de negoci i moneda, amb l'evidència que els acredita.",
    empty: "No hi ha exercicis econòmics informats",
    add: "Afegeix un exercici",
  },
  {
    key: "experiencia",
    title: "Experiència",
    description: "Referències d'execució: contractes fets o en curs, amb els imports realment executats cada exercici.",
    empty: "No hi ha referències d'execució informades",
    add: "Afegeix una referència",
  },
  {
    key: "assegurances",
    title: "Assegurances",
    description: "Pòlisses vigents i els seus límits, tal com consten a la pòlissa.",
    empty: "No hi ha assegurances informades",
    add: "Afegeix una assegurança",
  },
  {
    key: "registres",
    title: "Registres",
    description: "Inscripcions i registres: tipus, emissor, identificador i les línies de servei que inscriuen.",
    empty: "No hi ha registres informats",
    add: "Afegeix un registre",
  },
  {
    key: "persones",
    title: "Persones",
    description: "L'equip i els perfils professionals que cobreix cada persona.",
    empty: "No hi ha persones informades",
    add: "Afegeix una persona",
  },
  {
    key: "equips",
    title: "Equips",
    description: "Instruments i equips que l'empresa pot adscriure a un contracte. No és el catàleg de productes.",
    empty: "No hi ha equips informats",
    add: "Afegeix un equip",
  },
  {
    key: "recursos",
    title: "Recursos quantitatius",
    description: "Recursos que es compten, com les adreces IPv4 que l'empresa pot assignar.",
    empty: "No hi ha recursos quantitatius informats",
    add: "Informa",
  },
];

function isSection(value: string | null): value is SectionKey {
  return value !== null && QUALIFICATION_SECTIONS.some((section) => section.key === value);
}

type Editing =
  | { section: "solvencia"; record?: FinancialYear }
  | { section: "experiencia"; record?: Project }
  | { section: "assegurances"; record?: Insurance }
  | { section: "registres"; record?: Registration }
  | { section: "persones"; record?: Person }
  | { section: "equips"; record?: EquipmentRecord }
  | { section: "recursos"; factKey: string; record?: OperatingFactRecord };

function lifecycleOf(record: { lifecycle?: KnowledgeLifecycle }): KnowledgeLifecycle {
  return record.lifecycle ?? "current";
}

function validity(starts: string | null | undefined, expires: string | null | undefined): string | null {
  if (starts && expires) return `Vigent del ${formatDay(starts)} al ${formatDay(expires)}`;
  if (expires) return `Vigent fins al ${formatDay(expires)}`;
  if (starts) return `Vigent des del ${formatDay(starts)}`;
  return null;
}

function asOfText(asOf: string | null | undefined): string | null {
  return asOf ? `Informació a ${formatDay(asOf)}` : null;
}

function join(...parts: Array<string | null | undefined | false>): string {
  return parts.filter(Boolean).join(" · ");
}

/**
 * «Qualificació i mitjans»: l'entrada del coneixement que demanen els plecs per
 * acreditar solvència i mitjans. Cada fitxa porta el seu estat (Declarat,
 * Pendent de verificar, Verificat), la seva evidència i, per a qui pot, l'acció
 * de verificar. Aquí no s'avalua cap plec.
 */
export function QualificationTab({ view }: { view: KnowledgeView }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { index, records, vocabulary, canEdit, canVerify, canDelete, qualification } = view;
  const [picked, setPicked] = useState<SectionKey | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const urlSection = searchParams?.get("seccio") ?? null;
  const section: SectionKey = isSection(urlSection) ? urlSection : (picked ?? "solvencia");
  const common = { canEdit, canVerify, onChanged: view.reload };
  const isVerified = (id: string) => index.claims.get(id)?.source === "current_verified";
  const lineLabel = (code: string) => serviceLineLabel(code, vocabulary);

  // Enllaç antic `/experiencia/{id}` -> `?seccio=experiencia&project={id}`: obre el
  // calaix d'aquell projecte un sol cop i treu el paràmetre de la URL.
  const wantedProject = searchParams?.get("project") ?? null;
  const openedProject = useRef<string | null>(null);
  useEffect(() => {
    if (!wantedProject || openedProject.current === wantedProject) return;
    const project = qualification.projects.find((item) => item.id === wantedProject);
    if (!project) return;
    openedProject.current = wantedProject;
    if (canEdit) setEditing({ section: "experiencia", record: project });
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.delete("project");
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }, [wantedProject, qualification.projects, canEdit, searchParams, router, pathname]);

  function choose(next: SectionKey) {
    setPicked(next);
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set("seccio", next);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  /* Les fitxes de cada secció, ja filtrades pel cercador. */
  const years = [...qualification.financialYears]
    .sort((a, b) => b.year - a.year)
    .filter((item) => view.matches(String(item.year), item.notes));
  const projects = [...qualification.projects]
    .sort((a, b) => (b.start_date ?? "").localeCompare(a.start_date ?? ""))
    .filter((item) => view.matches(item.name, item.client_name, item.description, ...(item.service_line_codes ?? []).map(lineLabel)));
  const insurances = qualification.insurances.filter((item) => view.matches(INSURANCE_TYPE_LABELS[item.type], item.provider));
  const registrations = qualification.registrations.filter((item) =>
    view.matches(item.name, REGISTRATION_TYPE_LABELS[item.type], item.issuer, item.registration_number, ...(item.scope_service_lines ?? []).map(lineLabel)),
  );
  const people = qualification.people.filter((item) =>
    view.matches(item.first_name, item.last_name, item.role, ...(item.profile_codes ?? []).map((code) => profileLabel(code, vocabulary))),
  );
  const equipment = (records.equipment ?? []).filter((item) =>
    view.matches(equipmentTypeLabel(item.equipment_type, vocabulary), item.manufacturer, item.model),
  );
  const numericFacts = NUMERIC_FACT_KEYS.filter((key) => view.matches(FACT_LABELS[key]));
  const factOf = (key: string) => records.operating_facts.find((item) => item.key === key);

  const states: Record<SectionKey, EffectiveState[]> = {
    solvencia: qualification.financialYears.map((item) => recordState(item.id, lifecycleOf(item), index)),
    experiencia: qualification.projects.map((item) => recordState(item.id, lifecycleOf(item), index)),
    assegurances: qualification.insurances.map((item) => recordState(item.id, lifecycleOf(item), index)),
    registres: qualification.registrations.map((item) => recordState(item.id, lifecycleOf(item), index)),
    persones: qualification.people.map((item) => recordState(item.id, lifecycleOf(item), index)),
    equips: (records.equipment ?? []).map((item) => recordState(item.id, item.lifecycle, index)),
    recursos: NUMERIC_FACT_KEYS.map((key) => factOf(key))
      .filter((item): item is OperatingFactRecord => Boolean(item))
      .map((item) => recordState(item.id, item.lifecycle, index)),
  };
  const shown: Record<SectionKey, number> = {
    solvencia: years.length,
    experiencia: projects.length,
    assegurances: insurances.length,
    registres: registrations.length,
    persones: people.length,
    equips: equipment.length,
    recursos: numericFacts.filter((key) => factOf(key)).length,
  };
  const searching = view.search.trim().length > 0;
  if (searching && Object.values(shown).every((count) => count === 0) && numericFacts.length === 0) return <NoResults search={view.search} />;

  const info = QUALIFICATION_SECTIONS.find((item) => item.key === section) as SectionInfo;
  const open = (next: Editing) => setEditing(next);
  const addAction = (onClick: () => void, label = info.add) =>
    canEdit ? (
      <Button size="sm" variant="secondary" onClick={onClick}>
        {label}
      </Button>
    ) : null;
  const editButton = (label: string, onClick: () => void) =>
    canEdit ? (
      <Button size="sm" variant="ghost" className={ACTION} aria-label={label} onClick={onClick}>
        Edita
      </Button>
    ) : null;
  /** Cap buit sense sortida: qui pot, afegeix; qui no, sap qui ho pot fer. */
  const empty = (onAdd: () => void) => (
    <EmptyState
      className="py-10"
      title={info.empty}
      description="No saber-ho no vol dir que no: vol dir que encara ningú no ho ha informat."
      action={
        canEdit ? (
          <Button variant="primary" onClick={onAdd}>
            {info.add}
          </Button>
        ) : (
          <PermissionNote who={WHO_CAN_EDIT_KNOWLEDGE} what="informar-ho">
            <Button variant="primary" disabled>
              {info.add}
            </Button>
          </PermissionNote>
        )
      }
    />
  );

  let body: ReactNode;
  switch (section) {
    case "solvencia":
      body = years.length === 0 ? empty(() => open({ section: "solvencia" })) : (
        <ul>
          {years.map((item) => (
            <KnowledgeItem
              key={item.id}
              kind="financial_year"
              recordId={item.id}
              title={`Exercici ${item.year}`}
              subtitle={join(
                item.revenue !== null ? `Xifra de negoci: ${formatAmount(item.revenue, item.currency)}` : "Xifra de negoci sense informar",
                asOfText(item.as_of),
              )}
              state={recordState(item.id, lifecycleOf(item), index)}
              actions={editButton(`Edita l'exercici ${item.year}`, () => open({ section: "solvencia", record: item }))}
              {...common}
            />
          ))}
        </ul>
      );
      break;
    case "experiencia":
      body = projects.length === 0 ? empty(() => open({ section: "experiencia" })) : (
        <ul>
          {projects.map((item) => (
            <KnowledgeItem
              key={item.id}
              kind="project"
              recordId={item.id}
              title={item.name}
              subtitle={join(
                item.client_name,
                PROJECT_STATUS_LABELS[item.status],
                (item.start_date || item.end_date) && `${formatDay(item.start_date)} – ${item.end_date ? formatDay(item.end_date) : "en curs"}`,
                item.amount !== null && `Import total: ${formatAmount(item.amount, item.currency)}`,
                asOfText(item.as_of),
              )}
              state={recordState(item.id, lifecycleOf(item), index)}
              actions={editButton(`Edita ${item.name}`, () => open({ section: "experiencia", record: item }))}
              {...common}
            >
              {(item.service_line_codes ?? []).length > 0 ? (
                <Chips label="Línies:" items={(item.service_line_codes ?? []).map(lineLabel)} />
              ) : (
                <p className="mt-1.5 text-caption font-medium text-warning">Sense cap línia de servei triada</p>
              )}
              <Chips label="CPV:" items={(item.cpvs ?? []).map((cpv) => cpv.cpv_code)} />
              {(item.annual_amounts ?? []).length > 0 ? (
                <p className="mt-1 text-caption text-muted tabular-nums">
                  Executat per exercici:{" "}
                  {(item.annual_amounts ?? []).map((row) => `${row.year}: ${formatAmount(row.amount, item.currency)}`).join(" · ")}
                </p>
              ) : (
                <p className="mt-1 text-caption text-muted">Sense imports executats per exercici.</p>
              )}
            </KnowledgeItem>
          ))}
        </ul>
      );
      break;
    case "assegurances":
      body = insurances.length === 0 ? empty(() => open({ section: "assegurances" })) : (
        <ul>
          {insurances.map((item) => (
            <KnowledgeItem
              key={item.id}
              kind="insurance"
              recordId={item.id}
              title={join(INSURANCE_TYPE_LABELS[item.type], item.provider)}
              subtitle={join(validity(item.starts_at, item.expires_at), asOfText(item.as_of)) || undefined}
              state={recordState(item.id, lifecycleOf(item), index)}
              actions={editButton(`Edita l'assegurança ${INSURANCE_TYPE_LABELS[item.type]}`, () => open({ section: "assegurances", record: item }))}
              {...common}
            >
              {(item.limits ?? []).length > 0 ? (
                <Chips
                  label="Límits:"
                  items={(item.limits ?? []).map((limit) => `${INSURANCE_LIMIT_KIND_LABELS[limit.kind] ?? limit.kind}: ${formatAmount(limit.amount, limit.currency)}`)}
                />
              ) : (
                <p className="mt-1.5 text-caption text-muted">Sense límits informats.</p>
              )}
            </KnowledgeItem>
          ))}
        </ul>
      );
      break;
    case "registres":
      body = registrations.length === 0 ? empty(() => open({ section: "registres" })) : (
        <ul>
          {registrations.map((item) => (
            <KnowledgeItem
              key={item.id}
              kind="registration"
              recordId={item.id}
              title={item.name}
              subtitle={join(
                REGISTRATION_TYPE_LABELS[item.type],
                item.issuer,
                item.registration_number && `Núm. ${item.registration_number}`,
                validity(item.issued_at, item.expires_at),
                asOfText(item.as_of),
              )}
              state={recordState(item.id, lifecycleOf(item), index)}
              actions={editButton(`Edita ${item.name}`, () => open({ section: "registres", record: item }))}
              {...common}
            >
              {(item.scope_service_lines ?? []).length > 0 ? (
                <Chips label="Abast:" items={(item.scope_service_lines ?? []).map(lineLabel)} />
              ) : (
                <p className="mt-1.5 text-caption font-medium text-warning">Abast pendent d&apos;estructurar</p>
              )}
            </KnowledgeItem>
          ))}
        </ul>
      );
      break;
    case "persones":
      body = people.length === 0 ? empty(() => open({ section: "persones" })) : (
        <ul>
          {people.map((item) => {
            const name = [item.first_name, item.last_name].filter(Boolean).join(" ");
            const state = recordState(item.id, lifecycleOf(item), index);
            // Sense cap perfil no entra a la fotografia: no es pot verificar ni comptar com a mitjà.
            const noProfile = (item.profile_codes ?? []).length === 0 && state.state === "inactive";
            return (
              <KnowledgeItem
                key={item.id}
                kind="person"
                recordId={item.id}
                title={name}
                subtitle={join(item.role, item.lifecycle === "retired" ? "No activa" : null, asOfText(item.as_of)) || undefined}
                state={state}
                stateLabel={noProfile ? "Sense perfil de qualificació" : undefined}
                actions={editButton(`Edita ${name}`, () => open({ section: "persones", record: item }))}
                {...common}
              >
                {(item.profile_codes ?? []).length > 0 ? (
                  <Chips label="Perfils:" items={(item.profile_codes ?? []).map((code) => profileLabel(code, vocabulary))} />
                ) : (
                  <p className="mt-1.5 text-caption text-warning">
                    Afegeix almenys un perfil perquè aquesta persona pugui comptar com a mitjà professional.
                  </p>
                )}
              </KnowledgeItem>
            );
          })}
        </ul>
      );
      break;
    case "equips":
      body = equipment.length === 0 ? empty(() => open({ section: "equips" })) : (
        <ul>
          {equipment.map((item) => {
            const title = join(equipmentTypeLabel(item.equipment_type, vocabulary), [item.manufacturer, item.model].filter(Boolean).join(" "));
            return (
              <KnowledgeItem
                key={item.id}
                kind="equipment"
                recordId={item.id}
                title={title}
                subtitle={join(
                  item.quantity > 1 && `${item.quantity} unitats`,
                  item.access_mode && EQUIPMENT_ACCESS_LABELS[item.access_mode],
                  item.serial_reference && `Núm. de sèrie ${item.serial_reference}`,
                  item.calibration_required === true &&
                    (item.calibration_valid_until ? `Calibratge vàlid fins al ${formatDay(item.calibration_valid_until)}` : "Requereix calibratge (sense data)"),
                  item.calibration_required === false && "Sense calibratge",
                  asOfText(item.as_of),
                ) || undefined}
                state={recordState(item.id, item.lifecycle, index)}
                actions={editButton(`Edita ${title}`, () => open({ section: "equips", record: item }))}
                {...common}
              />
            );
          })}
        </ul>
      );
      break;
    case "recursos":
      body = (
        <ul>
          {numericFacts.map((key) => {
            const fact = factOf(key);
            const label = FACT_LABELS[key];
            if (!fact) {
              return (
                <KnowledgeGap
                  key={key}
                  title={label}
                  subtitle="Sense cap dada. No saber-ho no vol dir que no: vol dir que ningú no ho ha informat."
                  state={{ state: "unknown", flags: [] }}
                  actions={addAction(() => open({ section: "recursos", factKey: key }), "Informa")}
                />
              );
            }
            const number = fact.value_number != null ? Number(fact.value_number).toLocaleString("ca-ES") : null;
            return (
              <KnowledgeItem
                key={fact.id}
                kind="operating_fact"
                recordId={fact.id}
                title={label}
                subtitle={join(number !== null ? `${number} ${NUMERIC_FACT_UNITS[key] ?? ""}`.trim() : "Sense nombre", fact.detail, asOfText(fact.as_of))}
                state={recordState(fact.id, fact.lifecycle, index)}
                actions={editButton(`Edita ${label}`, () => open({ section: "recursos", factKey: key, record: fact }))}
                {...common}
              />
            );
          })}
        </ul>
      );
      break;
  }

  const sectionAction =
    section === "recursos"
      ? null
      : addAction(() => open({ section } as Editing));
  const drawer = { canDelete, onClose: () => setEditing(null), onSaved: view.reload };

  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Seccions de qualificació i mitjans">
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
          {QUALIFICATION_SECTIONS.map((item) => {
            const active = item.key === section;
            const total = states[item.key].length;
            const toVerify = states[item.key].filter((state) => state.state === "declared").length;
            return (
              <li key={item.key}>
                <button
                  type="button"
                  aria-current={active ? "true" : undefined}
                  data-section={item.key}
                  onClick={() => choose(item.key)}
                  className={cn(
                    "flex h-full min-h-11 w-full flex-col items-start rounded-card border px-3 py-2 text-left transition-colors",
                    active ? "border-brand bg-brand-soft" : "border-border bg-surface hover:bg-subtle",
                  )}
                >
                  <span className={cn("text-[13px] leading-5", active ? "font-semibold text-ink" : "font-medium text-ink")}>{item.title}</span>
                  <span className="text-caption text-muted tabular-nums">
                    {total === 0 ? "Sense dades" : `${total} ${total === 1 ? "fitxa" : "fitxes"}`}
                    {toVerify > 0 && ` · ${toVerify} per verificar`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <Card id={`qualificacio-${section}`}>
        <CardHeader title={info.title} description={info.description} action={sectionAction} />
        {body}
      </Card>

      {editing?.section === "solvencia" && <FinancialYearDrawer record={editing.record} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />}
      {editing?.section === "experiencia" && (
        <ProjectDrawer
          record={editing.record}
          vocabulary={vocabulary}
          canEdit={canEdit}
          verified={editing.record ? isVerified(editing.record.id) : false}
          {...drawer}
        />
      )}
      {editing?.section === "assegurances" && (
        <InsuranceDrawer record={editing.record} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />
      )}
      {editing?.section === "registres" && (
        <RegistrationDrawer record={editing.record} vocabulary={vocabulary} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />
      )}
      {editing?.section === "persones" && (
        <PersonDrawer record={editing.record} vocabulary={vocabulary} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />
      )}
      {editing?.section === "equips" && (
        <EquipmentDrawer record={editing.record} vocabulary={vocabulary} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />
      )}
      {editing?.section === "recursos" && (
        <NumericFactDrawer factKey={editing.factKey} record={editing.record} verified={editing.record ? isVerified(editing.record.id) : false} {...drawer} />
      )}
    </div>
  );
}
