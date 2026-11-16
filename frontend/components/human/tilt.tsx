"use client";

import { useEffect, useRef, useState } from "react";
import { motion, useMotionTemplate, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { cn } from "@/lib/utils";

const SPRING = { stiffness: 170, damping: 18, mass: 0.6 };

/**
 * Real 3D: the object turns towards the pointer in perspective, children at different depths
 * (see <Depth>) parallax against each other, and a soft highlight slides across the surface the
 * way lamp light does on paper. Inert on touch screens and with reduced motion.
 */
export function Tilt({
  children, className, innerClassName, max = 8, perspective = 1100, glare = true, baseX = 0, baseY = 0, hoverLift = 0,
  active, style,
}: {
  children: React.ReactNode; className?: string; innerClassName?: string; max?: number; perspective?: number;
  glare?: boolean; baseX?: number; baseY?: number; hoverLift?: number; active?: boolean; style?: React.CSSProperties;
}) {
  const reduced = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const [fine, setFine] = useState(false);
  useEffect(() => { setFine(window.matchMedia("(hover: hover) and (pointer: fine)").matches); }, []); // eslint-disable-line react-hooks/set-state-in-effect
  const on = fine && !reduced;

  const px = useMotionValue(0.5);
  const py = useMotionValue(0.5);
  const hover = useMotionValue(0);
  const rx = useSpring(useTransform(py, [0, 1], [baseX + max, baseX - max]), SPRING);
  const ry = useSpring(useTransform(px, [0, 1], [baseY - max, baseY + max]), SPRING);
  const z = useSpring(useTransform(hover, [0, 1], [0, hoverLift]), SPRING);
  const gx = useTransform(px, (v) => `${v * 100}%`);
  const gy = useTransform(py, (v) => `${v * 100}%`);
  const glareOpacity = useSpring(hover, { stiffness: 120, damping: 20 });
  const glareBg = useMotionTemplate`radial-gradient(520px circle at ${gx} ${gy}, oklch(1 0 0 / 0.55), transparent 55%)`;

  useEffect(() => {
    if (active !== undefined) hover.set(active ? 1 : 0);
  }, [active, hover]);

  const move = (e: React.PointerEvent) => {
    if (!on || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width);
    py.set((e.clientY - r.top) / r.height);
    hover.set(1);
  };
  const leave = () => { px.set(0.5); py.set(0.5); if (active === undefined) hover.set(0); };

  return (
    <div ref={ref} className={className} style={{ perspective, ...style }} onPointerMove={move} onPointerLeave={leave}>
      <motion.div
        className={cn("hw-3d relative h-full", innerClassName)}
        style={on ? { rotateX: rx, rotateY: ry, z } : { rotateX: baseX, rotateY: baseY }}
      >
        {children}
        {glare && on && (
          <motion.div aria-hidden className="pointer-events-none absolute inset-0 rounded-[inherit] mix-blend-soft-light" style={{ background: glareBg, opacity: glareOpacity, transform: "translateZ(1px)" }} />
        )}
      </motion.div>
    </div>
  );
}

/** Places a child at a depth inside a <Tilt>, so layers move against each other. */
export function Depth({ z, children, className, style }: { z: number; children?: React.ReactNode; className?: string; style?: React.CSSProperties }) {
  return <div className={cn("hw-3d", className)} style={{ transform: `translateZ(${z}px)`, ...style }}>{children}</div>;
}
