"use client";

import { SessionProvider } from "@/components/SessionProvider";
import { AppFrame } from "@/components/shell/AppFrame";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <AppFrame>{children}</AppFrame>
    </SessionProvider>
  );
}
