"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, ClipboardPaste, Feather, FileUp, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { humanizer, type HumanizeOptions } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { useHumanizer } from "@/stores/humanizer-store";

export function countWords(t: string): number {
  return (t.match(/[\p{L}\p{N}’'-]+/gu) ?? []).length;
}

export function InputPanel({ options, disabled, canRun }: { options: HumanizeOptions | null; disabled?: boolean; canRun: boolean }) {
  const { input, inputMeta, setInput, start } = useHumanizer();
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const maxChars = options?.limits.max_chars ?? 120_000;
  const accept = (options?.upload.extensions ?? [".txt", ".md", ".docx", ".pdf"]).join(",");

  const load = async (file: File) => {
    setLoading(file.name);
    try {
      const r = await humanizer.extract(file);
      if (r.text.length > maxChars) {
        toast.error(`That document has ${r.text.length.toLocaleString()} characters; the limit is ${maxChars.toLocaleString()}.`);
        return;
      }
      setInput(r.text, { filename: r.filename, source: r.source, warnings: r.warnings });
      toast.success(`Loaded ${r.words.toLocaleString()} words from ${r.filename}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(null);
    }
  };

  const paste = async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (!t.trim()) return toast("The clipboard has no text.");
      setInput(t.slice(0, maxChars));
    } catch {
      toast("Your browser blocked clipboard access. Press ⌘V / Ctrl+V in the editor instead.");
    }
  };

  const words = countWords(input);
  const over = input.length > maxChars;

  return (
    <section
      aria-label="Your text"
      className={cn("hw-paper relative flex min-h-[420px] flex-col rounded-[3px] transition-shadow lg:min-h-[560px]", dragging && "ring-2 ring-ink/40")}
      onDragOver={(e) => {
        if (disabled) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (disabled) return;
        const f = e.dataTransfer.files?.[0];
        if (f) load(f);
      }}
    >
      <header className="flex items-center gap-2 border-b border-dashed border-foreground/10 px-5 py-2.5">
        <h2 className="text-[13px] font-medium text-muted-foreground">Your text</h2>
        {inputMeta?.filename && (
          <span className="truncate rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground" title={inputMeta.filename}>
            {inputMeta.filename}
          </span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <Button size="sm" variant="ghost" onClick={paste} disabled={disabled}><ClipboardPaste /> Paste</Button>
          <Button size="sm" variant="ghost" onClick={() => fileRef.current?.click()} disabled={disabled || !!loading}>
            {loading ? <Loader2 className="animate-spin" /> : <FileUp />} Upload
          </Button>
          {input && (
            <Button size="icon-sm" variant="ghost" aria-label="Clear text" onClick={() => setInput("")} disabled={disabled}>
              <X />
            </Button>
          )}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) load(f);
            e.target.value = "";
          }}
        />
      </header>

      <AnimatePresence>
        {inputMeta?.warnings?.length ? (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <ul className="space-y-0.5 bg-warn-bg/70 px-5 py-2 text-[12px] text-[oklch(0.42_0.08_70)]">
              {inputMeta.warnings.map((w) => (
                <li key={w} className="flex gap-1.5"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{w}</li>
              ))}
            </ul>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div className="relative flex-1">
        <textarea
          aria-label="Text to improve"
          value={input}
          disabled={disabled}
          onChange={(e) => setInput(e.target.value, inputMeta)}
          placeholder=""
          spellCheck
          className="absolute inset-0 h-full w-full resize-none bg-transparent px-6 py-5 font-serif text-[16px] leading-[1.75] text-foreground outline-none placeholder:text-muted-foreground/70 disabled:opacity-70 sm:px-8"
        />
        {!input && !loading && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center">
            <p className="font-display text-[26px] leading-tight text-foreground/70">Paste your writing here</p>
            <p className="max-w-sm text-[13px] leading-relaxed text-muted-foreground">
              or drop a file — {(options?.upload.ocr_available ? "TXT, Markdown, Word, PDF or an image" : "TXT, Markdown, Word or PDF")}.
              Use <code className="rounded bg-muted px-1">#</code> for headings and <code className="rounded bg-muted px-1">-</code> for lists; they are kept as they are.
            </p>
          </div>
        )}
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-paper/70 text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> Reading {loading}…
          </div>
        )}
        {dragging && (
          <div className="pointer-events-none absolute inset-3 flex items-center justify-center rounded border-2 border-dashed border-ink/40 bg-paper/80">
            <span className="font-hand text-2xl text-ink">Drop to load it</span>
          </div>
        )}
      </div>

      <footer className="flex items-center gap-3 border-t border-dashed border-foreground/10 px-5 py-2 text-[12px] tabular-nums text-muted-foreground">
        <span>{words.toLocaleString()} words</span>
        <span className={cn(over && "font-medium text-destructive")}>{input.length.toLocaleString()} characters{over && ` (limit ${maxChars.toLocaleString()})`}</span>
        <Button className="ml-auto h-9 gap-2 px-5 text-[14px]" onClick={start} disabled={!canRun || !input.trim()}
          title="Rewrite the whole text with the current settings (⌘↵)">
          <Feather className="size-4" /> Humanize it
        </Button>
      </footer>
    </section>
  );
}
