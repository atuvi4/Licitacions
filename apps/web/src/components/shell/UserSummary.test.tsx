import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "@/lib/types";

import { UserSummary } from "./UserSummary";

const user: CurrentUser = {
  id: "u1",
  email: "demo@licitacions.dev",
  full_name: "Usuari Demo",
  is_active: true,
  last_login_at: null,
  memberships: [],
};

describe("UserSummary", () => {
  it("mostra inicials, nom i rol humanitzat", () => {
    render(<UserSummary user={user} role="bid_manager" logout={async () => undefined} />);
    expect(screen.getByText("UD")).toBeTruthy();
    expect(screen.getByText("Usuari Demo")).toBeTruthy();
    expect(screen.getByText("Responsable de candidatures")).toBeTruthy();
  });

  it("un rol desconegut es mostra tal qual i el botó tanca la sessió", () => {
    const logout = vi.fn(async () => undefined);
    render(<UserSummary user={{ ...user, full_name: null }} role="auditor" logout={logout} />);
    expect(screen.getByText("auditor")).toBeTruthy();
    expect(screen.getByText("demo@licitacions.dev")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Tanca la sessió" }));
    expect(logout).toHaveBeenCalled();
  });
});
