"use client";

import { useEffect, useRef } from "react";

/**
 * A soft desk-lamp glow that drifts towards the cursor, plus a gentle vignette.
 * Purely decorative: fixed behind all content, no pointer events, still when reduced motion is on.
 */
export function DeskLight() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (!window.matchMedia("(hover: hover)").matches) return;
    let tx = window.innerWidth * 0.5, ty = window.innerHeight * 0.2;
    let x = tx, y = ty, raf = 0;
    const step = () => {
      x += (tx - x) * 0.06;
      y += (ty - y) * 0.06;
      el.style.setProperty("--lx", `${x.toFixed(1)}px`);
      el.style.setProperty("--ly", `${y.toFixed(1)}px`);
      raf = Math.abs(tx - x) + Math.abs(ty - y) > 0.5 ? requestAnimationFrame(step) : 0;
    };
    const onMove = (e: PointerEvent) => {
      tx = e.clientX;
      // The lamp sits above the desk, so the light trails a little above the hand.
      ty = e.clientY - 120;
      if (!raf) raf = requestAnimationFrame(step);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => { window.removeEventListener("pointermove", onMove); cancelAnimationFrame(raf); };
  }, []);

  return <div ref={ref} aria-hidden className="hw-desk-light pointer-events-none fixed inset-0 -z-10" />;
}
