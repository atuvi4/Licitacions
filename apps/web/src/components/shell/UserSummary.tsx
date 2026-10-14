import { LogOut } from "lucide-react";

import { roleLabel } from "@/lib/labels";
import type { CurrentUser } from "@/lib/types";

interface UserSummaryProps {
  user: CurrentUser;
  /** Valor cru de `membership.role`; es mostra humanitzat amb `ROLE_LABELS`. */
  role: string;
  logout: () => Promise<void>;
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

/** Usuari a la capçalera (handoff L4): avatar quadrat de 30 px amb inicials, nom, rol i sortida. */
export function UserSummary({ user, role, logout }: UserSummaryProps) {
  const name = user.full_name ?? user.email;
  return (
    <div className="flex items-center gap-2.5" title={name}>
      <span
        aria-hidden
        className="grid h-[30px] w-[30px] shrink-0 place-items-center border border-brand bg-brand-soft font-heading text-[12px] font-semibold text-accent-800"
      >
        {initials(name)}
      </span>
      {/* Per sota de xl el nom no hi cap al costat de la cerca (auditoria UI P1-03): es diu igualment als lectors de pantalla. */}
      <span className="sr-only xl:hidden">
        {name}, {roleLabel(role)}
      </span>
      <span className="hidden min-w-0 xl:block">
        <span className="block max-w-44 truncate text-[13px] leading-tight font-medium text-ink" title={user.email}>
          {name}
        </span>
        <span className="block max-w-44 truncate text-[12px] leading-tight text-muted">{roleLabel(role)}</span>
      </span>
      <button
        type="button"
        onClick={() => void logout()}
        className="flex h-8 w-8 shrink-0 items-center justify-center text-muted transition-colors duration-150 hover:bg-subtle hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        aria-label="Tanca la sessió"
        title="Tanca la sessió"
      >
        <LogOut size={16} strokeWidth={1.5} aria-hidden />
      </button>
    </div>
  );
}
