"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, Check, ChevronDown, Loader2, RotateCcw, Square, TextCursorInput, Wand2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DiffView } from "@/components/suggestions/diff-view";
import { humanizer, type Flag, type HumanizeOptions, type RunSettings, type SelectionAction, type Tone } from "@/lib/humanizer";
import { cn } from "@/lib/utils";

const PRIMARY: SelectionAction[] = ["improve", "clarity", "simplify", "flow", "shorten"];
const MORE: SelectionAction[] = ["rephrase", "redundancy", "natural", "expand", "professional", "conversational"];
const LABELS: Record<SelectionAction, string> = {
  improve: "Improve writing", natural: "Make more natural", simplify: "Simplify", professional: "More professional",
  conversational: "More conversational", clarity: "Improve clarity", redundancy: "Reduce redundancy", rephrase: "Rephrase",
  expand: "Expand", shorten: "Shorten", flow: "Improve flow", tone: "Change tone", custom: "Custom instruction",
};
const FALLBACK_TONES: { value: Tone; label: string }[] = [
  { value: "formal", label: "Formal" }, { value: "friendly", label: "Friendly" }, { value: "confident", label: "Confident" },
  { value: "neutral", label: "Neutral" }, { value: "persuasive", label: "Persuasive" }, { value: "empathetic", label: "Empathetic" },
  { value: "casual", label: "Casual" },
];

interface Alternative { text: string; flags: Flag[] }

interface Sel { start: number; end: number }
interface Proposal {
  sel: Sel;
  original: string;
  action: SelectionAction;
  tone?: Tone;
  instruction?: string;
  count: number;
  status: "running" | "done" | "error";
  activity?: string;
  alternatives?: Alternative[];
  picked: number;
  explanation?: string | null;
  error?: string;
  errorFlags?: Flag[];
}

/** Expand a caret position to the sentence (or paragraph) around it. */
export function expandSelection(text: string, pos: number, unit: "sentence" | "paragraph"): Sel {
  const paraStart = Math.max(text.lastIndexOf("\n\n", pos - 1) + 2, 0);
  let paraEnd = text.indexOf("\n\n", pos);
  if (paraEnd < 0) paraEnd = text.length;
  const ps = text.lastIndexOf("\n\n", pos - 1) < 0 ? 0 : paraStart;
  if (unit === "paragraph") {
    let s = ps;
    let e = paraEnd;
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    return { start: s, end: e };
  }
  const para = text.slice(ps, paraEnd);
  const re = /[^.!?…]+(?:[.!?…]+["'”’)\]]*\s*|$)/g;
  let m: RegExpExecArray | null;
  const rel = pos - ps;
  while ((m = re.exec(para))) {
    if (!m[0]) break;
    const s = m.index;
    const e = m.index + m[0].length;
    if (rel >= s && rel <= e) {
      let a = ps + s;
      let b = ps + e;
      while (a < b && /\s/.test(text[a])) a++;
      while (b > a && /\s/.test(text[b - 1])) b--;
      return { start: a, end: b };
    }
  }
  return { start: ps, end: paraEnd };
}

export function SelectionEditor({
  text, onCommit, settings, options, readOnly,
}: { text: string; onCommit: (t: string, kind: "typing" | "action") => void; settings: RunSettings; options: HumanizeOptions | null; readOnly?: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [sel, setSel] = useState<Sel>({ start: 0, end: 0 });
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [custom, setCustom] = useState("");
  const [customOpen, setCustomOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const maxSel = options?.limits.max_selection_chars ?? 6000;

  const selected = text.slice(sel.start, sel.end);
  const hasSelection = sel.end > sel.start && /[\p{L}\p{N}]/u.test(selected);
  const words = (selected.match(/[\p{L}\p{N}’'-]+/gu) ?? []).length;

  const readSel = () => {
    const el = ref.current;
    if (el) setSel({ start: el.selectionStart, end: el.selectionEnd });
  };

  const select = (unit: "sentence" | "paragraph") => {
    const el = ref.current;
    if (!el) return;
    const r = expandSelection(text, el.selectionStart, unit);
    el.focus();
    el.setSelectionRange(r.start, r.end);
    setSel(r);
  };

  const run = async (action: SelectionAction, instruction?: string, explicit?: Sel, opts: { tone?: Tone; count?: number } = {}) => {
    const s = explicit ?? { ...sel };
    const original = text.slice(s.start, s.end);
    if (!(s.end > s.start && /[\p{L}\p{N}]/u.test(original))) return;
    if (original.length > maxSel) {
      toast.error(`Select at most ${maxSel.toLocaleString()} characters at a time.`);
      return;
    }
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const count = opts.count ?? 1;
    setProposal({ sel: s, original, action, tone: opts.tone, instruction, count, status: "running", picked: 0 });
    try {
      await humanizer.rewriteSelection(
        {
          text: original, before: text.slice(Math.max(0, s.start - 1500), s.start), after: text.slice(s.end, s.end + 1500), action,
          mode: settings.mode, custom_instruction: instruction || (settings.mode === "custom" ? settings.custom_instructions : undefined),
          profile_id: settings.profile_id, audience: settings.audience || undefined, tone: opts.tone, alternatives: count,
        },
        (event, data) => {
          if (event === "activity")
            setProposal((p) => p && { ...p, activity: data.activity === "thinking" ? "Thinking it through…" : data.activity === "retrying" ? `Retrying: ${data.reason}` : count > 1 ? `Writing ${count} alternatives…` : "Writing…" });
          else if (event === "completed")
            setProposal((p) => p && {
              ...p, status: "done", picked: 0, explanation: data.explanation,
              alternatives: (data.alternatives as Alternative[] | undefined) ?? [{ text: data.proposed, flags: data.flags ?? [] }],
            });
          else if (event === "error") setProposal((p) => p && { ...p, status: "error", error: data.message, errorFlags: data.flags });
          else if (event === "cancelled") setProposal(null);
        },
        ac.signal,
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setProposal((p) => p && { ...p, status: "error", error: (e as Error).message });
    }
  };

  const chosen = proposal?.alternatives?.[proposal.picked];
  const accept = () => {
    if (!proposal || !chosen) return;
    const { sel: s, original } = proposal;
    const proposed = chosen.text;
    let next: string | null = null;
    if (text.slice(s.start, s.end) === original) next = text.slice(0, s.start) + proposed + text.slice(s.end);
    else {
      const i = text.indexOf(original);
      if (i >= 0 && text.indexOf(original, i + 1) < 0) next = text.slice(0, i) + proposed + text.slice(i + original.length);
    }
    if (next === null) {
      toast.error("The selected text changed since this suggestion was made. Select it again and retry.");
      return;
    }
    onCommit(next, "action");
    setProposal(null);
    requestAnimationFrame(() => {
      const el = ref.current;
      if (!el) return;
      const at = next!.indexOf(proposed);
      el.focus();
      if (at >= 0) el.setSelectionRange(at, at + proposed.length);
      readSel();
    });
  };

  const stop = () => {
    abortRef.current?.abort();
    setProposal(null);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className={cn("sticky top-0 z-10 -mx-1 flex min-h-9 flex-wrap items-center gap-1.5 rounded-md bg-paper/95 px-1 py-1 backdrop-blur", proposal && "hidden")}>
        {hasSelection && !proposal ? (
          <>
            <span className="mr-1 text-[12px] text-muted-foreground">{words} word{words === 1 ? "" : "s"} selected</span>
            {PRIMARY.map((a) => (
              <Button key={a} size="sm" variant={a === "improve" ? "default" : "outline"} className={a !== "improve" ? "bg-paper" : ""} onClick={() => run(a)}>
                {a === "improve" && <Wand2 />} {LABELS[a]}
              </Button>
            ))}
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button size="sm" variant="outline" className="bg-paper" />}>Change tone <ChevronDown className="size-3" /></DropdownMenuTrigger>
              <DropdownMenuContent className="w-52">
                {(options?.tones ?? FALLBACK_TONES).map((t) => (
                  <DropdownMenuItem key={t.value} onClick={() => run("tone", undefined, undefined, { tone: t.value })}>{t.label}</DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button size="sm" variant="ghost" />}>More <ChevronDown className="size-3" /></DropdownMenuTrigger>
              <DropdownMenuContent className="w-60">
                {MORE.map((a) => (
                  <DropdownMenuItem key={a} onClick={() => run(a)}>{LABELS[a]}</DropdownMenuItem>
                ))}
                <DropdownMenuItem onClick={() => setCustomOpen(true)}>Custom instruction…</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>Three alternatives…</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="w-52">
                    {(["rephrase", "improve", "clarity", "flow", "shorten"] as SelectionAction[]).map((a) => (
                      <DropdownMenuItem key={a} onClick={() => run(a, undefined, undefined, { count: 3 })}>{LABELS[a]}</DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        ) : !proposal ? (
          <>
            <span className="text-[12px] text-muted-foreground">Select text to rewrite just that part, or</span>
            <Button size="sm" variant="ghost" onClick={() => select("sentence")} disabled={readOnly}><TextCursorInput /> Select sentence</Button>
            <Button size="sm" variant="ghost" onClick={() => select("paragraph")} disabled={readOnly}>Select paragraph</Button>
          </>
        ) : null}
      </div>

      <AnimatePresence>
        {customOpen && hasSelection && !proposal && (
          <motion.form initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!custom.trim()) return;
              setCustomOpen(false);
              run("custom", custom.trim());
            }}>
            <Input autoFocus value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={1000} placeholder="e.g. make this sound warmer, keep it to one sentence" />
            <Button type="submit" size="sm" className="h-8">Rewrite</Button>
            <Button type="button" size="icon-sm" variant="ghost" className="size-8" aria-label="Cancel" onClick={() => setCustomOpen(false)}><X /></Button>
          </motion.form>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {proposal && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            className="hw-card space-y-3 p-4" role="region" aria-label="Suggested rewrite">
            <div className="flex items-center gap-2 text-[12px]">
              <span className="font-hand text-[18px] text-ink">
                {LABELS[proposal.action]}{proposal.tone ? `: ${(options?.tones ?? FALLBACK_TONES).find((t) => t.value === proposal.tone)?.label ?? proposal.tone}` : ""}
              </span>
              {proposal.status === "running" && (
                <span className="flex items-center gap-1.5 text-muted-foreground"><Loader2 className="size-3 animate-spin" />{proposal.activity ?? "Starting…"}</span>
              )}
            </div>
            {proposal.status === "running" && <p className="font-serif text-[15px] leading-relaxed text-foreground/60">{proposal.original}</p>}
            {proposal.status === "done" && chosen && (
              <>
                {(proposal.alternatives?.length ?? 0) > 1 && (
                  <div role="tablist" aria-label="Alternatives" className="flex flex-wrap gap-1">
                    {proposal.alternatives!.map((alt, i) => (
                      <button key={i} type="button" role="tab" aria-selected={i === proposal.picked}
                        onClick={() => setProposal((p) => p && { ...p, picked: i })}
                        className={cn("rounded-full border px-2.5 py-0.5 text-[12px] transition-colors",
                          i === proposal.picked ? "border-ink/40 bg-ink/10 font-medium text-ink" : "border-foreground/10 hover:bg-foreground/[0.05]")}>
                        Option {i + 1}
                        {alt.flags.some((f) => f.severity === "high") && <AlertTriangle className="ml-1 inline size-3 text-warn" />}
                      </button>
                    ))}
                  </div>
                )}
                {chosen.text.trim() === proposal.original.trim() ? (
                  <p className="text-[13px] text-muted-foreground">The model kept this passage as it is; it found nothing to change.</p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <p className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Original</p>
                      <DiffView before={proposal.original} after={chosen.text} mode="before" className="text-[14.5px]" />
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Revised</p>
                      <DiffView before={proposal.original} after={chosen.text} mode="after" className="text-[14.5px]" />
                    </div>
                  </div>
                )}
                {proposal.explanation && chosen.text.trim() !== proposal.original.trim() && (
                  <p className="text-[12.5px] text-muted-foreground">{proposal.explanation}</p>
                )}
              </>
            )}
            {((proposal.status === "done" ? chosen?.flags : proposal.errorFlags) ?? []).length > 0 && (
              <ul className="space-y-1 rounded-md bg-warn-bg/70 p-2 text-[12px]">
                {((proposal.status === "done" ? chosen?.flags : proposal.errorFlags) ?? []).map((f, i) => (
                  <li key={i} className="flex gap-1.5"><AlertTriangle className="mt-0.5 size-3 shrink-0 text-warn" />{f.message}</li>
                ))}
              </ul>
            )}
            {proposal.status === "error" && <p className="flex gap-1.5 text-[13px] text-destructive"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{proposal.error}</p>}
            <div className="flex flex-wrap gap-2">
              {proposal.status === "running" && <Button size="sm" variant="outline" onClick={stop}><Square className="size-3 fill-current" /> Stop</Button>}
              {proposal.status === "done" && chosen && chosen.text.trim() !== proposal.original.trim() && (
                <Button size="sm" onClick={accept}><Check /> Accept{(proposal.alternatives?.length ?? 0) > 1 ? ` option ${proposal.picked + 1}` : ""}</Button>
              )}
              {proposal.status !== "running" && (
                <>
                  <Button size="sm" variant="outline" onClick={() => setProposal(null)}><X /> {proposal.status === "done" ? "Reject" : "Close"}</Button>
                  <Button size="sm" variant="ghost" onClick={() => run(proposal.action, proposal.instruction, proposal.sel, { tone: proposal.tone, count: proposal.count })}>
                    <RotateCcw /> Try again
                  </Button>
                  {proposal.count === 1 && (
                    <Button size="sm" variant="ghost" onClick={() => run(proposal.action, proposal.instruction, proposal.sel, { tone: proposal.tone, count: 3 })}>
                      Show 3 alternatives
                    </Button>
                  )}
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <textarea
        ref={ref}
        aria-label="Revised text (editable)"
        value={text}
        readOnly={readOnly || proposal?.status === "running"}
        onChange={(e) => onCommit(e.target.value, "typing")}
        onSelect={readSel}
        onKeyUp={readSel}
        onMouseUp={readSel}
        spellCheck
        className={cn(
          "field-sizing-content min-h-[420px] w-full resize-none bg-transparent font-serif text-[16px] leading-[1.8] outline-none",
          proposal && "opacity-80",
        )}
      />
    </div>
  );
}
