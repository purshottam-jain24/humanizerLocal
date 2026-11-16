"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertTriangle, CheckCircle2, CircleDashed, FileUp, Loader2, MoreHorizontal,
  RotateCcw, ScanLine, Trash2, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { PageShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { api, ApiError } from "@/lib/api";
import { useSystem } from "@/hooks/use-system";
import { formatBytes, timeAgo } from "@/lib/text";
import type { DocumentListItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { docName, FORWARD, Shared } from "@/components/motion/transitions";
import { Handwrite, Scribble, Sticky } from "@/components/human/ink";
import { Depth, Tilt } from "@/components/human/tilt";

// Kept between visits so returning to this page renders the list immediately (the document sheet
// can then glide back into its row) and the intro only plays once per session.
let cachedDocs: DocumentListItem[] | null = null;
let introPlayed = false;

const EASE = [0.22, 1, 0.36, 1] as const;
const rise = (delay: number) => ({
  initial: introPlayed ? false : { opacity: 0, y: 18, filter: "blur(6px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" },
  transition: { duration: 0.9, delay, ease: EASE },
});

export function Dashboard() {
  const [docs, setDocs] = useState<DocumentListItem[] | null>(cachedDocs);
  const [error, setError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<DocumentListItem | null>(null);
  const { system } = useSystem();

  const load = useCallback(async () => {
    try {
      const list = await api.documents();
      cachedDocs = list;
      setDocs(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch + polling
    load();
    const t = setInterval(load, 4000);
    const intro = setTimeout(() => { introPlayed = true; }, 1500);
    return () => { clearInterval(t); clearTimeout(intro); };
  }, [load]);

  return (
    <PageShell className="max-w-6xl">
      <section className="relative mx-auto mt-4 mb-6 max-w-3xl text-center sm:mt-8">
        <motion.p {...rise(0)} className="font-hand text-[22px] text-pencil -rotate-2">for people who still write their own words</motion.p>
        <h1 className="mt-2 font-display text-[46px] leading-[1.2] tracking-tight text-foreground sm:text-[68px]">
          <motion.span className="inline-block" {...rise(0.08)}>Writing that sounds</motion.span>
          <br />
          <motion.span className="inline-block" {...rise(0.2)}>
            like{" "}
            <span className="relative inline-block">
              <span className={cn("transition-colors duration-700", "text-foreground/45")}>a machine</span>
              <Scribble variant="strike" className="absolute inset-x-[-4%] top-[44%] h-[0.3em] w-[108%] text-ink" strokeWidth={3.2} delay={introPlayed ? 0 : 1.05} duration={0.5} />
              <span className="absolute -top-[0.5em] right-[-0.42em] flex flex-col items-center">
                <Handwrite className="text-[0.8em] leading-none text-ink -rotate-6" delay={introPlayed ? 0 : 1.55}>you.</Handwrite>
              </span>
              <Scribble variant="caret" className="absolute -bottom-[0.08em] right-[0.22em] size-[0.36em] text-ink" strokeWidth={2.6} delay={introPlayed ? 0 : 1.45} duration={0.3} />
            </span>
          </motion.span>
        </h1>
        <motion.p className="mx-auto mt-6 max-w-xl text-[17px] leading-relaxed text-muted-foreground" {...rise(0.34)}>
          Drop in a Word document. HumanWrite marks it up the way a good editor would, and you decide what to keep.
          Everything happens on your computer.
        </motion.p>
      </section>

      <motion.div {...rise(0.46)}>
        <UploadDropzone sheets={Math.min(4, Math.max(2, docs?.length ?? 2))} />
      </motion.div>

      {error && (
        <div className="mt-6 flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <XCircle className="mt-0.5 size-4 text-destructive" />
          <div>
            <p className="font-medium">Cannot reach the HumanWrite backend</p>
            <p className="text-muted-foreground">{error}</p>
          </div>
        </div>
      )}

      {system && (!system.ollama.available || !system.settings.ollama.model) && (
        <div className="mx-auto mt-6 flex max-w-3xl items-center gap-3 rounded-xl border bg-warn-bg/60 px-4 py-3 text-sm">
          <ScanLine className="size-4 shrink-0 text-warn" />
          <p className="flex-1">
            <span className="font-medium">The AI assistant is off.</span>{" "}
            <span className="text-muted-foreground">Spelling, grammar and style checks still work. Turn on AI for rewrites — it runs privately on your computer.</span>
          </p>
          <Link href="/settings" className="shrink-0 font-medium text-brand hover:underline">Set up</Link>
        </div>
      )}

      <motion.div className="mx-auto mt-16 max-w-5xl" {...rise(0.6)}>
        <section aria-labelledby="recent">
          <div className="mb-6 flex items-end justify-between gap-4 border-b border-dashed border-foreground/15 pb-3">
            <h2 id="recent" className="font-display text-[32px] leading-none">
              My documents
              {docs && docs.length > 0 && <span className="ml-3 font-hand text-[22px] text-pencil">({docs.length} on your desk)</span>}
            </h2>
          </div>
          {!docs && !error && (
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="hw-paper h-[150px] animate-pulse rounded-[3px] opacity-70" />
              ))}
            </div>
          )}
          {docs && docs.length === 0 && (
            <div className="py-10 text-center">
              <p className="font-hand text-[28px] text-pencil">Your desk is clear.</p>
              <p className="mt-1 text-sm text-muted-foreground">Drop a draft on the paper above to start.</p>
            </div>
          )}
          <ul className="grid gap-x-5 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
            <AnimatePresence initial={false}>
              {docs?.map((d, i) => (
                <motion.li key={d.id} layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0, transition: { delay: introPlayed ? 0 : 0.7 + Math.min(i, 8) * 0.05 } }} exit={{ opacity: 0, scale: 0.96 }}>
                  <DocumentCard doc={d} onDelete={() => setToDelete(d)} onChanged={load} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </section>
      </motion.div>

      <AlertDialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{toDelete?.filename}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes HumanWrite&apos;s stored copy of the original, all analysis results, suggestions, revision history and exports for this document.
              Files you saved elsewhere are not affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (!toDelete) return;
                await api.deleteDocument(toDelete.id);
                toast.success("Document and its local data deleted");
                setToDelete(null);
                load();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageShell>
  );
}



const STATUS: Record<string, { label: string; icon: React.ReactNode; cls: string }> = {
  ready: { label: "Ready", icon: <CheckCircle2 className="size-3.5" />, cls: "text-add" },
  processing: { label: "Processing", icon: <Loader2 className="size-3.5 animate-spin" />, cls: "text-brand" },
  uploaded: { label: "Queued", icon: <CircleDashed className="size-3.5" />, cls: "text-muted-foreground" },
  failed: { label: "Failed", icon: <AlertTriangle className="size-3.5" />, cls: "text-destructive" },
  cancelled: { label: "Cancelled", icon: <XCircle className="size-3.5" />, cls: "text-muted-foreground" },
  interrupted: { label: "Interrupted", icon: <AlertTriangle className="size-3.5" />, cls: "text-warn" },
};

/** A small, stable "hand-placed" angle per document, so the desk doesn't look machine-aligned. */
function placed(id: string, range = 1.4) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return ((((h >>> 0) % 1000) / 1000) * 2 - 1) * range;
}

function DocumentCard({ doc, onDelete, onChanged }: { doc: DocumentListItem; onDelete: () => void; onChanged: () => void }) {
  const st = STATUS[doc.running ? "processing" : doc.status] ?? STATUS.uploaded;
  const href = doc.status === "ready" ? `/documents/${doc.id}` : `/documents/${doc.id}/scan`;
  const ready = doc.status === "ready";
  const angle = placed(doc.id);
  return (
    <Tilt max={7} hoverLift={18} perspective={900} className="group h-full" style={{ rotate: `${angle}deg` }}>
      <div className="hw-paper hw-3d relative flex h-full min-h-[150px] gap-4 rounded-[3px] p-4 pr-5 transition-shadow duration-300 group-hover:shadow-[var(--shadow-paper-lift)]">
        <Depth z={10} className="pointer-events-none">
          <Shared name={docName(doc.id)}>
            <div aria-hidden className="relative h-[84px] w-[62px] shrink-0 rounded-[2px] bg-paper shadow-[0_1px_2px_oklch(0.3_0.02_60/0.14),0_6px_12px_-6px_oklch(0.3_0.02_60/0.3)] ring-1 ring-foreground/[0.06]">
              <span className="absolute top-2 left-[9px] h-[68px] w-px bg-ink/25" />
              <span className="absolute inset-x-3 top-2.5 left-3.5 space-y-[4px]">
                {[90, 100, 70, 100, 85, 55, 95, 60].map((w, i) => <span key={i} className="block h-[2px] rounded-full bg-foreground/15" style={{ width: `${w}%` }} />)}
              </span>
            </div>
          </Shared>
        </Depth>
        <div className="min-w-0 flex-1 pt-0.5">
          <Link href={href} transitionTypes={FORWARD} className="outline-none after:absolute after:inset-0 after:content-['']">
            <p className="line-clamp-2 font-serif text-[15px] leading-snug font-medium [overflow-wrap:anywhere] decoration-ink/40 underline-offset-[3px] group-hover:underline">{doc.filename.replace(/\.docx$/i, "").replace(/[_.-]/g, "$&\u200b")}</p>
          </Link>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {doc.summary ? <>{doc.summary.words.toLocaleString()} words · {doc.summary.pages ?? "?"} pages</> : formatBytes(doc.size_bytes)}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">Edited {timeAgo(doc.updated_at)}</p>
          {!ready && (
            <p className={cn("mt-2 inline-flex items-center gap-1 text-xs", st.cls)}>{st.icon}{st.label}</p>
          )}
          {doc.error && <p className="mt-1 truncate text-xs text-destructive" title={doc.error}>{doc.error}</p>}
        </div>

        {ready && (
          <Depth z={26} className="pointer-events-none absolute -top-3 -right-3">
            <div className="hw-sticky grid min-w-[74px] place-items-center px-2.5 py-1.5 transition-transform duration-300 group-hover:rotate-0" style={{ rotate: `${4 + angle}deg` }}>
              {doc.pending > 0 ? (
                <span className="font-hand text-[19px] leading-none text-ink"><span className="text-[22px] font-bold">{doc.pending}</span> to fix</span>
              ) : (
                <span className="inline-flex items-center gap-0.5 font-hand text-[18px] leading-none text-[oklch(0.42_0.1_155)]">
                  <Scribble variant="tick" className="size-4" inView /> all done
                </span>
              )}
            </div>
          </Depth>
        )}

        <div className="absolute right-2 bottom-2 z-10 flex items-center">
          {ready && doc.accepted > 0 && <span className="mr-1 font-hand text-[16px] text-[oklch(0.42_0.1_155)]">{doc.accepted} kept</span>}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`More actions for ${doc.filename}`} className="opacity-60 group-hover:opacity-100" />}>
              <MoreHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem render={<Link href={href} transitionTypes={FORWARD} />}>{ready ? "Open" : doc.running ? "View scan" : "Continue"}</DropdownMenuItem>
              <DropdownMenuItem render={<Link href={`/documents/${doc.id}/analysis`} transitionTypes={FORWARD} />}>Full report</DropdownMenuItem>
              <DropdownMenuItem render={<Link href={`/documents/${doc.id}/export`} transitionTypes={FORWARD} />}>Export &amp; history</DropdownMenuItem>
              {doc.status !== "processing" && !doc.running && (
                <DropdownMenuItem
                  onClick={async () => {
                    await api.process(doc.id, { restart: doc.status === "ready" });
                    toast("Reprocessing started");
                    onChanged();
                  }}
                >
                  <RotateCcw /> {ready ? "Re-run analysis" : "Resume processing"}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={onDelete}>
                <Trash2 /> Delete document data
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </Tilt>
  );
}

const PILE = [
  { z: -8, r: -2.6, x: -10, y: 8 },
  { z: -16, r: 2.2, x: 12, y: 14 },
  { z: -24, r: -4.4, x: -4, y: 20 },
  { z: -32, r: 3.6, x: 8, y: 26 },
];

function UploadDropzone({ sheets }: { sheets: number }) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [sheetFor, setSheetFor] = useState<string | null>(null);
  const lifted = drag || !!busy;

  const upload = async (file: File) => {
    setProblem(null);
    if (!file.name.toLowerCase().endsWith(".docx")) {
      setProblem(file.name.toLowerCase().endsWith(".doc") ? "Legacy .doc files are not supported. Save as .docx in Word first." : "Only .docx files are supported.");
      return;
    }
    setBusy(file.name);
    try {
      const doc = await api.upload(file);
      // Give this sheet the document's identity first, so the navigation morphs it into the scanned page.
      flushSync(() => setSheetFor(doc.id));
      router.push(`/documents/${doc.id}/scan`, { transitionTypes: FORWARD });
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  };

  return (
    <div
      className="relative mx-auto max-w-[600px] px-2 pt-2 pb-10"
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDrag(false); }}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files[0];
        if (f) upload(f);
      }}
    >
      {/* Pencil resting beside the pile */}
      <motion.div aria-hidden className="pointer-events-none absolute top-[58%] -right-24 hidden w-56 lg:block"
        initial={introPlayed ? false : { opacity: 0, x: 40, rotate: -30 }} animate={{ opacity: 1, x: 0, rotate: -24 }} transition={{ duration: 1.1, delay: 0.9, ease: EASE }}>
        <Pencil />
      </motion.div>
      {/* A margin note pointing at the paper */}
      <motion.div aria-hidden className="pointer-events-none absolute top-4 -left-52 hidden w-48 text-right lg:block" initial={introPlayed ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.9 }}>
        <Handwrite className="text-[24px] leading-tight text-pencil -rotate-3" delay={introPlayed ? 0 : 1.9}>start here</Handwrite>
        <Scribble variant="arrow" className="mt-1 ml-auto h-12 w-28 text-pencil" strokeWidth={2} delay={introPlayed ? 0 : 2.3} />
      </motion.div>

      <Tilt max={6} baseX={10} perspective={1500} hoverLift={22} active={lifted} glare className="relative">
        {/* cast shadow on the desk */}
        <Depth z={-60} className="pointer-events-none absolute inset-x-[6%] -bottom-6 h-16 rounded-[50%] bg-[oklch(0.35_0.04_60/0.22)] blur-2xl" />
        {/* the rest of the pile */}
        {PILE.slice(0, sheets - 1).map((p, i) => (
          <motion.div key={i} aria-hidden className="hw-paper absolute inset-0 rounded-[3px]"
            initial={false}
            animate={lifted
              ? { x: p.x * 2.2, y: p.y * 0.4, rotate: p.r * 1.8, z: p.z }
              : { x: p.x, y: p.y * 0.5, rotate: p.r, z: p.z }}
            transition={{ type: "spring", stiffness: 160, damping: 18 }}
            style={{ filter: `brightness(${0.985 - i * 0.012})` }} />
        ))}

        <Shared name={sheetFor ? docName(sheetFor) : undefined}>
          <motion.div
            className={cn("hw-paper hw-ruled hw-3d relative flex flex-col items-center rounded-[3px] px-8 pt-12 pb-10 text-center", lifted && "ring-2 ring-ink/30")}
            animate={{ rotateX: lifted ? -4 : 0, y: lifted ? -14 : 0 }}
            transition={{ type: "spring", stiffness: 200, damping: 20 }}
            style={{ backgroundPosition: "0 18px" }}
          >
            {/* red margin rule, like an exercise book */}
            <span aria-hidden className="absolute inset-y-0 left-12 w-px bg-ink/30" />
            <span aria-hidden className="absolute inset-y-0 left-[52px] w-px bg-ink/15" />
            {/* punched holes */}
            <span aria-hidden className="absolute top-0 bottom-0 left-4 flex flex-col justify-around py-8">
              {[0, 1, 2].map((i) => <span key={i} className="size-3 rounded-full bg-[oklch(0.94_0.012_80)] shadow-[inset_0_1px_2px_oklch(0.3_0.03_60/0.3)]" />)}
            </span>

            <Depth z={30} className="relative">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div key={busy ? "busy" : drag ? "drag" : "idle"} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
                  <p className="font-hand text-[40px] leading-none text-foreground sm:text-[46px]">
                    {busy ? "Opening it up…" : drag ? "Let go, I've got it" : "Drop your draft here"}
                  </p>
                  <Scribble variant={busy ? "squiggle" : "underline"} className="mx-auto mt-1 h-3 w-64 text-ink" strokeWidth={2.6} delay={introPlayed || busy || drag ? 0 : 1.2} />
                </motion.div>
              </AnimatePresence>
            </Depth>
            {busy ? (
              <p className="relative mt-5 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> “{busy}”</p>
            ) : (
              <Depth z={18} className="relative">
                <p className="mt-4 text-[13px] text-muted-foreground">A Word file (.docx) up to 50 MB. Your original is never changed.</p>
                <Button size="lg" className="mt-6 h-11 px-6 text-[15px] shadow-[0_8px_18px_-8px_oklch(0.2_0.02_60/0.6)]" onClick={() => input.current?.click()}>
                  <FileUp /> Choose a Word file
                </Button>
              </Depth>
            )}
            <input
              ref={input}
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              className="sr-only"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload(f);
                e.target.value = "";
              }}
            />
            {problem && (
              <p role="alert" className="relative mt-4 flex items-center gap-1.5 text-sm text-destructive">
                <AlertTriangle className="size-4" /> {problem}
              </p>
            )}
            {/* sticky note on the corner of the sheet */}
            <Depth z={40} className="pointer-events-none absolute -top-5 -right-6 hidden sm:block">
              <motion.div initial={introPlayed ? false : { opacity: 0, scale: 0.8, rotate: 14 }} animate={{ opacity: 1, scale: 1, rotate: 5 }} transition={{ type: "spring", stiffness: 220, damping: 16, delay: 1.4 }}>
                <Sticky rotate={0} className="w-36 text-left">
                  <p className="font-hand text-[19px] leading-[1.05] text-[oklch(0.35_0.03_70)]">Stays on your computer. Always.</p>
                  <Scribble variant="underline" className="mt-0.5 h-2 w-16 text-ink/70" strokeWidth={1.8} delay={introPlayed ? 0 : 2} />
                </Sticky>
              </motion.div>
            </Depth>
          </motion.div>
        </Shared>
      </Tilt>
    </div>
  );
}

/** A plain pencil, drawn in SVG, resting on the desk. */
function Pencil() {
  return (
    <svg viewBox="0 0 240 28" className="w-full drop-shadow-[0_10px_8px_oklch(0.3_0.04_60/0.25)]">
      <defs>
        <linearGradient id="hw-pencil-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="oklch(0.86 0.13 85)" />
          <stop offset="0.45" stopColor="oklch(0.8 0.15 80)" />
          <stop offset="1" stopColor="oklch(0.66 0.14 70)" />
        </linearGradient>
        <linearGradient id="hw-pencil-wood" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="oklch(0.86 0.05 70)" />
          <stop offset="1" stopColor="oklch(0.78 0.06 65)" />
        </linearGradient>
      </defs>
      <rect x="2" y="5" width="22" height="18" rx="3" fill="oklch(0.72 0.12 20)" />
      <rect x="22" y="4" width="12" height="20" fill="oklch(0.78 0.02 90)" />
      <rect x="24" y="4" width="2" height="20" fill="oklch(0.65 0.02 90)" />
      <rect x="30" y="4" width="2" height="20" fill="oklch(0.65 0.02 90)" />
      <rect x="34" y="5" width="158" height="18" fill="url(#hw-pencil-body)" />
      <path d="M34 11h158" stroke="oklch(0.9 0.1 90 / 0.6)" strokeWidth="1" />
      <path d="M192 5 L226 14 L192 23 Z" fill="url(#hw-pencil-wood)" />
      <path d="M216 11.3 L226 14 L216 16.7 Z" fill="oklch(0.3 0.01 60)" />
    </svg>
  );
}
