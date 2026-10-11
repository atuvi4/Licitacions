import { describe, expect, it } from "vitest";

import { buildQuery } from "@/lib/tenders";

import {
  CATALOGUE_SORT_OPTIONS,
  DEFAULT_CATALOGUE_SORT,
  EMPTY_CATALOGUE_FILTERS,
  EMPTY_FILTERS,
  MODES,
  PAGE_SIZE,
  SORT_OPTIONS,
  catalogueQuery,
  catalogueSortOptions,
  countApplied,
  countCatalogueFilters,
  feedQuery,
  hasAmountFilter,
  modeFromParam,
  tabFromParams,
  tenderStatusFilterOptions,
  type CatalogueFilters,
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
    expect(modeFromParam("all")).toBe("candidate");
    expect(modeFromParam("")).toBe("candidate");
    expect(modeFromParam(null)).toBe("candidate");
    expect(modeFromParam("go_candidate")).toBe("candidate");
  });

  it("countApplied compta cada filtre amb valor i «només obertes» si és cert", () => {
    expect(countApplied(EMPTY_FILTERS)).toBe(0);
    expect(countApplied({ ...EMPTY_FILTERS, q: "fibra" })).toBe(1);
    expect(countApplied({ ...EMPTY_FILTERS, q: "fibra", only_open: true, has_lots: "false" })).toBe(3);
    expect(countApplied(FILTER_CASES[9].filters)).toBe(12);
  });
});

describe("«Totes»: el contracte de consulta del catàleg", () => {
  it("sense filtres: mode=all, ordre neutral per publicació, pàgina de 25, mai min_score ni match_status", () => {
    const query = catalogueQuery({ applied: EMPTY_CATALOGUE_FILTERS, offset: 0 });
    expect(query).toBe("mode=all&sort=published&limit=25&offset=0");
    expect(DEFAULT_CATALOGUE_SORT).toBe("published");
    expect(query).not.toContain("min_score");
    expect(query).not.toContain("match_status");
  });

  it("cap combinació de filtres ni d'ordres no afegeix min_score ni match_status", () => {
    const all: CatalogueFilters = {
      q: "fibra òptica",
      cpv: "4531",
      buyer_q: "Sabadell",
      nuts: "ES51",
      place_q: "Vallès",
      min_amount: "1000",
      max_amount: "9000",
      published_from: "2026-01-01",
      published_to: "2026-06-30",
      deadline_from: "2026-07-01",
      deadline_to: "2026-12-31",
      contract_type: "services",
      procedure_type: "open",
      status: "open",
      source: "PLACSP",
      source_removed: "false",
    };
    for (const sort of [...CATALOGUE_SORT_OPTIONS.map((option) => option.value), "recent"]) {
      const params = new URLSearchParams(catalogueQuery({ applied: all, sort, offset: 50 }));
      expect(params.has("min_score")).toBe(false);
      expect(params.has("match_status")).toBe(false);
      expect(params.get("sort")).toBe(sort);
      expect(params.get("offset")).toBe("50");
    }
    expect(catalogueQuery({ applied: all, offset: 0 })).toBe(
      "mode=all&q=fibra+%C3%B2ptica&cpv=4531&buyer_q=Sabadell&nuts=ES51&place_q=Vall%C3%A8s&min_amount=1000&max_amount=9000&published_from=2026-01-01&published_to=2026-06-30&deadline_from=2026-07-01&deadline_to=2026-12-31T23%3A59%3A59&contract_type=services&procedure_type=open&status=open&source=PLACSP&source_removed=false&sort=published&limit=25&offset=0",
    );
  });

  it("els filtres buits o amb només espais no viatgen", () => {
    expect(catalogueQuery({ applied: { ...EMPTY_CATALOGUE_FILTERS, q: "   ", buyer_q: " " }, offset: 0 })).toBe(
      "mode=all&sort=published&limit=25&offset=0",
    );
  });

  it("compta els filtres fixats i només ofereix «Rellevància» amb text", () => {
    expect(countCatalogueFilters(EMPTY_CATALOGUE_FILTERS)).toBe(0);
    expect(countCatalogueFilters({ ...EMPTY_CATALOGUE_FILTERS, q: "x", source_removed: "true" })).toBe(2);
    expect(catalogueSortOptions(EMPTY_CATALOGUE_FILTERS).map((option) => option.value)).not.toContain("relevance");
    expect(catalogueSortOptions({ ...EMPTY_CATALOGUE_FILTERS, q: "x" }).map((option) => option.value)).toContain("relevance");
    expect(hasAmountFilter({ ...EMPTY_CATALOGUE_FILTERS, max_amount: "5" })).toBe(true);
    expect(hasAmountFilter(EMPTY_CATALOGUE_FILTERS)).toBe(false);
  });

  it("pestanya: ?vista= mana; ?match_status= és «Per revisar»; ?q= i res és «Totes»", () => {
    const params = (value: string) => new URLSearchParams(value);
    expect(tabFromParams(params(""))).toBe("totes");
    expect(tabFromParams(params("q=fibra"))).toBe("totes");
    expect(tabFromParams(params("match_status=candidate"))).toBe("revisar");
    expect(tabFromParams(params("vista=revisar"))).toBe("revisar");
    expect(tabFromParams(params("vista=totes&match_status=candidate"))).toBe("totes");
    expect(tabFromParams(params("vista=inventada"))).toBe("totes");
  });
});

describe("F2: «En termini» és l'estat accionable, no l'estat cru de la font", () => {
  it("el valor `in_term` del filtre d'estat viatja com a `in_term=true`, mai com a `status=open`", () => {
    const feed = new URLSearchParams(feedQuery({ applied: { ...EMPTY_FILTERS, status: "in_term" }, mode: "all", sort: "score", offset: 0 }));
    expect(feed.get("in_term")).toBe("true");
    expect(feed.has("status")).toBe(false);
    const catalogue = new URLSearchParams(catalogueQuery({ applied: { ...EMPTY_CATALOGUE_FILTERS, status: "in_term" }, offset: 0 }));
    expect(catalogue.get("in_term")).toBe("true");
    expect(catalogue.has("status")).toBe(false);
  });

  it("un estat cru de la font continua sent `status=`", () => {
    const catalogue = new URLSearchParams(catalogueQuery({ applied: { ...EMPTY_CATALOGUE_FILTERS, status: "awarded" }, offset: 0 }));
    expect(catalogue.get("status")).toBe("awarded");
    expect(catalogue.has("in_term")).toBe(false);
  });

  it("les opcions del filtre: «En termini» (accionable) primer i els estats crus amb el seu nom de font", () => {
    const options = tenderStatusFilterOptions(["open", "awarded", "cancelled"]);
    expect(options[0]).toEqual({ value: "in_term", label: "En termini" });
    expect(options.slice(1)).toEqual([
      { value: "open", label: "Publicada (estat de la font)" },
      { value: "awarded", label: "Adjudicada (estat de la font)" },
      { value: "cancelled", label: "Anul·lada (estat de la font)" },
    ]);
    expect(options.filter((option) => option.label === "En termini")).toHaveLength(1);
  });
});
