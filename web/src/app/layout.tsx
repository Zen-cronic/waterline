import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Waterline — briefing for the lakes that don't have one",
  description: "A live, provenance-first flight briefing for curated station-less Canadian water destinations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
