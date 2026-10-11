import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";
import type { CompletenessSection, ExpiryAlert } from "@/lib/types";

import {
  AREA_META,
  SECTION_LINKS,
  areaActionLabel,
  areaTitle,
  emptyAreas,
  expiryLine,
  expiryRoute,
  footerLine,
  groupAreas,
  isRadarSection,
  listNames,
  profileContext,
  splitRadarSections,
} from "./areas";

// Les onze claus reals de `completeness.sections` (apps/api services/completeness.py).
const KEYS = [
  "general",
  "services",
  "territories",
  "cpv",
  "solvency",
  "certifications",
  "registrations",
  "insurances",
  "team",
  "experience",
  "documents",
];

function section(key: string, status: CompletenessSection["status"]): CompletenessSection {
  return {
    key,
    label: key,
    weight: 10,
    ratio: status === "complete" ? 1 : status === "partial" ? 0.5 : 0,
    points: status === "complete" ? 10 : status === "partial" ? 5 : 0,
    status,
    detail: status === "empty" ? "cap dada" : "alguna dada",
    requires_evidence: false,
  };
}

const SECTIONS: CompletenessSection[] = [
  section("general", "complete"),
  section("services", "complete"),
  section("territories", "complete"),
  section("cpv", "complete"),
  section("solvency", "complete"),
  section("certifications", "complete"),
  section("registrations", "empty"),
  section("insurances", "empty"),
  section("team", "complete"),
  section("experience", "partial"),
  section("documents", "partial"),
];

function alert(overrides: Partial<ExpiryAlert>): ExpiryAlert {
  return {
    entity_type: "company_certification",
    entity_id: "x",
    label: "ISO 9001",
    expires_at: "2026-10-16",
    days_left: 30,
    temporal_status: "expiring_soon",
    ...overrides,
  };
}

describe("enllaços i textos fixos per àrea", () => {
  it("cada clau real té enllaç, subtítol i un «què en depèn» sense cap xifra (C-5)", () => {
    for (const key of KEYS) {
      expect(SECTION_LINKS[key], key).toMatch(/^\//);
      expect(AREA_META[key]?.subtitle, key).toBeTruthy();
      expect(AREA_META[key]?.dependsOn, key).toBeTruthy();
      expect(AREA_META[key]?.dependsOn, key).not.toMatch(/\d/);
      expect(AREA_META[key]?.add, key).toBeTruthy();
    }
  });

  it("les dades generals porten al formulari mogut a /empresa/dades", () => {
    expect(SECTION_LINKS.general).toBe("/empresa/dades");
    expect(SECTION_LINKS.experience).toBe("/experiencia");
    expect(SECTION_LINKS.documents).toBe("/documents");
  });

  it("el títol d'una àrea coneguda es mostra accentuat; una de desconeguda conserva l'etiqueta de l'API", () => {
    expect(areaTitle({ key: "territories", label: "Ambit geografic" })).toBe("Àmbit geogràfic");
    expect(areaTitle({ key: "team", label: "Equip huma" })).toBe("Equip humà");
    expect(areaTitle({ key: "nova_area", label: "Etiqueta de l'API" })).toBe("Etiqueta de l'API");
  });

  it("una llista llarga de noms s'escurça a quatre i diu quants en queden", () => {
    expect(listNames(["A", "B"])).toBe("A, B");
    expect(listNames(["A", "B", "C", "D"])).toBe("A, B, C, D");
    expect(listNames(["A", "B", "C", "D", "E"])).toBe("A, B, C, D i 1 més");
    expect(listNames(["A", "B", "C", "D", "E", "F", "G"])).toBe("A, B, C, D i 3 més");
  });

  it("l'acció depèn de l'estat real: afegir, afegir-hi dades o revisar; mai «completar» (B1)", () => {
    expect(areaActionLabel(section("registrations", "empty"))).toBe(AREA_META.registrations.add);
    expect(areaActionLabel(section("experience", "partial"))).toBe("Afegeix-hi dades");
    expect(areaActionLabel(section("cpv", "complete"))).toBe("Revisa");
    expect(areaActionLabel(section("desconeguda", "empty"))).toBe("Afegeix-hi dades");
    expect(areaActionLabel(section("experience", "partial"))).not.toMatch(/Completa/);
  });
});

describe("groupAreas", () => {
  it("agrupa per estat, en ordre sense dada → incompleta → al dia, i plega les que estan al dia", () => {
    const groups = groupAreas(SECTIONS);
    expect(groups.map((group) => group.key)).toEqual(["empty", "partial", "complete"]);
    expect(groups.map((group) => group.label)).toEqual(["Sense dada", "Amb dada incompleta", "Al dia"]);
    expect(groups[0].tint).toBe("blocking");
    expect(groups[0].rows.map((row) => row.key)).toEqual(["registrations", "insurances"]);
    expect(groups[1].collapsed).toBeFalsy();
    expect(groups[2].collapsed).toBe(true);
    expect(groups.reduce((sum, group) => sum + group.rows.length, 0)).toBe(SECTIONS.length);
  });

  it("no pinta cap grup buit", () => {
    const groups = groupAreas(SECTIONS.filter((item) => item.status !== "empty"));
    expect(groups.map((group) => group.key)).toEqual(["partial", "complete"]);
  });
});

describe("línies de context", () => {
  it("la capçalera diu empresa, nombre d'àrees i última modificació real", () => {
    const at = "2026-09-13T10:00:00Z";
    expect(profileContext("Empresa Demo Telecom SL", 11, at)).toBe(
      `Empresa Demo Telecom SL · 11 àrees · última modificació ${formatDate(at)}`,
    );
    expect(profileContext("Empresa Demo Telecom SL", 11, null)).toBe("Empresa Demo Telecom SL · 11 àrees");
  });

  it("el peu compta les àrees que demanen atenció, sense cap percentatge de completitud (B1)", () => {
    expect(footerLine(SECTIONS)).toBe("4 àrees demanen atenció · 7 al dia");
    expect(footerLine(SECTIONS.map((item) => ({ ...item, status: "complete" as const })))).toBe("Les 11 àrees estan al dia");
    expect(footerLine([section("cpv", "empty"), ...SECTIONS.slice(1, 3)])).toBe("1 àrea demana atenció · 2 al dia");
    expect(footerLine(SECTIONS)).not.toMatch(/%/);
  });

  it("emptyAreas dona el recompte i els noms de les àrees sense dada", () => {
    expect(emptyAreas(SECTIONS)).toEqual({ count: 2, names: ["Registres i classificacions", "Assegurances"], first: SECTIONS[6] });
    expect(emptyAreas(SECTIONS.slice(0, 3))).toEqual({ count: 0, names: [], first: null });
  });

  it("les preferències del Radar (CPV i àmbit geogràfic) van a part del coneixement corporatiu (B4)", () => {
    expect(KEYS.filter(isRadarSection)).toEqual(["territories", "cpv"]);
    const { radar, knowledge } = splitRadarSections(SECTIONS);
    expect(radar.map((item) => item.key)).toEqual(["territories", "cpv"]);
    expect(knowledge.map((item) => item.key)).toEqual(KEYS.filter((key) => !isRadarSection(key)));
    expect(knowledge).toHaveLength(9);
  });
});

describe("caducitats", () => {
  it("diu la data real i els dies («en N dies» / «fa N dies»), amb singular", () => {
    expect(expiryLine(alert({ days_left: 30 }))).toBe(`Caduca el ${formatDate("2026-10-16")} · en 30 dies`);
    expect(expiryLine(alert({ days_left: 1 }))).toBe(`Caduca el ${formatDate("2026-10-16")} · en 1 dia`);
    expect(expiryLine(alert({ days_left: 0 }))).toBe(`Caduca avui, ${formatDate("2026-10-16")}`);
    expect(expiryLine(alert({ days_left: -14, expires_at: "2026-09-02", temporal_status: "expired" }))).toBe(
      `Va caducar el ${formatDate("2026-09-02")} · fa 14 dies`,
    );
    expect(expiryLine(alert({ days_left: -1, temporal_status: "expired" }))).toContain("fa 1 dia");
  });

  it("cada tipus d'entitat real de l'API porta a la seva àrea", () => {
    expect(expiryRoute("company_certification")).toBe("/empresa/certificacions");
    expect(expiryRoute("company_insurance")).toBe("/empresa/assegurances");
    expect(expiryRoute("company_registration")).toBe("/empresa/registres");
    expect(expiryRoute("person_qualification")).toBe("/empresa/equip");
    expect(expiryRoute("document")).toBe("/documents");
    expect(expiryRoute("una_cosa_nova")).toBe("/empresa");
  });
});
