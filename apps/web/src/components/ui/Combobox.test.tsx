import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Combobox } from "./Combobox";

const LINES = [
  { value: "voice_pbx", label: "Centraleta i veu IP" },
  { value: "access_ftth", label: "Accessos FTTH" },
  { value: "mobile_telephony", label: "Telefonia mòbil" },
];

function Host({ initial = "", onChange = vi.fn() }: { initial?: string; onChange?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <label>
        Línia de servei
        <Combobox
          value={value}
          options={LINES}
          placeholder="Tria una línia"
          onChange={(next) => {
            setValue(next);
            onChange(next);
          }}
        />
      </label>
      <output data-testid="value">{value}</output>
    </form>
  );
}

function type(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
}

describe("Combobox", () => {
  it("escrivint «mobil» troba «Telefonia mòbil», sense accents ni majúscules", () => {
    render(<Host />);
    const input = screen.getByRole("combobox", { name: "Línia de servei" });
    type(input, "MOBIL");
    const list = screen.getByRole("listbox");
    expect(within(list).getAllByRole("option").map((option) => option.textContent)).toEqual(["Telefonia mòbil"]);
  });

  it("clicar una opció la tria: el valor és el codi i el camp mostra el rètol", () => {
    const onChange = vi.fn();
    render(<Host onChange={onChange} />);
    const input = screen.getByRole("combobox") as HTMLInputElement;
    type(input, "mobil");
    fireEvent.click(screen.getByRole("option", { name: "Telefonia mòbil" }));
    expect(onChange).toHaveBeenCalledWith("mobile_telephony");
    expect(screen.getByTestId("value").textContent).toBe("mobile_telephony");
    expect(input.value).toBe("Telefonia mòbil");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("es pot triar amb el teclat: fletxes i Retorn, i Retorn no envia el formulari", () => {
    const onChange = vi.fn();
    render(<Host onChange={onChange} />);
    const input = screen.getByRole("combobox");
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe(screen.getByRole("option", { name: "Telefonia mòbil" }).id);
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.getAttribute("aria-activedescendant")).toBe(screen.getByRole("option", { name: "Accessos FTTH" }).id);
    const submitted = fireEvent.keyDown(input, { key: "Enter" });
    expect(submitted).toBe(false); // interceptat: no arriba al formulari
    expect(onChange).toHaveBeenCalledWith("access_ftth");
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });

  it("Escape tanca la llista i torna al rètol de l'opció triada", () => {
    render(<Host initial="voice_pbx" />);
    const input = screen.getByRole("combobox") as HTMLInputElement;
    expect(input.value).toBe("Centraleta i veu IP");
    type(input, "mob");
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input.value).toBe("Centraleta i veu IP");
    expect(screen.getByTestId("value").textContent).toBe("voice_pbx");
  });

  it("en sortir del camp, un text que no és cap opció no canvia res, i buidar-lo vol dir cap", () => {
    const onChange = vi.fn();
    render(<Host initial="voice_pbx" onChange={onChange} />);
    const input = screen.getByRole("combobox") as HTMLInputElement;
    type(input, "inexistent");
    expect(screen.getByRole("listbox").textContent).toContain("Cap opció coincideix");
    fireEvent.blur(input);
    expect(input.value).toBe("Centraleta i veu IP");
    expect(onChange).not.toHaveBeenCalled();

    type(input, "");
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith("");
    expect(screen.getByTestId("value").textContent).toBe("");
  });

  it("un text que és exactament una opció la tria en sortir del camp", () => {
    const onChange = vi.fn();
    render(<Host onChange={onChange} />);
    const input = screen.getByRole("combobox");
    type(input, "telefonia mobil");
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith("mobile_telephony");
  });
});
