import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { explanation } from "@/lib/analysis";
import { GONE_REQUIREMENT_ID, analysisFixture, requirementsFixture, structureFixture } from "@/test/analysis-fixture";

import type { AnalysisLookup } from "./AnalysisTab";
import { CriteriaPoints } from "./CriteriaPoints";
import { DecisionFooter } from "./DecisionFooter";
import { Delivery } from "./Delivery";
import { Questions } from "./Questions";
import { RequirementsFit } from "./RequirementsFit";
import { ResolveBeforeDeciding } from "./ResolveBeforeDeciding";

vi.mock("@/components/WorkspaceLauncher", () => ({ WorkspaceLauncher: () => <button type="button">Obre la candidatura</button> }));

function lookup(overrides: Partial<AnalysisLookup> = {}): AnalysisLookup {
  return {
    requirements: new Map(requirementsFixture.requirements.map((r) => [r.id, r])),
    criteria: new Map(requirementsFixture.award_criteria.map((c) => [c.id, c])),
    structure: structureFixture,
    structureFailed: false,
    vocabulary: { service_lines: [{ code: "access_ftth", label: "Accessos FTTH" }, { code: "voice_pbx", label: "Centraleta i veu IP" }], capabilities: [], standards: [] },
    services: new Map([["svc-1", { id: "svc-1", name: "Desplegament sintètic" } as never]]),
    partnerRoles: new Map(),
    ...overrides,
  };
}

describe("Què cal resoldre abans de decidir", () => {
  it("ordre per tipus, «no confirmat», text canònic, cita i On es resol només quan existeixen", () => {
    render(<ResolveBeforeDeciding run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("Possible bloqueig (no confirmat)");
    expect(items[0].textContent).toContain("Cobertura al municipi");
    expect(within(items[0]).queryByRole("link", { name: /On es resol/ })).toBeNull(); // MANDATORY_DOES_NOT_SATISFY: sense destí
    expect(items[1].textContent).toContain("Falta informació de l'empresa");
    expect(items[1].textContent).not.toMatch(/no confirmat/);
    expect(items[2].textContent).not.toMatch(/no confirmat/);
    expect(within(items[1]).getByRole("link", { name: /On es resol/ }).getAttribute("href")).toBe("/empresa/coneixement?tab=qualificacio&seccio=solvencia");
    expect(items[1].textContent).toContain("Plec administratiu.pdf"); // cita manual amb pàgina
    expect(screen.getByText(/2 preguntes generades/)).toBeTruthy();
  });

  it("un finding només amb element d'abast mostra l'element, sense UUID", () => {
    const base = analysisFixture().findings[0];
    const findings = [
      { ...base, id: "g1", finding_type: "delivery_gap", explanation_code: "NO_AVAILABLE_DELIVERY", criterion_id: null, requirement_id: null, scope_item_id: "s-ftth" },
      { ...base, id: "g2", finding_type: "delivery_gap", explanation_code: "NO_AVAILABLE_DELIVERY", criterion_id: null, requirement_id: null, scope_item_id: "s-absent" },
    ];
    const { container } = render(<ResolveBeforeDeciding run={analysisFixture({ findings })} lookup={lookup()} tenderId="t1" />);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("Accessos FTTH");
    expect(items[1].textContent).toContain("Element d'abast sense nom disponible");
    expect(container.textContent).not.toContain("s-absent");
  });

  it("sense findings: ho diu en neutre", () => {
    render(<ResolveBeforeDeciding run={analysisFixture({ findings: [] })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText(/No hi ha cap element pendent de resoldre/)).toBeTruthy();
  });
});

describe("Requisits", () => {
  it("agrupats per categoria, quatre dimensions separades i «verificat» només com a verificació", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const ok = screen.getByText("Suport 24x7").closest("li") as HTMLElement;
    expect(within(ok).getByText("Compleix")).toBeTruthy();
    for (const name of ["Prova:", "Font del coneixement:", "Verificació:", "Territori:"]) expect(within(ok).getByText(name)).toBeTruthy();
    const dims = ok.querySelector("dl") as HTMLElement;
    expect(dims.textContent).toBe("Prova: suficientFont del coneixement: coneixement actual de l'empresaVerificació: verificatTerritori: no aplica");
    expect(within(ok).getByText(/p\. 7/)).toBeTruthy();
  });

  it("les dimensions no repeteixen mai el prefix («Prova: Prova: …», «Font: …»)", () => {
    const { container } = render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const text = [...container.querySelectorAll("dl")].map((dl) => dl.textContent).join(" | ");
    expect(text).not.toMatch(/Prova: Prova|Verificació: Verificació|Territori: Territori|Font: /);
    expect(text).toContain("Territori: no se sap");
    expect(text).toContain("Prova: falta");
    expect(text).not.toContain("Sense territori");
  });

  it("un requisit que la lectura ja no té no mostra cap UUID", () => {
    const { container } = render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Requisit sense títol disponible")).toBeTruthy();
    expect(container.textContent).not.toContain(GONE_REQUIREMENT_ID);
    expect(container.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/);
  });

  it("expandible amb teclat i aria-expanded; el text del motor tal com és", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const toggle = screen.getAllByRole("button", { name: /Per què/ })[0];
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.querySelector("svg")?.getAttribute("class")).not.toContain("rotate-90");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.querySelector("svg")?.getAttribute("class")).toContain("rotate-90");
    const panel = document.getElementById(toggle.getAttribute("aria-controls") as string) as HTMLElement;
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain(explanation("FACT_TRUE_VERIFIED"));
    expect(toggle.getAttribute("aria-label")).toBe("Per què? Suport 24x7");
  });

  it("filtres per estat amb aria-pressed i recompte anunciat", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const filter = screen.getByRole("button", { name: /No se sap/ });
    fireEvent.click(filter);
    expect(filter.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("status", { name: "Recompte de requisits" }).textContent).toMatch(/2 requisits/);
    expect(screen.queryByText("Suport 24x7")).toBeNull();
    expect(screen.getByText("Volum de negoci mínim").closest("li")?.querySelector("[data-level]")?.getAttribute("data-level")).toBe("neutral");
  });

  it("el filtre actiu es veu (píndola plena i negreta, no només aria-pressed) i «Tots» el treu", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const all = screen.getByRole("button", { name: "Tots (4)" });
    const unknown = screen.getByRole("button", { name: /No se sap/ });
    const pill = (button: HTMLElement) => button.querySelector("[data-level]") as HTMLElement;
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(pill(all).getAttribute("data-outline")).toBeNull();
    expect(pill(unknown).getAttribute("data-outline")).toBe("true");

    fireEvent.click(unknown);
    expect(unknown.getAttribute("aria-pressed")).toBe("true");
    expect(all.getAttribute("aria-pressed")).toBe("false");
    expect(pill(unknown).getAttribute("data-level")).toBe("action");
    expect(pill(unknown).getAttribute("data-outline")).toBeNull();
    expect(pill(unknown).className).toContain("font-semibold");
    expect(pill(all).getAttribute("data-outline")).toBe("true");
    expect(pill(all).className).not.toContain("font-semibold");

    fireEvent.click(all);
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(unknown.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("status", { name: "Recompte de requisits" }).textContent).toMatch(/4 requisits/);
    expect(screen.getByText("Suport 24x7")).toBeTruthy();
  });

  it("cada fila té l'àncora estable #req-{id} (destí dels enllaços de Preguntes)", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Volum de negoci mínim").closest("li")?.id).toBe("req-r-turnover");
  });
});

describe("Criteris i punts", () => {
  it("una fila per criteri amb punts i totals; sense criteris, text neutre", () => {
    const { rerender } = render(<CriteriaPoints run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Millora de canals")).toBeTruthy();
    expect(screen.getByText("Memòria tècnica")).toBeTruthy();
    expect(screen.getByText(/Total/).closest("tr")?.textContent).toContain("15");
    const region = screen.getByRole("region", { name: "Criteris i punts (desplaçable)" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.className).toContain("focus-visible:outline");
    expect(screen.getByText("Desplaça horitzontalment per veure totes les columnes.").className).toContain("md:hidden");
    expect(screen.getByText("Millora de canals").closest("tr")?.id).toBe("crit-k-auto");
    rerender(<CriteriaPoints run={analysisFixture({ criterion_assessments: [] })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText(/El plec no té criteris de puntuació analitzats/)).toBeTruthy();
  });
});

describe("Criteris i punts · taula desplaçable", () => {
  it("les columnes numèriques tenen espaiat horitzontal i capçaleres sense salt de línia; la regió continua desplaçable", () => {
    render(<CriteriaPoints run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const region = screen.getByRole("region", { name: /Criteris i punts/ });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.className).toContain("overflow-x-auto");
    const table = within(region).getByRole("table");
    const numeric = within(table).getAllByRole("columnheader").filter((th) => th.className.includes("text-right"));
    expect(numeric.map((th) => th.textContent)).toEqual(["Màxim", "Assegurats", "En risc", "Perduts", "No avaluables"]);
    for (const th of numeric) {
      expect(th.className).toContain("whitespace-nowrap");
      expect(th.className).toMatch(/(^|\s)p[xl]-\d/);
    }
    const cells = [...table.querySelectorAll("tbody td.text-right, tfoot td.text-right")];
    expect(cells.length).toBeGreaterThan(5);
    for (const td of cells) expect(td.className).toMatch(/(^|\s)p[xl]-\d/);
  });
});

describe("Execució", () => {
  it("tradueix fidelment els candidats, amb noms i sense UUIDs; subcontractació desconeguda neutra", () => {
    const { container } = render(<Delivery run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Accessos FTTH")).toBeTruthy();
    expect(screen.getByText(/Bloc de fibra/)).toBeTruthy();
    expect(screen.getByText("Propi")).toBeTruthy();
    expect(screen.getByText(/Desplegament sintètic/)).toBeTruthy();
    expect(screen.getByText("Subcontractació: no se sap")).toBeTruthy();
    expect(screen.getByText("Pendent de partner")).toBeTruthy();
    expect(container.textContent).not.toMatch(/s-ftth|svc-1|precio|preu|BOM/);
  });

  it("no hi ha abast: ho diu en neutre", () => {
    render(
      <Delivery
        run={analysisFixture({ delivery_candidates: [] })}
        lookup={lookup({ structure: { ...structureFixture, scope_items: [] } })}
        tenderId="t1"
      />,
    );
    expect(screen.getByText("El plec no té elements d'abast estructurats.")).toBeTruthy();
  });

  it("hi ha abast però cap candidat: es mostra l'element sense inventar-ne cap", () => {
    const structure = {
      ...structureFixture,
      scope_items: [...structureFixture.scope_items, { id: "s-extra", service_line_code: "civil_works", block_id: null, site_id: null }],
    };
    render(
      <Delivery
        run={analysisFixture()}
        lookup={lookup({
          structure,
          vocabulary: { service_lines: [{ code: "access_ftth", label: "Accessos FTTH" }, { code: "voice_pbx", label: "Centraleta i veu IP" }, { code: "civil_works", label: "Obra civil" }], capabilities: [], standards: [] },
        })}
        tenderId="t1"
      />,
    );
    const extra = screen.getByText("Obra civil").closest("li") as HTMLElement;
    expect(within(extra).getByText("Cap candidat d'execució en aquesta anàlisi.")).toBeTruthy();
    expect(within(extra).queryByText(/Propi|Partner|Subcontractació/)).toBeNull();
  });
});

describe("Preguntes", () => {
  it("dues llistes, text tal com arriba, «generades» i mai «obertes»", () => {
    const { container } = render(<Questions run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByRole("heading", { name: "Per al comprador" })).toBeTruthy();
    expect(screen.getByText("Demaneu a l'organ la llista de territoris del requisit «Cobertura».")).toBeTruthy();
    expect(container.textContent).toMatch(/generades/);
    expect(container.textContent).not.toMatch(/obertes|resoltes/i);
  });

  it("«Sobre: …» enllaça al requisit o al criteri d'origen quan la fila hi és; el text de la pregunta, intacte", () => {
    const base = analysisFixture();
    const questions = [
      base.questions[0],
      { ...base.questions[1], id: "q3", requirement_id: null, criterion_id: "k-auto", text: "Pregunta sintètica sobre un criteri." },
      { ...base.questions[1], id: "q4", requirement_id: "r-absent", text: "Pregunta sintètica sense fila." },
    ];
    render(<Questions run={analysisFixture({ questions })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByRole("link", { name: "Cobertura al municipi" }).getAttribute("href")).toBe("#req-r-no");
    expect(screen.getByRole("link", { name: "Millora de canals" }).getAttribute("href")).toBe("#crit-k-auto");
    expect(screen.getByText("Pregunta sintètica sobre un criteri.")).toBeTruthy();
    // Sense fila a l'anàlisi: el nom neutre, sense enllaç trencat.
    expect(screen.getByText(/Requisit sense títol disponible/).closest("a")).toBeNull();
  });
});

describe("Peu de decisió", () => {
  it("la decisió és vostra; amb candidatura, enllaç al pas Decidir", () => {
    render(<DecisionFooter companyId="c1" tenderId="t1" workspaceId="w1" checked />);
    expect(screen.getByText(/Tuvi no recomana sí ni no/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Continua la candidatura per decidir" }).getAttribute("href")).toBe("/candidatures/w1?pas=decidir");
  });
  it("sense candidatura: el llançador existent", () => {
    render(<DecisionFooter companyId="c1" tenderId="t1" workspaceId={null} checked />);
    expect(screen.getByRole("button", { name: "Obre la candidatura" })).toBeTruthy();
  });
});

describe("Execució: casos addicionals", () => {
  it("estructura que ha fallat i sense candidats: ho diu, sense afirmar res sobre el plec", () => {
    render(<Delivery run={analysisFixture({ delivery_candidates: [] })} lookup={lookup({ structure: null, structureFailed: true })} tenderId="t1" />);
    expect(screen.getByText("No s'ha pogut carregar l'estructura del plec.")).toBeTruthy();
    expect(screen.queryByText("El plec no té elements d'abast estructurats.")).toBeNull();
  });

  it("estructura que encara es carrega: esquelet, mai «no s'ha pogut carregar»", () => {
    const { container } = render(<Delivery run={analysisFixture({ delivery_candidates: [] })} lookup={lookup({ structure: null })} tenderId="t1" />);
    expect(container.querySelector(".animate-skeleton")).toBeTruthy();
    expect(screen.queryByText(/No s'ha pogut/)).toBeNull();
    expect(screen.queryByText("El plec no té elements d'abast estructurats.")).toBeNull();
  });

  it("candidat amb rol de partner: nom del partner i etiqueta del rol", () => {
    const base = analysisFixture();
    const delivery_candidates = [{ ...base.delivery_candidates[1], partner_role_id: "pr-1", candidate_type: "partner_current" }];
    const { container } = render(
      <Delivery
        run={analysisFixture({ delivery_candidates })}
        lookup={lookup({ partnerRoles: new Map([["pr-1", { role: "subcontractor", partner: "Partner Sintètic SL" }]]) })}
        tenderId="t1"
      />,
    );
    expect(screen.getByText("Partner Sintètic SL · Subcontractista")).toBeTruthy();
    expect(screen.getByText("Partner").getAttribute("data-level")).toBe("ready");
    expect(container.textContent).not.toMatch(/pr-1|Valor no disponible/);
  });
});
