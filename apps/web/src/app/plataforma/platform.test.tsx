import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const replace = vi.fn();
const push = vi.fn();
let search = "";
let pathname = "/plataforma/empreses";
let params: Record<string, string> = {};
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...a: unknown[]) => get(...a),
      post: (...a: unknown[]) => post(...a),
      patch: (...a: unknown[]) => patch(...a),
    },
  };
});
// Estables, com els de Next.
const router = { replace, push, refresh: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(search),
  useParams: () => params,
  redirect: vi.fn(),
}));

import PlatformLayout from "./layout";
import CompanyDetailPage from "./empreses/[companyId]/page";
import CompaniesPage from "./empreses/page";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const operator = { id: "op", email: "admin@tuvi.app", full_name: "Albert", is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: true, memberships: [] };
const now = new Date().toISOString();
const list = {
  counts: { all: 3, pilot: 1, active: 1, paused: 1 },
  items: [
    { id: "c1", legal_name: "Enginyeria i Projectes Canigó, S.L.", trade_name: "Enginyeria Canigó", tax_id: "B17458392", platform_status: "pilot", pilot_ends_at: "2026-11-28", owner: { id: "o1", full_name: "Marta Vilanova", email: "marta@canigo.cat", assigned_at: now, last_login_at: null }, users_count: 6, tenders_count: 6, last_activity_at: now, created_at: now },
    { id: "c2", legal_name: "Obres Fluvià SL", trade_name: null, tax_id: "B17223018", platform_status: "active", pilot_ends_at: null, owner: { id: "o2", full_name: "Jordi Puig", email: "jordi@fluvia.cat", assigned_at: now, last_login_at: null }, users_count: 11, tenders_count: 12, last_activity_at: null, created_at: now },
    { id: "c3", legal_name: "Aigües del Ter Serveis SL", trade_name: null, tax_id: "B17664120", platform_status: "paused", pilot_ends_at: null, owner: null, users_count: 5, tenders_count: 0, last_activity_at: null, created_at: now },
  ],
};
const detail = {
  id: "c1a2b3c4-0000-0000-0000-000000000000",
  legal_name: "Enginyeria i Projectes Canigó, S.L.",
  trade_name: "Enginyeria Canigó",
  tax_id: "B17458392",
  created_at: "2026-09-01T08:00:00Z",
  onboarded_by: { id: "op", full_name: "Albert", email: "admin@tuvi.app" },
  status: { platform_status: "pilot", pilot_started_at: "2026-09-01", pilot_ends_at: "2026-10-31", status_changed_at: "2026-09-01T08:00:00Z" },
  owner: { id: "o1", full_name: "Marta Vilanova", email: "marta@canigo.cat", assigned_at: "2026-09-01T08:00:00Z", last_login_at: null },
  client_space: {
    tenders: { total: 8, active: 6, preparing: 3, submitted: 3 },
    recent_tenders: [{ title: "Estudi de mobilitat del nucli antic", buyer_name: "Ajuntament de Figueres", status: "preparing_bid", deadline_at: "2026-10-28T00:00:00Z" }],
    recent_activity: [
      { at: now, action: "create", entity_type: "bid_workspace", summary: "Workspace de candidatura obert", actor: { id: "o1", full_name: "Marta Vilanova", email: "marta@canigo.cat" }, by_platform: false },
      { at: "2026-09-01T08:00:00Z", action: "create", entity_type: "companies", summary: "Empresa donada d'alta en pilot fins al 2026-10-31", actor: { id: "op", full_name: "Albert", email: "admin@tuvi.app" }, by_platform: true },
    ],
    users: { total: 6, active_last_7_days: 4 },
  },
};

function respond(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "/auth/me": operator,
    "/config": { environment: "pilot", max_upload_size_mb: 25, allowed_mime_types: [], expiry_warning_days: 30 },
    "/platform/companies": list,
    [`/platform/companies/${detail.id}`]: detail,
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    const value = routes[path];
    if (value instanceof Error) return Promise.reject(value);
    return value === undefined ? Promise.reject(new ApiError(404, "not_found", "No trobat")) : Promise.resolve(value);
  });
}

function renderInLayout(page: React.ReactNode) {
  return render(<PlatformLayout>{page}</PlatformLayout>);
}

beforeEach(() => {
  for (const mock of [get, post, patch, replace, push]) mock.mockReset();
  search = "";
  pathname = "/plataforma/empreses";
  params = {};
});

describe("Accés a l'Administració Tuvi", () => {
  it("un propietari d'empresa no hi entra i no es demana cap dada de plataforma", async () => {
    respond({ "/auth/me": { ...operator, is_platform_admin: false, memberships: [{ company_id: "x", role: "owner" }] } });
    renderInLayout(<CompaniesPage />);
    await flush();
    expect(screen.getByText(/Els comptes d'empresa, també els propietaris, no hi tenen accés/)).toBeTruthy();
    expect(get.mock.calls.map(([path]) => path)).toEqual(["/auth/me"]);
  });

  it("amb una contrasenya temporal pendent, primer s'ha de canviar", async () => {
    respond({ "/auth/me": { ...operator, must_change_password: true } });
    renderInLayout(<CompaniesPage />);
    await flush();
    expect(replace).toHaveBeenCalledWith("/compte/contrasenya");
  });

  it("sense sessió, al login", async () => {
    respond({ "/auth/me": new ApiError(401, "not_authenticated", "Cal iniciar sessió") });
    renderInLayout(<CompaniesPage />);
    await flush();
    expect(replace).toHaveBeenCalledWith("/login");
  });
});

describe("Marc de la consola", () => {
  it("es distingeix de l'app client: ADMIN, Administració Tuvi, entorn pilot i l'operador", async () => {
    respond();
    renderInLayout(<CompaniesPage />);
    await flush();
    await flush();
    const nav = screen.getByRole("navigation", { name: "Administració Tuvi" });
    expect(within(nav).getByRole("link", { name: /Empreses/ }).textContent).toContain("3");
    expect(within(nav).getByText("Dashboard").closest("[aria-disabled]")).toBeTruthy();
    expect(screen.getByText("Admin")).toBeTruthy();
    expect(screen.getByText("Consola interna de Tuvi")).toBeTruthy();
    expect(document.querySelector("[data-environment='pilot']")?.textContent).toMatch(/Entorn pilot/);
    expect(screen.getByText("Operador Tuvi")).toBeTruthy();
    expect(screen.queryByText("Administració de l'empresa")).toBeNull();
  });

  it("fora del pilot no es mostra l'indicador d'entorn", async () => {
    respond({ "/config": { environment: "local", max_upload_size_mb: 25, allowed_mime_types: [], expiry_warning_days: 30 } });
    renderInLayout(<CompaniesPage />);
    await flush();
    await flush();
    expect(document.querySelector("[data-environment]")).toBeNull();
  });

  it("la cerca superior porta al llistat filtrat", async () => {
    respond();
    renderInLayout(<CompaniesPage />);
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "Cerca empreses, NIF o propietaris" }), { target: { value: "B1722" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(push).toHaveBeenCalledWith("/plataforma/empreses?q=B1722");
  });
});

describe("Empreses", () => {
  async function showList() {
    respond();
    renderInLayout(<CompaniesPage />);
    await flush();
    await flush();
    return screen.getByRole("table", { name: "Empreses clients" });
  }

  it("llista els clients amb estat, propietari, recomptes i pestanyes", async () => {
    const table = await showList();
    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual(["Totes3", "Actives1", "Pilot1", "Pausades1"]);
    const canigo = within(table).getByText("Enginyeria Canigó").closest("tr")!;
    expect(within(canigo).getByText("NIF B17458392")).toBeTruthy();
    expect(within(canigo).getByText("Pilot")).toBeTruthy();
    expect(within(canigo).getByText("Marta Vilanova")).toBeTruthy();
    expect(within(canigo).getByRole("link", { name: "Obrir" }).getAttribute("href")).toBe("/plataforma/empreses/c1");
    expect(within(table).getByText("Aigües del Ter Serveis SL").closest("tr")!.textContent).toContain("Pausada");
  });

  it("la pestanya i la cerca de la URL filtren", async () => {
    search = "estat=paused";
    const table = await showList();
    expect(within(table).queryByText("Enginyeria Canigó")).toBeNull();
    expect(within(table).getByText("Aigües del Ter Serveis SL")).toBeTruthy();
  });

  it("la cerca troba per NIF encara que no sigui el nom", async () => {
    search = "q=B17223018";
    const table = await showList();
    expect(within(table).getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "OFObres Fluvià SLNIF B17223018",
    ]);
  });

  it("«Nova empresa» obre el calaix, i l'alta demana el NIF i comença en pilot de 60 dies", async () => {
    await showList();
    fireEvent.click(screen.getByRole("button", { name: /Nova empresa/ }));
    expect(replace).toHaveBeenCalledWith("/plataforma/empreses?nova=1", { scroll: false });
  });

  it("el calaix d'alta envia el flux existent i mostra la temporal una sola vegada", async () => {
    search = "nova=1";
    post.mockResolvedValue({
      company: { id: "new", legal_name: "Indaleccius BroadCasting SL", trade_name: null, tax_id: "B12345678", platform_status: "pilot", pilot_ends_at: "2026-11-28" },
      owner: { id: "ow", email: "owner@indaleccius.com", full_name: "Propietària" },
      temporary_password: "Tmp9xYzAbCdEfGhK",
    });
    await showList();
    const drawer = screen.getByRole("dialog", { name: "Nova empresa" });
    expect((within(drawer).getByLabelText(/NIF/) as HTMLInputElement).required).toBe(true);
    expect((within(drawer).getByRole("radio", { name: /Pilot/ }) as HTMLInputElement).checked).toBe(true);
    const end = within(drawer).getByLabelText(/Fi del pilot/) as HTMLInputElement;
    fireEvent.change(within(drawer).getByLabelText(/Nom legal/), { target: { value: "Indaleccius BroadCasting SL" } });
    fireEvent.change(within(drawer).getByLabelText(/NIF/), { target: { value: "B12345678" } });
    fireEvent.change(within(drawer).getByLabelText(/Nom i cognoms/), { target: { value: "Propietària" } });
    fireEvent.change(within(drawer).getByLabelText(/Correu del propietari/), { target: { value: "owner@indaleccius.com" } });
    fireEvent.submit(drawer.querySelector("form")!);
    await flush();

    expect(post).toHaveBeenCalledWith("/platform/companies", {
      legal_name: "Indaleccius BroadCasting SL",
      trade_name: null,
      tax_id: "B12345678",
      initial_status: "pilot",
      pilot_ends_at: end.value,
      owner_full_name: "Propietària",
      owner_email: "owner@indaleccius.com",
    });
    const created = screen.getByRole("dialog", { name: "Empresa creada" });
    expect(within(created).getByRole("region", { name: "Contrasenya temporal" }).textContent).toContain("Tmp9xYzAbCdEfGhK");
    // Es refà el llistat perquè hi surti la nova.
    expect(get.mock.calls.filter(([path]) => path === "/platform/companies").length).toBe(2);
  });

  it("pausar des de la fila demana confirmació, diu que encara no bloqueja i envia l'estat", async () => {
    patch.mockResolvedValue(detail);
    const table = await showList();
    const row = within(table).getByText("Obres Fluvià SL").closest("tr")!;
    fireEvent.keyDown(within(row).getByRole("button", { name: "Accions de Obres Fluvià SL" }), { key: "Enter" });
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Obrir fitxa",
      "Canviar estat",
      "Copiar NIF",
      "Pausar empresa",
    ]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Pausar empresa" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Pausar Obres Fluvià SL?" });
    expect(confirm.textContent).toMatch(/encara no bloqueja l'accés/);
    expect(patch).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "Pausar empresa" }));
    await flush();
    expect(patch).toHaveBeenCalledWith("/platform/companies/c2/status", { status: "paused" });
  });

  describe("escriure al calaix «Nova empresa»", () => {
    const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])';

    /**
     * Escriu com una persona, tecla a tecla, al camp que ja té el focus; i el
     * focus s'hi ha de quedar després de cadascuna. No l'hi posa: qui crida
     * decideix si abans hi ha hagut un clic (`input.focus()`) o no.
     */
    function type(input: HTMLInputElement, text: string) {
      expect(document.activeElement, "abans d'escriure").toBe(input);
      let written = "";
      for (const char of text) {
        fireEvent.keyDown(input, { key: char });
        written += char;
        fireEvent.change(input, { target: { value: written } });
        fireEvent.keyUp(input, { key: char });
        expect(document.activeElement, `després d'escriure «${written}»`).toBe(input);
      }
    }

    /**
     * Tab com el fa el navegador: si ningú no l'intercepta, el focus passa a
     * l'element enfocable següent (o anterior) en l'ordre del document. jsdom
     * envia la tecla però no mou el focus.
     */
    function pressTab(options: { shift?: boolean } = {}) {
      const notIntercepted = fireEvent.keyDown(document.activeElement ?? document.body, { key: "Tab", shiftKey: options.shift ?? false });
      if (!notIntercepted) return;
      const order = [...document.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const index = order.indexOf(document.activeElement as HTMLElement);
      order[index + (options.shift ? -1 : 1)]?.focus();
    }

    async function openDrawer() {
      search = "nova=1";
      await showList();
      return screen.getByRole("dialog", { name: "Nova empresa" });
    }

    it("en clicar «Nova empresa» el cursor ja és a «Nom legal»: s'hi escriu sense clicar cap camp", async () => {
      respond();
      const view = renderInLayout(<CompaniesPage />);
      await flush();
      await flush();
      // El router simulat no navega. Aquí sí, com el de Next: canvia la URL i la pàgina es repinta.
      replace.mockImplementation((url: string) => {
        search = url.split("?")[1] ?? "";
        view.rerender(
          <PlatformLayout>
            <CompaniesPage />
          </PlatformLayout>,
        );
      });
      const button = screen.getByRole("button", { name: /Nova empresa/ });
      // Com el clic d'un navegador: el botó es queda el focus i després obre.
      button.focus();
      fireEvent.click(button);

      const drawer = screen.getByRole("dialog", { name: "Nova empresa" });
      const legal = within(drawer).getByLabelText(/Nom legal/) as HTMLInputElement;
      expect(document.activeElement).toBe(legal);
      type(legal, "Indaleccius Telecomunicacions");
      expect(legal.value).toBe("Indaleccius Telecomunicacions");
      expect(document.activeElement).toBe(legal);

      pressTab();
      expect(document.activeElement).toBe(within(drawer).getByLabelText(/Nom comercial/));
      pressTab({ shift: true });
      expect(document.activeElement).toBe(legal);

      fireEvent.keyDown(document, { key: "Escape" });
      expect(replace).toHaveBeenLastCalledWith("/plataforma/empreses", { scroll: false });
      expect(screen.queryByRole("dialog")).toBeNull();
      // I el focus torna al botó que l'havia obert, no es perd.
      expect(document.activeElement).toBe(button);
      expect(post).not.toHaveBeenCalled();
    });

    it("es poden escriure frases senceres sense perdre el focus, camp per camp", async () => {
      const drawer = await openDrawer();
      const fields: Array<[RegExp, string]> = [
        [/Nom legal/, "Indaleccius Telecomunicacions"],
        [/Nom i cognoms/, "Maria Rovira i Soler"],
        [/Correu del propietari/, "maria.rovira@exemple.cat"],
      ];
      for (const [label, text] of fields) {
        const input = within(drawer).getByLabelText(label) as HTMLInputElement;
        input.focus(); // el clic al camp
        type(input, text);
        expect(input.value).toBe(text);
        expect(document.activeElement).toBe(input);
      }
      // El calaix és el mateix node de principi a fi: no s'ha remuntat a cada tecla,
      // i el que s'ha escrit als altres camps hi continua.
      expect(screen.getByRole("dialog", { name: "Nova empresa" })).toBe(drawer);
      expect((within(drawer).getByLabelText(/Nom legal/) as HTMLInputElement).value).toBe("Indaleccius Telecomunicacions");
      // El botó de tancar no ha rebut el focus en cap moment, i no s'ha enviat res.
      expect(document.activeElement).not.toBe(within(drawer).getByRole("button", { name: "Tanca" }));
      expect(replace).not.toHaveBeenCalled();
      expect(post).not.toHaveBeenCalled();
    });

    it("Tab passa al camp següent i Shift+Tab torna, també després d'escriure", async () => {
      const drawer = await openDrawer();
      const legal = within(drawer).getByLabelText(/Nom legal/) as HTMLInputElement;
      legal.focus();
      type(legal, "Indaleccius");

      pressTab();
      expect(document.activeElement).toBe(within(drawer).getByLabelText(/Nom comercial/));
      pressTab();
      expect(document.activeElement).toBe(within(drawer).getByLabelText(/NIF/));
      pressTab({ shift: true });
      pressTab({ shift: true });
      expect(document.activeElement).toBe(legal);
      expect(legal.value).toBe("Indaleccius");
    });

    it("el focus no surt del calaix, i Escape el tanca", async () => {
      const drawer = await openDrawer();
      const close = within(drawer).getByRole("button", { name: "Tanca" });
      const submit = within(drawer).getByRole("button", { name: "Crear empresa" });
      const legal = within(drawer).getByLabelText(/Nom legal/) as HTMLInputElement;
      legal.focus();
      type(legal, "Indaleccius");

      // Des de l'últim element, Tab torna al primer; des del primer, Shift+Tab va a l'últim.
      submit.focus();
      pressTab();
      expect(document.activeElement).toBe(close);
      pressTab({ shift: true });
      expect(document.activeElement).toBe(submit);

      fireEvent.keyDown(document, { key: "Escape" });
      expect(replace).toHaveBeenCalledWith("/plataforma/empreses", { scroll: false });
    });
  });
});

describe("Fitxa d'empresa", () => {
  async function showDetail() {
    params = { companyId: detail.id };
    pathname = `/plataforma/empreses/${detail.id}`;
    respond();
    renderInLayout(<CompanyDetailPage />);
    await flush();
    await flush();
  }

  it("separa «Gestionat per Tuvi» de l'«Espai del client · vista de seguiment»", async () => {
    await showDetail();
    const managed = screen.getByRole("region", { name: "Gestionat per Tuvi" });
    expect(within(managed).getByText("Enginyeria i Projectes Canigó, S.L.")).toBeTruthy();
    expect(within(managed).getByText("B17458392")).toBeTruthy();
    expect(within(managed).getByText("c1a2b3c4")).toBeTruthy();
    expect(within(managed).getByText("Albert · operador Tuvi")).toBeTruthy();
    expect(within(managed).getByRole("progressbar", { name: "Progrés del pilot" })).toBeTruthy();
    expect(within(managed).getByText("marta@canigo.cat")).toBeTruthy();
    expect(within(managed).getByText("Encara no ha entrat")).toBeTruthy();

    const space = screen.getByRole("region", { name: "Espai del client · vista de seguiment" });
    expect(within(space).getByText("Estudi de mobilitat del nucli antic")).toBeTruthy();
    expect(within(space).getByText("Ajuntament de Figueres")).toBeTruthy();
    // «Adjudicades» no existeix a les dades: no s'inventa.
    expect(within(space).queryByText(/Adjudicad/)).toBeNull();
    expect(within(space).getByText("8 licitacions · es mostren les 1 més recents")).toBeTruthy();
    const activity = within(space).getByRole("list", { name: "Activitat recent" });
    expect(within(activity).getAllByRole("listitem")[1].textContent).toContain("Operador Tuvi");
    expect(within(space).getByText("Fase posterior")).toBeTruthy();
    expect(within(space).getByText(/4 actius els últims 7 dies/)).toBeTruthy();
    // Les altres pestanyes són d'una fase posterior.
    expect(screen.getAllByRole("tab").filter((tab) => tab.getAttribute("aria-disabled") === "true").map((tab) => tab.textContent)).toEqual([
      "LicitacionsAviat",
      "ActivitatAviat",
      "UsuarisAviat",
    ]);
  });

  it("allargar el pilot envia la data nova i la fitxa s'actualitza", async () => {
    patch.mockResolvedValue({ ...detail, status: { ...detail.status, pilot_ends_at: "2026-11-30" } });
    await showDetail();
    fireEvent.click(screen.getByRole("button", { name: "Allargar el pilot" }));
    const dialog = await screen.findByRole("dialog", { name: "Allargar el pilot" });
    fireEvent.change(within(dialog).getByLabelText(/Nova data de fi/), { target: { value: "2026-11-30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Allarga el pilot" }));
    await flush();
    expect(patch).toHaveBeenCalledWith(`/platform/companies/${detail.id}/status`, { status: "pilot", pilot_ends_at: "2026-11-30" });
  });

  it("convertir en activa demana confirmació", async () => {
    patch.mockResolvedValue({ ...detail, status: { ...detail.status, platform_status: "active" } });
    await showDetail();
    fireEvent.click(screen.getByRole("button", { name: "Convertir en activa" }));
    const confirm = await screen.findByRole("alertdialog", { name: /Convertir Enginyeria Canigó en activa/ });
    fireEvent.click(within(confirm).getByRole("button", { name: "Convertir en activa" }));
    await flush();
    expect(patch).toHaveBeenCalledWith(`/platform/companies/${detail.id}/status`, { status: "active" });
  });

  it("una empresa que no existeix ho diu", async () => {
    params = { companyId: "missing" };
    respond();
    renderInLayout(<CompanyDetailPage />);
    await flush();
    await flush();
    expect(screen.getByText(/ja no hi és/)).toBeTruthy();
  });
});
