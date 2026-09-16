import type { ReactNode } from "react";

import { ProfileSubnav } from "@/components/shell/ProfileSubnav";

/** Secció Empresa (handoff L4): totes les àrees del perfil comparteixen la subnavegació. */
export default function EmpresaLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <ProfileSubnav />
      {children}
    </>
  );
}
