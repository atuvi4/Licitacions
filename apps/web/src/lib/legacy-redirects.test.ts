import { describe, expect, it } from "vitest";

import nextConfig from "../../next.config";
import { QUALIFICATION_SECTIONS } from "@/components/knowledge/qualification/QualificationTab";

import { LEGACY_REDIRECTS, QUALIFICATION_PATH } from "./legacy-redirects";

describe("pantalles 4C antigues → «Qualificació i mitjans»", () => {
  it("next.config aplica exactament aquestes redireccions", async () => {
    const redirects = await nextConfig.redirects?.();
    expect(redirects).toEqual(LEGACY_REDIRECTS);
  });

  it.each([
    ["/experiencia", "experiencia", ""],
    ["/experiencia/:projectId", "experiencia", "&project=:projectId"],
    ["/empresa/assegurances", "assegurances", ""],
    ["/empresa/registres", "registres", ""],
  ])("%s porta a la secció «%s» de la pestanya de qualificació", (source, section, extra) => {
    const redirect = LEGACY_REDIRECTS.find((item) => item.source === source);
    expect(redirect?.destination).toBe(`/empresa/coneixement?tab=qualificacio&seccio=${section}${extra}`);
    expect(redirect?.permanent).toBe(false);
    expect(QUALIFICATION_SECTIONS.map((item) => item.key)).toContain(section);
  });

  it("cap destinació torna a ser una font: no hi ha cap bucle", () => {
    const sources = LEGACY_REDIRECTS.map((item) => item.source.replace(/\/:[^/]+/g, ""));
    for (const { destination } of LEGACY_REDIRECTS) {
      const path = destination.split("?")[0];
      expect(path).toBe(QUALIFICATION_PATH);
      expect(sources.some((source) => path === source || path.startsWith(`${source}/`))).toBe(false);
    }
  });

  it("no redirigeix pantalles amb conceptes que la secció nova no té", () => {
    const sources = LEGACY_REDIRECTS.map((item) => item.source);
    // Preferències de contractació i titulacions de les persones.
    expect(sources.some((source) => source.startsWith("/empresa/solvencia"))).toBe(false);
    expect(sources.some((source) => source.startsWith("/empresa/equip"))).toBe(false);
    expect(sources.some((source) => source.startsWith("/empresa/coneixement"))).toBe(false);
  });
});
