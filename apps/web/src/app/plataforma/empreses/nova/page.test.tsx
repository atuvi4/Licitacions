import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

const get = vi.fn();
const post = vi.fn();
const replace = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});
// Estable, com el de Next: un objecte nou a cada render faria recarregar la sessió en bucle.
const router = { replace, push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

import NewCompanyPage from "./page";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const admin = { id: "u1", email: "ops@tuvi.cat", full_name: "Ops", is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: true, memberships: [] };
const created = {
  company: { id: "c9", legal_name: "Indaleccius SL", trade_name: "Indaleccius", tax_id: "B12345678" },
  owner: { id: "u9", email: "owner@indaleccius.cat", full_name: "Propietària" },
  temporary_password: "Tmp9xYzAbCdEfGhK",
};

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("Alta d'empresa i propietari", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    replace.mockReset();
  });

  it("l'administració dona d'alta i veu la temporal una sola vegada, amb l'avís", async () => {
    get.mockResolvedValue(admin);
    post.mockResolvedValue(created);
    render(<NewCompanyPage />);
    await flush();

    fill(/Nom legal/, "Indaleccius SL");
    fill(/NIF/, "B12345678");
    fill(/Nom del propietari/, "Propietària");
    fill(/Correu del propietari/, "owner@indaleccius.cat");
    fireEvent.click(screen.getByRole("button", { name: "Dona d'alta" }));
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/platform/companies", {
      legal_name: "Indaleccius SL",
      trade_name: null,
      tax_id: "B12345678",
      owner_full_name: "Propietària",
      owner_email: "owner@indaleccius.cat",
    });
    expect(screen.getByRole("heading", { name: "Empresa donada d'alta" })).toBeTruthy();
    const secret = screen.getByRole("region", { name: "Contrasenya temporal" });
    expect(secret.textContent).toContain("Tmp9xYzAbCdEfGhK");
    expect(secret.textContent).toMatch(/Només es mostra ara/);
    expect(secret.textContent).toMatch(/l'haurà de canviar en entrar/);

    // En tornar al formulari, la temporal desapareix de la pantalla.
    fireEvent.click(screen.getByRole("button", { name: "Dona d'alta una altra empresa" }));
    expect(screen.queryByText("Tmp9xYzAbCdEfGhK")).toBeNull();
    expect((screen.getByLabelText(/Nom legal/) as HTMLInputElement).value).toBe("");
  });

  it("un correu o un NIF repetits es diuen i no es mostra cap contrasenya", async () => {
    get.mockResolvedValue(admin);
    post.mockRejectedValue(new ApiError(409, "owner_email_in_use", "Ja hi ha un usuari amb aquest correu."));
    render(<NewCompanyPage />);
    await flush();
    fill(/Nom legal/, "Indaleccius SL");
    fill(/Nom del propietari/, "Propietària");
    fill(/Correu del propietari/, "owner@indaleccius.cat");
    fireEvent.click(screen.getByRole("button", { name: "Dona d'alta" }));
    await flush();

    expect(screen.getByRole("alert").textContent).toBe("Ja hi ha un usuari amb aquest correu.");
    expect(screen.queryByRole("region", { name: "Contrasenya temporal" })).toBeNull();
  });

  it("qui no administra la plataforma no veu el formulari ni pot enviar res", async () => {
    get.mockResolvedValue({ ...admin, is_platform_admin: false, memberships: [{ company_id: "c1" }] });
    render(<NewCompanyPage />);
    await flush();

    expect(screen.getByText(/El teu compte no hi té accés/)).toBeTruthy();
    expect(screen.queryByLabelText(/Nom legal/)).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("amb una contrasenya temporal pendent, primer s'ha de canviar", async () => {
    get.mockResolvedValue({ ...admin, must_change_password: true });
    render(<NewCompanyPage />);
    await flush();
    expect(replace).toHaveBeenCalledWith("/compte/contrasenya");
  });
});
