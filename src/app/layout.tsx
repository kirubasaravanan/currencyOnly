import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "currencyOnly — FX Paper Trading Engine",
  description: "Hybrid-gate FX paper trading engine (V109 + confluence scoring), 17 pairs, commission-aware.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
