"use client";

import { Tabs, type TabItem } from "@/components/ui";

import { TAB_PARAM, type CatalogueTab } from "@/app/(app)/licitacions/query";

const ITEMS: TabItem[] = [
  { key: "totes", label: "Totes" },
  { key: "revisar", label: "Per revisar" },
];

/**
 * Les dues vistes de Licitacions: el catàleg complet i la vista prioritzada
 * pel pre-fit. L'estat és a la URL (`?vista=`); cap de les dues amaga res a
 * l'altra.
 */
export function CatalogueTabs({ value, className }: { value: CatalogueTab; className?: string }) {
  return <Tabs items={ITEMS} value={value} param={TAB_PARAM} history="push" onChange={() => undefined} className={className} />;
}
