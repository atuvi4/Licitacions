import { redirect } from "next/navigation";

/**
 * L5: «Avui» és la pantalla d'entrada. El dashboard de completitud i
 * caducitats passa a la secció Empresa a L8 (el codi anterior és al commit
 * 86fd38a, `src/app/(app)/dashboard/page.tsx`).
 */
export default function DashboardPage() {
  redirect("/avui");
}
