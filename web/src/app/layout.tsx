import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Waterline — briefing for the lakes that don't have one",
  description: "A live flight briefing for the 446 Canadian seaplane bases with no identifier, no station.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
