import { Building2, ChevronDown } from "lucide-react";

import { cn } from "@/components/ui";
import type { Membership } from "@/lib/types";

interface CompanySwitcherProps {
  membership: Membership;
  companies: Membership[];
  selectCompany: (companyId: string) => void;
}

const BOX = "flex h-9 min-w-0 items-center gap-2 border border-neutral-400 px-2.5 text-[13.5px] font-medium text-ink";

/**
 * Empresa activa, sempre visible (handoff L4.10). Amb una sola empresa és text
 * pla dins la mateixa caixa; amb més d'una, un selector que crida
 * `selectCompany` (i deixa la clau `licitacions.activeCompany` a la sessió).
 */
export function CompanySwitcher({ membership, companies, selectCompany }: CompanySwitcherProps) {
  const name = membership.company.legal_name;

  if (companies.length <= 1) {
    return (
      <div className={BOX} title={membership.company.trade_name ?? undefined}>
        <Building2 size={16} strokeWidth={1.5} aria-hidden className="shrink-0 text-muted" />
        <span className="truncate">{name}</span>
      </div>
    );
  }

  return (
    <label className={cn(BOX, "relative pr-8")}>
      <Building2 size={16} strokeWidth={1.5} aria-hidden className="shrink-0 text-muted" />
      <select
        value={membership.company_id}
        onChange={(event) => selectCompany(event.target.value)}
        aria-label="Empresa activa"
        className="max-w-64 min-w-0 appearance-none truncate bg-transparent font-medium text-ink outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      >
        {companies.map((item) => (
          <option key={item.company_id} value={item.company_id}>
            {item.company.legal_name}
          </option>
        ))}
      </select>
      <ChevronDown size={14} strokeWidth={1.5} aria-hidden className="pointer-events-none absolute right-2.5 text-muted" />
    </label>
  );
}
