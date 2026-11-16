"use client";

import { motion } from "motion/react";
import { useId } from "react";
import { cn } from "@/lib/utils";

/** Single-select segmented control (keyboard accessible radio group). */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "sm",
  className,
  ariaLabel,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode; title?: string }[];
  size?: "xs" | "sm";
  className?: string;
  ariaLabel?: string;
}) {
  const id = useId();
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn("inline-flex rounded-lg bg-muted p-0.5", className)}
      onKeyDown={(e) => {
        const idx = options.findIndex((o) => o.value === value);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          onChange(options[(idx + 1) % options.length].value);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          onChange(options[(idx - 1 + options.length) % options.length].value);
        }
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cn(
              "relative rounded-md font-medium text-muted-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              size === "xs" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
              active && "text-foreground",
            )}
          >
            {active && (
              <motion.span
                layoutId={`seg-${id}`}
                className="absolute inset-0 rounded-md bg-card shadow-sm ring-1 ring-border"
                transition={{ type: "spring", duration: 0.3, bounce: 0.1 }}
              />
            )}
            <span className="relative">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function NativeSelect({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-8 rounded-lg border border-input bg-card px-2 pr-7 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/40 disabled:opacity-50",
        "appearance-none bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%23667%22 stroke-width=%222%22><path d=%22m6 9 6 6 6-6%22/></svg>')] bg-[length:12px] bg-[right_8px_center] bg-no-repeat",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}
