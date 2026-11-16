"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { docName, Shared } from "@/components/motion/transitions";
import { Eye, Loader2, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { api } from "@/lib/api";
import { useWorkspace } from "@/stores/workspace-store";
import { cn } from "@/lib/utils";

/**
 * Renders the *proposed* document (original DOCX + approved changes, built by
 * the same engine used for export) with docx-preview. The backend inserts a
 * bookmark `hw_<paragraphId>` into each paragraph of the preview copy only, so
 * rendered elements map back to paragraph ids for two-way selection.
 */
export function DocxPreviewPanel({ simple = false, pendingIds, aiMarks }: { simple?: boolean; pendingIds?: string[]; aiMarks?: Record<string, "strong" | "likely" | "possible"> | null }) {
  const { docId, revision, selectedPid, selectionSource, select, structure } = useWorkspace();
  const bodyRef = useRef<HTMLDivElement>(null);
  const styleRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [version, setVersion] = useState<"current" | "original">("current");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(0.8);
  const [shown, setShown] = useState(false); // first render finished: the placeholder sheet can go
  const renderSeq = useRef(0);
  const renderedOnce = useRef(false);

  // Render (or re-render after an accepted change).
  useEffect(() => {
    if (!docId || !bodyRef.current) return;
    const seq = ++renderSeq.current;
    const scroller = scrollRef.current;
    const keepScroll = scroller?.scrollTop ?? 0;
    setState((s) => (s === "ready" ? "ready" : "loading"));
    (async () => {
      try {
        const res = await fetch(`${api.previewUrl(docId, version)}&r=${revision}`);
        if (!res.ok) throw new Error(`Preview failed (${res.status})`);
        const blob = await res.blob();
        const { renderAsync } = await import("docx-preview");
        if (seq !== renderSeq.current || !bodyRef.current) return;
        const staging = document.createElement("div");
        await renderAsync(blob, staging, styleRef.current ?? undefined, {
          className: "docx",
          inWrapper: true,
          breakPages: true,
          ignoreLastRenderedPageBreak: false,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
          useBase64URL: true,
          experimental: true,
        });
        if (seq !== renderSeq.current || !bodyRef.current) return;
        if (styleRef.current) fixSymbolBullets(styleRef.current);
        tagParagraphs(staging, structure?.paragraphs.filter((p) => p.modified).map((p) => p.id) ?? []);
        bodyRef.current.replaceChildren(...Array.from(staging.childNodes));
        if (scroller) scroller.scrollTop = keepScroll;
        setState("ready");
        setShown(true);
        const sel = useWorkspace.getState().selectedPid;
        markSelected(bodyRef.current, sel, false);
        // After an accepted change, the edited paragraph glows once so you can see where the edit landed.
        if (renderedOnce.current && version === "current" && sel) {
          const el = bodyRef.current.querySelector<HTMLElement>(`[data-hw-pid="${sel}"][data-hw-modified="true"]`);
          if (el) {
            el.dataset.hwFlash = "true";
            setTimeout(() => { delete el.dataset.hwFlash; }, 1600);
          }
        }
        renderedOnce.current = true;
      } catch (e) {
        if (seq === renderSeq.current) {
          setError((e as Error).message);
          setState("error");
        }
      }
    })();
  }, [docId, revision, version]); // eslint-disable-line react-hooks/exhaustive-deps

  // Paragraphs that still have suggestions waiting get a small marker.
  const pendingKey = (pendingIds ?? []).join(",");
  useEffect(() => {
    const root = bodyRef.current;
    if (!root || state !== "ready") return;
    const set = new Set(pendingKey ? pendingKey.split(",") : []);
    root.querySelectorAll<HTMLElement>("[data-hw-pid]").forEach((el) => {
      if (set.has(el.dataset.hwPid!)) el.dataset.hwPending = "true";
      else delete el.dataset.hwPending;
    });
  }, [pendingKey, state, revision]);

  // Passages the AI detector flagged get a highlighter tint (only while the user has it switched on).
  const aiKey = aiMarks ? Object.entries(aiMarks).map(([k, v]) => `${k}:${v}`).join(",") : "";
  useEffect(() => {
    const root = bodyRef.current;
    if (!root || state !== "ready") return;
    const map = new Map(aiKey ? aiKey.split(",").map((kv) => kv.split(":") as [string, string]) : []);
    root.querySelectorAll<HTMLElement>("[data-hw-pid]").forEach((el) => {
      const v = map.get(el.dataset.hwPid!);
      if (v) el.dataset.hwAi = v;
      else delete el.dataset.hwAi;
    });
  }, [aiKey, state, revision]);

  // Selection → highlight + scroll.
  useEffect(() => {
    if (!bodyRef.current || state !== "ready") return;
    markSelected(bodyRef.current, selectedPid, selectionSource !== "preview");
  }, [selectedPid, selectionSource, state]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-dashed border-foreground/10 bg-[oklch(0.962_0.012_80/0.6)] backdrop-blur px-3 py-2">
        <Eye className="size-3.5 text-muted-foreground" />
        <span className="text-sm font-medium">{simple ? "Your document" : "Live preview"}</span>
        {simple && <span className="hidden text-xs text-muted-foreground lg:inline">Click any paragraph to work on it</span>}
        <Segmented size="xs" ariaLabel="Preview version" value={version} onChange={setVersion}
          options={[{ value: "current", label: simple ? "With my changes" : "With changes" }, { value: "original", label: "Original" }]} />
        <div className="ml-auto flex items-center gap-0.5">
          {state === "loading" && <Loader2 className="mr-1 size-3.5 animate-spin text-muted-foreground" />}
          <Button size="icon-xs" variant="ghost" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}><Minus /></Button>
          <span className="w-9 text-center font-mono text-[11px] text-muted-foreground">{Math.round(zoom * 100)}%</span>
          <Button size="icon-xs" variant="ghost" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.1).toFixed(2)))}><Plus /></Button>
        </div>
      </div>
      <div ref={scrollRef} className="hw-preview relative min-h-0 flex-1 overflow-auto">
        {state === "error" && <p className="p-6 text-sm text-destructive">{error}</p>}
        {/* Placeholder page underneath; the real page fades in on top of it, then it goes. */}
        <AnimatePresence>
          {!shown && state !== "error" && docId && (
            <motion.div key="sheet" className="pointer-events-none absolute inset-x-0 top-0" exit={{ opacity: 0, transition: { delay: 0.3, duration: 0.1 } }}>
              <PlaceholderSheet docId={docId} {...pageSize(structure, zoom)} />
            </motion.div>
          )}
        </AnimatePresence>
        <div ref={styleRef} />
        <div
          ref={bodyRef}
          className={cn("relative transition-opacity duration-300 ease-out", !shown && "opacity-0")}
          style={{ zoom }}
          onClick={(e) => {
            const p = (e.target as HTMLElement).closest<HTMLElement>("[data-hw-pid]");
            if (p?.dataset.hwPid) select(p.dataset.hwPid, "preview");
          }}
        />
      </div>
    </div>
  );
}

/**
 * Stand-in for the first page while the real Word rendering loads. It carries the document's
 * identity, so the page from the scan screen (or the thumbnail on My documents) lands here and the
 * real page then fades in underneath it.
 */
export function PlaceholderSheet({ docId, width = 600, aspect = 0.707 }: { docId: string; width?: number; aspect?: number }) {
  return (
    <div className="flex justify-center px-4 pt-6">
      <Shared name={docName(docId)}>
        <div className="max-w-full rounded-[2px] bg-paper px-[12%] pt-[11%] shadow-[0_1px_2px_oklch(0_0_0/0.06),0_8px_24px_oklch(0_0_0/0.06)]" style={{ width, aspectRatio: aspect }}>
          <div className="space-y-2.5">
            <div className="mb-5 h-3 w-1/2 rounded-full bg-foreground/10" />
            {[100, 96, 99, 92, 60, 0, 98, 100, 94, 97, 70, 0, 99, 95, 100, 88, 52].map((w, i) =>
              w ? <div key={i} className="h-[7px] rounded-full bg-foreground/[0.07]" style={{ width: `${w}%` }} /> : <div key={i} className="h-2" />)}
          </div>
        </div>
      </Shared>
    </div>
  );
}

/** Size of the first page as docx-preview will draw it (twips → CSS px at 96 dpi, times zoom). */
function pageSize(structure: { sections: { page_width_twips?: number | null; page_height_twips?: number | null }[] } | null, zoom: number) {
  const s = structure?.sections[0];
  if (!s?.page_width_twips || !s.page_height_twips) return { width: Math.round(612 * zoom * 1.333), aspect: 0.773 };
  return { width: Math.round((s.page_width_twips / 15) * zoom), aspect: s.page_width_twips / s.page_height_twips };
}

/** Same chrome as the real panel, shown before the document data arrives. */
export function PreviewPanelSkeleton({ docId }: { docId: string }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[41px] items-center gap-2 border-b border-dashed border-foreground/10 bg-[oklch(0.962_0.012_80/0.6)] backdrop-blur px-3">
        <Eye className="size-3.5 text-muted-foreground" />
        <span className="text-sm font-medium">Your document</span>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden"><PlaceholderSheet docId={docId} /></div>
    </div>
  );
}

function tagParagraphs(root: HTMLElement, modified: string[]) {
  const mod = new Set(modified);
  root.querySelectorAll<HTMLElement>('span[id^="hw_"]').forEach((bm) => {
    const pid = bm.id.slice(3);
    const p = bm.closest("p");
    if (p && !p.dataset.hwPid) {
      p.dataset.hwPid = pid;
      if (mod.has(pid)) p.dataset.hwModified = "true";
    }
    bm.removeAttribute("id"); // ids would duplicate across header/footer repeats
    bm.dataset.hwBm = pid;
  });
}

function markSelected(root: HTMLElement, pid: string | null, scroll: boolean) {
  root.querySelectorAll('[data-hw-selected="true"]').forEach((el) => el.removeAttribute("data-hw-selected"));
  if (!pid) return;
  const els = root.querySelectorAll<HTMLElement>(`[data-hw-pid="${pid}"]`);
  els.forEach((el) => el.setAttribute("data-hw-selected", "true"));
  if (scroll && els[0]) els[0].scrollIntoView({ block: "center", behavior: "smooth" });
}

/** Word bullets use private-use glyphs from the Symbol/Wingdings fonts, which browsers can't render. */
const SYMBOL_BULLETS: Record<string, string> = { "\uF0B7": "•", "\uF0A7": "▪", "\uF0D8": "➢", "\uF0FC": "✓", "\uF076": "❖", "\uF0A8": "□", "\uF06E": "■", "\uF06C": "●" };
function fixSymbolBullets(root: HTMLElement) {
  root.querySelectorAll("style").forEach((st) => {
    const text = st.textContent ?? "";
    if (!/[\uF000-\uF0FF]/.test(text)) return;
    st.textContent = text
      .replace(/[\uF000-\uF0FF]/g, (c) => SYMBOL_BULLETS[c] ?? "•")
      .replace(/font-family:\s*(Symbol|Wingdings)[^;]*;/gi, "");
  });
}
