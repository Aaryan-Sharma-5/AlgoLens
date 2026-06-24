import type { Metadata } from "next";
import { Inter, Silkscreen, Geist_Mono } from "next/font/google";
import "./globals.css";

// Design system (CLAUDE.md): Silkscreen for the wordmark/headings, Inter for body.
// Geist_Mono is kept for the small monospace readouts (pointer values, cells).
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const silkscreen = Silkscreen({
  variable: "--font-silkscreen",
  subsets: ["latin"],
  weight: ["400", "700"], // not a variable font — weights must be explicit
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AlgoLens — Not a linter. A pedagogue.",
  description:
    "Deterministic AST contract verification with live execution-trace animation and Socratic feedback for algorithmic patterns.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${silkscreen.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
