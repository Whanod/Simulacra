import type { Metadata } from "next";
import "./globals.css";

import AppPrivyProvider from "@/components/auth/PrivyProvider";
import AuthGate from "@/components/auth/AuthGate";

export const metadata: Metadata = {
  title: "Simulacra — Simulation Studio",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {/* PrivyProvider lives at the root so AuthGate can mount above
            any route, gated or public. When NEXT_PUBLIC_PRIVY_APP_ID is
            unset the provider is a transparent shim — no SDK in the
            bundle, no overlay, no Authorization header. */}
        <AppPrivyProvider>
          <AuthGate>{children}</AuthGate>
        </AppPrivyProvider>
      </body>
    </html>
  );
}
