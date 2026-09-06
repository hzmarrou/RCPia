import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-serif/latin-400.css";
import "@fontsource/ibm-plex-serif/latin-400-italic.css";
import "@fontsource/ibm-plex-serif/latin-600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "RCPia",
  description:
    "Provisional RCP preparation support for urological oncology cases. All outputs require human review.",
  // Invitation links carry a token; never hand it to the next site in a Referer header.
  referrer: "no-referrer",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="fr">
      <body>
        <header className="banner">
          RCPia ne remplace pas la RCP humaine. Toutes les sorties générées sont provisoires.
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
