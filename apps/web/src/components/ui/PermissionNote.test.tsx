import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PermissionNote } from "./PermissionNote";

describe("PermissionNote", () => {
  it("manté el botó visible i diu qui pot fer-ho", () => {
    render(
      <PermissionNote
        who="el responsable de candidatures"
        what="signar la decisió"
        action={<a href="#demana">Demana-ho</a>}
      >
        <button type="button" disabled>
          Presentar-se · GO
        </button>
      </PermissionNote>,
    );
    const button = screen.getByRole("button", { name: "Presentar-se · GO" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("Només el responsable de candidatures pot signar la decisió.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Demana-ho" })).toBeTruthy();
  });
});
