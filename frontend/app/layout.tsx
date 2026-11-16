import type { Metadata } from "next";
import { Caveat, Geist, Geist_Mono, Instrument_Serif, Source_Serif_4 } from "next/font/google";
import { DeskLight } from "@/components/human/desk";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

// next/font downloads these at build time and serves them locally — no runtime requests to Google.
const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const sourceSerif = Source_Serif_4({ variable: "--font-source-serif", subsets: ["latin"], style: ["normal", "italic"] });
const caveat = Caveat({ variable: "--font-caveat", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const instrumentSerif = Instrument_Serif({ variable: "--font-instrument-serif", subsets: ["latin"], weight: "400", style: ["normal", "italic"] });

export const metadata: Metadata = {
  title: "HumanWrite",
  description: "Local-first document analysis, writing improvement and DOCX editing.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${sourceSerif.variable} ${instrumentSerif.variable} ${caveat.variable} h-full antialiased`}>
      <body className="hw-desk min-h-full text-foreground">
        <DeskLight />
        <TooltipProvider delay={300}>{children}</TooltipProvider>
        <Toaster position="bottom-right" theme="light" />
      </body>
    </html>
  );
}
