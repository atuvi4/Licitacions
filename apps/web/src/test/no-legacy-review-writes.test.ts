import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * La revisio de producte es NOMES la canonica per empresa (`/revisio`, API
 * `/companies/{c}/tenders/{t}/review/...`). Els endpoints antics
 * `POST /requirements/{id}/review` i `POST /award-criteria/{id}/review`
 * escriuen a la fila extreta compartida per totes les empreses: cap pantalla
 * no els ha de tornar a cridar.
 */
const SRC = join(__dirname, "..");
const LEGACY = /[`'"]\/(requirements|award-criteria)\/\$\{[^}]+\}\/review[`'"]/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("cap escriptura de revisio antiga des del producte", () => {
  it("cap fitxer de l'aplicacio crida /requirements/{id}/review ni /award-criteria/{id}/review", () => {
    const offenders = sources(SRC)
      .filter((path) => LEGACY.test(readFileSync(path, "utf8")))
      .map((path) => relative(SRC, path));
    expect(offenders).toEqual([]);
  });

  it("el patro detecta la crida antiga (el test no passa per buit)", () => {
    expect(LEGACY.test("await api.post(`/requirements/${id}/review`, {})")).toBe(true);
    expect(LEGACY.test("api.post(`/award-criteria/${criterion.id}/review`)")).toBe(true);
    expect(LEGACY.test("`/companies/${c}/tenders/${t}/review/start`")).toBe(false);
  });
});
