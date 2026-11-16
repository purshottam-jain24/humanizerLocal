"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Download, Loader2, RefreshCw, ScanSearch } from "lucide-react";
import { create } from "zustand";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api, postSSE, type AiDetectionReport, type DetectorStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Stamp } from "@/components/human/ink";
import { FORWARD, ringName, Shared } from "@/components/motion/transitions";

/* ------------------------------------------------------------------ store */

interface DetectorStore {
  status: DetectorStatus | null;
  progress: { message: string; pct: number | null; remote?: boolean } | null;
  refresh: () => Promise<DetectorStatus | null>;
  install: () => Promise<boolean>;
}

export const useDetector = create<DetectorStore>((set, get) => ({
  status: null,
  progress: null,
  refresh: async () => {
    try {
      const s = await api.detectorStatus();
      const p = get().progress;
      if (s.installing && !p) {
        // Setup was started in another tab: show it and follow along until it finishes.
        set({ status: s, progress: { message: "Setting up AI detection…", pct: null, remote: true } });
      } else if (!s.installing && p?.remote) {
        set({ status: s, progress: null });
      } else {
        set({ status: s });
      }
      if (s.installing && get().progress?.remote) setTimeout(() => get().refresh(), 3000);
      return s;
    } catch {
      return null;
    }
  },
  install: async () => {
    if (get().progress) return false;
    set({ progress: { message: "Starting…", pct: null } });
    let ok = false;
    try {
      await postSSE("/api/ai-detector/install", { confirm: true }, (event, d) => {
        if (event === "progress") {
          set({ progress: { message: d.phase === "libraries" ? "Installing detector libraries…" : "Downloading the detector model…", pct: d.total ? Math.round((d.completed / d.total) * 100) : null } });
        } else if (event === "done") ok = true;
        else if (event === "error") toast.error(d.message);
      });
    } catch (e) {
      toast.error((e as Error).message);
    }
    set({ progress: null });
    await get().refresh();
    if (ok) toast.success("AI detection is ready");
    return ok;
  },
}));

/* ------------------------------------------------------------------ helpers */

export function verdictCopy(r: AiDetectionReport): { title: string; sub: string } {
  switch (r.verdict) {
    case "ai":
      return { title: "Likely AI-generated", sub: "Most of this text reads like it was written by an AI model." };
    case "mixed":
      return { title: "Mix of AI and human writing", sub: "Some passages read like AI output; others read like a person wrote them." };
    case "human":
      return { title: "Likely human-written", sub: "Very little of this text reads like AI output." };
    default:
      return { title: "Not enough text to judge", sub: "AI detection needs at least a few full paragraphs of running text." };
  }
}

/** The verdict as a rubber stamp, the way a reviewer would mark a paper. */
export function VerdictStamp({ report, className, delay = 0.5 }: { report: AiDetectionReport; className?: string; delay?: number }) {
  if (!report.available || report.verdict === "insufficient" || !report.verdict) return null;
  const v = { human: { t: "Human", tone: "green" }, mixed: { t: "Mixed", tone: "amber" }, ai: { t: "AI-like", tone: "ink" } }[report.verdict as "human" | "mixed" | "ai"];
  if (!v) return null;
  return <Stamp tone={v.tone as "green" | "amber" | "ink"} className={className} delay={delay}>{v.t}</Stamp>;
}

export function aiMarksFrom(r: AiDetectionReport | null | undefined): Record<string, "strong" | "likely" | "possible"> | null {
  if (!r?.paragraphs) return null;
  const out: Record<string, "strong" | "likely" | "possible"> = {};
  for (const [pid, v] of Object.entries(r.paragraphs)) {
    if (v.label === "ai") out[pid] = "strong";
    else if (v.label === "likely_ai") out[pid] = "likely";
    else if (v.label === "possible") out[pid] = "possible";
  }
  return out;
}

/* ------------------------------------------------------------------ ring */

export function AiRing({ percent, size = 112, stroke = 9, instant = false }: { percent: number | null; size?: number; stroke?: number; instant?: boolean }) {
  const reduce = useReducedMotion() || instant;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = percent ?? 0;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-border)" strokeWidth={stroke} />
        {percent !== null && (
          <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-brand)" strokeWidth={stroke}
            strokeLinecap="round" strokeDasharray={c}
            initial={{ strokeDashoffset: reduce ? c * (1 - p / 100) : c }}
            animate={{ strokeDashoffset: c * (1 - p / 100) }}
            transition={{ duration: reduce ? 0 : 1.1, ease: [0.22, 1, 0.36, 1] }} />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-display leading-none tabular-nums" style={{ fontSize: size * 0.3 }}>
          {percent === null ? "—" : <CountUp to={p} instant={instant} />}
          {percent !== null && <span style={{ fontSize: size * 0.15 }}>%</span>}
        </span>
        <span className="mt-0.5 text-[10px] tracking-wide text-muted-foreground uppercase">AI</span>
      </div>
    </div>
  );
}

function CountUp({ to, instant = false }: { to: number; instant?: boolean }) {
  const reduce = useReducedMotion() || instant;
  const [v, setV] = useState(reduce ? to : 0);
  useEffect(() => {
    if (reduce) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 1100);
      setV(Math.round(to * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, reduce]);
  return <>{reduce ? to : v}</>;
}

/** Horizontal split bar: AI share vs human share of the scored words. */
export function AiSplitBar({ report, className }: { report: AiDetectionReport; className?: string }) {
  const ai = report.ai_percent ?? 0;
  const maybe = report.possible_percent ?? 0;
  return (
    <div className={className}>
      <div className="flex h-2 overflow-hidden rounded-full bg-secondary">
        <motion.div className="h-full bg-brand" initial={{ width: 0 }} animate={{ width: `${ai}%` }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }} />
        {maybe > 0 && <motion.div className="h-full bg-warn/70" initial={{ width: 0 }} animate={{ width: `${maybe}%` }} transition={{ duration: 0.9, delay: 0.2, ease: [0.22, 1, 0.36, 1] }} />}
      </div>
      <div className="mt-1.5 flex flex-wrap justify-between gap-x-3 text-xs text-muted-foreground">
        <span><span className="mr-1 inline-block size-2 rounded-full bg-brand" />AI-generated {ai}%</span>
        {maybe > 0 && <span><span className="mr-1 inline-block size-2 rounded-full bg-warn/70" />Possibly AI {maybe}%</span>}
        <span>Human-written {100 - ai - maybe}%<span className="ml-1 inline-block size-2 rounded-full bg-secondary ring-1 ring-border" /></span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ setup */

export function DetectorSetupDialog({ open, onOpenChange, onReady }: { open: boolean; onOpenChange: (v: boolean) => void; onReady?: () => void }) {
  const { status, install } = useDetector();
  const mb = (status?.upgrade_available ? status.upgrade_mb : status?.download_mb) || 2250;
  const libs = status ? !status.libraries_installed : true;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{status?.upgrade_available ? "Upgrade AI detection?" : "Turn on AI detection?"}</AlertDialogTitle>
          <AlertDialogDescription>
            HumanWrite will download {status?.upgrade_available ? "a second open AI-text detection model" : "two open AI-text detection models"} (about {Math.round(mb)} MB) from Hugging Face
            {libs ? ", plus the PyTorch libraries that run it (about 500 MB)" : ""}. This happens once. After that, detection
            runs entirely on this computer and your documents are never uploaded.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Not now</AlertDialogCancel>
          <AlertDialogAction onClick={async () => { onOpenChange(false); if (await install()) onReady?.(); }}>
            <Download /> Download &amp; turn on
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function InstallProgress({ className }: { className?: string }) {
  const progress = useDetector((s) => s.progress);
  if (!progress) return null;
  return (
    <div className={cn("text-xs", className)}>
      <Progress value={progress.pct ?? 0} />
      <p className="mt-1.5 text-muted-foreground">{progress.message}{progress.pct !== null ? ` ${progress.pct}%` : ""}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ hook */

// Last report per document, so the next screen can draw it in its first frame (the ring morphs).
const reportCache = new Map<string, AiDetectionReport>();
export const cachedAiReport = (docId: string) => reportCache.get(docId) ?? null;

/** Loads the stored report; can (re)run detection on the current text. */
export function useAiDetection(docId: string, initial?: AiDetectionReport | null) {
  const [report, setReport] = useState<AiDetectionReport | null | undefined>(initial);
  useEffect(() => { if (report?.available) reportCache.set(docId, report); }, [docId, report]);
  const [running, setRunning] = useState(false);
  const status = useDetector((s) => s.status);
  const refresh = useDetector((s) => s.refresh);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (initial !== undefined) return;
    api.analysis(docId).then((a) => setReport(a.reports.ai_detection ?? null)).catch(() => setReport(null));
  }, [docId, initial]);

  const run = async () => {
    setRunning(true);
    try {
      setReport(await api.runDetection(docId));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  };
  return { report, setReport, running, run, status };
}

/* ------------------------------------------------------------------ compact card (review screen) */

export function AiDetectionCard({ docId, highlight, onHighlight, onReport }: {
  docId: string;
  highlight: boolean;
  onHighlight: (v: boolean) => void;
  onReport?: (r: AiDetectionReport | null) => void;
}) {
  const { report, running, run, status } = useAiDetection(docId);
  const progress = useDetector((s) => s.progress);
  const [setup, setSetup] = useState(false);

  useEffect(() => { onReport?.(report ?? null); }, [report]); // eslint-disable-line react-hooks/exhaustive-deps

  const has = report?.available;
  return (
    <div className="relative hw-card p-4">
      {has && <VerdictStamp report={report!} className="pointer-events-none absolute -top-2.5 right-24" />}
      <div className="flex items-center gap-2">
        <ScanSearch className="size-4 text-muted-foreground" />
        <p className="text-sm font-medium">AI detection</p>
        {has && (
          <button onClick={run} disabled={running} className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50" title="Check again, including your accepted changes">
            <RefreshCw className={cn("size-3", running && "animate-spin")} /> Re-check
          </button>
        )}
      </div>

      {report === undefined || status === null ? (
        <div className="mt-3 h-16 animate-pulse rounded-lg bg-muted" />
      ) : has ? (
        <>
          <div className="mt-3 flex items-center gap-4">
            <Shared name={ringName(docId)}><div><AiRing percent={report!.ai_percent ?? null} size={84} stroke={7} /></div></Shared>
            <div className="min-w-0">
              <p className="font-display text-xl leading-tight">{verdictCopy(report!).title}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {report!.ai_percent !== null
                  ? `${report!.ai_percent}% AI${report!.possible_percent ? `, ${report!.possible_percent}% possibly AI` : ""} · ${report!.words_scored?.toLocaleString()} words checked`
                  : verdictCopy(report!).sub}
              </p>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3 text-xs">
            {Object.keys(aiMarksFrom(report) ?? {}).length > 0 && (
              <label className="inline-flex cursor-pointer items-center gap-1.5 select-none">
                <input type="checkbox" className="accent-[var(--color-brand)]" checked={highlight} onChange={(e) => onHighlight(e.target.checked)} />
                Highlight in document
              </label>
            )}
            <Link href={`/documents/${docId}/analysis?section=ai_detection`} transitionTypes={FORWARD} className="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
              Details <ArrowRight className="size-3" />
            </Link>
          </div>
        </>
      ) : status.ready ? (
        <div className="mt-2">
          <p className="text-xs text-muted-foreground">{report?.reason ?? "This document hasn't been checked yet."}</p>
          <Button size="sm" variant="outline" className="mt-2.5" onClick={run} disabled={running}>
            {running ? <><Loader2 className="animate-spin" /> Checking…</> : "Check for AI writing"}
          </Button>
        </div>
      ) : progress ? (
        <InstallProgress className="mt-3" />
      ) : (
        <div className="mt-2">
          <p className="text-xs text-muted-foreground">See how much of this document looks AI-generated. Runs on your computer after a one-time download.</p>
          <Button size="sm" variant="outline" className="mt-2.5" onClick={() => setSetup(true)}>Set up AI detection</Button>
        </div>
      )}
      <DetectorSetupDialog open={setup} onOpenChange={setSetup} onReady={run} />
    </div>
  );
}
