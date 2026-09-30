import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const replace = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: vi.fn() } };
});
// Estable, com el de Next: un objecte nou a cada render faria recarregar la sessió en bucle.
const router = { replace, push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

import { SessionProvider } from "./SessionProvider";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const base = { id: "u1", email: "u@x.cat", full_name: "U", is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: false, memberships: [] };

describe("SessionProvider i l'alta", () => {
  beforeEach(() => {
    get.mockReset();
    replace.mockReset();
  });

  it("amb una contrasenya temporal porta a canviar-la i no demana res més", async () => {
    get.mockResolvedValue({ ...base, must_change_password: true });
    render(<SessionProvider><p>app</p></SessionProvider>);
    await flush();
    expect(replace).toHaveBeenCalledWith("/compte/contrasenya");
    expect(get.mock.calls.map(([path]) => path)).toEqual(["/auth/me"]);
    expect(screen.queryByText("app")).toBeNull();
  });

  it("l'Administració Tuvi no entra a l'app d'empresa: va a la seva àrea i no demana res més", async () => {
    get.mockImplementation((path: string) => Promise.resolve(path === "/auth/me" ? { ...base, is_platform_admin: true } : {}));
    render(<SessionProvider><p>app</p></SessionProvider>);
    await flush();
    expect(replace).toHaveBeenCalledWith("/plataforma/empreses");
    expect(get.mock.calls.map(([path]) => path)).toEqual(["/auth/me"]);
    expect(screen.queryByText("app")).toBeNull();
  });

  it("qui no administra i no té empresa continua veient només «tanca la sessió»", async () => {
    get.mockImplementation((path: string) => Promise.resolve(path === "/auth/me" ? base : {}));
    render(<SessionProvider><p>app</p></SessionProvider>);
    await flush();
    expect(screen.getByText("Aquest usuari no té cap empresa assignada")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Dona d'alta una empresa" })).toBeNull();
    expect(screen.getByRole("button", { name: "Tanca la sessió" })).toBeTruthy();
  });
});
