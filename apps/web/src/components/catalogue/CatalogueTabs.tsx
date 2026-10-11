"use client";

import { Tabs, type TabItem } from "@/components/ui";

import { TAB_PARAM, type CatalogueTab } from "@/app/(app)/licitacions/query";

// «Radar · prioritzades» (addendum 3B-2, D2): són les licitacions que el Radar prioritza amb el pre-fit, no una cua de revisió.
const ITEMS: TabItem[] = [
  { key: "totes", label: "Totes" },
  { key: "revisar", label: "Radar · prioritzades" },
];

/**
 * Les dues vistes de Licitacions: el catàleg complet i la vista prioritzada
 * pel pre-fit. L'estat és a la URL (`?vista=`); cap de les dues amaga res a
 * l'altra.
 */
export function CatalogueTabs({ value, className }: { value: CatalogueTab; className?: string }) {
  return <Tabs items={ITEMS} value={value} param={TAB_PARAM} history="push" onChange={() => undefined} className={className} />;
}
