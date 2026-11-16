"use client";

import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, Copy, Feather, Loader2, SlidersHorizontal, Square, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NativeSelect, Segmented } from "@/components/ui/segmented";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Approach, HumanizeOptions, Intensity, Mode, Speed, StyleProfile } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { useHumanizer } from "@/stores/humanizer-store";

const FALLBACK_MODES: HumanizeOptions["modes"] = [
  { value: "natural", label: "Natural", hint: "Clear, fluent everyday writing" },
];
const FALLBACK_APPROACHES: HumanizeOptions["approaches"] = [
  { value: "balanced", label: "Balanced", hint: "Better sentences, close to your structure" },
  { value: "restructured", label: "Restructured", hint: "Main point first, sentences reorganised" },
  { value: "concise", label: "Concise", hint: "Tightest version, every point kept" },
  { value: "flowing", label: "Flowing", hint: "Connected, varied rhythm" },
];
const INTENSITIES: { value: Intensity; label: string; title: string }[] = [
  { value: "light", label: "Light", title: "Minimal changes: fix awkward phrasing and grammar" },
  { value: "medium", label: "Medium", title: "Better sentences, less redundancy, smoother transitions" },
  { value: "extensive", label: "Extensive", title: "Rework sentence and paragraph structure for flow" },
];

export function ConfigBar({
  options, profiles, onManageProfiles, canRun,
}: { options: HumanizeOptions | null; profiles: StyleProfile[]; onManageProfiles: () => void; canRun: boolean }) {
  const { settings, setSettings, start, cancel, processing, input } = useHumanizer();
  const running = processing?.status === "running" || processing?.status === "cancelling";
  const modes = options?.modes ?? FALLBACK_MODES;
  const mode = modes.find((m) => m.value === settings.mode) ?? modes[0];
  const profile = profiles.find((p) => p.id === settings.profile_id) ?? null;
  const optionCount = [settings.document_type !== "auto", !!settings.audience.trim(), settings.mode !== "custom" && !!settings.custom_instructions.trim(), !settings.ai_validation]
    .filter(Boolean).length;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="outline" className="h-9 gap-2 bg-paper pr-2 pl-3" aria-label={`Writing mode: ${mode.label}`} disabled={running} />}
          >
            <span className="text-muted-foreground">Mode</span>
            <span className="font-medium">{mode.label}</span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-72">
            <DropdownMenuRadioGroup value={settings.mode} onValueChange={(v) => setSettings({ mode: v as Mode })}>
              {modes.map((m) => (
                <DropdownMenuRadioItem key={m.value} value={m.value} closeOnClick className="items-start py-1.5">
                  <span className="flex flex-col">
                    <span className="font-medium">{m.label}</span>
                    <span className="text-xs text-muted-foreground">{m.hint}</span>
                  </span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="flex h-9 items-center gap-2 rounded-lg border border-border bg-paper px-1.5">
          <span className="pl-1.5 text-sm text-muted-foreground">Intensity</span>
          <Segmented ariaLabel="Intensity" value={settings.intensity} onChange={(v) => !running && setSettings({ intensity: v })}
            options={INTENSITIES.map((i) => ({ value: i.value, label: i.label, title: i.title }))} />
        </div>

        <div className="flex h-9 items-center gap-2 rounded-lg border border-border bg-paper px-1.5">
          <span className="pl-1.5 text-sm text-muted-foreground">Speed</span>
          <Segmented ariaLabel="Speed" value={settings.speed ?? "fast"} onChange={(v) => !running && setSettings({ speed: v })}
            options={[
              { value: "fast" as Speed, label: "Fast", title: `Writes directly, without a long reasoning phase${options?.speed.fast_model ? ` (${options.speed.fast_model})` : ""}. Several times quicker; best for long documents.` },
              { value: "thorough" as Speed, label: "Thorough", title: `Lets ${options?.speed.thorough_model ?? "the model"} reason before writing. Can be slightly more careful, but much slower: minutes per section.` },
            ]} />
        </div>

        <VariationsMenu options={options} disabled={running} />

        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" className="h-9 max-w-[220px] gap-2 bg-paper pr-2 pl-3" disabled={running} />}>
            <UserRound className="size-3.5 text-muted-foreground" />
            <span className="truncate">{profile ? profile.name : "No style profile"}</span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-64">
            <DropdownMenuRadioGroup value={settings.profile_id ?? ""} onValueChange={(v) => setSettings({ profile_id: (v as string) || null })}>
              <DropdownMenuRadioItem value="" closeOnClick>No style profile</DropdownMenuRadioItem>
              {profiles.map((p) => (
                <DropdownMenuRadioItem key={p.id} value={p.id} closeOnClick>
                  <span className="truncate">{p.name}</span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onManageProfiles}>{profiles.length ? "Manage writing profiles…" : "Create a writing profile…"}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Popover>
          <PopoverTrigger render={<Button variant="ghost" className="h-9 gap-1.5" disabled={running} />}>
            <SlidersHorizontal className="size-3.5" /> Options
            {optionCount > 0 && <span className="rounded-full bg-ink/10 px-1.5 text-[11px] font-medium text-ink">{optionCount}</span>}
          </PopoverTrigger>
          <PopoverContent className="w-80 gap-3 p-4" align="start">
            <label className="grid gap-1 text-[13px]">
              <span className="font-medium">Document type</span>
              <NativeSelect value={settings.document_type} onChange={(e) => setSettings({ document_type: e.target.value })}>
                {(options?.document_types ?? ["auto"]).map((d) => (
                  <option key={d} value={d}>{d === "auto" ? "Detect automatically" : d.replace(/^\w/, (c) => c.toUpperCase())}</option>
                ))}
              </NativeSelect>
            </label>
            <label className="grid gap-1 text-[13px]">
              <span className="font-medium">Target audience</span>
              <Input value={settings.audience} maxLength={200} placeholder="e.g. first-year students, my manager"
                onChange={(e) => setSettings({ audience: e.target.value })} />
            </label>
            {settings.mode !== "custom" && (
              <label className="grid gap-1 text-[13px]">
                <span className="font-medium">Extra instructions</span>
                <Textarea value={settings.custom_instructions} maxLength={2000} rows={3} className="min-h-0 text-[13px]"
                  placeholder="e.g. keep British spelling; don't use “utilise”"
                  onChange={(e) => setSettings({ custom_instructions: e.target.value })} />
              </label>
            )}
            <label className="flex items-start justify-between gap-3 text-[13px]">
              <span>
                <span className="block font-medium">AI meaning check</span>
                <span className="block text-xs leading-snug text-muted-foreground">
                  A second pass compares each rewritten section with the original. More thorough, but adds one model call per section.
                </span>
              </span>
              <Switch checked={settings.ai_validation} onCheckedChange={(v) => setSettings({ ai_validation: !!v })} aria-label="AI meaning check" />
            </label>
          </PopoverContent>
        </Popover>

        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <Button variant="outline" className="h-9 bg-paper" onClick={cancel} disabled={processing?.status === "cancelling"}>
              {processing?.status === "cancelling" ? <Loader2 className="animate-spin" /> : <Square className="size-3 fill-current" />}
              {processing?.status === "cancelling" ? "Stopping…" : "Stop"}
            </Button>
          ) : (
            <Button className="h-9 gap-2 px-4 text-[14px]" onClick={start} disabled={!canRun || !input.trim()}
              title="Rewrite the whole text (⌘↵)">
              <Feather className="size-4" /> Humanize
            </Button>
          )}
        </div>
      </div>

      <AnimatePresence initial={false}>
        {settings.mode === "custom" && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <Textarea
              aria-label="Describe your preferred writing style"
              value={settings.custom_instructions}
              maxLength={2000}
              rows={2}
              disabled={running}
              placeholder="Describe the style you want, e.g. “warm and direct, short paragraphs, no rhetorical questions, keep it under the original length”."
              className={cn("min-h-0 bg-paper text-[14px]", !settings.custom_instructions.trim() && "border-ink/40")}
              onChange={(e) => setSettings({ custom_instructions: e.target.value })}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function VariationsMenu({ options, disabled }: { options: HumanizeOptions | null; disabled: boolean }) {
  const { settings, setSettings } = useHumanizer();
  const approaches = options?.approaches ?? FALLBACK_APPROACHES;
  const max = options?.max_variations ?? 3;
  const chosen: Approach[] = settings.approaches?.length ? settings.approaches : ["balanced"];
  const toggle = (a: Approach, on: boolean) => {
    const next = on ? [...chosen, a].slice(-max) : chosen.filter((x) => x !== a);
    // Keep the order of the menu so variation 1 is always the first listed approach.
    setSettings({ approaches: approaches.map((x) => x.value).filter((x) => next.includes(x)) as Approach[] });
  };
  const label = chosen.length === 1 ? approaches.find((a) => a.value === chosen[0])?.label ?? "Balanced" : `${chosen.length} variations`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" className="h-9 gap-2 bg-paper pr-2 pl-3" disabled={disabled}
        aria-label={`Rewriting approach: ${label}`} title="Generate up to three different rewrites to compare and combine" />}>
        <Copy className="size-3.5 text-muted-foreground" />
        <span>{label}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-72">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-[11.5px] font-normal leading-snug text-muted-foreground">
            Pick up to {max}. Each one is a separate rewrite with its own approach, so the run takes about that many times longer.
          </DropdownMenuLabel>
          {approaches.map((a) => {
            const on = chosen.includes(a.value);
            return (
              <DropdownMenuCheckboxItem key={a.value} checked={on} closeOnClick={false}
                disabled={(on && chosen.length === 1) || (!on && chosen.length >= max)}
                onCheckedChange={(v) => toggle(a.value, !!v)} className="items-start py-1.5">
                <span className="flex flex-col">
                  <span className="font-medium">{a.label}</span>
                  <span className="text-xs text-muted-foreground">{a.hint}</span>
                </span>
              </DropdownMenuCheckboxItem>
            );
          })}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
