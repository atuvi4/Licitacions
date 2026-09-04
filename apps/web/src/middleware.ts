import { NextResponse, type NextRequest } from "next/server";

/**
 * Barrera de primera linia: si no hi ha cookie de sessio, no s'entra a
 * l'aplicacio. La comprovacio real (signatura, caducitat, pertinenca a
 * l'empresa) la fa sempre el backend; aixo nomes evita pantalles buides.
 */
const SESSION_COOKIE = "licitacions_session";
const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (PUBLIC_PATHS.includes(pathname)) {
    if (hasSession) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return NextResponse.next();
  }

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    if (pathname !== "/") {
      loginUrl.searchParams.set("next", pathname);
    }
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // El proxy /bff i els assets no passen pel middleware.
  matcher: ["/((?!bff|_next/static|_next/image|favicon.ico).*)"],
};
