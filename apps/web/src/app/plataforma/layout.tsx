"use client";

import { PlatformSessionProvider } from "@/components/platform/PlatformSession";
import { PlatformShell } from "@/components/platform/PlatformShell";

/** Administració Tuvi: sessió i marc propis, separats de l'app client. */
export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  return (
    <PlatformSessionProvider>
      <PlatformShell>{children}</PlatformShell>
    </PlatformSessionProvider>
  );
}
