"use client";

import { useMemo } from "react";
import { displayText, wordDiff } from "@/lib/text";
import { cn } from "@/lib/utils";

/** Inline word diff: deletions struck in red, additions in green. */
export function DiffView({ before, after, className, mode = "inline" }: { before: string; after: string; className?: string; mode?: "inline" | "after" | "before" }) {
  const parts = useMemo(() => wordDiff(displayText(before), displayText(after)), [before, after]);
  return (
    <p className={cn("whitespace-pre-wrap font-serif text-[15px] leading-relaxed", className)}>
      {parts.map((p, i) => {
        if (p.added) {
          if (mode === "before") return null;
          return <ins key={i} className="rounded-[2px] bg-add-bg px-px text-add no-underline decoration-add/40">{p.value}</ins>;
        }
        if (p.removed) {
          if (mode === "after") return null;
          return <del key={i} className="rounded-[2px] bg-del-bg px-px text-del decoration-del/60">{p.value}</del>;
        }
        return <span key={i}>{p.value}</span>;
      })}
    </p>
  );
}

export function diffStats(before: string, after: string) {
  const parts = wordDiff(before, after);
  let added = 0;
  let removed = 0;
  for (const p of parts) {
    const n = p.value.trim() ? p.value.trim().split(/\s+/).length : 0;
    if (p.added) added += n;
    if (p.removed) removed += n;
  }
  return { added, removed };
}
