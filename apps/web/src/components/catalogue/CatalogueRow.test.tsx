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
    expect(container.textContent).toContain("En termini");
    expect(container.textContent).toContain("Ja no disponible a la font");
    expect(container.textContent).not.toContain("Anul·lada");

    rerender(<CatalogueRow tender={tender({ status: "cancelled" })} match={null} />);
    expect(container.textContent).toContain("Anul·lada");
    expect(container.textContent).not.toContain("Ja no disponible a la font");
  });

  it("sense pre-fit diu «Sense calcular» sense cap puntuació ni to d'alerta", () => {
    const { container } = render(<CatalogueRow tender={tender()} match={null} />);
    expect(container.textContent).toContain("Sense calcular");
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

  it("accions: Veure porta a la fitxa i Analitzar a la pestanya Anàlisi", () => {
    render(<CatalogueRow tender={tender({ id: "abc" })} match={null} />);
    expect(screen.getByRole("link", { name: "Veure" }).getAttribute("href")).toBe("/licitacions/abc");
    expect(screen.getByRole("link", { name: "Analitzar" }).getAttribute("href")).toBe("/licitacions/abc?tab=analisi");
    expect(screen.getByRole("link", { name: "Servei de manteniment" }).getAttribute("href")).toBe("/licitacions/abc");
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
