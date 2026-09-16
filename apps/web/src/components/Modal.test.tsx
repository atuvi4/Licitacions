import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Modal } from "./Modal";

describe("Modal", () => {
  it("titula amb un encapçalament i es tanca amb Escape", () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Ens presentem?" onClose={onClose}>
        <p>Resum</p>
      </Modal>,
    );
    expect(screen.getByRole("dialog", { name: "Ens presentem?" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Ens presentem?" })).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
