import { describe, expect, it } from "vitest";

import { NAVIGATION_GROUPS, PROFILE_SUBNAV, isNavigationActive, isSubnavActive } from "./navigation";

describe("navegació del shell", () => {
  it("té quatre destins en dos grups: Treball i Empresa", () => {
    expect(NAVIGATION_GROUPS.map((group) => group.label)).toEqual(["Treball", "Administració de l'empresa"]);
    expect(NAVIGATION_GROUPS[0].items.map((item) => item.label)).toEqual(["Avui", "Oportunitats", "Candidatures"]);
    expect(NAVIGATION_GROUPS[1].items.map((item) => item.label)).toEqual(["El nostre perfil"]);
    expect(NAVIGATION_GROUPS.flatMap((group) => group.items)).toHaveLength(4);
    // B1: «El nostre perfil» no porta cap recompte (abans el percentatge de completitud).
    expect(NAVIGATION_GROUPS[1].items[0].countKey).toBeUndefined();
    expect(NAVIGATION_GROUPS[0].items.map((item) => item.countKey)).toEqual(["avui", "oportunitats", "candidatures"]);
  });

  it("la secció Empresa té cinc subdestins: les preferències del Radar a part del coneixement (B4)", () => {
    expect(PROFILE_SUBNAV.map((item) => [item.href, item.label])).toEqual([
      ["/empresa", "Totes les àrees"],
      ["/empresa/radar", "Preferències del Radar"],
      ["/empresa/coneixement", "Coneixement"],
      ["/documents", "Biblioteca de documents"],
      ["/experiencia", "Experiència"],
    ]);
    expect(isSubnavActive("/empresa/radar", "/empresa/radar")).toBe(true);
    expect(isSubnavActive("/empresa/radar", "/empresa")).toBe(false);
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
    // «Coneixement» penja d'/empresa: només ell queda marcat, no «Totes les àrees».
    expect(isSubnavActive("/empresa/coneixement", "/empresa/coneixement")).toBe(true);
    expect(isSubnavActive("/empresa/coneixement", "/empresa")).toBe(false);
  });
});
