"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useSession } from "@/components/SessionProvider";

import { AppSidebar } from "./AppSidebar";
import { AppTopbar } from "./AppTopbar";
import { CompanySwitcher } from "./CompanySwitcher";
import { GlobalSearch } from "./GlobalSearch";
import { JobCenter } from "./JobCenter";
import { ProfileSubnav } from "./ProfileSubnav";
import { SidebarNavigation } from "./SidebarNavigation";
import { UserSummary } from "./UserSummary";
import { useShellCounts } from "./counts";

export { ProfileSubnav };

/**
 * El marc invariable (handoff L4, mockup 01): barra lateral amb els quatre
 * destins, capçalera amb empresa activa, cerca, centre de feina i usuari.
 * Cap flux canvia: la sessió, el selector d'empresa, els permisos i la
 * sortida són els de `SessionProvider`.
 */
export function AppFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, membership, companies, companyId, selectCompany, logout } = useSession();
  const [mobileOpen, setMobileOpen] = useState(false);
  const counts = useShellCounts(companyId, pathname);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const footer = (
    <div className="border-t border-white/12 px-3.5 pt-3 pb-4 text-[12.5px] leading-5 text-sidebar-ink/60">
      <p className="truncate text-sidebar-ink/80" title={membership.company.legal_name}>
        {membership.company.legal_name}
      </p>
      {counts.profileLine && (
        <Link href="/empresa" className="block truncate hover:text-white">
          {counts.profileLine} →
        </Link>
      )}
    </div>
  );

  return (
    <div className="flex min-h-screen">
      <AppSidebar mobileOpen={mobileOpen} footer={footer}>
        <SidebarNavigation
          pathname={pathname}
          onNavigate={() => setMobileOpen(false)}
          counts={{
            avui: counts.avui,
            oportunitats: counts.oportunitats,
            candidatures: counts.candidatures,
            perfil: counts.perfil,
          }}
        />
      </AppSidebar>

      {mobileOpen && <div className="fixed inset-0 z-30 bg-ink/40 lg:hidden" onClick={() => setMobileOpen(false)} aria-hidden />}

      <div className="flex min-w-0 flex-1 flex-col">
        <AppTopbar
          mobileOpen={mobileOpen}
          onToggleMenu={() => setMobileOpen((open) => !open)}
          companySwitcher={<CompanySwitcher membership={membership} companies={companies} selectCompany={selectCompany} />}
          search={<GlobalSearch catalogueTotal={counts.catalogue ?? null} />}
          workCenter={<JobCenter companyId={companyId} />}
          user={<UserSummary user={user} role={membership.role} logout={logout} />}
        />
        <main className="flex-1 px-4 py-6 lg:px-8 lg:py-7">
          <div className="mx-auto w-full max-w-[1280px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
