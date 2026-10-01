import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { NEUTRAL_COVERAGE, cleanCoverage, coverageSummary, isNeutralCoverage, retypeArea, sameCoverage, type Coverage } from "@/lib/knowledge";

import { CoverageEditor } from "./CoverageEditor";

/**
 * L'editor de cobertura: la completesa i una llista d'àrees a mà, sense mapa
 * ni autocompletat. Comença en l'estat neutre («sense especificar», cap àrea)
 * i només diu el que la persona hi escriu.
 */

function Host({ initial = NEUTRAL_COVERAGE, onChange }: { initial?: Coverage; onChange?: (value: Coverage) => void }) {
  const [value, setValue] = useState<Coverage>(initial);
  return (
    <CoverageEditor
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe("CoverageEditor", () => {
  it("comença neutre i deixa triar la completesa i afegir, omplir i treure àrees", () => {
    let latest: Coverage = NEUTRAL_COVERAGE;
    render(<Host onChange={(value) => (latest = value)} />);
    const completeness = screen.getByLabelText(/^Completesa/) as HTMLSelectElement;
    expect(completeness.value).toBe("unknown");
    expect(screen.queryByRole("list")).toBeNull();

    fireEvent.change(completeness, { target: { value: "complete" } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix una àrea" }));
    fireEvent.click(screen.getByRole("button", { name: "Afegeix una àrea" }));
    expect(screen.getAllByLabelText(/^Nom de l'àrea/)).toHaveLength(2);

    fireEvent.change(screen.getByLabelText("Nom de l'àrea 1"), { target: { value: "Municipi A" } });
    fireEvent.change(screen.getByLabelText("Tipus de l'àrea 2"), { target: { value: "comarca" } });
    fireEvent.change(screen.getByLabelText("Nom de l'àrea 2"), { target: { value: "  Alt   Empordà " } });
    fireEvent.change(screen.getByLabelText("Codi de l'àrea 2"), { target: { value: "02" } });
    fireEvent.change(screen.getByLabelText("Sistema de codi de l'àrea 2"), { target: { value: "IDESCAT" } });
    expect(latest.completeness).toBe("complete");
    expect(latest.areas).toHaveLength(2);
    // El municipi sense codi també és vàlid, i porta l'INE sense que ningú l'escrigui.
    expect(cleanCoverage(latest)).toEqual({
      completeness: "complete",
      areas: [
        { kind: "municipality", label: "Municipi A", code: null, code_system: "INE" },
        { kind: "comarca", label: "Alt Empordà", code: "02", code_system: "IDESCAT" },
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: "Treu l'àrea 1" }));
    expect(screen.getAllByLabelText(/^Nom de l'àrea/)).toHaveLength(1);
    expect((screen.getByLabelText("Nom de l'àrea 1") as HTMLInputElement).value).toBe("  Alt   Empordà ");
    expect(cleanCoverage(latest).areas.map((area) => area.label)).toEqual(["Alt Empordà"]);
  });

  it("mostra la cobertura que ja té la dada", () => {
    render(
      <Host
        initial={{
          completeness: "partial",
          areas: [{ kind: "municipality", label: "Girona", code: "17079", code_system: "INE" }],
        }}
      />,
    );
    expect((screen.getByLabelText(/^Completesa/) as HTMLSelectElement).value).toBe("partial");
    expect((screen.getByLabelText("Nom de l'àrea 1") as HTMLInputElement).value).toBe("Girona");
    // Municipi: «Tipus | Nom | Codi INE», sense cap quart camp de sistema.
    expect((screen.getByLabelText("Codi INE de l'àrea 1") as HTMLInputElement).value).toBe("17079");
    expect(screen.queryByLabelText(/Sistema de codi de l'àrea 1/)).toBeNull();
    expect(screen.queryByLabelText("Codi de l'àrea 1")).toBeNull();
    expect(screen.getByText("Codi INE")).toBeTruthy();
  });

  it("un municipi: codi INE sense escriure el sistema, codi opcional, i un municipi antic sense sistema no falla", () => {
    let latest: Coverage = NEUTRAL_COVERAGE;
    render(
      <Host
        initial={{ completeness: "complete", areas: [{ kind: "municipality", label: "L'Escala", code: null, code_system: null }] }}
        onChange={(value) => (latest = value)}
      />,
    );
    // Antic, sense sistema: es mostra igual, i obrir-lo no el toca.
    expect((screen.getByLabelText("Nom de l'àrea 1") as HTMLInputElement).value).toBe("L'Escala");
    expect(screen.queryByLabelText(/Sistema de codi/)).toBeNull();
    expect(latest).toBe(NEUTRAL_COVERAGE);

    fireEvent.click(screen.getByRole("button", { name: "Afegeix una àrea" }));
    fireEvent.change(screen.getByLabelText("Nom de l'àrea 2"), { target: { value: "Banyoles" } });
    fireEvent.change(screen.getByLabelText("Codi INE de l'àrea 2"), { target: { value: "17015" } });
    expect(screen.queryByLabelText(/Sistema de codi/)).toBeNull();
    expect(cleanCoverage(latest).areas).toEqual([
      // En desar un canvi material, el municipi antic es normalitza a INE.
      { kind: "municipality", label: "L'Escala", code: null, code_system: "INE" },
      { kind: "municipality", label: "Banyoles", code: "17015", code_system: "INE" },
    ]);
  });

  it("canviar el tipus buida el codi i el sistema: cap INE amagat, i el camp de sistema torna", () => {
    let latest: Coverage = NEUTRAL_COVERAGE;
    render(
      <Host
        initial={{ completeness: "partial", areas: [{ kind: "municipality", label: "Banyoles", code: "17015", code_system: "INE" }] }}
        onChange={(value) => (latest = value)}
      />,
    );
    fireEvent.change(screen.getByLabelText("Tipus de l'àrea 1"), { target: { value: "comarca" } });
    expect(latest.areas[0]).toEqual({ kind: "comarca", label: "Banyoles", code: null, code_system: null });
    expect((screen.getByLabelText("Codi de l'àrea 1") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Sistema de codi de l'àrea 1") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("Sistema de codi")).toBeTruthy();
    expect(cleanCoverage(latest).areas[0]).toEqual({ kind: "comarca", label: "Banyoles", code: null, code_system: null });

    // La resta de tipus conserven el sistema lliure, i tornar a municipi no recupera cap codi vell.
    fireEvent.change(screen.getByLabelText("Codi de l'àrea 1"), { target: { value: "ES512" } });
    fireEvent.change(screen.getByLabelText("Sistema de codi de l'àrea 1"), { target: { value: "NUTS" } });
    expect(cleanCoverage(latest).areas[0]).toEqual({ kind: "comarca", label: "Banyoles", code: "ES512", code_system: "NUTS" });
    fireEvent.change(screen.getByLabelText("Tipus de l'àrea 1"), { target: { value: "municipality" } });
    expect(latest.areas[0]).toEqual({ kind: "municipality", label: "Banyoles", code: null, code_system: "INE" });
    expect(screen.queryByLabelText(/Sistema de codi/)).toBeNull();
  });

  it("explica que «sense especificar» no vol dir «enlloc»", () => {
    render(<Host />);
    expect(screen.getByText(/no que no es cobreixi enlloc/).textContent).toContain("no se'n diu res");
  });
});

describe("cobertura: comparació i resum", () => {
  const abc: Coverage = {
    completeness: "complete",
    areas: ["Municipi A", "Municipi B", "Municipi C"].map((label) => ({ kind: "municipality" as const, label, code: null, code_system: null })),
  };

  it("el mateix contingut és la mateixa cobertura, tant se val l'ordre i els espais", () => {
    const shuffled: Coverage = { ...abc, areas: [...abc.areas].reverse().map((area) => ({ ...area, label: ` ${area.label} ` })) };
    expect(sameCoverage(abc, shuffled)).toBe(true);
    expect(sameCoverage(abc, { ...abc, completeness: "partial" })).toBe(false);
    expect(sameCoverage(abc, { ...abc, areas: abc.areas.slice(0, 2) })).toBe(false);
    expect(sameCoverage(abc, { ...abc, areas: [{ ...abc.areas[0], code: "1" }, ...abc.areas.slice(1)] })).toBe(false);
  });

  it("les àrees són un conjunt: la mateixa identitat només s'envia un cop, i un duplicat no és cap canvi", () => {
    const banyoles = { kind: "municipality" as const, label: "Banyoles", code: null, code_system: null };
    const twice: Coverage = { completeness: "complete", areas: [banyoles, { ...banyoles, label: "  Banyoles " }] };
    // Un municipi surt sempre amb l'INE com a sistema.
    expect(cleanCoverage(twice).areas).toEqual([{ ...banyoles, code_system: "INE" }]);
    expect(sameCoverage(twice, { completeness: "complete", areas: [banyoles] })).toBe(true);
    // Diferents de debò: un altre tipus, o amb codi.
    const distinct: Coverage = {
      completeness: "complete",
      areas: [banyoles, { ...banyoles, kind: "comarca" }, { ...banyoles, code: "17015", code_system: "INE" }],
    };
    expect(cleanCoverage(distinct).areas).toHaveLength(3);
    expect(sameCoverage(distinct, twice)).toBe(false);
  });

  it("retypeArea: el mateix tipus no toca res; un altre tipus buida codi i sistema", () => {
    const banyoles = { kind: "municipality" as const, label: "Banyoles", code: "17015", code_system: "INE" };
    expect(retypeArea(banyoles, "municipality")).toBe(banyoles);
    expect(retypeArea(banyoles, "region")).toEqual({ kind: "region", label: "Banyoles", code: null, code_system: null });
    const nuts = { kind: "region" as const, label: "Catalunya", code: "ES51", code_system: "NUTS" };
    expect(retypeArea(nuts, "municipality")).toEqual({ kind: "municipality", label: "Catalunya", code: null, code_system: "INE" });
  });

  it("un municipi antic sense sistema i el mateix amb INE són la mateixa àrea: cap canvi, cap duplicat", () => {
    const legacy = { kind: "municipality" as const, label: "Banyoles", code: null, code_system: null };
    const ine = { ...legacy, code_system: "INE" };
    expect(sameCoverage({ completeness: "complete", areas: [legacy] }, { completeness: "complete", areas: [ine] })).toBe(true);
    expect(cleanCoverage({ completeness: "complete", areas: [legacy, ine] }).areas).toEqual([ine]);
    // Un codi INE diferent sí que és una altra àrea.
    expect(cleanCoverage({ completeness: "complete", areas: [ine, { ...ine, code: "17015" }] }).areas).toHaveLength(2);
  });

  it("l'estat neutre és «unknown» sense àrees, i una fila sense nom no compta", () => {
    expect(isNeutralCoverage(undefined)).toBe(true);
    expect(isNeutralCoverage({ completeness: "unknown", areas: [{ kind: "municipality", label: "  ", code: null, code_system: null }] })).toBe(true);
    expect(isNeutralCoverage({ completeness: "partial", areas: [] })).toBe(false);
    expect(isNeutralCoverage({ completeness: "complete", areas: [] })).toBe(false);
  });

  it("resumeix en una línia curta, o no diu res", () => {
    expect(coverageSummary(undefined)).toBeNull();
    expect(coverageSummary(NEUTRAL_COVERAGE)).toBeNull();
    expect(coverageSummary(abc)).toBe("Cobertura completa: Municipi A, Municipi B, Municipi C");
    expect(coverageSummary({ completeness: "partial", areas: [] })).toBe("Cobertura parcial: cap àrea");
    const many: Coverage = { completeness: "partial", areas: [...abc.areas, ...abc.areas].map((area, i) => ({ ...area, label: `M${i}` })) };
    expect(coverageSummary(many)).toBe("Cobertura parcial: M0, M1, M2, M3 i 2 més");
  });
});
