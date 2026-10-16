import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TenderMatch } from "@/lib/matching";
import type { TenderListItem } from "@/lib/tenders";

import { CatalogueRow } from "./CatalogueRow";

function tender(overrides: Partial<TenderListItem> = {}): TenderListItem {
  return {
    id: "t1",
    external_id: "ext",
    expediente_number: "EXP-1",
    title: "Servei de manteniment",
    status: "open",
    procedure_type: "open",
    contract_type: "services",
    estimated_value: "50000",
    base_budget: null,
    currency: "EUR",
    publication_date: "2026-09-01",
    deadline_at: null,
    nuts_code: null,
    place_of_execution: null,
    buyer: { id: "b", name: "Ajuntament", tax_identifier: null, city: null, nuts_code: null },
    cpvs: [],
    lot_count: 0,
    source_publication_count: 1,
    source_code: "PLACSP",
    is_modified: false,
    ...overrides,
  };
}

describe("CatalogueRow", () => {
  it("«Anul·lada» només quan la font ho diu; «Ja no disponible a la font» és una altra cosa", () => {
    const { container, rerender } = render(<CatalogueRow tender={tender()} match={null} />);
    expect(container.textContent).not.toContain("Anul·lada");
    expect(container.textContent).not.toContain("Ja no disponible a la font");

    rerender(<CatalogueRow tender={tender({ source_removed_at: "2026-09-20T08:00:00Z" })} match={null} />);
    // Sense termini conegut, l'estat és el de la font («Publicada»), mai «En termini» (F2).
    expect(container.textContent).toContain("Publicada");
    expect(container.textContent).not.toContain("En termini");
    expect(container.textContent).toContain("Ja no disponible a la font");
    expect(container.textContent).not.toContain("Anul·lada");

    rerender(<CatalogueRow tender={tender({ status: "cancelled" })} match={null} />);
    expect(container.textContent).toContain("Anul·lada");
    expect(container.textContent).not.toContain("Ja no disponible a la font");
  });

  it("termini passat → «Fora de termini» amb l'estat de la font a part (A12); termini obert → l'estat de la font", () => {
    const { container, rerender } = render(<CatalogueRow tender={tender({ deadline_at: "2026-09-01T10:00:00Z" })} match={null} />);
    expect(container.textContent).toContain("Fora de termini");
    // F2: l'estat cru (PLACSP PUB) es diu «Publicada», mai «En termini» sota «Fora de termini».
    expect(container.textContent).toContain("Estat a la font: Publicada");
    expect(container.textContent).not.toContain("En termini");
    rerender(<CatalogueRow tender={tender({ deadline_at: "2026-10-05T12:00:00Z" })} match={null} />);
    expect(container.textContent).toContain("Fora de termini");
    expect(container.textContent).not.toContain("En termini");
    rerender(<CatalogueRow tender={tender({ deadline_at: null })} match={null} />);
    expect(container.textContent).not.toContain("En termini");
    rerender(<CatalogueRow tender={tender({ deadline_at: "2099-09-01T10:00:00Z" })} match={null} />);
    expect(container.textContent).toContain("En termini");
    expect(container.textContent).not.toContain("Fora de termini");
    expect(container.textContent).not.toContain("Estat a la font");
    // Una anul·lació o una adjudicació de la font manen sobre el termini passat com a
    // ESTAT (cap «Estat a la font»); el comptador del termini continua dient que ha passat.
    const pills = () => [...container.querySelectorAll("[data-level]")].map((p) => p.textContent?.trim());
    rerender(<CatalogueRow tender={tender({ status: "cancelled", deadline_at: "2026-09-01T10:00:00Z" })} match={null} />);
    expect(pills()).toContain("Anul·lada");
    expect(container.textContent).not.toContain("Estat a la font");
    rerender(<CatalogueRow tender={tender({ status: "awarded", deadline_at: "2026-09-01T10:00:00Z" })} match={null} />);
    expect(pills()).toContain("Adjudicada");
    expect(container.textContent).not.toContain("Estat a la font");
  });

  it("un termini passat és una pastilla neutra, mai vermella: el vermell és per als imminents", () => {
    const { container, rerender } = render(<CatalogueRow tender={tender({ deadline_at: "2026-09-01T10:00:00Z" })} match={null} />);
    const pill = [...container.querySelectorAll("[data-level]")].find((p) => p.textContent?.trim() === "Fora de termini")!;
    expect(pill.getAttribute("data-level")).toBe("neutral");
    const soon = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
    rerender(<CatalogueRow tender={tender({ deadline_at: soon })} match={null} />);
    expect(container.querySelector("[data-level='blocking']")).not.toBeNull();
  });

  it("sense pre-fit la fila no diu res del pre-fit (A9): cap «Sense calcular», cap puntuació ni to d'alerta", () => {
    const { container } = render(<CatalogueRow tender={tender()} match={null} />);
    expect(container.textContent).not.toContain("Sense calcular");
    expect(container.textContent).not.toMatch(/Pre-fit/);
    expect(container.querySelector("[data-level='blocking']")).toBeNull();
    expect(container.textContent).not.toMatch(/\b0\b/);
  });

  it("amb pre-fit el mostra com a dada, amb el motiu", () => {
    const match = {
      id: "m",
      tender_id: "t1",
      score: 61,
      status: "low_fit",
      factors: [],
      is_stale: false,
    } as unknown as TenderMatch;
    const { container } = render(<CatalogueRow tender={tender()} match={match} />);
    expect(container.textContent).toContain("61");
    expect(container.textContent).not.toContain("Sense calcular");
  });

  it("una sola acció, «Obre la fitxa», que porta a la fitxa (D3): cap «Analitzar» ambigu", () => {
    render(<CatalogueRow tender={tender({ id: "abc" })} match={null} />);
    const open = screen.getByRole("link", { name: "Obre la fitxa" });
    expect(open.getAttribute("href")).toBe("/licitacions/abc");
    expect(open.getAttribute("data-variant")).toBe("primary");
    expect(screen.queryByRole("link", { name: /Analitzar|^Veure$/ })).toBeNull();
    expect(screen.getByRole("link", { name: "Servei de manteniment" }).getAttribute("href")).toBe("/licitacions/abc");
  });

  it("el CPV principal porta el nom oficial, i mai el codi repetit com a descripció (D4)", () => {
    const { container, rerender } = render(
      <CatalogueRow tender={tender({ cpvs: [{ cpv_code: "32500000", is_main: true, description: "CPV 32500000" }] })} match={null} />,
    );
    expect(container.textContent).toContain("32500000");
    expect(container.textContent).not.toContain("CPV 32500000");
    rerender(<CatalogueRow tender={tender({ cpvs: [{ cpv_code: "32500000", is_main: true, description: "Equip de telecomunicacions" }] })} match={null} />);
    expect(container.textContent).toContain("32500000 · Equip de telecomunicacions");
  });

  it("auditoria UI P1-05: la fila diu «Primer CPV de la font», no «CPV principal», i n'explica el perquè", () => {
    const { container } = render(
      <CatalogueRow
        tender={tender({
          cpvs: [
            { cpv_code: "32562100", is_main: true, description: "Cables de fibra òptica" },
            { cpv_code: "64200000", is_main: false, description: "Serveis de telecomunicacions" },
          ],
        })}
        match={null}
      />,
    );
    expect(container.textContent).toContain("Primer CPV de la font");
    expect(container.textContent).not.toContain("CPV principal");
    const value = [...container.querySelectorAll("[title]")].find((el) => el.textContent?.includes("32562100"));
    expect(value?.getAttribute("title")).toMatch(/no indica quin CPV és el principal/);
    // P2-04: l'explicació també és visible, sense passar-hi el ratolí.
    expect(container.querySelector("[data-cpv-provenance]")?.textContent).toBe("La font no indica el principal: és el primer que publica.");
  });

  it("revisió del patch: sense cap CPV marcat, la fila no diu «Primer CPV de la font»", () => {
    const { container } = render(
      <CatalogueRow tender={tender({ cpvs: [{ cpv_code: "64200000", is_main: false, description: "Serveis de telecomunicacions" }] })} match={null} />,
    );
    expect(container.textContent).toContain("64200000");
    expect(container.textContent).not.toContain("Primer CPV de la font");
    expect(container.querySelector("[data-cpv-provenance]")).toBeNull();
  });

  it("amb un filtre de CPV, diu quin CPV de la fila ha coincidit quan no és el principal (C4)", () => {
    const cpvs = [
      { cpv_code: "45314320", is_main: true, description: "Instal·lació de cables" },
      { cpv_code: "32000000", is_main: false, description: "Equip de ràdio" },
    ];
    const { container, rerender } = render(<CatalogueRow tender={tender({ cpvs })} match={null} cpvFilter="32" />);
    const line = container.querySelector("[data-cpv-match]")!;
    expect(line.getAttribute("data-cpv-match")).toBe("secondary");
    expect(line.textContent).toContain("Coincideix amb el filtre per un altre CPV de la llista, 32000000 · Equip de ràdio");

    rerender(<CatalogueRow tender={tender({ cpvs })} match={null} cpvFilter="4531" />);
    expect(container.querySelector("[data-cpv-match]")!.getAttribute("data-cpv-match")).toBe("main");
    expect(container.textContent).not.toContain("per un altre CPV de la llista");

    rerender(<CatalogueRow tender={tender({ cpvs })} match={null} cpvFilter="50" />);
    expect(container.querySelector("[data-cpv-match]")!.textContent).toContain("per un CPV d'un lot");

    rerender(<CatalogueRow tender={tender({ cpvs })} match={null} />);
    expect(container.querySelector("[data-cpv-match]")).toBeNull();
  });

  it("no inventa dades: sense import, sense CPV i sense font ho diu", () => {
    const { container } = render(
      <CatalogueRow tender={tender({ estimated_value: null, base_budget: null, cpvs: [], source_code: null })} match={null} />,
    );
    expect(container.textContent).toContain("Import no informat");
    expect(container.textContent).toContain("Sense CPV");
    expect(container.textContent).toContain("Sense font");
  });

  it("l'import coalesceix estimat, base sense IVA i base, com el filtre", () => {
    const { container, rerender } = render(
      <CatalogueRow tender={tender({ estimated_value: null, base_budget_without_tax: "12345", base_budget: "99999" })} match={null} />,
    );
    expect(container.textContent).toContain("12.345");
    expect(container.textContent).not.toContain("99.999");
    expect(container.textContent).not.toContain("Import no informat");
    rerender(<CatalogueRow tender={tender({ estimated_value: null, base_budget_without_tax: null, base_budget: "77777" })} match={null} />);
    expect(container.textContent).toContain("77.777");
  });

  it("és una targeta que s'apila: columna al mòbil, graella a partir de md", () => {
    const { container } = render(<CatalogueRow tender={tender()} match={null} />);
    const card = container.querySelector("article")!;
    expect(card.className).toContain("flex-col");
    expect(card.className).toContain("md:grid");
    expect(container.querySelector("table")).toBeNull();
  });

  it("el nom de la font surt de les facetes quan hi és; si no, del codi conegut o cru", () => {
    const { container, rerender } = render(
      <CatalogueRow
        tender={tender({ source_code: "placsp_agregadas" })}
        match={null}
        sources={[{ code: "placsp_agregadas", name: "Nom de la faceta", count: 1 }]}
      />,
    );
    expect(container.textContent).toContain("PLACSP · plataformes agregades (PSCP)");
    expect(container.textContent).not.toContain("Nom de la faceta");
    rerender(
      <CatalogueRow
        tender={tender({ source_code: "nova_font" })}
        match={null}
        sources={[{ code: "nova_font", name: "Nom de la faceta", count: 1 }]}
      />,
    );
    expect(container.textContent).toContain("Nom de la faceta");
    rerender(<CatalogueRow tender={tender({ source_code: "manual" })} match={null} />);
    expect(container.textContent).toContain("Importació manual");
    rerender(<CatalogueRow tender={tender({ source_code: "altra_font" })} match={null} />);
    expect(container.textContent).toContain("Font desconeguda");
    expect(container.textContent).not.toContain("altra_font");
    rerender(<CatalogueRow tender={tender({ source_code: "constructor" })} match={null} />);
    expect(container.textContent).toContain("Font desconeguda");
  });

  it("un estat o tipus que el diccionari no coneix no mostra mai el codi cru", () => {
    const { container } = render(
      <CatalogueRow
        tender={tender({ status: "nou_estat" as never, procedure_type: "nou_proc" as never, contract_type: "toString" as never })}
        match={null}
      />,
    );
    expect(container.textContent).toContain("Estat desconegut");
    expect(container.textContent).toContain("Tipus desconegut");
    expect(container.textContent).not.toMatch(/nou_estat|nou_proc|toString/);
  });
});
