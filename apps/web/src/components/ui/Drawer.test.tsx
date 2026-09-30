import { fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Drawer } from "./Drawer";

/**
 * El calaix gestiona el focus quan s'obre i quan es tanca, i prou. Que el pare
 * es torni a pintar (perquè algú escriu en un camp de dins) no pot tornar a
 * moure'l: el calaix d'alta d'empresa hi perdia el focus a cada tecla. I en
 * obrir-se, qui mana és el fill que ha demanat el focus amb `autoFocus`.
 */

/** Un pare com els reals: l'estat del formulari viu aquí i `onClose` es crea a cada render. */
function Host({ onClosed, autoFocusName = false }: { onClosed: (renders: number) => void; autoFocusName?: boolean }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [renders, setRenders] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Obre
      </button>
      <button type="button" onClick={() => setRenders((value) => value + 1)}>
        Repinta
      </button>
      <Drawer
        open={open}
        title="Calaix de prova"
        // Identitat nova a cada render, com una funció declarada dins del component.
        onClose={() => {
          onClosed(renders);
          setOpen(false);
        }}
        footer={<button type="button">Desa</button>}
      >
        <label>
          Nom
          <input value={name} onChange={(event) => setName(event.target.value)} autoFocus={autoFocusName} />
        </label>
        <label>
          Correu
          <input />
        </label>
      </Drawer>
    </>
  );
}

function open({ autoFocusName = false, strict = false }: { autoFocusName?: boolean; strict?: boolean } = {}) {
  const onClosed = vi.fn();
  const host = <Host onClosed={onClosed} autoFocusName={autoFocusName} />;
  // El mode estricte és el de `next dev`: munta, desmunta i torna a muntar els efectes.
  render(strict ? <StrictMode>{host}</StrictMode> : host);
  const opener = screen.getByRole("button", { name: "Obre" });
  // Com el clic d'un navegador: el botó es queda el focus i després obre.
  opener.focus();
  fireEvent.click(opener);
  return { opener, onClosed, dialog: screen.getByRole("dialog", { name: "Calaix de prova" }) };
}

describe("Drawer", () => {
  describe.each([
    ["en producció", false],
    ["en mode estricte", true],
  ])("focus en obrir i en tancar, %s", (_, strict) => {
    it("si un fill demana `autoFocus`, el focus és seu i el botó de tancar no l'hi pren", () => {
      const { dialog } = open({ autoFocusName: true, strict });
      expect(document.activeElement).toBe(screen.getByLabelText("Nom"));
      expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Tanca" }));
      expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it("si cap fill no el demana, el rep el primer element del calaix", () => {
      const { dialog } = open({ strict });
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Tanca" }));
      expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it.each([
      ["amb `autoFocus`", true],
      ["sense", false],
    ])("en tancar-se torna el focus a qui l'havia obert (%s)", (_label, autoFocusName) => {
      const { opener } = open({ autoFocusName, strict });
      fireEvent.change(screen.getByLabelText("Nom"), { target: { value: "Anna" } });
      fireEvent.click(screen.getByRole("button", { name: "Tanca" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(opener);
    });
  });

  it("que el pare es torni a pintar no mou el focus ni remunta el calaix", () => {
    const { dialog } = open();
    const name = screen.getByLabelText("Nom") as HTMLInputElement;
    name.focus();

    // Cada canvi repinta el pare i li dona al calaix una `onClose` nova.
    for (const value of ["I", "In", "Ind"]) {
      fireEvent.change(name, { target: { value } });
      expect(document.activeElement).toBe(name);
    }
    fireEvent.click(screen.getByRole("button", { name: "Repinta" }));
    expect(document.activeElement).toBe(name);
    expect(name.value).toBe("Ind");
    // És el mateix node: el calaix no s'ha desmuntat i tornat a muntar.
    expect(screen.getByRole("dialog", { name: "Calaix de prova" })).toBe(dialog);
    expect(screen.getByLabelText("Nom")).toBe(name);
  });

  it("amb `autoFocus`, s'hi escriu tot just obert i el focus no se'n mou", () => {
    open({ autoFocusName: true });
    const name = screen.getByLabelText("Nom") as HTMLInputElement;
    for (const value of ["I", "In", "Ind"]) {
      fireEvent.change(name, { target: { value } });
      expect(document.activeElement).toBe(name);
    }
    expect(name.value).toBe("Ind");
  });

  it("Escape tanca amb l'`onClose` vigent, no amb una d'antiga", () => {
    const { onClosed } = open();
    fireEvent.click(screen.getByRole("button", { name: "Repinta" }));
    fireEvent.click(screen.getByRole("button", { name: "Repinta" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClosed).toHaveBeenCalledTimes(1);
    expect(onClosed).toHaveBeenCalledWith(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("atrapa el focus: Tab des de l'últim torna al primer i Shift+Tab des del primer va a l'últim", () => {
    open({ autoFocusName: true });
    const first = screen.getByRole("button", { name: "Tanca" });
    const last = screen.getByRole("button", { name: "Desa" });
    const middle = screen.getByLabelText("Nom");

    // Al mig no s'hi fica: el navegador passa al camp següent.
    expect(document.activeElement).toBe(middle);
    expect(fireEvent.keyDown(document, { key: "Tab" })).toBe(true);
    expect(fireEvent.keyDown(document, { key: "Tab", shiftKey: true })).toBe(true);
    expect(document.activeElement).toBe(middle);

    last.focus();
    expect(fireEvent.keyDown(document, { key: "Tab" })).toBe(false); // interceptat
    expect(document.activeElement).toBe(first);

    expect(fireEvent.keyDown(document, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(last);
  });
});
