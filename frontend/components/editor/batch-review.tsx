"use client";

import { useMemo, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { NativeSelect } from "@/components/ui/segmented";
import { api } from "@/lib/api";
import { displayText, label } from "@/lib/text";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/workspace-store";

/** Review one category at a time / high-priority first; accept or reject selected suggestions. */
export function BatchReviewDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { suggestions, docId, refresh, select } = useWorkspace();
  const [category, setCategory] = useState("all");
  const [priority, setPriority] = useState("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const pending = useMemo(() => suggestions.filter((s) => s.status === "pending"), [suggestions]);
  const categories = useMemo(() => [...new Set(pending.map((s) => s.category))].sort(), [pending]);
  const rank = { high: 0, medium: 1, low: 2 } as const;
  const list = pending
    .filter((s) => (category === "all" || s.category === category) && (priority === "all" || s.priority === priority))
    .sort((a, b) => rank[a.priority] - rank[b.priority]);

  const run = async (action: "accept" | "reject") => {
    if (!docId || picked.size === 0) return;
    setBusy(true);
    try {
      const r = await api.batch(docId, [...picked], action);
      toast.success(`${r.done.length} ${action === "accept" ? "accepted" : "rejected"}${r.skipped.length ? ` · ${r.skipped.length} skipped (need individual review)` : ""}`);
      setPicked(new Set());
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Batch review</DialogTitle>
          <DialogDescription>
            Suggestions that change facts, sources or possibly meaning are never batch-accepted — they are skipped and left for individual review.
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-2">
          <NativeSelect value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="all">All categories ({pending.length})</option>
            {categories.map((c) => <option key={c} value={c}>{label(c)} ({pending.filter((s) => s.category === c).length})</option>)}
          </NativeSelect>
          <NativeSelect value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority">
            <option value="all">Any priority</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </NativeSelect>
          <Button variant="ghost" size="sm" className="ml-auto self-center" onClick={() => setPicked(new Set(list.filter((s) => !s.verification?.required).map((s) => s.id)))}>
            Select all safe
          </Button>
        </div>
        <ul className="max-h-[50vh] divide-y overflow-y-auto rounded-lg border">
          {list.map((s) => (
            <li key={s.id} className={cn("flex items-start gap-3 px-3 py-2 text-sm", picked.has(s.id) && "bg-brand-soft/40")}>
              <input type="checkbox" className="mt-1 accent-[var(--brand)]" checked={picked.has(s.id)} aria-label="Select suggestion"
                onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(s.id); else n.delete(s.id); return n; })} />
              <button className="min-w-0 flex-1 text-left" onClick={() => { select(s.paragraph_id, "nav"); onOpenChange(false); }}>
                <p className="text-xs text-muted-foreground">{label(s.category)} · {s.priority}{s.verification?.required && <span className="ml-1 inline-flex items-center gap-0.5 text-warn"><ShieldAlert className="size-3" /> verify</span>}</p>
                <p className="truncate font-serif"><del className="text-del">{displayText(s.original_text)}</del> → <ins className="text-add no-underline">{displayText(s.proposed_text) || "(remove)"}</ins></p>
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="p-6 text-center text-sm text-muted-foreground">Nothing pending here.</li>}
        </ul>
        <DialogFooter>
          <span className="mr-auto self-center text-xs text-muted-foreground">{picked.size} selected</span>
          <Button variant="outline" disabled={busy || !picked.size} onClick={() => run("reject")}>Reject selected</Button>
          <Button disabled={busy || !picked.size} onClick={() => run("accept")}>Accept selected</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
