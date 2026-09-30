"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { PageSkeleton } from "@/components/ui";
import { PLATFORM_HOME } from "@/lib/platform";

/** L'alta ara és un calaix sobre el llistat: aquesta adreça hi porta obert. */
export default function NewCompanyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace(`${PLATFORM_HOME}?nova=1`);
  }, [router]);
  return <PageSkeleton variant="table" title="Empreses" context="obrint l'alta…" />;
}
