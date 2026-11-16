"use client";

import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * Marks a person makes on paper: pen strokes that draw themselves, handwriting that writes itself
 * out, rubber stamps and sticky notes. Used sparingly so the interface feels handled, not printed.
 */

type Variant = "underline" | "double" | "strike" | "circle" | "caret" | "tick" | "arrow" | "squiggle";

const PATHS: Record<Variant, { viewBox: string; d: string[]; stretch?: boolean }> = {
  underline: { viewBox: "0 0 300 14", stretch: true, d: ["M3 9.5C46 6 96 5.2 146 6.4S240 9.6 297 5.2"] },
  double: { viewBox: "0 0 300 18", stretch: true, d: ["M3 7C60 4.5 140 4 200 5.4S270 7.4 297 5.8", "M14 13.6C80 11 170 11.4 290 12.4"] },
  strike: { viewBox: "0 0 300 20", stretch: true, d: ["M2 12.5C50 10 110 9.4 170 9.8S262 8.4 298 6.6", "M8 14.2C90 12.8 190 11.6 292 9.4"] },
  circle: {
    viewBox: "0 0 200 60", stretch: true,
    d: ["M64 7.5C26 8.5 4.5 19 6 31.5 7.8 47 52 55.5 108 53.5 163 51.5 196 41 194 25 192 9.5 147 3.5 98 4.8 74 5.4 55 8.2 44 11.5"],
  },
  caret: { viewBox: "0 0 24 24", d: ["M2.5 22.5C5.5 15.5 8.7 9 12 2.2 15 9.4 18.2 16 21.8 22.6"] },
  tick: { viewBox: "0 0 28 24", d: ["M2.5 13.5C5.4 15.4 8 18 10.2 21.4 14.5 13.4 19.6 7 25.5 2.4"] },
  arrow: { viewBox: "0 0 120 50", d: ["M4 44C26 14 62 5.5 104 13.5", "M90.5 4.5C96 8.3 100.6 11 105.4 13.6 100.6 17.2 96.6 21 93 26"] },
  squiggle: { viewBox: "0 0 300 14", stretch: true, d: ["M2 8C12 2 20 2 28 8S44 14 52 8 68 2 76 8 92 14 100 8 116 2 124 8 140 14 148 8 164 2 172 8 188 14 196 8 212 2 220 8 236 14 244 8 260 2 268 8 284 14 298 6"] },
};

export function Scribble({
  variant, className, delay = 0, duration = 0.6, strokeWidth = 2.4, show = true, inView = false,
}: {
  variant: Variant; className?: string; delay?: number; duration?: number; strokeWidth?: number; show?: boolean; inView?: boolean;
}) {
  const reduced = useReducedMotion();
  const p = PATHS[variant];
  const per = duration / p.d.length;
  return (
    <svg viewBox={p.viewBox} preserveAspectRatio={p.stretch ? "none" : "xMidYMid meet"} aria-hidden className={cn("pointer-events-none overflow-visible", className)} fill="none">
      {p.d.map((d, i) => {
        const target = { pathLength: show ? 1 : 0, opacity: show ? 1 : 0 };
        const transition = { pathLength: { duration: reduced ? 0 : per, delay: reduced ? 0 : delay + i * per * 0.9, ease: [0.55, 0.1, 0.3, 1] as const }, opacity: { duration: 0.01, delay: reduced ? 0 : delay + i * per * 0.9 } };
        return (
          <motion.path
            key={i}
            d={d}
            stroke="currentColor"
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            initial={{ pathLength: 0, opacity: 0 }}
            {...(inView ? { whileInView: target, viewport: { once: true, margin: "-40px" } } : { animate: target })}
            transition={transition}
          />
        );
      })}
    </svg>
  );
}

/** Handwritten text that writes itself out from left to right. */
export function Handwrite({
  children, className, delay = 0, speed = 0.045, as = "span",
}: { children: string; className?: string; delay?: number; speed?: number; as?: "span" | "p" }) {
  const reduced = useReducedMotion();
  const Comp = as === "p" ? motion.p : motion.span;
  return (
    <Comp
      className={cn("font-hand inline-block", className)}
      initial={{ clipPath: "inset(-20% 100% -20% -4%)" }}
      animate={{ clipPath: "inset(-20% -12% -20% -4%)" }}
      transition={reduced ? { duration: 0 } : { duration: Math.min(1.8, Math.max(0.35, children.length * speed)), delay, ease: [0.45, 0.05, 0.4, 1] }}
    >
      {children}
    </Comp>
  );
}

const STAMP_TONES = {
  ink: "text-ink border-ink",
  green: "text-[oklch(0.45_0.11_155)] border-[oklch(0.45_0.11_155)]",
  pencil: "text-pencil border-pencil",
  amber: "text-[oklch(0.55_0.13_65)] border-[oklch(0.55_0.13_65)]",
} as const;

/** A rubber stamp, pressed onto the page with a little thud. */
export function Stamp({
  children, tone = "ink", className, delay = 0, rotate = -7,
}: { children: React.ReactNode; tone?: keyof typeof STAMP_TONES; className?: string; delay?: number; rotate?: number }) {
  const reduced = useReducedMotion();
  return (
    <motion.span
      className={cn(
        "hw-stamp inline-flex items-center gap-1.5 rounded-[5px] border-[2.5px] px-2 py-0.5 text-[11px] font-bold tracking-[0.16em] uppercase outline-[1.5px] outline-offset-2 outline-current/40 outline",
        STAMP_TONES[tone], className,
      )}
      initial={{ scale: 1.9, opacity: 0, rotate: rotate - 10 }}
      animate={{ scale: 1, opacity: 0.88, rotate }}
      transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 520, damping: 22, mass: 0.9, delay }}
    >
      {children}
    </motion.span>
  );
}

/** A sticky note with a strip of tape. */
export function Sticky({
  children, className, rotate = 2.5, tape = true,
}: { children: React.ReactNode; className?: string; rotate?: number; tape?: boolean }) {
  return (
    <div className={cn("hw-sticky relative px-3 pt-3 pb-2.5", className)} style={{ rotate: `${rotate}deg` }}>
      {tape && <span aria-hidden className="hw-tape absolute -top-2 left-1/2 h-4 w-12 -translate-x-1/2 -rotate-3" />}
      {children}
    </div>
  );
}

/** A hand-drawn progress line: ink flows along a slightly wobbly stroke. */
export function InkProgress({ value, className, tone = "text-ink" }: { value: number; className?: string; tone?: string }) {
  const d = "M2 7C40 4.6 80 8.6 120 6.2S200 4.4 240 7 290 6.4 298 5.6";
  return (
    <svg viewBox="0 0 300 12" preserveAspectRatio="none" className={cn("h-3 w-full overflow-visible", className)} aria-hidden fill="none">
      <path d={d} stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" vectorEffect="non-scaling-stroke" className="text-foreground/[0.09]" />
      <motion.path d={d} stroke="currentColor" strokeWidth={3} strokeLinecap="round" vectorEffect="non-scaling-stroke" className={tone}
        initial={false} animate={{ pathLength: Math.max(0.001, Math.min(1, value)), opacity: value > 0.005 ? 1 : 0 }} transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }} />
    </svg>
  );
}
