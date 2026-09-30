import { describe, expect, it } from "vitest";

import { displayName, initials, isoDatePlus, pilotProgress, PLATFORM_STATUS_LABELS, relativeTime } from "./platform";

describe("presentació de la consola Tuvi", () => {
  it("noms i inicials com al disseny", () => {
    expect(displayName({ legal_name: "Enginyeria i Projectes Canigó, S.L.", trade_name: "Enginyeria Canigó" })).toBe("Enginyeria Canigó");
    expect(displayName({ legal_name: "Indaleccius BroadCasting SL", trade_name: null })).toBe("Indaleccius BroadCasting SL");
    expect(initials("Enginyeria Canigó")).toBe("EC");
    expect(initials("Instal·lacions Vall d'en Bas")).toBe("IV");
    expect(initials("Albert")).toBe("AL");
    expect(initials(null)).toBe("?");
  });

  it("temps relatiu: minuts, hores, ahir, dies i després la data", () => {
    const now = new Date("2026-09-29T12:00:00Z");
    expect(relativeTime("2026-09-29T11:48:00Z", now)).toBe("fa 12 min");
    expect(relativeTime("2026-09-29T09:00:00Z", now)).toBe("fa 3 h");
    expect(relativeTime("2026-09-28T10:00:00Z", now)).toBe("ahir");
    expect(relativeTime("2026-09-27T10:00:00Z", now)).toBe("fa 2 dies");
    expect(relativeTime("2026-09-01T10:00:00Z", now)).toMatch(/2026/);
    expect(relativeTime(null, now)).toBe("—");
  });

  it("progrés del pilot: «Dia 29 de 61», sense sortir dels límits", () => {
    const today = new Date(2026, 8, 29);
    expect(pilotProgress("2026-09-01", "2026-10-31", today)).toEqual({ day: 29, total: 61, percent: 48 });
    expect(pilotProgress("2026-09-01", "2026-09-10", today)).toMatchObject({ day: 10, total: 10, percent: 100 });
    expect(pilotProgress(null, "2026-10-31", today)).toBeNull();
  });

  it("dates ISO locals i etiquetes d'estat", () => {
    expect(isoDatePlus(60, new Date(2026, 8, 29))).toBe("2026-11-28");
    expect(PLATFORM_STATUS_LABELS).toEqual({ pilot: "Pilot", active: "Activa", paused: "Pausada" });
  });
});
