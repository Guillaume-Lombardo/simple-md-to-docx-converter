import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthProvider } from "../src/auth/context";
import "./globals.css";

export const metadata: Metadata = {
  title: "Markweave",
  description: "Markdown document conversion",
};
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
