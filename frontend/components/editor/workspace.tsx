"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BarChart3, CheckCheck, Download, Loader2, Redo2, RefreshCw, Undo2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TopBar } from "@/components/app-shell";
import { api } from "@/lib/api";
import { useWorkspace } from "@/stores/workspace-store";
import { Navigator } from "./navigator";
import { WritingPanel } from "./writing-panel";
import { DocxPreviewPanel } from "@/components/document-preview/docx-preview-panel";
import { BatchReviewDialog } from "./batch-review";
import { BACK, FORWARD } from "@/components/motion/transitions";

export function Workspace({ docId }: { docId: string }) {
  const { init, structure, loading, error, history, suggestions, refresh, selectedPid } = useWorkspace();
  const [batchOpen, setBatchOpen] = useState(false);

  useEffect(() => {
    init(docId, new URLSearchParams(window.location.search).get("p"));
  }, [docId, init]);

  const undo = async () => {
    const r = await api.undo(docId);
    if (r.op) toast("Undone");
    refresh();
  };
  const redo = async () => {
    const r = await api.redo(docId);
    if (r.op) toast("Redone");
    refresh();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select,[contenteditable]")) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const pending = suggestions.filter((s) => s.status === "pending").length;
  const accepted = suggestions.filter((s) => s.status === "accepted").length;

  return (
    <div className="flex h-screen flex-col">
      <TopBar>
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">Documents</Link>
          <span className="text-muted-foreground/50">/</span>
          <span className="truncate text-sm font-medium">{structure?.document.filename ?? "…"}</span>
          {structure && (
            <span className="hidden text-xs text-muted-foreground md:inline">
              {structure.summary.words.toLocaleString()} words · {pending} pending · {accepted} accepted
            </span>
          )}
        </div>
        <div className="ml-4 flex items-center gap-1">
          <IconBtn label="Undo (⌘Z)" disabled={!history?.can_undo} onClick={undo}><Undo2 /></IconBtn>
          <IconBtn label="Redo (⇧⌘Z)" disabled={!history?.can_redo} onClick={redo}><Redo2 /></IconBtn>
          <IconBtn label="Re-analyse selected paragraph" disabled={!selectedPid} onClick={async () => {
            const r = await api.reanalyze(docId, selectedPid ? [selectedPid] : undefined);
            toast(`Re-analysed: ${r.findings} finding(s), ${r.suggestions} suggestion(s)`);
            refresh();
          }}><RefreshCw /></IconBtn>
          <Button size="sm" variant="ghost" onClick={() => setBatchOpen(true)}><CheckCheck /> Batch review</Button>
          <Button size="sm" variant="ghost" render={<Link href={`/documents/${docId}`} transitionTypes={BACK} />}>Simple view</Button>
          <Button size="sm" variant="ghost" render={<Link href={`/documents/${docId}/analysis`} transitionTypes={FORWARD} />}><BarChart3 /> Analysis</Button>
          <Button size="sm" render={<Link href={`/documents/${docId}/export`} transitionTypes={FORWARD} />}><Download /> Export</Button>
        </div>
      </TopBar>

      {loading && (
        <div className="grid flex-1 place-items-center text-sm text-muted-foreground"><Loader2 className="size-5 animate-spin" /></div>
      )}
      {error && (
        <div className="grid flex-1 place-items-center">
          <div className="text-center text-sm">
            <XCircle className="mx-auto mb-2 size-6 text-destructive" />
            <p>{error}</p>
            <Button className="mt-4" variant="outline" render={<Link href={`/documents/${docId}/scan`} />}>View processing</Button>
          </div>
        </div>
      )}
      {structure && !loading && (
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel defaultSize="22%" minSize="16%" maxSize="35%" className="border-r bg-sidebar">
            <Navigator />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="40%" minSize="28%">
            <WritingPanel />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize="38%" minSize="22%">
            <DocxPreviewPanel />
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
      <BatchReviewDialog open={batchOpen} onOpenChange={setBatchOpen} />
    </div>
  );
}

function IconBtn({ label, children, ...props }: { label: string; children: React.ReactNode } & React.ComponentProps<typeof Button>) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label={label} {...props} />}>{children}</TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
