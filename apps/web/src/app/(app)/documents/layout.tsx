import type { ReactNode } from "react";

import { ProfileSubnav } from "@/components/shell/ProfileSubnav";

/** La biblioteca de documents forma part de la secció Empresa (handoff L4). */
export default function DocumentsLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <ProfileSubnav />
      {children}
    </>
  );
}
