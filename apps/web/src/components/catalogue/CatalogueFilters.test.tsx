import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_CATALOGUE_FILTERS, type CatalogueFilters as Filters } from "@/app/(app)/licitacions/query";
import type { TenderFacets } from "@/lib/tenders";

const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));

import { CatalogueFilters, SEARCH_PLACEHOLDER } from "./CatalogueFilters";

const facets: TenderFacets = {
  statuses: ["open", "cancelled"],
  procedure_types: ["open"],
  contract_types: ["services"],
  nuts_codes: [],
  total_tenders: 10,
  sources: [{ code: "placsp_agregadas", name: "PLACSP · plataformes agregades", count: 4 }],
};

function setup(filters: Filters = EMPTY_CATALOGUE_FILTERS) {
  const onChange = vi.fn();
  const onApply = vi.fn();
  const onClear = vi.fn();
  const view = render(
    <CatalogueFilters filters={filters} onChange={onChange} onApply={onApply} onClear={onClear} facets={facets} open onOpenChange={() => undefined} />,
  );
  return { onChange, onApply, onClear, ...view };
}

describe("CatalogueFilters", () => {
  beforeEach(() => get.mockReset());

  it("la caixa de cerca porta el text exacte del contracte", () => {
    setup();
    expect(SEARCH_PLACEHOLDER).toBe("Cerca per títol, expedient, organisme o descripció");
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe(SEARCH_PLACEHOLDER);
  });

  it("la font surt de les facetes amb el codi desat com a valor", () => {
    setup();
    const select = screen.getByLabelText("Font") as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["", "placsp_agregadas"]);
    expect(select.options[1].textContent).toBe("PLACSP · plataformes agregades");
  });

  it("«ja no disponible a la font» és tri-estat: indiferent, només els que ja no hi són, només els vigents", () => {
    const { onChange } = setup();
    const select = screen.getByLabelText("Disponibilitat a la font") as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["", "true", "false"]);
    fireEvent.change(select, { target: { value: "true" } });
    expect(onChange).toHaveBeenCalledWith({ ...EMPTY_CATALOGUE_FILTERS, source_removed: "true" });
  });

  it("enviar el formulari aplica; Neteja neteja", () => {
    const { onApply, onClear } = setup();
    fireEvent.submit(screen.getByRole("search"));
    expect(onApply).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Neteja" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("el resum diu quants filtres hi ha (el text de la cerca no compta)", () => {
    setup({ ...EMPTY_CATALOGUE_FILTERS, q: "fibra", nuts: "ES51", source: "PLACSP" });
    expect(screen.getByText(/Filtres · 2 actius/)).toBeTruthy();
  });

  it("el cercador de CPV espera, consulta /cpv-codes?q= i no consulta amb menys de dues lletres", async () => {
    vi.useFakeTimers();
    try {
      get.mockResolvedValue([{ code: "32500000", description: "Equip de telecomunicacions" }]);
      const { onChange } = setup();
      const input = screen.getByLabelText(/^CPV/);
      fireEvent.change(input, { target: { value: "t" } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(get).not.toHaveBeenCalled();
      fireEvent.change(input, { target: { value: "telecom" } });
      expect(onChange).toHaveBeenLastCalledWith({ ...EMPTY_CATALOGUE_FILTERS, cpv: "" });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(get).toHaveBeenCalledWith("/cpv-codes?q=telecom&limit=8");
      expect(screen.getByRole("button", { name: /32500000/ })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
