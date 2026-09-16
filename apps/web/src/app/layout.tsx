import type { Metadata } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";

import "./globals.css";

export const metadata: Metadata = {
  title: "Licitacions · Perfil d'empresa",
  description:
    "Plataforma per mantenir el perfil acreditable de l'empresa i les seves evidències documentals.",
};

// Tipografia del sistema Industry, servida per next/font (cap <link> extern):
// Barlow per al cos i Barlow Condensed per a títols, rètols, botons i passos.
// Les variables les llegeix `@theme` a globals.css (--font-sans / --font-heading).
const barlow = Barlow({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600"],
  variable: "--font-barlow",
  display: "swap",
});

const barlowCondensed = Barlow_Condensed({
  subsets: ["latin", "latin-ext"],
  weight: ["600", "700"],
  variable: "--font-barlow-condensed",
  display: "swap",
});

// `suppressHydrationWarning` a <html>: algunes extensions del navegador
// (traductors, correctors ortogràfics) hi injecten atributs abans que React
// hidrati, i això provocaria un avís d'hidratació que no és culpa del codi.
// Les variables de tipus de lletra van a <html> perquè `@theme` les resol a
// :root; si anessin a <body>, --font-sans quedaria sense valor.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ca" suppressHydrationWarning className={`${barlow.variable} ${barlowCondensed.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
