import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const replace = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post: (...a: unknown[]) => post(...a) } };
});
const router = { replace, push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams("") }));

import LoginPage from "./page";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const user = { id: "u", email: "x@y.cat", full_name: null, is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: false, memberships: [{ company_id: "c1" }] };

async function signIn() {
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText(/Correu electrònic/), { target: { value: "x@y.cat" } });
  fireEvent.change(screen.getByLabelText(/Contrasenya/), { target: { value: "secret-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Entra" }));
  await flush();
}

describe("On porta el login", () => {
  beforeEach(() => {
    post.mockReset();
    replace.mockReset();
  });

  it("un usuari d'empresa va a l'app", async () => {
    post.mockResolvedValue(user);
    await signIn();
    expect(replace).toHaveBeenCalledWith("/avui");
  });

  it("l'Administració Tuvi va a la seva àrea, no a l'app d'empresa", async () => {
    post.mockResolvedValue({ ...user, is_platform_admin: true, memberships: [] });
    await signIn();
    expect(replace).toHaveBeenCalledWith("/plataforma/empreses");
  });

  it("una contrasenya temporal es canvia primer, sigui qui sigui", async () => {
    post.mockResolvedValue({ ...user, is_platform_admin: true, memberships: [], must_change_password: true });
    await signIn();
    expect(replace).toHaveBeenCalledWith("/compte/contrasenya");
  });
});
