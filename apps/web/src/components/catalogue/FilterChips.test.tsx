import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_CATALOGUE_FILTERS, EMPTY_FILTERS } from "@/app/(app)/licitacions/query";
import { sourceLabel, type TenderFacets } from "@/lib/tenders";

import { FilterChips, catalogueFilterChips, feedFilterChips } from "./FilterChips";

const facets: TenderFacets = {
  statuses: ["open"],
  procedure_types: ["open"],
  contract_types: ["services"],
  nuts_codes: ["ES511"],
  total_tenders: 3,
  sources: [{ code: "placsp_agregadas", name: "PLACSP · plataformes agregades", count: 3 }],
};

describe("xips dels filtres actius (C5)", () => {
  it("un xip per filtre fixat del catàleg, amb el valor en paraules i cap codi cru", () => {
    const chips = catalogueFilterChips(
      {
        ...EMPTY_CATALOGUE_FILTERS,
        q: "fibra òptica",
        cpv: "4531",
        nuts: "ES51",
        place_q: "L'Escala",
        min_amount: "50000",
        deadline_to: "2026-10-31",
        contract_type: "services",
        status: "open",
        source: "placsp_agregadas",
        source_removed: "true",
      },
      facets,
    );
    expect(chips.map((chip) => chip.key)).toEqual(["q", "cpv", "nuts", "place_q", "min_amount", "deadline_to", "contract_type", "status", "source", "source_removed"]);
    const label = (key: string) => chips.find((chip) => chip.key === key)?.label;
    expect(label("q")).toBe("Text: «fibra òptica»");
    expect(label("cpv")).toBe("CPV: 4531");
    expect(label("nuts")).toBe("NUTS: ES51");
    expect(label("place_q")).toBe("Lloc d'execució: «L'Escala»");
    expect(label("min_amount")).toContain("Import mínim: 50.000");
    expect(label("deadline_to")).toMatch(/^Termini fins a: .*2026/);
    expect(label("contract_type")).toBe("Tipus de contracte: Serveis");
    // F2: `open` és l'estat cru de la font («Publicada»); «En termini» és només `in_term`.
    expect(label("status")).toBe("Estat a la font: Publicada");
    expect(catalogueFilterChips({ ...EMPTY_CATALOGUE_FILTERS, status: "in_term" }, facets)[0].label).toBe("Estat: En termini");
    expect(label("source")).toBe(`Font: ${sourceLabel("placsp_agregadas", facets.sources)}`);
    expect(label("source_removed")).toBe("Ja no disponible a la font");
    expect(chips.map((chip) => chip.label).join(" ")).not.toMatch(/placsp_agregadas|services\b|\bopen\b/);
    expect(catalogueFilterChips(EMPTY_CATALOGUE_FILTERS, facets)).toEqual([]);
  });

  it("els del feed: lots, només obertes i la resta amb els mateixos rètols", () => {
    const chips = feedFilterChips({ ...EMPTY_FILTERS, q: "manteniment", has_lots: "false", only_open: true, procedure_type: "restricted" });
    // C3: «termini obert» no és l'estat «En termini»: diu que no mira l'estat de la font.
    expect(chips.map((chip) => chip.label)).toEqual(["Text: «manteniment»", "Procediment: Restringit", "Sense lots", "Termini obert (qualsevol estat de la font)"]);
    expect(feedFilterChips(EMPTY_FILTERS)).toEqual([]);
  });

  it("cada xip té una «×» que treu només aquell filtre; sense xips no es pinta res", () => {
    const onRemove = vi.fn();
    const { container, rerender } = render(
      <FilterChips chips={[{ key: "cpv", label: "CPV: 4531" }, { key: "nuts", label: "NUTS: ES51" }]} onRemove={onRemove} />,
    );
    const list = screen.getByRole("list", { name: "Filtres actius" });
    expect(list.textContent).toContain("CPV: 4531");
    fireEvent.click(screen.getByRole("button", { name: "Treu el filtre CPV: 4531" }));
    expect(onRemove).toHaveBeenCalledWith("cpv");
    expect(onRemove).not.toHaveBeenCalledWith("nuts");
    rerender(<FilterChips chips={[]} onRemove={onRemove} />);
    expect(container.querySelector("ul")).toBeNull();
  });
});
