import { describe, expect, it } from "vitest";

import { ROLE_SOURCE_LABELS, roleSourceLabel, type RoleSource } from "./tenders";

describe("d'on surt el rol d'un document (`RoleSource`)", () => {
  it("cada origen té text humà, i el d'una versió anterior diu que no s'aplica", () => {
    const all: RoleSource[] = ["classifier", "person", "operator", "global_classifier", "feed", "stale_override"];
    for (const source of all) {
      expect(ROLE_SOURCE_LABELS[source], source).toBeTruthy();
      expect(roleSourceLabel(source)).not.toMatch(/_/);
    }
    expect(roleSourceLabel("stale_override")).toMatch(/versió anterior/);
    expect(roleSourceLabel("stale_override")).toMatch(/no s'aplica/);
  });

  it("un origen desconegut no surt mai cru", () => {
    expect(roleSourceLabel("un_origen_nou")).toBe("Origen del tipus desconegut");
  });
});
