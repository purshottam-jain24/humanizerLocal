"use client";

import { useState } from "react";
import { motion } from "motion/react";
import {
  AlertTriangle, Check, CheckCheck, Copy, GitCompare, Pencil, RefreshCw, ShieldAlert, Sparkles, Undo2, X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { fromEditable, label, toEditable } from "@/lib/text";
import type { Suggestion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/workspace-store";
import { DiffView } from "./diff-view";

const STATUS_STYLE: Record<string, string> = {
  pending: "text-brand",
  accepted: "text-add",
  rejected: "text-muted-foreground",
  incomplete: "text-warn",
  stale: "text-warn",
  superseded: "text-muted-foreground",
  reverted: "text-muted-foreground",
  failed: "text-destructive",
};

export function SuggestionCard({
  s, alternatives, onAlternative, onCompare,
}: {
  s: Suggestion;
  alternatives: number;
  onAlternative: (s: Suggestion) => void;
  onCompare: (groupId: string) => void;
}) {
  const docId = useWorkspace((st) => st.docId)!;
  const refresh = useWorkspace((st) => st.refresh);
  const upsert = useWorkspace((st) => st.upsertSuggestion);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(toEditable(s.proposed_text));
  const [busy, setBusy] = useState<string | null>(null);
  const open = s.status === "pending" || s.status === "incomplete" || s.status === "stale";
  const verification = s.verification?.required;
  const meaning = s.meaning;
  const ctx = spanContext(s);

  const run = async (name: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(name);
    try {
      await fn();
      if (ok) toast.success(ok);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
      await refresh(["suggestions"]);
    } finally {
      setBusy(null);
    }
  };

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ duration: 0.2 }}
      className={cn("hw-card shadow-xs", !open && "opacity-75")}
      aria-label={`${label(s.category)} suggestion`}
    >
      <header className="flex items-center gap-2 border-b px-4 py-2.5">
        <span className={cn("inline-flex size-5 items-center justify-center rounded-md", s.source.startsWith("llm") ? "bg-brand-soft text-brand" : "bg-secondary text-muted-foreground")}>
          {s.source.startsWith("llm") ? <Sparkles className="size-3" /> : <Check className="size-3" />}
        </span>
        <span className="text-sm font-medium">{label(s.category)}</span>
        <span className="text-xs text-muted-foreground">
          {s.scope === "span" ? "Correction" : s.scope === "sentence" ? "Sentence" : "Paragraph"} · {s.reason}
          {s.model && <> · <span className="font-mono">{s.model}</span></>}
        </span>
        <span className={cn("ml-auto text-xs font-medium capitalize", STATUS_STYLE[s.status])}>{s.status === "incomplete" ? "incomplete draft" : s.status}</span>
      </header>

      <div className="space-y-3 px-4 py-3">
        <section>
          <h4 className="mb-1 text-[10px] font-medium tracking-[0.12em] text-muted-foreground uppercase">Original text</h4>
          <DiffView before={ctx.before} after={ctx.after} mode="before" className="text-muted-foreground" />
        </section>
        <section>
          <h4 className="mb-1 text-[10px] font-medium tracking-[0.12em] text-muted-foreground uppercase">Suggested version</h4>
          {editing ? (
            <div className="space-y-2">
              <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} className="min-h-24 font-serif text-[15px]" autoFocus />
              {s.original_text.includes("￼") && (
                <p className="text-xs text-muted-foreground">Keep markers like ⟦1⟧ — they are footnotes, images or fields in the Word file.</p>
              )}
            </div>
          ) : (
            <DiffView before={ctx.before} after={ctx.after} mode="after" />
          )}
        </section>
        {s.explanation && (
          <section>
            <h4 className="mb-1 text-[10px] font-medium tracking-[0.12em] text-muted-foreground uppercase">Why this change?</h4>
            <p className="text-sm text-foreground/85">{s.explanation}</p>
          </section>
        )}
        {(meaning && meaning.level !== "likely_preserved") || verification || s.status === "incomplete" || s.status === "stale" ? (
          <div className="space-y-1.5">
            {s.status === "incomplete" && (
              <Notice tone="warn" icon={<AlertTriangle className="size-3.5" />}>Generation was interrupted. This is a partial draft — edit or regenerate it before accepting.</Notice>
            )}
            {s.status === "stale" && (
              <Notice tone="warn" icon={<AlertTriangle className="size-3.5" />}>The paragraph changed since this suggestion was made. Regenerate it.</Notice>
            )}
            {meaning && meaning.level !== "likely_preserved" && (
              <Notice tone={meaning.level === "possible_change" ? "danger" : "warn"} icon={<AlertTriangle className="size-3.5" />}>
                <span className="font-medium">{meaning.label}.</span> {meaning.warnings.join(" ")}
              </Notice>
            )}
            {verification && (
              <Notice tone="warn" icon={<ShieldAlert className="size-3.5" />}>
                <span className="font-medium">Requires verification.</span> {s.verification!.items.join(" ")}
              </Notice>
            )}
          </div>
        ) : meaning ? (
          <p className="flex items-center gap-1.5 text-xs text-add"><CheckCheck className="size-3.5" /> {meaning.label} <span className="text-muted-foreground">({meaning.method})</span></p>
        ) : null}
      </div>

      <footer className="flex flex-wrap items-center gap-1.5 border-t bg-muted/40 px-3 py-2">
        {open && !editing && (
          <>
            <Button size="sm" disabled={!!busy || s.status === "stale"} onClick={() => run("accept", () => api.accept(docId, s.id), "Change applied to the proposed document")}>
              <Check /> Accept
            </Button>
            <Button size="sm" variant="outline" disabled={!!busy} onClick={() => run("reject", () => api.reject(docId, s.id))}>
              <X /> Reject
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setDraft(toEditable(s.proposed_text)); setEditing(true); }}>
              <Pencil /> Edit manually
            </Button>
            {s.scope !== "span" && (
              <Button size="sm" variant="ghost" onClick={() => onAlternative(s)}>
                <RefreshCw /> Another version
              </Button>
            )}
            {alternatives > 1 && (
              <Button size="sm" variant="ghost" onClick={() => onCompare(s.group_id)}>
                <GitCompare /> Compare {alternatives}
              </Button>
            )}
          </>
        )}
        {editing && (
          <>
            <Button size="sm" disabled={!!busy} onClick={() => run("accept", () => api.accept(docId, s.id, fromEditable(draft)), "Edited version applied").then(() => setEditing(false))}>
              <Check /> Accept edited
            </Button>
            <Button size="sm" variant="outline" disabled={!!busy} onClick={async () => {
              try {
                upsert(await api.patchSuggestion(docId, s.id, { proposed_text: fromEditable(draft) }));
                setEditing(false);
              } catch (e) { toast.error((e as Error).message); }
            }}>
              Save draft
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </>
        )}
        {s.status === "accepted" && (
          <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => run("restore", () => api.restoreParagraph(docId, s.paragraph_id), "Paragraph restored to original")}>
            <Undo2 /> Restore original
          </Button>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button size="icon-sm" variant="ghost" aria-label="Copy suggested text" onClick={() => { navigator.clipboard.writeText(s.proposed_text.replace(/￼/g, "")); toast("Copied"); }}>
            <Copy />
          </Button>
          <label className="flex cursor-pointer items-center gap-1.5 px-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={!!s.reviewed} onChange={async (e) => upsert(await api.patchSuggestion(docId, s.id, { reviewed: e.target.checked }))} className="accent-[var(--brand)]" />
            Reviewed
          </label>
        </div>
      </footer>
    </motion.article>
  );
}

/** For span corrections, show the surrounding sentence so the change is understandable. */
function spanContext(s: Suggestion): { before: string; after: string } {
  if (s.scope !== "span" || s.span_start === null || s.span_end === null) return { before: s.original_text, after: s.proposed_text };
  const t = s.base_text;
  let a = s.span_start;
  while (a > 0 && !/[.!?]\s/.test(t.slice(a - 2, a)) && s.span_start - a < 160) a--;
  let b = s.span_end;
  while (b < t.length && !/[.!?]/.test(t[b - 1] ?? "") && b - s.span_end < 160) b++;
  const pre = (a > 0 ? "…" : "") + t.slice(a, s.span_start);
  const post = t.slice(s.span_end, b) + (b < t.length ? "…" : "");
  return { before: pre + s.original_text + post, after: pre + s.proposed_text + post };
}

function Notice({ tone, icon, children }: { tone: "warn" | "danger"; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <p className={cn("flex gap-2 rounded-lg px-2.5 py-2 text-xs", tone === "warn" ? "bg-warn-bg text-[oklch(0.42_0.1_70)]" : "bg-del-bg text-del")}>
      <span className="mt-px shrink-0">{icon}</span>
      <span>{children}</span>
    </p>
  );
}
