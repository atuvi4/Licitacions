import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Sense `globals: true`, Testing Library no registra la neteja sola.
afterEach(() => cleanup());
