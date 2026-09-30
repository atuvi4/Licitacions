import { redirect } from "next/navigation";

import { PLATFORM_HOME } from "@/lib/platform";

/** De moment, la consola comença pel llistat d'empreses (el dashboard vindrà després). */
export default function PlatformIndex() {
  redirect(PLATFORM_HOME);
}
