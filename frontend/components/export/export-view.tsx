"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { AlertTriangle, ArrowLeft, Check, Download, FileCheck2, FileDown, History, Loader2, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";
import { TopBar } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { DiffView } from "@/components/suggestions/diff-view";
import { api } from "@/lib/api";
import { formatBytes, MODULE_LABELS } from "@/lib/text";
import type { DocumentRecord, ExportReport, RevisionHistory } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BACK } from "@/components/motion/transitions";

export function ExportView({ docId }: { docId: string }) {
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [history, setHistory] = useState<RevisionHistory | null>(null);
  const [exports, setExports] = useState<Awaited<ReturnType<typeof api.exports>>>([]);
  const [report, setReport] = useState<ExportReport | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    api.document(docId).then(setDoc);
    api.revisions(docId).then(setHistory);
    api.exports(docId).then(setExports);
  };
  useEffect(load, [docId]);

  const run = async () => {
    setBusy(true);
    try {
      const r = await api.exportDoc(docId);
      setReport(r);
      if (r.ok) toast.success("Revised document exported and validated");
      else toast.error(r.message ?? "Export failed validation");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const applied = history?.ops.filter((o) => o.state === "applied" && o.after_text !== null) ?? [];

  return (
    <div className="min-h-screen">
      <TopBar>
        <div className="flex min-w-0 items-center gap-3 text-sm">
          <Link href={`/documents/${docId}`} transitionTypes={BACK} className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3.5" /> Back to review</Link>
          <span className="text-muted-foreground/50">/</span>
          <span className="truncate font-medium">{doc?.filename}</span>
          <span className="text-muted-foreground/50">/</span>
          <span>Export</span>
        </div>
      </TopBar>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="mb-6 flex items-end justify-between gap-6">
          <div>
            <h1 className="font-display text-[34px] leading-tight tracking-tight">Download your revised document</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Builds a new .docx from your untouched original and applies only the changes you approved. Every other part of the package — styles, images, tables, headers, footnotes, relationships — is copied byte-for-byte, then the result is validated.
            </p>
          </div>
          <Button size="lg" onClick={run} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <FileDown />} Export .docx
          </Button>
        </div>

        {report && <ReportCard report={report} />}

        <section className="mt-8">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-medium"><History className="size-4" /> Changes that will be applied ({applied.length > 0 ? new Set(applied.map((o) => o.paragraph_id)).size : 0} paragraphs)</h2>
          {applied.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No approved changes yet — an export now would be identical to the original.</p>
          ) : (
            <ul className="space-y-2">
              {applied.slice().reverse().map((o) => (
                <li key={o.seq} className="hw-card p-4">
                  <p className="mb-1.5 text-xs text-muted-foreground"><span className="font-mono">#{o.seq}</span> · {o.op_type === "manual" ? "Manual edit" : o.op_type === "restore" ? "Restored original" : `Accepted: ${o.note}`} · {new Date(o.created_at * 1000).toLocaleString()}</p>
                  {o.before_text !== null && o.after_text !== null && <DiffView before={o.before_text} after={o.after_text} className="text-[14px]" />}
                </li>
              ))}
            </ul>
          )}
          {history && history.ops.some((o) => o.state !== "applied") && (
            <p className="mt-2 text-xs text-muted-foreground">
              {history.ops.filter((o) => o.state === "undone").length} undone and {history.ops.filter((o) => o.state === "discarded").length} discarded operation(s) are kept in history but not applied.
            </p>
          )}
        </section>

        <section className="mt-8">
          <h2 className="mb-3 text-sm font-medium">Previous exports</h2>
          <ul className="divide-y hw-card">
            <li className="flex items-center gap-3 px-4 py-3 text-sm">
              <FileCheck2 className="size-4 text-muted-foreground" />
              <span className="flex-1">Original document <span className="text-xs text-muted-foreground">(read-only, never modified)</span></span>
              <Button size="sm" variant="ghost" render={<a href={api.originalUrl(docId)} />}><Download /> Original</Button>
            </li>
            {exports.map((e) => (
              <li key={e.id} className="flex items-center gap-3 px-4 py-3 text-sm">
                <FileDown className="size-4 text-muted-foreground" />
                <span className="flex-1 truncate">{e.filename}</span>
                <span className="text-xs text-muted-foreground">{e.paragraphs_changed} paragraph(s) · {new Date(e.created_at * 1000).toLocaleString()}</span>
                <span className={cn("text-xs", e.validation_ok ? "text-add" : "text-destructive")}>{e.validation_ok ? "validated" : "failed"}</span>
                <Button size="sm" variant="ghost" render={<a href={api.downloadUrl(e.id)} />}><Download /></Button>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}

function ReportCard({ report }: { report: ExportReport }) {
  if (!report.ok) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-del-bg/50 p-5 text-sm">
        <p className="flex items-center gap-2 font-medium text-del"><X className="size-4" /> {report.message}</p>
        <ul className="mt-2 space-y-1">{report.validation?.checks?.map((c) => <Check1 key={c.name} c={c} />)}</ul>
      </div>
    );
  }
  const unresolved = Object.entries(report.unresolved_findings);
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="hw-card">
      <div className="flex items-center gap-3 border-b px-5 py-4">
        <div className="grid size-9 place-items-center rounded-full bg-add-bg text-add"><Check className="size-4.5" /></div>
        <div className="flex-1">
          <p className="font-medium">{report.filename}</p>
          <p className="text-xs text-muted-foreground">{formatBytes(report.size_bytes)} · {report.paragraphs_changed} paragraph(s) changed · original unchanged</p>
        </div>
        <Button render={<a href={api.downloadUrl(report.export_id)} />}><Download /> Download</Button>
      </div>
      <div className="grid grid-cols-2 gap-6 p-5 text-sm">
        <div>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Review summary</h3>
          <ul className="space-y-1">
            <li>Changes applied: <b>{report.paragraphs_changed}</b> paragraph(s) ({report.suggestions.accepted} accepted suggestion(s), {report.manual_edits} manually edited)</li>
            <li>Suggestions rejected: <b>{report.suggestions.rejected}</b></li>
            <li>Suggestions still pending: <b>{report.suggestions.pending}</b></li>
            <li>Findings left unresolved: <b>{unresolved.reduce((a, [, n]) => a + n, 0)}</b>{unresolved.length > 0 && <span className="text-muted-foreground"> ({unresolved.map(([m, n]) => `${MODULE_LABELS[m] ?? m} ${n}`).join(", ")})</span>}</li>
          </ul>
          {report.needs_verification.length > 0 && (
            <div className="mt-3 rounded-lg bg-warn-bg p-3 text-xs">
              <p className="flex items-center gap-1.5 font-medium text-warn"><ShieldAlert className="size-3.5" /> {report.needs_verification.length} accepted change(s) need manual verification</p>
              <ul className="mt-1 list-disc pl-4 text-[oklch(0.42_0.1_70)]">{report.needs_verification.flatMap((v) => v.items).slice(0, 8).map((i, k) => <li key={k}>{i}</li>)}</ul>
            </div>
          )}
          {report.failed.length > 0 && (
            <div className="mt-3 rounded-lg bg-del-bg p-3 text-xs text-del">
              <p className="flex items-center gap-1.5 font-medium"><AlertTriangle className="size-3.5" /> {report.failed.length} change(s) could not be applied safely and were left as in the original</p>
              <ul className="mt-1 list-disc pl-4">{report.failed.map((f) => <li key={f.paragraph_id}>{f.reason}</li>)}</ul>
            </div>
          )}
          {report.warnings.length > 0 && (
            <ul className="mt-3 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Formatting validation</h3>
          <ul className="space-y-1">{report.validation.checks.map((c) => <Check1 key={c.name} c={c} />)}</ul>
          <p className="mt-3 text-xs text-muted-foreground">
            Edits are written into the existing Word runs, so bold, italics, fonts, hyperlinks and footnote markers stay attached to their text. Validation re-opens the file and re-extracts every paragraph to confirm it matches what you approved.
          </p>
        </div>
      </div>
    </motion.div>
  );
}

function Check1({ c }: { c: { name: string; ok: boolean; detail: string } }) {
  return (
    <li className="flex items-start gap-2">
      {c.ok ? <Check className="mt-0.5 size-3.5 shrink-0 text-add" /> : <X className="mt-0.5 size-3.5 shrink-0 text-del" />}
      <span>{c.name}{c.detail && <span className="text-muted-foreground"> — {c.detail}</span>}</span>
    </li>
  );
}
