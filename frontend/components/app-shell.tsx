"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Feather, LayoutGrid, Library, Settings2 } from "lucide-react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { ModelStatusPill } from "@/components/settings/model-status-pill";
import { BACK } from "@/components/motion/transitions";

const NAV = [
  { href: "/", label: "My documents", icon: LayoutGrid },
  { href: "/humanize", label: "Humanizer", icon: Feather },
  { href: "/corpus", label: "Compare sources", icon: Library },
  { href: "/settings", label: "Settings", icon: Settings2 },
];

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("relative inline-flex items-baseline font-display text-[23px] leading-none tracking-tight", className)}>
      Human
      <span className="relative ml-0.5 font-hand text-[27px] font-semibold text-ink -rotate-3">
        write
        <svg viewBox="0 0 60 8" aria-hidden className="absolute -bottom-1.5 left-0 h-2 w-full text-ink/70" fill="none" preserveAspectRatio="none">
          <path d="M2 5.5C14 3 30 2.6 44 3.6 50 4 54 4.6 58 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        </svg>
      </span>
    </span>
  );
}

export function TopBar({ children }: { children?: React.ReactNode }) {
  const path = usePathname();
  return (
    <header style={{ viewTransitionName: "app-header" }} className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b border-dashed border-foreground/[0.12] bg-[oklch(0.962_0.012_80/0.72)] px-4 backdrop-blur-md sm:gap-8 sm:px-6">
      <Link href="/" transitionTypes={BACK} className="text-sm" aria-label="HumanWrite home">
        <Logo />
      </Link>
      {children ?? (
        <nav className="flex items-center gap-1 text-sm" aria-label="Main">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "relative inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:text-foreground",
                  active && "text-foreground",
                )}
              >
                <n.icon className="size-4 sm:size-3.5" />
                <span className="sr-only sm:not-sr-only">{n.label}</span>
                {active && (
                  <motion.svg layoutId="nav-ink" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden fill="none"
                    className="absolute inset-x-1.5 -bottom-1 h-2 text-ink" transition={{ type: "spring", stiffness: 380, damping: 30 }}>
                    <path d="M2 5C22 2.6 48 2.2 70 3.4S92 5.2 98 3.6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                  </motion.svg>
                )}
              </Link>
            );
          })}
        </nav>
      )}
      <div className="ml-auto hidden items-center gap-2 md:flex">
        <ModelStatusPill />
      </div>
    </header>
  );
}

export function PageShell({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <main className={cn("mx-auto w-full max-w-6xl flex-1 px-6 py-8", className)}>{children}</main>
    </div>
  );
}
