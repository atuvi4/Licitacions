"use client";

import { Clock3 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import type { AnalysisLimits } from "@/lib/jobs";

/**
 * Centre de feina honest (handoff L4.10): el recompte de peticions actives de
 * l'empresa segons `analysis-limits`. Cap zero abans de saber-ho; si la crida
 * falla no es mostra res. La llista de tasques és el cas C-2: mentre no hi
 * hagi endpoint, l'enllaç porta a Candidatures, d'on surt la feina.
 */
export function JobCenter({ companyId }: { companyId: string }) {
  const pathname = usePathname();
  // undefined = encara no se sap; null = la crida ha fallat.
  const [limits, setLimits] = useState<AnalysisLimits | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    api
      .get<AnalysisLimits>(`/companies/${companyId}/analysis-limits`)
      .then((value) => {
        if (!cancelled) setLimits(value);
      })
      .catch(() => {
        if (!cancelled) setLimits(null);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, pathname]);

  if (!limits) return null;

  if (limits.active_now === 0) {
    return (
      <StatusPill level="neutral" outline>
        Cap tasca en curs
      </StatusPill>
    );
  }

  const label = limits.active_now === 1 ? "1 tasca en curs" : `${limits.active_now} tasques en curs`;
  return (
    <Link
      href="/candidatures"
      title={`${limits.active_now} de ${limits.max_active_per_company} anàlisis simultànies`}
      className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
    >
      <StatusPill level="running">
        <Clock3 size={14} strokeWidth={1.5} aria-hidden />
        {label}
      </StatusPill>
    </Link>
  );
}
