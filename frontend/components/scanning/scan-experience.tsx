"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Circle, Loader2, Minus, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api } from "@/lib/api";
import { useDocumentEvents } from "@/hooks/use-document-events";
import type { DocumentRecord, Stage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ScanEngine } from "./scan-engine";
import { ScanPaper } from "./scan-paper";
import { BACK, docName, FORWARD, Shared } from "@/components/motion/transitions";
import { InkProgress } from "@/components/human/ink";

const FRIENDLY_STEPS: [string[], string][] = [
  [["upload", "validate"], "Opening your document"],
  [["structure", "text", "ocr", "elements"], "Reading your document"],
  [["paragraphs"], "Checking spelling, grammar and style"],
  [["originality"], "Checking originality and readability"],
  [["suggestions", "workspace"], "Preparing your suggestions"],
];

export function ScanExperience({ docId }: { docId: string }) {
  const router = useRouter();
  // One mutable engine per mount; React re-renders from the `frame` counter the animation loop bumps.
  const [engine] = useState(() => new ScanEngine());
  const [frame, setFrame] = useState(0);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [manualPage, setManualPage] = useState<number | null>(null);
  const [details, setDetails] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const reduced = useReducedMotion();

  useEffect(() => {
    api.document(docId).then((d) => {
      setDoc(d);
      if (d.stages) engine.setStages(d.stages);
      if ((d.status === "interrupted" || d.status === "uploaded") && !d.running) api.process(docId);
    }).catch((e) => toast.error(e.message));
  }, [docId, engine]);

  useEffect(() => {
    if (reduced) engine.skipAnimation();
  }, [reduced, engine]);

  useDocumentEvents(docId, (ev) => engine.ingest(ev));

  // A single animation loop drives the whole scene.
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let lastPaint = 0;
    const loop = (t: number) => {
      engine.tick(Math.min(100, t - last), t);
      last = t;
      if (engine.dirty && t - lastPaint > 33) {
        engine.dirty = false;
        lastPaint = t;
        setFrame((f) => f + 1);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [engine]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea")) return;
      if (e.key === " ") { e.preventDefault(); engine.togglePause(); }
      else if (e.key.toLowerCase() === "s") engine.skipAnimation();
      else if (e.key === "Escape" && !details) router.push("/", { transitionTypes: BACK });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [engine, router, details]);

  const followPage = engine.pageOf(engine.activePid ?? engine.paragraphs[engine.paragraphs.length - 1]?.id ?? null);
  const page = manualPage ?? followPage;
  const pages = useMemo(() => {
    const s = new Set<number>();
    for (const p of engine.paragraphs) s.add(p.page ?? 1);
    return [...s].sort((a, b) => a - b);
  }, [engine, engine.paragraphs.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const stages = engine.stages;
  const t = engine.truth;
  const finished = engine.status === "completed";
  const ready = finished && engine.visualComplete;
  const active = stages.find((s) => s.state === "active");
  const done = stages.filter((s) => s.state === "completed" || s.state === "skipped").length;
  const pct = stages.length ? Math.round((done / stages.length) * 100) : 0;
  // When the scan is done, carry on to the review screen by itself after a short beat — unless the
  // person is looking around (opened Details, flipped pages, or clicked "Stay here").
  const [stay, setStay] = useState(false);
  const reviewHref = `/documents/${docId}`;
  const autoContinue = ready && !stay && !details && manualPage === null;
  useEffect(() => {
    if (!autoContinue) return;
    const t = setTimeout(() => router.push(reviewHref, { transitionTypes: FORWARD }), AUTO_CONTINUE_MS);
    return () => clearTimeout(t);
  }, [autoContinue, router, reviewHref]);

  const label = engine.status === "failed" ? "Something went wrong"
    : engine.status === "cancelled" ? "Stopped"
    : finished ? (ready ? "Your document is ready" : "Finishing up")
    : FRIENDLY_STEPS.find(([keys]) => active && keys.includes(active.key))?.[1] ?? "Getting started";

  return (
    <div className="fixed inset-0 flex flex-col">
      <header style={{ viewTransitionName: "app-header" }} className="relative flex h-14 shrink-0 items-center gap-4 px-5">
        <Link href="/" transitionTypes={BACK} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
          <ArrowLeft className="size-4" /> My documents
        </Link>
        <p className="absolute left-1/2 max-w-[40vw] -translate-x-1/2 truncate text-sm text-muted-foreground">{doc?.filename}</p>
        <div className="ml-auto flex items-center gap-1 text-sm">
          {!ready && engine.status !== "failed" && engine.status !== "cancelled" && !engine.instant && (
            <button onClick={() => engine.skipAnimation()} className="rounded-md px-2.5 py-1 text-muted-foreground transition-colors hover:text-foreground">Skip animation</button>
          )}
          <button onClick={() => setDetails(true)} className="rounded-md px-2.5 py-1 text-muted-foreground transition-colors hover:text-foreground">Details</button>
        </div>
      </header>

      <main className="relative min-h-0 flex-1 overflow-y-auto">
        <div className="flex min-h-full flex-col items-center px-6 pt-4 pb-60">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={page}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.45, ease: [0.22, 0.8, 0.2, 1] }}
            >
              <Shared name={docName(docId)}>
                <div><ScanPaper engine={engine} page={page} frame={frame} /></div>
              </Shared>
            </motion.div>
          </AnimatePresence>
          {pages.length > 1 && (
            <div className="mt-5 flex items-center gap-3 text-xs text-muted-foreground">
              <button aria-label="Previous page" disabled={page <= pages[0]} onClick={() => setManualPage(Math.max(pages[0], page - 1))} className="rounded p-1 hover:text-foreground disabled:opacity-30"><ChevronLeft className="size-4" /></button>
              <div className="flex gap-1.5">
                {pages.map((n) => (
                  <button key={n} aria-label={`Page ${n}`} onClick={() => setManualPage(n === followPage ? null : n)}
                    className={cn("h-1.5 rounded-full transition-all", n === page ? "w-5 bg-foreground" : engine.pageState(n) === "pending" ? "w-1.5 bg-foreground/15" : "w-1.5 bg-foreground/40")} />
                ))}
              </div>
              <button aria-label="Next page" disabled={page >= pages[pages.length - 1]} onClick={() => setManualPage(Math.min(pages[pages.length - 1], page + 1))} className="rounded p-1 hover:text-foreground disabled:opacity-30"><ChevronRight className="size-4" /></button>
              {manualPage !== null && <button onClick={() => setManualPage(null)} className="ml-1 underline-offset-2 hover:underline">follow</button>}
            </div>
          )}
        </div>
      </main>

      {/* Status — the only chrome on screen */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center bg-gradient-to-t from-scan-bg via-scan-bg/90 to-transparent px-6 pt-16 pb-8">
        <motion.div layout className="pointer-events-auto w-full max-w-md text-center" aria-live="polite">
          <AnimatePresence mode="wait">
            <motion.p key={label} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.25 }}
              className="inline-flex items-center justify-center gap-2.5 font-hand text-[38px] leading-tight text-foreground">
              {ready && <DrawnCheck />}
              {label}{engine.status === "live" && !finished && <span className="text-muted-foreground">…</span>}
            </motion.p>
          </AnimatePresence>
          {engine.failure && <p className="mt-1 text-sm text-destructive">{engine.failure}</p>}

          {!ready ? (
            <>
              <InkProgress className="mx-auto mt-2 w-72" value={pct / 100} />
              <p className="mt-3 text-sm text-muted-foreground tabular-nums">
                {t.pagesProcessed || pages.length || 0} {(t.pagesProcessed || pages.length) === 1 ? "page" : "pages"} · {t.wordsExtracted.toLocaleString()} words
                {t.suggestions > 0 && <> · {t.suggestions} suggestions</>}
              </p>
              {engine.status === "live" || finished ? (
                <p className={cn("mt-2 text-xs text-muted-foreground/80", finished && "invisible")}>
                  You can <Link href="/" transitionTypes={BACK} className="underline underline-offset-2 hover:text-foreground">leave this page</Link> — it keeps working in the background.
                </p>
              ) : (
                <Button className="mt-4" variant="outline" onClick={() => api.process(docId).then(() => window.location.reload())}>Try again</Button>
              )}
            </>
          ) : (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <p className="mt-2 text-sm text-muted-foreground">
                {t.suggestions > 0
                  ? `${t.suggestions} suggestion${t.suggestions === 1 ? "" : "s"} for ${t.wordsExtracted.toLocaleString()} words. You choose which to keep.`
                  : "No issues found. You can still ask the AI to polish any paragraph."}
              </p>
              <Button size="lg" className="relative mt-5 h-11 overflow-hidden px-6 text-[15px]" render={<Link href={reviewHref} transitionTypes={FORWARD} />}>
                {autoContinue && (
                  <motion.span aria-hidden className="absolute inset-y-0 left-0 bg-white/15" initial={{ width: "0%" }} animate={{ width: "100%" }}
                    transition={{ duration: AUTO_CONTINUE_MS / 1000, ease: "linear" }} />
                )}
                <span className="relative inline-flex items-center gap-1.5">Review suggestions <ArrowRight /></span>
              </Button>
              <p className="mt-3 h-4 text-xs text-muted-foreground">
                {autoContinue && <button className="underline-offset-2 hover:text-foreground hover:underline" onClick={() => setStay(true)}>Stay here</button>}
              </p>
            </motion.div>
          )}
        </motion.div>
      </div>

      {/* Details: the full, technical, real-time view — optional */}
      <Dialog open={details} onOpenChange={setDetails}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Processing details</DialogTitle>
            <DialogDescription>Live values from the local backend. Page numbers are estimated when Word didn&apos;t store page breaks.</DialogDescription>
          </DialogHeader>
          <ol className="space-y-1.5">
            {stages.map((s) => <StageRow key={s.key} s={s} />)}
          </ol>
          <dl className="mt-2 grid grid-cols-4 gap-3 border-t pt-4 text-center">
            {([
              ["Pages", t.pagesProcessed], ["Paragraphs", t.paragraphsExtracted], ["Words", t.wordsExtracted], ["Headings", t.headings],
              ["Tables", t.tables], ["Images", t.images], ["References", t.references], ["Findings", t.findings],
            ] as [string, number][]).map(([k, v]) => (
              <div key={k}><dd className="font-mono text-base tabular-nums">{v.toLocaleString()}</dd><dt className="text-[11px] text-muted-foreground">{k}</dt></div>
            ))}
          </dl>
          {t.aiTotal > 0 && <p className="text-xs text-muted-foreground">AI review: {t.aiReviewed} of {t.aiTotal} paragraphs</p>}
          <div className="flex items-center justify-between border-t pt-4">
            <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => engine.togglePause()}>{engine.paused ? "Resume animation" : "Pause animation"}</button>
            {engine.status === "live" && !finished && (
              <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setConfirmCancel(true)}>Cancel processing</Button>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop processing this document?</AlertDialogTitle>
            <AlertDialogDescription>Finished steps are kept, and you can continue later. Leaving this page does not stop processing.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep going</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => api.cancel(docId)}>Stop</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const AUTO_CONTINUE_MS = 2600;

/** A pen-stroke tick that draws itself when the document is ready. */
function DrawnCheck() {
  return (
    <svg viewBox="0 0 28 28" className="size-7 text-add" aria-hidden>
      <motion.circle cx="14" cy="14" r="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.35"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.5, ease: "easeOut" }} />
      <motion.path d="M8.5 14.5l3.6 3.6 7.4-8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.4, delay: 0.35, ease: "easeOut" }} />
    </svg>
  );
}

function StageRow({ s }: { s: Stage }) {
  return (
    <li className="flex items-start gap-2.5 text-sm">
      <span className="mt-0.5">
        {s.state === "completed" ? <Check className="size-4 text-add" />
          : s.state === "active" ? <Loader2 className="size-4 animate-spin text-brand" />
          : s.state === "failed" ? <TriangleAlert className="size-4 text-destructive" />
          : s.state === "skipped" ? <Minus className="size-4 text-muted-foreground" />
          : <Circle className="size-4 text-foreground/15" />}
      </span>
      <span className={cn("flex-1", s.state === "pending" && "text-muted-foreground")}>
        {s.label}
        {s.detail && <span className="block text-xs text-muted-foreground">{s.detail}</span>}
      </span>
    </li>
  );
}
