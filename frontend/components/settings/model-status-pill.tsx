"use client";

import Link from "next/link";
import { Cpu } from "lucide-react";
import { useSystem } from "@/hooks/use-system";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function ModelStatusPill() {
  const { system, error } = useSystem();
  let tone = "bg-muted-foreground/40";
  let text = "Connecting…";
  let detail = "Checking the local backend and Ollama.";
  if (error) {
    tone = "bg-destructive";
    text = "Backend offline";
    detail = error;
  } else if (system) {
    const m = system.settings.ollama.model;
    if (!system.ollama.available) {
      tone = "bg-warn";
      text = "Ollama not running";
      detail = system.ollama.error ?? "Start the Ollama app to enable AI features. Rule-based analysis still works.";
    } else if (!m) {
      tone = "bg-warn";
      text = "No model selected";
      detail = "Choose an installed model in Settings → Local AI.";
    } else {
      const loaded = system.ollama.loaded.some((l) => l.name === m);
      tone = "bg-add";
      text = m;
      detail = `Local model via Ollama ${system.ollama.version ?? ""}${loaded ? " · loaded in memory" : " · loads on first use"}`;
    }
  }
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/settings"
            className="inline-flex items-center gap-2 rounded-full border bg-card px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          />
        }
      >
        <span className={cn("size-1.5 rounded-full", tone)} aria-hidden />
        <Cpu className="size-3" />
        <span className="max-w-40 truncate font-mono">{text}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-72">
        {detail}
      </TooltipContent>
    </Tooltip>
  );
}
