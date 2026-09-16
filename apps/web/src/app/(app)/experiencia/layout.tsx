import type { ReactNode } from "react";

import { ProfileSubnav } from "@/components/shell/ProfileSubnav";

/** Experiència forma part de la secció Empresa (handoff L4). */
export default function ExperienciaLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <ProfileSubnav />
      {children}
    </>
  );
}
