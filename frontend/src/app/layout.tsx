import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Auto Trader Dashboard",
  description: "Manage day trading agents backed by FastAPI.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
