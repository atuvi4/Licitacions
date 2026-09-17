import { describe, expect, it } from "vitest";

import { buildQuery } from "@/lib/tenders";

import {
  EMPTY_FILTERS,
  MODES,
  PAGE_SIZE,
  SORT_OPTIONS,
  countApplied,
  feedQuery,
  modeFromParam,
  type Filters,
} from "./query";

/* -------------------------------------------------------------------------- */
/* Oracle: l'expressió literal de licitacions/page.tsx abans de L6              */
/* (commit 7f1f6c3). Si feedQuery canvia el significat d'un sol paràmetre,      */
/* aquesta comparació ho diu.                                                  */
/* -------------------------------------------------------------------------- */

const LEGACY_MODES = [
  { value: "all", status: undefined },
  { value: "candidate", status: "candidate" },
  { value: "low_fit", status: "low_fit" },
  { value: "excluded", status: "excluded" },
  { value: "insufficient_data", status: "insufficient_data" },
  { value: "closed", status: "closed" },
] as const;

const LEGACY_EMPTY_FILTERS = {
  q: "",
  cpv: "",
  status: "",
  procedure_type: "",
  contract_type: "",
  nuts: "",
  min_amount: "",
  max_amount: "",
  published_from: "",
  deadline_to: "",
  has_lots: "",
  only_open: false,
};

function legacyQuery(applied: typeof LEGACY_EMPTY_FILTERS, mode: string, sort: string, offset: number) {
  const activeMode = LEGACY_MODES.find((option) => option.value === mode) ?? LEGACY_MODES[0];
  return buildQuery({
    ...applied,
    has_lots: applied.has_lots || undefined,
    only_open: applied.only_open || undefined,
    match_status: activeMode.status,
    sort,
    limit: 25,
    offset,
  });
}

const FILTER_CASES: Array<{ name: string; filters: Filters }> = [
  { name: "sense filtres", filters: EMPTY_FILTERS },
  { name: "text", filters: { ...EMPTY_FILTERS, q: "fibra òptica" } },
  { name: "cpv + nuts", filters: { ...EMPTY_FILTERS, cpv: "45314320", nuts: "ES51" } },
  { name: "estat + procediment + tipus", filters: { ...EMPTY_FILTERS, status: "open", procedure_type: "open", contract_type: "services" } },
  { name: "imports", filters: { ...EMPTY_FILTERS, min_amount: "1000", max_amount: "250000" } },
  { name: "dates", filters: { ...EMPTY_FILTERS, published_from: "2026-01-01", deadline_to: "2026-12-31" } },
  { name: "amb lots", filters: { ...EMPTY_FILTERS, has_lots: "true" } },
  { name: "sense lots", filters: { ...EMPTY_FILTERS, has_lots: "false" } },
  { name: "només obertes", filters: { ...EMPTY_FILTERS, only_open: true } },
  {
    name: "tot alhora",
    filters: {
      q: "manteniment",
      cpv: "50330000",
      status: "open",
      procedure_type: "restricted",
      contract_type: "works",
      nuts: "ES511",
      min_amount: "50000",
      max_amount: "900000",
      published_from: "2026-06-01",
      deadline_to: "2026-10-31",
      has_lots: "true",
      only_open: true,
    },
  },
];

describe("feedQuery: la mateixa consulta que abans de L6", () => {
  it("les constants no han canviat: 6 modes, 5 ordres, 12 filtres buits i pàgina de 25", () => {
    expect(MODES.map((mode) => mode.value)).toEqual(LEGACY_MODES.map((mode) => mode.value));
    expect(MODES.map((mode) => mode.status)).toEqual(LEGACY_MODES.map((mode) => mode.status));
    expect(SORT_OPTIONS.map((option) => option.value)).toEqual(["score", "recent", "published", "deadline", "amount"]);
    expect(EMPTY_FILTERS).toEqual(LEGACY_EMPTY_FILTERS);
    expect(PAGE_SIZE).toBe(25);
  });

  it("6 modes × 5 ordres × 10 jocs de filtres × 3 pàgines donen exactament la query antiga", () => {
    let compared = 0;
    for (const mode of MODES) {
      for (const sort of SORT_OPTIONS) {
        for (const { name, filters } of FILTER_CASES) {
          for (const offset of [0, 25, 50]) {
            const actual = feedQuery({ applied: filters, mode: mode.value, sort: sort.value, offset });
            const expected = legacyQuery(filters, mode.value, sort.value, offset);
            expect(actual, `${mode.value} · ${sort.value} · ${name} · offset ${offset}`).toBe(expected);
            compared += 1;
          }
        }
      }
    }
    expect(compared).toBe(6 * 5 * FILTER_CASES.length * 3);
  });

  it("valors literals coneguts (per si l'oracle i la implementació s'equivoquessin igual)", () => {
    expect(feedQuery({ applied: EMPTY_FILTERS, mode: "all", sort: "score", offset: 0 })).toBe(
      "sort=score&limit=25&offset=0",
    );
    expect(feedQuery({ applied: EMPTY_FILTERS, mode: "candidate", sort: "deadline", offset: 0 })).toBe(
      "match_status=candidate&sort=deadline&limit=25&offset=0",
    );
    expect(feedQuery({ applied: { ...EMPTY_FILTERS, q: "fibra òptica" }, mode: "all", sort: "score", offset: 50 })).toBe(
      "q=fibra+%C3%B2ptica&sort=score&limit=25&offset=50",
    );
    expect(
      feedQuery({ applied: { ...EMPTY_FILTERS, has_lots: "false", only_open: true }, mode: "closed", sort: "amount", offset: 25 }),
    ).toBe("has_lots=false&only_open=true&match_status=closed&sort=amount&limit=25&offset=25");
    expect(feedQuery({ applied: FILTER_CASES[9].filters, mode: "low_fit", sort: "published", offset: 0 })).toBe(
      "q=manteniment&cpv=50330000&status=open&procedure_type=restricted&contract_type=works&nuts=ES511&min_amount=50000&max_amount=900000&published_from=2026-06-01&deadline_to=2026-10-31&has_lots=true&only_open=true&match_status=low_fit&sort=published&limit=25&offset=0",
    );
  });

  it("un mode desconegut cau a «Totes», com abans", () => {
    expect(feedQuery({ applied: EMPTY_FILTERS, mode: "inventat", sort: "score", offset: 0 })).toBe(
      legacyQuery(LEGACY_EMPTY_FILTERS, "inventat", "score", 0),
    );
  });
});

describe("lectura de la URL i recompte de filtres", () => {
  it("?match_status= només accepta els modes reals; «all», buit o desconegut és «Totes»", () => {
    expect(modeFromParam("candidate")).toBe("candidate");
    expect(modeFromParam("closed")).toBe("closed");
    expect(modeFromParam("all")).toBe("all");
    expect(modeFromParam("")).toBe("all");
    expect(modeFromParam(null)).toBe("all");
    expect(modeFromParam("go_candidate")).toBe("all");
  });

  it("countApplied compta cada filtre amb valor i «només obertes» si és cert", () => {
    expect(countApplied(EMPTY_FILTERS)).toBe(0);
    expect(countApplied({ ...EMPTY_FILTERS, q: "fibra" })).toBe(1);
    expect(countApplied({ ...EMPTY_FILTERS, q: "fibra", only_open: true, has_lots: "false" })).toBe(3);
    expect(countApplied(FILTER_CASES[9].filters)).toBe(12);
  });
});
