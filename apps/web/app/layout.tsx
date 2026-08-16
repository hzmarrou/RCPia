import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "RCPia",
  description:
    "Provisional RCP preparation support for urological oncology cases. All outputs require human review.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="banner">
          RCPia does not replace a human RCP. All generated outputs are provisional.
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
