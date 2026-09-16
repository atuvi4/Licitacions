import { describe, expect, it } from "vitest";

import { NAVIGATION_GROUPS, PROFILE_SUBNAV, isNavigationActive, isSubnavActive } from "./navigation";

describe("navegació del shell", () => {
  it("té quatre destins en dos grups: Treball i Empresa", () => {
    expect(NAVIGATION_GROUPS.map((group) => group.label)).toEqual(["Treball", "Empresa"]);
    expect(NAVIGATION_GROUPS[0].items.map((item) => item.label)).toEqual(["Avui", "Oportunitats", "Candidatures"]);
    expect(NAVIGATION_GROUPS[1].items.map((item) => item.label)).toEqual(["El nostre perfil"]);
    expect(NAVIGATION_GROUPS.flatMap((group) => group.items)).toHaveLength(4);
  });

  it("la secció Empresa té tres subdestins", () => {
    expect(PROFILE_SUBNAV.map((item) => [item.href, item.label])).toEqual([
      ["/empresa", "Totes les àrees"],
      ["/documents", "Biblioteca de documents"],
      ["/experiencia", "Experiència"],
    ]);
  });

  it("«El nostre perfil» és actiu a /empresa/*, /experiencia i /documents", () => {
    for (const pathname of ["/empresa", "/empresa/cpv", "/experiencia", "/experiencia/p1", "/documents"]) {
      expect(isNavigationActive(pathname, "/empresa"), pathname).toBe(true);
    }
    expect(isNavigationActive("/licitacions", "/empresa")).toBe(false);
  });

  it("els altres destins són actius al seu prefix i prou", () => {
    expect(isNavigationActive("/licitacions/abc", "/licitacions")).toBe(true);
    expect(isNavigationActive("/candidatures", "/licitacions")).toBe(false);
    expect(isNavigationActive("/avui", "/avui")).toBe(true);
    expect(NAVIGATION_GROUPS[0].items[0].href).toBe("/avui");
  });

  it("la subnavegació marca només la seva secció", () => {
    expect(isSubnavActive("/empresa/cpv", "/empresa")).toBe(true);
    expect(isSubnavActive("/documents", "/empresa")).toBe(false);
    expect(isSubnavActive("/documents", "/documents")).toBe(true);
    expect(isSubnavActive("/experiencia/p1", "/experiencia")).toBe(true);
  });
});
