"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Segmented, NativeSelect } from "@/components/ui/segmented";
import { displayText, label, MODULE_LABELS } from "@/lib/text";
import type { Paragraph } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/workspace-store";

type Tab = "paragraphs" | "outline" | "findings";

export function Navigator() {
  const { structure, findings, suggestions, selectedPid, selectionSource, select } = useWorkspace();
  const [tab, setTab] = useState<Tab>("paragraphs");
  const [q, setQ] = useState("");
  const [page, setPage] = useState("all");
  const [filter, setFilter] = useState("all");
  const [moduleFilter, setModuleFilter] = useState("all");

  const paragraphs = useMemo(() => structure?.paragraphs.filter((p) => p.kind !== "empty") ?? [], [structure]);
  const pages = useMemo(() => [...new Set(paragraphs.map((p) => p.page).filter(Boolean))] as number[], [paragraphs]);
  const pendingBy = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of suggestions) if (s.status === "pending") m.set(s.paragraph_id, (m.get(s.paragraph_id) ?? 0) + 1);
    return m;
  }, [suggestions]);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return paragraphs.filter((p) => {
      if (page !== "all" && String(p.page) !== page) return false;
      if (needle && !p.text.toLowerCase().includes(needle)) return false;
      if (filter === "findings" && !Object.keys(p.findings).length) return false;
      if (filter === "pending" && !pendingBy.get(p.id)) return false;
      if (filter === "modified" && !p.modified) return false;
      return true;
    });
  }, [paragraphs, q, page, filter, pendingBy]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virt = useVirtualizer({ count: list.length, getScrollElement: () => scrollRef.current, estimateSize: () => 64, overscan: 8 });

  useEffect(() => {
    if (tab !== "paragraphs" || selectionSource === "nav" || !selectedPid) return;
    const i = list.findIndex((p) => p.id === selectedPid);
    if (i >= 0) virt.scrollToIndex(i, { align: "center" });
  }, [selectedPid, selectionSource, tab, list, virt]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const i = list.findIndex((p) => p.id === selectedPid);
    const next = list[Math.max(0, Math.min(list.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      select(next.id, "nav");
      virt.scrollToIndex(list.indexOf(next), { align: "auto" });
    }
  };

  if (!structure) return null;
  const visibleFindings = findings.filter((f) => moduleFilter === "all" || f.module === moduleFilter);

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b p-3">
        <Segmented<Tab> ariaLabel="Navigator view" value={tab} onChange={setTab} className="w-full [&>button]:flex-1"
          options={[{ value: "paragraphs", label: "Paragraphs" }, { value: "outline", label: "Outline" }, { value: "findings", label: `Findings ${findings.length}` }]} />
        {tab === "paragraphs" && (
          <>
            <div className="relative">
              <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search text" className="h-8 pl-8 text-sm" aria-label="Search paragraphs" />
            </div>
            <div className="flex gap-2">
              <NativeSelect value={page} onChange={(e) => setPage(e.target.value)} aria-label="Page" className="h-7 flex-1 text-xs">
                <option value="all">All pages</option>
                {pages.map((p) => <option key={p} value={p}>Page {p}</option>)}
              </NativeSelect>
              <NativeSelect value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter" className="h-7 flex-1 text-xs">
                <option value="all">All</option>
                <option value="findings">With findings</option>
                <option value="pending">Pending changes</option>
                <option value="modified">Modified</option>
              </NativeSelect>
            </div>
          </>
        )}
        {tab === "findings" && (
          <NativeSelect value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)} aria-label="Module" className="h-7 w-full text-xs">
            <option value="all">All categories</option>
            {Object.entries(MODULE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        )}
      </div>

      {tab === "paragraphs" && (
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto outline-none" tabIndex={0} onKeyDown={onKeyDown} role="listbox" aria-label="Paragraphs" aria-activedescendant={selectedPid ? `nav-${selectedPid}` : undefined}>
          <div style={{ height: virt.getTotalSize(), position: "relative" }}>
            {virt.getVirtualItems().map((vi) => {
              const p = list[vi.index];
              return (
                <div key={p.id} data-index={vi.index} ref={virt.measureElement} style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${vi.start}px)` }}>
                  <ParagraphRow p={p} index={paragraphs.indexOf(p) + 1} selected={p.id === selectedPid} pending={pendingBy.get(p.id) ?? 0} onClick={() => select(p.id, "nav")} />
                </div>
              );
            })}
          </div>
          {list.length === 0 && <p className="p-6 text-center text-xs text-muted-foreground">No paragraphs match.</p>}
        </div>
      )}

      {tab === "outline" && (
        <ul className="min-h-0 flex-1 overflow-y-auto p-2">
          {paragraphs.filter((p) => p.part === "body" && (p.kind === "heading" || p.kind === "title" || p.probable_heading)).map((p) => (
            <li key={p.id}>
              <button onClick={() => select(p.id, "nav")} className={cn("block w-full truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-secondary", p.id === selectedPid && "bg-secondary font-medium")}
                style={{ paddingLeft: 8 + Math.max(0, (p.heading_level ?? 1) - 1) * 12 }}>
                {p.text || "(untitled)"}
                {p.page && <span className="float-right font-mono text-[10px] text-muted-foreground">{p.page}</span>}
              </button>
            </li>
          ))}
          {!paragraphs.some((p) => p.kind === "heading" || p.kind === "title") && <p className="p-4 text-xs text-muted-foreground">No heading styles detected in this document.</p>}
        </ul>
      )}

      {tab === "findings" && (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {visibleFindings.slice(0, 1000).map((f) => (
            <li key={f.id}>
              <button onClick={() => f.paragraph_id && select(f.paragraph_id, "nav")} disabled={!f.paragraph_id}
                className={cn("block w-full border-b px-3 py-2 text-left hover:bg-secondary/60", f.paragraph_id === selectedPid && "bg-secondary/80")}>
                <p className="flex items-center gap-1.5 text-xs">
                  <span className={cn("size-1.5 rounded-full", f.priority === "high" ? "bg-del" : f.priority === "medium" ? "bg-warn" : "bg-muted-foreground/40")} />
                  <span className="font-medium">{label(f.category)}</span>
                  <span className="text-muted-foreground">{MODULE_LABELS[f.module]}</span>
                </p>
                <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{f.passage ? `“${displayText(f.passage)}” — ` : ""}{f.explanation}</p>
              </button>
            </li>
          ))}
          {visibleFindings.length === 0 && <p className="p-6 text-center text-xs text-muted-foreground">No open findings.</p>}
        </ul>
      )}
    </div>
  );
}

function ParagraphRow({ p, index, selected, pending, onClick }: { p: Paragraph; index: number; selected: boolean; pending: number; onClick: () => void }) {
  const nf = Object.values(p.findings).reduce((a, b) => a + b, 0);
  const heading = p.kind === "heading" || p.kind === "title";
  return (
    <button id={`nav-${p.id}`} role="option" aria-selected={selected} onClick={onClick}
      className={cn("group relative block w-full border-b px-3 py-2 text-left transition-colors hover:bg-secondary/60", selected && "bg-brand-soft/70 hover:bg-brand-soft")}>
      {selected && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand" />}
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="font-mono">¶{index}</span>
        {p.part !== "body" && <span className="rounded bg-secondary px-1">{p.part}</span>}
        {p.table && <span className="rounded bg-secondary px-1">table</span>}
        {p.page && <span>p.{p.page}</span>}
        <span className="ml-auto flex items-center gap-1">
          {p.modified && <span className="rounded bg-add-bg px-1 text-add">edited</span>}
          {pending > 0 && <span className="rounded bg-brand-soft px-1 text-brand">{pending} pending</span>}
          {nf > 0 && <span className="rounded bg-secondary px-1">{nf}</span>}
        </span>
      </div>
      <p className={cn("mt-0.5 line-clamp-2 text-[13px] leading-snug", heading ? "font-semibold" : "text-foreground/85")}>{displayText(p.text) || <em className="text-muted-foreground">(no text)</em>}</p>
    </button>
  );
}
