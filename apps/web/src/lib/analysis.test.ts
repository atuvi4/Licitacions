import { describe, expect, it } from "vitest";

import vocabulary from "./analysis-vocabulary.generated.json";
import {
  DIMENSION_VALUES,
  FINDING_ORDER,
  LABELS,
  MISSING_DESTINATION,
  MISSING_TEXT,
  RESOLVE,
  decisionAnalysisState,
  dimensionValue,
  explanation,
  formatPoints,
  label,
  levelOf,
  resolveFor,
  sortFindings,
  summaryOf,
  summarySentences,
  type RenderedGroup,
} from "./analysis";
import { analysisFixture } from "@/test/analysis-fixture";

describe("vocabulari del motor", () => {
  it("cada valor que la UI pinta té text (cobertura contra el JSON generat del backend)", () => {
    for (const [group, values] of Object.entries(vocabulary.rendered)) {
      for (const value of values) {
        expect(LABELS[group as RenderedGroup]?.[value], `${group}.${value}`).toBeTruthy();
      }
    }
  });

  it("no hi ha etiquetes ni destins de valors que el backend ja no té", () => {
    for (const [group, labels] of Object.entries(LABELS)) {
      expect(Object.keys(labels).sort()).toEqual([...(vocabulary.rendered as Record<string, string[]>)[group]].sort());
    }
    const current = (vocabulary.explanations as Record<string, Record<string, string>>)[vocabulary.rules_version];
    for (const code of Object.keys(RESOLVE)) expect(current).toHaveProperty(code);
    expect([...FINDING_ORDER].sort()).toEqual([...vocabulary.rendered.finding_type].sort());
  });

  it("les explicacions són el text canònic del backend, tal com és", () => {
    const tables = vocabulary.explanations as Record<string, Record<string, string>>;
    expect(vocabulary.rules_version).toBe("phase3a-analysis-v3");
    // Cada run, amb el copy de la seva versió.
    expect(explanation("TURNOVER_UNKNOWN", "phase3a-analysis-v2")).toBe(tables["phase3a-analysis-v2"].TURNOVER_UNKNOWN);
    expect(explanation("TURNOVER_UNKNOWN", "phase3a-analysis-v3")).toBe(tables["phase3a-analysis-v3"].TURNOVER_UNKNOWN);
    // Un codi que el JSON encara no coneix: text neutre, mai el codi.
    expect(explanation("SOME_NEW_CODE", vocabulary.rules_version)).toBe("Explicació no disponible.");
    // Una versió desconeguda: text neutre, mai la taula d'una altra versió.
    expect(explanation("TURNOVER_UNKNOWN", "phase3a-analysis-v1")).toBe("Explicació no disponible.");
    expect(explanation("TURNOVER_UNKNOWN", "")).toBe("Explicació no disponible.");
  });

  it("«no se sap» és neutre i «verificat» no és mai una font", () => {
    expect(levelOf("fit_result", "unknown")).toBe("neutral");
    expect(levelOf("candidate_type", "subcontract_unknown")).toBe("neutral");
    expect(label("analysis_knowledge_source", "current_verified")).toBe("Coneixement actual de l'empresa");
    expect(Object.values(LABELS.analysis_knowledge_source).join(" ")).not.toMatch(/Verificat/);
    expect(label("assessment_verification", "verified")).toBe("Verificat");
    expect(label("finding_type", "blocker_potential")).toMatch(/no confirmat/);
    expect(label("fit_result", "unexpected")).toBe("Valor no disponible");
  });

  it("valors curts de les dimensions de Requisits: cobreixen el vocabulari i no repeteixen el prefix", () => {
    for (const [group, values] of Object.entries(DIMENSION_VALUES)) {
      expect(Object.keys(values).sort(), group).toEqual([...(vocabulary.rendered as Record<string, string[]>)[group]].sort());
      expect(Object.values(values).join(" | "), group).not.toMatch(/Prova|Font|Verificació|Territori/);
    }
    expect(Object.values(DIMENSION_VALUES.analysis_knowledge_source).join(" ")).not.toMatch(/verificat/i);
    expect(dimensionValue("proof_status", "sufficient")).toBe("suficient");
    expect(dimensionValue("territory_verdict", "not_applicable")).toBe("no aplica");
    expect(dimensionValue("analysis_knowledge_source", "not_applicable")).toBe("no aplica");
    expect(dimensionValue("assessment_verification", "unexpected")).toBe("valor no disponible");
    // Les etiquetes llargues de les altres superfícies no canvien.
    expect(label("territory_verdict", "not_applicable")).toBe("Sense territori");
  });

  it("«On es resol» només on el destí és inequívoc", () => {
    expect(resolveFor("TURNOVER_UNKNOWN")?.href).toBe("/empresa/coneixement?tab=qualificacio&seccio=solvencia");
    expect(resolveFor("NO_STRUCTURE_TO_COMPARE")).toBeNull();
    expect(resolveFor("CRITERION_NOT_ASSESSABLE_PRICE")).toBeNull();
    expect(resolveFor("PROOF_MISSING")).toBeNull();
    expect(resolveFor("PROOF_MISSING_FOR_CLAIM")).toBeNull();
    expect(levelOf("finding_type", "blocker_potential")).toBe("attention");
  });
});

describe("resum i ordre", () => {
  it("els findings segueixen l'ordre de la spec", () => {
    const ordered = sortFindings(analysisFixture().findings).map((f) => f.finding_type);
    expect(ordered).toEqual(["blocker_potential", "missing_information", "warning"]);
  });

  it("les frases surten només dels comptadors i diuen que no se sap no és negatiu", () => {
    const sentences = summarySentences(summaryOf(analysisFixture()));
    expect(sentences.join(" ")).toContain("2 requisits no es poden avaluar amb la informació actual (no se sap): no vol dir que no es compleixin.");
    expect(sentences.join(" ")).toContain("1 possible bloqueig a revisar: cap no està confirmat.");
    expect(sentences.join(" ")).toContain("1 requisit consta com a «No compleix» en l'anàlisi actual.");
    // Les revisions necessàries, just després dels possibles bloqueigs (spec §5.5.1).
    expect(sentences[1]).toBe("1 element marcat com a «Cal revisar».");
    expect(summarySentences(summaryOf({ summary: { review_required_count: 3 } as never }))).toEqual(["3 elements marcats com a «Cal revisar»."]);
    expect(sentences.join(" ")).not.toMatch(/%|puntuació|recomana|dada actual/i);
  });

  it("un sol formatador de punts per a totes les superfícies", () => {
    expect(formatPoints(15)).toBe("15");
    expect(formatPoints(2.5)).toBe("2,5");
    expect(formatPoints(1 / 3)).toBe("0,33");
  });

  it("el 409 `requirements` té un destí a la pestanya Requisits", () => {
    expect(Object.keys(MISSING_DESTINATION)).toEqual(Object.keys(MISSING_TEXT));
    expect(MISSING_DESTINATION.requirements("t9").href).toBe("/licitacions/t9?tab=requisits");
  });

  it("un resum antic incomplet no peta ni mostra NaN", () => {
    const partial = summaryOf({ summary: { fit: { satisfies: 1 } } as never });
    expect(partial.questions).toEqual({ buyer: 0, company: 0 });
    expect(partial.fit.unknown).toBe(0);
    expect(summarySentences(partial).join(" ")).not.toMatch(/NaN|undefined/);
  });
});

describe("decisionAnalysisState", () => {
  const run = analysisFixture();
  const base = { data: null, loading: false, error: null, status: null };
  it("els cinc estats", () => {
    expect(decisionAnalysisState({ ...base, loading: true })).toEqual({ kind: "loading" });
    expect(decisionAnalysisState({ ...base, data: run })).toEqual({ kind: "current", run });
    const stale = { ...run, stale: true };
    expect(decisionAnalysisState({ ...base, data: stale })).toEqual({ kind: "stale", run: stale });
    expect(decisionAnalysisState({ ...base, error: "x", status: 404 })).toEqual({ kind: "absent" });
    expect(decisionAnalysisState({ ...base, error: "x", status: 500 })).toEqual({ kind: "error" });
  });
  it("una recàrrega amb dades prèvies no torna a loading", () => {
    expect(decisionAnalysisState({ ...base, data: run, loading: true }).kind).toBe("current");
  });
});
