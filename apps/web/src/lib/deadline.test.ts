import { describe, expect, it } from "vitest";

import { deadlineCountdown, deadlineCountdownView, deadlineLevel, isDeadlinePassed } from "./deadline";

/**
 * El cas del pilot (A11): a 11 dies i 18 hores del termini, la fitxa i
 * Oportunitats deien «12 dies» (arrodoniment cap amunt) i Candidatures «11»
 * (cap avall, del servidor). La regla única: dies naturals sencers que queden a
 * Europe/Madrid. 9 d'octubre a les 18:00 → 21 d'octubre a les 12:00 són 12 dies.
 */
const NOW = new Date("2026-10-09T16:00:00Z"); // 18:00 a Madrid
const PILOT_DEADLINE = "2026-10-21T10:00:00Z"; // 12:00 a Madrid

describe("deadlineCountdown", () => {
  it("compta dies naturals sencers a Europe/Madrid, amb un sol arrodoniment", () => {
    expect(deadlineCountdown(PILOT_DEADLINE, NOW)).toBe(12);
    // 00:30 del 22 a Madrid (22:30Z del 21): ja és un dia més.
    expect(deadlineCountdown("2026-10-21T22:30:00Z", NOW)).toBe(13);
    // El mateix dia natural, encara que faltin minuts: 0.
    expect(deadlineCountdown("2026-10-09T21:59:00Z", NOW)).toBe(0);
    expect(deadlineCountdown("2026-10-08T10:00:00Z", NOW)).toBe(-1);
  });

  it("accepta una Date o un ISO, i un termini absent o il·legible és null", () => {
    expect(deadlineCountdown(new Date(PILOT_DEADLINE), NOW)).toBe(12);
    expect(deadlineCountdown(null, NOW)).toBeNull();
    expect(deadlineCountdown(undefined, NOW)).toBeNull();
    expect(deadlineCountdown("no és una data", NOW)).toBeNull();
  });

  it("el canvi d'hora no mou el recompte: són dies de calendari, no blocs de 24 h", () => {
    // 24 → 26 d'octubre de 2026 travessa el final de l'horari d'estiu a Madrid.
    expect(deadlineCountdown("2026-10-26T11:00:00Z", new Date("2026-10-24T11:30:00Z"))).toBe(2);
  });
});

describe("isDeadlinePassed i deadlineCountdownView (A12)", () => {
  it("«Fora de termini» quan el termini ja ha passat, encara que sigui el mateix dia", () => {
    expect(isDeadlinePassed("2026-10-09T15:00:00Z", NOW)).toBe(true);
    expect(isDeadlinePassed("2026-10-09T17:00:00Z", NOW)).toBe(false);
    expect(isDeadlinePassed(null, NOW)).toBe(false);
    // Un termini passat no és cap urgència: pastilla neutra. El vermell és per als imminents.
    expect(deadlineCountdownView("2026-10-09T15:00:00Z", NOW)).toEqual({ days: 0, passed: true, text: "Fora de termini", level: "neutral" });
    expect(deadlineCountdownView("2026-10-01T15:00:00Z", NOW)).toEqual({ days: -8, passed: true, text: "Fora de termini", level: "neutral" });
  });

  it("el text i el nivell del comptador, iguals a totes les pantalles", () => {
    expect(deadlineCountdownView("2026-10-09T17:00:00Z", NOW)).toEqual({ days: 0, passed: false, text: "Venç avui", level: "blocking" });
    expect(deadlineCountdownView("2026-10-10T10:00:00Z", NOW)).toEqual({ days: 1, passed: false, text: "1 dia", level: "blocking" });
    expect(deadlineCountdownView(PILOT_DEADLINE, NOW)).toEqual({ days: 12, passed: false, text: "12 dies", level: "attention" });
    expect(deadlineCountdownView("2026-11-30T10:00:00Z", NOW)).toMatchObject({ days: 52, text: "52 dies", level: "neutral" });
    expect(deadlineCountdownView(null, NOW)).toEqual({ days: null, passed: false, text: "Sense termini", level: "neutral" });
  });

  it("el nivell: una setmana bloqueja, tres setmanes demanen atenció; un termini passat és neutre", () => {
    expect(deadlineLevel(null)).toBe("neutral");
    expect(deadlineLevel(-1)).toBe("neutral");
    expect(deadlineLevel(0)).toBe("blocking");
    expect(deadlineLevel(7)).toBe("blocking");
    expect(deadlineLevel(8)).toBe("attention");
    expect(deadlineLevel(21)).toBe("attention");
    expect(deadlineLevel(22)).toBe("neutral");
  });
});
