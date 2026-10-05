import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { groupQuestions, type AnalysisQuestion } from "@/lib/analysis";
import { analysisFixture, requirementsFixture } from "@/test/analysis-fixture";

import type { AnalysisLookup } from "./AnalysisTab";
import { Questions } from "./Questions";

const N = 21;
const sites = Array.from({ length: N }, (_, i) => ({ id: `site-${i}`, code: `S${String(i + 1).padStart(2, "0")}`, name: `Seu sintètica ${i + 1}` }));
const scopeItems = sites.map((s, i) => ({ id: `si-${i}`, service_line_code: "access_ftth", block_id: null, site_id: s.id }));

function lookup(over: Partial<AnalysisLookup> = {}): AnalysisLookup {
  return {
    requirements: new Map(requirementsFixture.requirements.map((r) => [r.id, r])),
    criteria: new Map(requirementsFixture.award_criteria.map((c) => [c.id, c])),
    structure: { blocks: [], sites, scope_items: scopeItems },
    structureFailed: false,
    vocabulary: { service_lines: [{ code: "access_ftth", label: "Accessos FTTH" }], capabilities: [], standards: [] },
    services: new Map(),
    partnerRoles: new Map(),
    ...over,
  };
}

function q(i: number, over: Partial<AnalysisQuestion> = {}): AnalysisQuestion {
  return {
    id: `q${i}`,
    audience: "buyer",
    question_code: "buyer.provide_scope_site",
    rule_code: "R",
    requirement_id: null,
    criterion_id: null,
    scope_item_id: `si-${i}`,
    params_json: { service_line: "access_ftth" },
    text: "Indiqueu la seu de l'element FTTH.",
    ...over,
  };
}

describe("groupQuestions", () => {
  it("21 preguntes iguals amb scope_item_id diferent formen 1 grup", () => {
    const groups = groupQuestions(Array.from({ length: N }, (_, i) => q(i)));
    expect(groups).toHaveLength(1);
    expect(groups[0].items).toHaveLength(N);
  });
  it("ordre de claus diferent, també niat, va al mateix grup", () => {
    const a = q(0, { params_json: { a: 1, n: { x: 1, y: [1, 2] } } });
    const b = q(1, { params_json: { n: { y: [1, 2], x: 1 }, a: 1 } });
    expect(groupQuestions([a, b])).toHaveLength(1);
  });
  it("arrays amb un altre ordre no s'agrupen", () => {
    const a = q(0, { params_json: { l: [1, 2] } });
    const b = q(1, { params_json: { l: [2, 1] } });
    expect(groupQuestions([a, b])).toHaveLength(2);
  });
  it("mateix text amb paràmetres diferents no s'agrupa", () => {
    expect(groupQuestions([q(0), q(1, { params_json: { service_line: "voice_pbx" } })])).toHaveLength(2);
  });
  it("audience diferent no s'agrupa", () => {
    expect(groupQuestions([q(0), q(1, { audience: "company" })])).toHaveLength(2);
  });
  it("sense scope_item_id no s'agrupa", () => {
    const g = groupQuestions([q(0, { scope_item_id: null }), q(1, { scope_item_id: null })]);
    expect(g).toHaveLength(2);
  });
});

describe("Questions agrupades", () => {
  it("21 seus: el text un cop, totes les seus llistades i el total 21", () => {
    const questions = Array.from({ length: N }, (_, i) => q(i));
    const { container } = render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getAllByText("Indiqueu la seu de l'element FTTH.")).toHaveLength(1);
    const list = screen.getByRole("list", { name: /Afecta 21/ });
    expect(within(list).getAllByRole("listitem")).toHaveLength(N);
    expect(list.textContent).toContain("S01");
    expect(list.textContent).toContain("Seu sintètica 21");
    expect(container.textContent).toContain("21 preguntes (1 grup)");
  });
  it("element d'abast absent: nom neutre, sense UUID", () => {
    const uuid = "9f1c2b3a-1111-4222-8333-444455556666";
    const questions = [q(0, { scope_item_id: uuid }), q(1, { scope_item_id: "otro-9f1c2b3a" })];
    const { container } = render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getAllByText("Element d'abast sense nom disponible")).toHaveLength(2);
    expect(container.textContent).not.toContain(uuid);
    expect(container.textContent).not.toContain("otro-9f1c2b3a");
  });
  it("conserva l'enllaç «Sobre» de cada pregunta", () => {
    const questions = [q(0, { requirement_id: "r-no" }), q(1, { requirement_id: "r-no" })];
    render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByRole("link", { name: "Cobertura al municipi" }).getAttribute("href")).toBe("#req-r-no");
  });
  it("preguntes de termini sense scope_item_id queden separades", () => {
    const questions = [0, 1, 2].map((i) =>
      q(i, { question_code: "buyer.provide_submission_deadline", scope_item_id: null, requirement_id: "r-no", params_json: null, text: `Demaneu el termini ${i}.` }),
    );
    const { container } = render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getAllByText(/Demaneu el termini/)).toHaveLength(3);
    expect(container.textContent).not.toMatch(/grup/);
  });
  it("una pregunta d'abast sola (grup d'1) diu a quin element i seu es refereix, amb el format de la llista (C-4)", () => {
    const { container } = render(<Questions run={analysisFixture({ questions: [q(4)] })} lookup={lookup()} tenderId="t1" />);
    expect(container.textContent).toContain("Afecta: Accessos FTTH — S05 · Seu sintètica 5");
    expect(screen.queryByRole("list", { name: /Afecta/ })).toBeNull();
    expect(container.textContent).not.toContain("si-4");
    expect(container.textContent).not.toMatch(/grup/);
  });

  it("una pregunta sense element d'abast no diu «Afecta»", () => {
    const { container } = render(<Questions run={analysisFixture({ questions: [q(0, { scope_item_id: null })] })} lookup={lookup()} tenderId="t1" />);
    expect(container.textContent).not.toContain("Afecta");
  });

  it("dins d'un grup, un mateix element repetit es llista un cop i «Afecta N» compta elements diferents; el total de preguntes es manté (M3)", () => {
    const questions = [q(0), q(1), q(1, { id: "q1-bis" }), q(2), q(2, { id: "q2-bis" })];
    const { container } = render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    const list = screen.getByRole("list", { name: /Afecta 3 elements/ });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(list.textContent?.match(/S02/g)).toHaveLength(1);
    expect(container.textContent).toContain("5 preguntes (1 grup)");
  });

  it("un grup amb un sol element (repetit) es mostra com una pregunta d'un element", () => {
    const questions = [q(3), q(3, { id: "q3-bis" })];
    const { container } = render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.queryByRole("list", { name: /Afecta/ })).toBeNull();
    expect(container.textContent).toContain("Afecta: Accessos FTTH — S04 · Seu sintètica 4");
    expect(container.textContent).toContain("2 preguntes (1 grup)");
  });

  it("dos grups amb paràmetres que només difereixen en puntuació: ids diferents i cada llista amb el seu títol", () => {
    const questions = [
      q(0, { params_json: { k: "a b" } }),
      q(1, { params_json: { k: "a b" } }),
      q(2, { params_json: { k: "a-b" } }),
      q(3, { params_json: { k: "a-b" } }),
    ];
    render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    const lists = screen.getAllByRole("list", { name: /Afecta 2/ });
    expect(lists).toHaveLength(2);
    const ids = lists.map((l) => l.getAttribute("aria-labelledby"));
    expect(new Set(ids).size).toBe(2);
    ids.forEach((id, i) => expect(document.getElementById(id as string)?.nextElementSibling).toBe(lists[i]));
  });
});
