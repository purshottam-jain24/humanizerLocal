"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loader2, ScanSearch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { AiDetectionReport, DetectorStatus } from "@/lib/api";
import { humanizer } from "@/lib/humanizer";

/**
 * Runs the app's existing AI detector on the current text, only when the user asks.
 * The result is displayed here and nowhere else: the humanizer never reads it, never rewrites
 * because of it and never re-runs it automatically.
 */
export function DetectorDialog({ open, onOpenChange, text }: { open: boolean; onOpenChange: (o: boolean) => void; text: string }) {
  const [status, setStatus] = useState<DetectorStatus | null | "error">(null);
  const [report, setReport] = useState<AiDetectionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkedText, setCheckedText] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    humanizer.detectorStatus().then((s) => live && setStatus(s)).catch(() => live && setStatus("error"));
    return () => { live = false; };
  }, [open]);
  // A result only applies to the exact text it was computed for.
  const shown = checkedText === text ? report : null;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await humanizer.detectText(text));
      setCheckedText(text);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><ScanSearch className="size-4" /> AI detector</DialogTitle>
          <DialogDescription>
            Runs HumanWrite&apos;s separate AI detector on the current text, using its usual method. The humanizer doesn&apos;t use this score
            and doesn&apos;t rewrite text to change it.
          </DialogDescription>
        </DialogHeader>
        {status === null && <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Checking the detector…</p>}
        {status === "error" && <p className="text-destructive">Couldn&apos;t reach the backend.</p>}
        {status && status !== "error" && !status.ready && (
          <div className="space-y-3">
            <p>The AI detector isn&apos;t set up on this computer yet. It&apos;s an optional download in Settings.</p>
            <Button render={<Link href="/settings" />} variant="outline" size="sm">Open Settings</Button>
          </div>
        )}
        {status && status !== "error" && status.ready && (
          <div className="space-y-3">
            {shown && shown.available !== false && (
              <div className="rounded-lg bg-muted/60 p-3">
                {shown.ai_percent == null ? (
                  <p>Not enough text to judge (it needs at least about 120 words of running prose).</p>
                ) : (
                  <>
                    <p className="font-display text-[34px] leading-none">{shown.ai_percent}%</p>
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      of scored text looks AI-generated{shown.possible_percent ? `; ${shown.possible_percent}% possibly` : ""}. {shown.words_scored} of {shown.words_total} words scored.
                    </p>
                  </>
                )}
              </div>
            )}
            {shown?.available === false && <p>{shown.reason}</p>}
            {error && <p className="text-destructive">{error}</p>}
            <p className="text-[12px] leading-relaxed text-muted-foreground">
              No detector is reliable proof of authorship. Results vary between tools, and formal writing, non-native English and short texts are often misjudged.
            </p>
            <Button onClick={run} disabled={busy || !text.trim()} size="sm">
              {busy ? <Loader2 className="animate-spin" /> : <ScanSearch />} {shown ? "Check again" : "Run check"}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
