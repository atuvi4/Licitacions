import { describe, expect, it } from "vitest";

import { reviewRequirements } from "@/test/review-fixture";

import {
  type EditableRequirement,
  correctionPayload,
  correctionSummary,
  diff,
  draftFrom,
  isNoop,
  newValue,
  nextKey,
  noteRequired,
  operatorKey,
  operatorOptions,
  parseNumber,
  rebase,
  validate,
} from "./correction";

const base: EditableRequirement = {
  title: "Latència màxima en serveis de dades",
  description: "Latència entre dos punts.",
  category: "technical_requirement",
  obligation: "mandatory",
  modality: "mandatory",
  values: [
    { key: "latencia", operator: "gte", value: 70, unit_label: "ms", source: "document" },
    { key: "reviewer_value_1", operator: ">=", value: 64, unit_label: "trucades", source: "reviewer" },
  ],
};

const item = reviewRequirements[0];

describe("càrrega diferencial", () => {
  it("obrir i desar sense tocar res és un no-op", () => {
    expect(isNoop(diff(base, draftFrom(base)))).toBe(true);
  });

  it("cada camp de text o de llista viatja sol, només si canvia", () => {
    const draft = draftFrom(base);
    expect(diff(base, { ...draft, title: "  Latència < 70 ms  " })).toEqual({
      title: "Latència < 70 ms",
      values: [],
      remove_values: [],
    });
    expect(diff(base, { ...draft, description: "" }).description).toBeNull();
    expect(diff(base, { ...draft, category: "contract_condition" }).category).toBe("contract_condition");
    expect(diff(base, { ...draft, obligation: "informative" })).toEqual({ obligation: "informative", values: [], remove_values: [] });
    expect(diff(base, { ...draft, modality: "conditional" })).toEqual({ modality: "conditional", values: [], remove_values: [] });
  });

  it("editar un valor conserva la clau i l'operador equivalent; el valor intacte no s'envia", () => {
    const draft = draftFrom(base);
    draft.values[0] = { ...draft.values[0], text: "80" };
    const content = diff(base, draft);
    expect(content.values).toEqual([{ key: "latencia", operator: "gte", value: 80, unit_label: "ms" }]);
    // Canviar l'operador a «≥» un altre cop no és un canvi: `gte` i `>=` són el mateix.
    const same = draftFrom(base);
    same.values[0] = { ...same.values[0], operator: ">=" };
    expect(isNoop(diff(base, same))).toBe(true);
  });

  it("un valor nou rep una clau lliure i vàlida que no trepitja ningú", () => {
    const draft = draftFrom(base);
    draft.removed = ["reviewer_value_2"];
    const row = newValue(draft, base);
    expect(row.key).toBe("reviewer_value_3");
    expect(row.key).toMatch(/^[a-z][a-z0-9_]{0,59}$/);
    expect(nextKey([])).toBe("reviewer_value_1");
  });

  it("treure un valor existent va a remove_values; treure'n un de nou, no", () => {
    const draft = draftFrom(base);
    const fresh = newValue(draft, base);
    const content = diff(base, { ...draft, values: [draft.values[1]], removed: ["latencia"] });
    expect(content.remove_values).toEqual(["latencia"]);
    expect(content.values).toEqual([]);
    expect(diff(base, { ...draft, values: [...draft.values] }).remove_values).toEqual([]);
    expect(fresh.isNew).toBe(true);
  });

  it("número, text, sí/no i sense valor, sense conversions silencioses", () => {
    const draft = draftFrom(base);
    const row = newValue(draft, base);
    const send = (patch: Partial<typeof row>) => diff(base, { ...draft, values: [...draft.values, { ...row, ...patch }] }).values[0];
    expect(send({ type: "number", text: "70" }).value).toBe(70);
    expect(send({ type: "number", text: "99,5" }).value).toBe(99.5);
    expect(send({ type: "text", text: "70" }).value).toBe("70");
    expect(send({ type: "boolean", bool: false }).value).toBe(false);
    expect(send({ type: "none" }).value).toBeNull();
    expect(send({ type: "number", text: "5", unit: "  trucades  " }).unit_label).toBe("trucades");
  });

  it("els números han de ser finits", () => {
    for (const bad of ["NaN", "Infinity", "-Infinity", "1e999", "7O", "", "70 ms"]) expect(parseNumber(bad)).toBeUndefined();
    expect(parseNumber("-3.25")).toBe(-3.25);
  });

  it("valida el títol, les unitats (fins a 32) i els números", () => {
    const draft = draftFrom(base);
    const unit = (text: string) => validate({ ...draft, values: [{ ...draft.values[0], unit: text }] }).values.latencia;
    expect(unit("u".repeat(32))).toBeUndefined();
    expect(unit("u".repeat(33))).toMatch(/32/);
    expect(validate({ ...draft, title: "  " }).title).toMatch(/buit/);
    expect(validate({ ...draft, values: [{ ...draft.values[0], text: "Infinity" }] }).values.latencia).toMatch(/número/);
  });

  it("l'estat sempre va explícit: el d'ara o verificat", () => {
    const content = diff(base, { ...draftFrom(base), title: "Nou" });
    expect(correctionPayload(item, content, "keep", "").verification_status).toBe("unreviewed");
    expect(correctionPayload(item, content, "verified", " comprovat ")).toMatchObject({ verification_status: "verified", note: "comprovat" });
    expect(correctionPayload(item, content, "keep", "")).not.toHaveProperty("values");
    expect(noteRequired({ ...item, verification_status: "partial" }, "keep")).toBe(true);
    expect(noteRequired({ ...item, verification_status: "partial" }, "verified")).toBe(false);
    expect(noteRequired(item, "keep")).toBe(false);
  });

  it("els operadors es mostren sense codis i es conserven els poc habituals", () => {
    expect(operatorKey("gte")).toBe(">=");
    expect(operatorOptions("gte").find((o) => o.value === ">=")?.label).toBe("≥");
    expect(operatorOptions("one_of").at(-1)).toEqual({ value: "one_of", label: "un de" });
    expect(operatorOptions("exists").find((o) => o.value === "exists")?.label).toBe("existeix");
  });
});

describe("rebase després d'un 409", () => {
  it("el que no s'ha tocat és el del servidor, el propi es manté i el comú es marca", () => {
    const current: EditableRequirement = { ...base, title: "Títol nou del servidor", modality: "conditional" };
    const draft = { ...draftFrom(base), modality: "optional", category: "contract_condition" };

    const result = rebase(base, current, draft);

    expect(result.draft.title).toBe("Títol nou del servidor");
    expect(result.draft.modality).toBe("optional");
    expect(result.draft.category).toBe("contract_condition");
    expect(result.changedOnServer).toEqual(["Títol", "Modalitat"]);
    expect(result.conflicts).toEqual(["Modalitat"]);
    // Contra la versió nova, només viatja el que ha canviat la persona.
    const content = diff(current, result.draft);
    expect(content).toMatchObject({ modality: "optional", category: "contract_condition" });
    expect(content.title).toBeUndefined();
  });

  it("valors: els intactes segueixen el servidor i una clau nova no en trepitja cap", () => {
    const current: EditableRequirement = {
      ...base,
      values: [base.values[0], { ...base.values[1], value: 65 }, { key: "reviewer_value_2", operator: null, value: 1, unit_label: null, source: "reviewer" }],
    };
    const draft = draftFrom(base);
    const mine = newValue(draft, base);
    expect(mine.key).toBe("reviewer_value_2");
    draft.values.push({ ...mine, text: "5" });

    const result = rebase(base, current, draft);

    expect(result.draft.values.find((v) => v.key === "reviewer_value_1")?.text).toBe("65");
    const keys = result.draft.values.map((v) => v.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toContain("reviewer_value_3");
    expect(result.changedOnServer).toEqual(["Valors"]);
    expect(result.conflicts).toEqual([]);
  });
});

describe("resum de l'historial", () => {
  it("camps i nombre de valors, cap valor ni clau", () => {
    expect(correctionSummary({ fields: ["title", "obligation"], values_set: ["a", "b"], values_removed: ["c"] })).toBe(
      "Títol · Obligació · 2 valors · 1 valor tret",
    );
    expect(correctionSummary({ fields: [], values_set: [], values_removed: [] })).toBeNull();
    expect(correctionSummary(undefined)).toBeNull();
  });
});
