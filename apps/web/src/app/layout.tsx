import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Licitacions · Perfil d'empresa",
  description:
    "Plataforma per mantenir el perfil acreditable de l'empresa i les seves evidències documentals.",
};

// `suppressHydrationWarning` a <html>: algunes extensions del navegador
// (traductors, correctors ortogràfics) hi injecten atributs abans que React
// hidrati, i això provocaria un avís d'hidratació que no és culpa del codi.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ca" suppressHydrationWarning>
      <body className="antialiased">{children}</body>
    </html>
  );
}
