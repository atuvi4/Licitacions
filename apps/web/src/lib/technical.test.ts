import { describe, expect, it } from "vitest";

import { requirementFixture, solutionFixture } from "@/test/technical-fixture";
import {
  applyFilter,
  awaitsValidation,
  canEditSolutions,
  diffDraft,
  draftFrom,
  groupRows,
  isDirty,
  isRequirementMissing,
  joinRequirements,
  memberName,
  requirementsInScope,
  scopeDependsOnOffer,
  staleReasonLabel,
} from "@/lib/technical";

describe("Abast: quins requisits apliquen", () => {
  const general = requirementFixture({ id: "g", tender_lot_id: null });
  const lot1 = requirementFixture({ id: "l1", tender_lot_id: "lot-1" });
  const lot2 = requirementFixture({ id: "l2", tender_lot_id: "lot-2" });

  it("candidatura d'un lot: els generals i els del seu lot", () => {
    const rows = requirementsInScope([general, lot1, lot2], { tender_lot_id: "lot-1" }, new Set());
    expect(rows.map((row) => row.id)).toEqual(["g", "l1"]);
  });

  it("candidatura global: els generals i els dels lots on ens presentem", () => {
    const rows = requirementsInScope([general, lot1, lot2], { tender_lot_id: null }, new Set(["lot-2"]));
    expect(rows.map((row) => row.id)).toEqual(["g", "l2"]);
  });

  it("candidatura global sense cap lot triat: només els generals, no s'assumeix res", () => {
    const rows = requirementsInScope([general, lot1, lot2], { tender_lot_id: null }, new Set());
    expect(rows.map((row) => row.id)).toEqual(["g"]);
  });

  it("l'oferta només cal quan pot canviar l'abast", () => {
    expect(scopeDependsOnOffer([general, lot1], { tender_lot_id: null })).toBe(true);
    expect(scopeDependsOnOffer([general, lot1], { tender_lot_id: "lot-1" })).toBe(false);
    expect(scopeDependsOnOffer([general], { tender_lot_id: null })).toBe(false);
  });
});

describe("Requisit → solució", () => {
  it("cada requisit troba la seva solució lligada; les manuals van a part", () => {
    const linked = solutionFixture({ id: "s1", requirement_id: "r1" });
    const manual = solutionFixture({ id: "s2", requirement_id: null, source: "manual" });
    const requirements = [requirementFixture({ id: "r1" }), requirementFixture({ id: "r2" })];
    const { rows, missing, outOfScope, manual: manuals } = joinRequirements(requirements, requirements, [linked, manual]);
    expect(rows.map((row) => row.solution?.id ?? null)).toEqual(["s1", null]);
    expect(missing).toEqual([]);
    expect(outOfScope).toEqual([]);
    expect(manuals.map((item) => item.id)).toEqual(["s2"]);
  });

  it("agrupa per categoria real, en l'ordre d'arribada", () => {
    const requirements = [
      requirementFixture({ id: "a", category: "technical_requirement" }),
      requirementFixture({ id: "b", category: "personnel" }),
      requirementFixture({ id: "c", category: "technical_requirement" }),
    ];
    const { rows } = joinRequirements(requirements, requirements, []);
    const groups = groupRows(rows, (category) => category.toUpperCase());
    expect(groups.map((group) => group.label)).toEqual(["TECHNICAL_REQUIREMENT", "PERSONNEL"]);
    expect(groups[0].rows.map((row) => row.key)).toEqual(["a", "c"]);
  });
});

describe("Requisit absent ≠ requisit fora de l'abast", () => {
  const general = requirementFixture({ id: "g", tender_lot_id: null });
  const altreLot = requirementFixture({ id: "l2", tender_lot_id: "lot-2" });
  const all = [general, altreLot];

  it("existeix i és a l'abast: fila normal", () => {
    const solution = solutionFixture({ id: "s1", requirement_id: "g" });
    const { rows, outOfScope, missing } = joinRequirements([general], all, [solution]);
    expect(rows[0].solution?.id).toBe("s1");
    expect(outOfScope).toEqual([]);
    expect(missing).toEqual([]);
  });

  it("existeix però fora de l'abast: història, amb el requisit resolt i cap senyal de pèrdua", () => {
    const solution = solutionFixture({ id: "s2", requirement_id: "l2" });
    const { rows, outOfScope, missing } = joinRequirements([general], all, [solution]);
    expect(rows).toHaveLength(1);
    expect(rows[0].solution).toBeNull();
    expect(missing).toEqual([]);
    expect(outOfScope).toHaveLength(1);
    expect(outOfScope[0].solution?.id).toBe("s2");
    // El requisit es pot ensenyar: existeix.
    expect(outOfScope[0].requirement?.id).toBe("l2");
    expect(isRequirementMissing(solution)).toBe(false);
  });

  it("un requisit que no és a l'anàlisi vigent tampoc no és «absent»: és història", () => {
    // Pot venir d'una anàlisi anterior; el servidor encara el resol, així que
    // deduir-ne la pèrdua seria inventar-s'ho.
    const solution = solutionFixture({ id: "s3", requirement_id: "d-una-altra-run" });
    const { outOfScope, missing } = joinRequirements([general], all, [solution]);
    expect(missing).toEqual([]);
    expect(outOfScope.map((row) => row.solution?.id)).toEqual(["s3"]);
    expect(outOfScope[0].requirement).toBeNull();
  });

  it("absent de debò: el vincle és buit, que és la condició amb què el backend ho diu", () => {
    const solution = solutionFixture({ id: "s4", requirement_id: null, source: "requirement" });
    const { missing, outOfScope } = joinRequirements([general], all, [solution]);
    expect(outOfScope).toEqual([]);
    expect(missing.map((row) => row.solution?.id)).toEqual(["s4"]);
    expect(isRequirementMissing(solution)).toBe(true);
  });

  it("absent de debò: la caducitat de la validació ho declara", () => {
    const solution = solutionFixture({
      id: "s5",
      requirement_id: "d-una-altra-run",
      status: "validated",
      validated_at: "2026-09-13T09:00:00Z",
      validation_stale: true,
      validation_stale_reasons: ["requirement_missing"],
    });
    const { missing, outOfScope } = joinRequirements([general], all, [solution]);
    expect(outOfScope).toEqual([]);
    expect(missing.map((row) => row.solution?.id)).toEqual(["s5"]);
  });

  it("una solució manual mai no és cap de les dues coses", () => {
    const solution = solutionFixture({ id: "m1", requirement_id: null, source: "manual" });
    const { missing, outOfScope, manual } = joinRequirements([general], all, [solution]);
    expect(missing).toEqual([]);
    expect(outOfScope).toEqual([]);
    expect(manual.map((item) => item.id)).toEqual(["m1"]);
    expect(isRequirementMissing(solution)).toBe(false);
  });
});

describe("Filtres", () => {
  const mine = solutionFixture({ id: "mine", requirement_id: "r1", owner_id: "me", status: "drafting" });
  const pending = solutionFixture({ id: "pending", requirement_id: "r2", owner_id: "other", status: "needs_validation" });
  const stale = solutionFixture({ id: "stale", requirement_id: "r3", owner_id: null, status: "validated", validation_stale: true, validation_stale_reasons: ["content_changed"] });
  const requirements = [requirementFixture({ id: "r1" }), requirementFixture({ id: "r2" }), requirementFixture({ id: "r3" }), requirementFixture({ id: "r4" })];
  const { rows } = joinRequirements(requirements, requirements, [mine, pending, stale]);

  it("Totes: tot", () => {
    expect(applyFilter(rows, "all", "me")).toHaveLength(4);
  });

  it("Em toquen a mi: només les solucions de qui mira", () => {
    expect(applyFilter(rows, "mine", "me").map((row) => row.solution?.id)).toEqual(["mine"]);
    expect(applyFilter(rows, "mine", null)).toEqual([]);
  });

  it("Sense solució: requisits sense solució, i no és cap urgència", () => {
    expect(applyFilter(rows, "unsolved", "me").map((row) => row.requirement?.id)).toEqual(["r4"]);
  });

  it("Per validar: pendents de validar i validacions caducades", () => {
    expect(applyFilter(rows, "to_validate", "me").map((row) => row.solution?.id)).toEqual(["pending", "stale"]);
    expect(awaitsValidation(mine)).toBe(false);
  });
});

describe("Esborrany i PATCH", () => {
  const original = solutionFixture({ status: "drafting", notes: null, owner_id: "u1" });

  it("només viatja el que ha canviat", () => {
    const draft = { ...draftFrom(original), title: "Nou títol", notes: "una nota" };
    expect(diffDraft(original, draft)).toEqual({ title: "Nou títol", notes: "una nota" });
    expect(isDirty(original, draftFrom(original))).toBe(false);
  });

  it("buidar un camp opcional envia null, que és com el servidor l'esborra", () => {
    const draft = { ...draftFrom(original), supplier_name: "   " };
    expect(diffDraft(original, draft)).toEqual({ supplier_name: null });
  });

  it("mai no s'envia source, requirement_id ni cap camp de la firma", () => {
    const draft = { ...draftFrom(original), title: "x" };
    const patch = diffDraft(original, draft) as Record<string, unknown>;
    for (const key of ["source", "requirement_id", "validated_by", "validated_at", "validated_note", "id", "workspace_id"]) {
      expect(patch).not.toHaveProperty(key);
    }
  });

  it("validated no es pot escriure per PATCH: si l'esborrany ho diu, l'estat no surt", () => {
    const validated = solutionFixture({ status: "validated" });
    const draft = { ...draftFrom(validated), title: "canvi" };
    expect(diffDraft(validated, draft)).toEqual({ title: "canvi" });
    const toValidated = { ...draftFrom(original), status: "validated" as const };
    expect(diffDraft(original, toValidated)).toEqual({});
  });
});

describe("Etiquetes i permisos", () => {
  it("els tres motius reals de caducitat tenen frase; un codi desconegut es mostra tal qual", () => {
    expect(staleReasonLabel("content_changed")).toBe("La solució ha canviat des de la validació");
    expect(staleReasonLabel("requirement_changed")).toBe("El requisit ha canviat des de la validació");
    expect(staleReasonLabel("requirement_missing")).toBe("El requisit vinculat ja no està disponible");
    expect(staleReasonLabel("cosa_nova")).toBe("cosa_nova");
  });

  it("els rols d'edició són els del backend; consulta no hi és", () => {
    for (const role of ["owner", "admin", "bid_manager", "technical"]) expect(canEditSolutions(role)).toBe(true);
    expect(canEditSolutions("viewer")).toBe(false);
    expect(canEditSolutions(null)).toBe(false);
  });

  it("el responsable es resol amb l'equip de la candidatura, amb sortida honesta si no hi és", () => {
    const workspace = { members: [{ id: "u1", email: "anna@example.org", full_name: "Anna Puig" }, { id: "u2", email: "pere@example.org", full_name: null }] };
    expect(memberName(workspace, "u1")).toBe("Anna Puig");
    expect(memberName(workspace, "u2")).toBe("pere@example.org");
    expect(memberName(workspace, "u9")).toBe("Membre no disponible");
    expect(memberName(workspace, null)).toBeNull();
  });
});
