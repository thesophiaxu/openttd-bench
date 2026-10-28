import type { Metadata } from "next";
import type { ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

import "./globals.css";

export const metadata: Metadata = {
  title: "OpenTTD bench trajectory viewer",
  description: "Browse verifiers traces of OpenTTD bench episodes: reasoning, tool calls, game state and scores.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="h-screen overflow-hidden bg-background text-foreground antialiased">
        <TooltipProvider>{children}</TooltipProvider>
      </body>
    </html>
  );
}
