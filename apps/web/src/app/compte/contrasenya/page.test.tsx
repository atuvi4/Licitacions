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

import ChangePasswordPage from "./page";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const owner = { id: "u9", email: "owner@indaleccius.cat", full_name: "Propietària", is_active: true, last_login_at: null, must_change_password: true, is_platform_admin: false, memberships: [] };

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("Canvi de contrasenya", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    replace.mockReset();
    get.mockResolvedValue(owner);
  });

  it("amb la temporal: diu per què, i en desar la nova continua cap a l'aplicació", async () => {
    post.mockResolvedValue({ message: "ok" });
    render(<ChangePasswordPage />);
    await flush();

    expect(screen.getByRole("heading", { name: "Tria una contrasenya nova" })).toBeTruthy();
    expect(screen.getByText(/Has entrat amb una contrasenya temporal/)).toBeTruthy();
    // Sense sortida cap a l'aplicació fins que es canvia.
    expect(screen.queryByRole("link", { name: "Torna" })).toBeNull();

    fill(/Contrasenya temporal/, "Tmp9xYzAbCdEfGhK");
    fill(/^Contrasenya nova/, "Indalec-2026-nova");
    fill(/Repeteix/, "Indalec-2026-nova");
    fireEvent.click(screen.getByRole("button", { name: "Desa i continua" }));
    await flush();

    expect(post).toHaveBeenCalledWith("/auth/password", { current_password: "Tmp9xYzAbCdEfGhK", new_password: "Indalec-2026-nova" });
    expect(replace).toHaveBeenCalledWith("/avui");
  });

  it("no envia res si les noves no coincideixen o són massa curtes", async () => {
    render(<ChangePasswordPage />);
    await flush();
    fill(/Contrasenya temporal/, "Tmp9xYzAbCdEfGhK");
    fill(/^Contrasenya nova/, "Indalec-2026-nova");
    fill(/Repeteix/, "Indalec-2026-altra");
    fireEvent.click(screen.getByRole("button", { name: "Desa i continua" }));
    expect(screen.getByRole("alert").textContent).toBe("Les dues contrasenyes noves no coincideixen.");

    fill(/^Contrasenya nova/, "curta1");
    fill(/Repeteix/, "curta1");
    fireEvent.click(screen.getByRole("button", { name: "Desa i continua" }));
    expect(screen.getByRole("alert").textContent).toMatch(/almenys 10 caràcters/);
    expect(post).not.toHaveBeenCalled();
  });

  it("si la temporal no és bona, ho diu el servidor i no es continua", async () => {
    post.mockRejectedValue(new ApiError(401, "not_authenticated", "La contrasenya actual no es correcta."));
    render(<ChangePasswordPage />);
    await flush();
    fill(/Contrasenya temporal/, "equivocada");
    fill(/^Contrasenya nova/, "Indalec-2026-nova");
    fill(/Repeteix/, "Indalec-2026-nova");
    fireEvent.click(screen.getByRole("button", { name: "Desa i continua" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("La contrasenya actual no es correcta.");
    expect(replace).not.toHaveBeenCalledWith("/avui");
  });

  it("sense canvi pendent és un canvi normal, amb sortida", async () => {
    get.mockResolvedValue({ ...owner, must_change_password: false });
    render(<ChangePasswordPage />);
    await flush();
    expect(screen.getByRole("heading", { name: "Canvia la contrasenya" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Torna" }).getAttribute("href")).toBe("/avui");
  });
});
