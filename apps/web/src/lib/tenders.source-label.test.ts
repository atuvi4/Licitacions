import { describe, expect, it } from "vitest";

import { sourceLabel } from "./tenders";

describe("sourceLabel", () => {
  it("primer el diccionari català, després la faceta, després «Font desconeguda»", () => {
    const facets = [
      { code: "manual", name: "manual", count: 1 },
      { code: "placsp_agregadas", name: "placsp_agregadas", count: 2 },
      { code: "nova", name: "Nova font", count: 3 },
    ];
    expect(sourceLabel("manual", facets)).toBe("Importació manual");
    expect(sourceLabel("PLACSP", facets)).toBe("PLACSP · perfils de contractant");
    expect(sourceLabel("placsp_agregadas", facets)).toBe("PLACSP · plataformes agregades (PSCP)");
    expect(sourceLabel("nova", facets)).toBe("Nova font");
    expect(sourceLabel("altra", facets)).toBe("Font desconeguda");
    expect(sourceLabel(null, facets)).toBeNull();
  });
});
