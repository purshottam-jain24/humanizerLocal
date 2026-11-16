"use client";

import { useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Info, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { humanizer, type Flag, type Metrics } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { currentText, useHumanizer, type Version } from "@/stores/humanizer-store";

const TYPE_LABEL: Record<string, string> = {
  missing_information: "Missing information", changed_claim: "Changed claim", number_or_date: "Number or date", terminology: "Terminology",
  citation: "Citation", quotation: "Quotation", certainty: "Certainty", argument: "Argument", added_information: "Added information",
  contradiction: "Contradiction", link: "Link", kept_original: "Kept original", repaired: "Repaired",
};

const STAGE_ROWS: { key: string; label: string; what: string }[] = [
  { key: "structure", label: "Structure", what: "sentence construction and paragraph organisation" },
  { key: "vocabulary", label: "Vocabulary", what: "generic, repetitive or needlessly formal words; terms kept exact" },
  { key: "style", label: "Style", what: "the chosen style, register consistency and intensity" },
  { key: "grammar", label: "Grammar & flow", what: "grammar, readability, repeated sentences and paragraph openings" },
  { key: "validation", label: "Meaning", what: "checked against the original; problems repaired or reverted" },
];

function scrollToExcerpt(excerpt: string) {
  const probe = excerpt.replace(/…$/, "").trim().slice(0, 40).toLowerCase();
  if (!probe) return;
  const rows = Array.from(document.querySelectorAll<HTMLElement>("[data-row]"));
  const hit = rows.find((r) => r.textContent?.toLowerCase().includes(probe));
  if (hit) {
    hit.scrollIntoView({ block: "center", behavior: "smooth" });
    hit.animate([{ backgroundColor: "oklch(0.93 0.11 98 / 0.7)" }, { backgroundColor: "transparent" }], { duration: 1600 });
  }
}

function FlagItem({ f }: { f: Flag }) {
  const tone = f.severity === "high" ? "text-del" : f.severity === "medium" ? "text-warn" : "text-muted-foreground";
  return (
    <li className="group rounded-md px-2 py-2 hover:bg-foreground/[0.03]">
      <button type="button" className="w-full text-left" onClick={() => scrollToExcerpt(f.revised_excerpt || f.original_excerpt)}>
        <div className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide uppercase">
          {f.source === "pipeline" ? <Info className="size-3 text-muted-foreground" /> : <AlertTriangle className={cn("size-3", tone)} />}
          <span className={tone}>{TYPE_LABEL[f.type] ?? f.type}</span>
          <span className="ml-auto font-normal normal-case text-muted-foreground">{f.source === "ai" ? "AI check" : f.source === "pipeline" ? "Rewrite" : "Automated"}</span>
        </div>
        <p className="mt-1 text-[13px] leading-snug">{f.message}</p>
        {(f.original_excerpt || f.revised_excerpt) && f.source !== "pipeline" && (
          <div className="mt-1.5 space-y-1 font-serif text-[12.5px] leading-snug text-muted-foreground">
            {f.original_excerpt && <p className="line-clamp-2"><span className="font-sans text-[10px] uppercase">Was </span>{f.original_excerpt}</p>}
            {f.revised_excerpt && <p className="line-clamp-2"><span className="font-sans text-[10px] uppercase">Now </span>{f.revised_excerpt}</p>}
          </div>
        )}
      </button>
    </li>
  );
}

function MetricRow({ label, before, after, better, fmt = (v) => String(v), hint }: {
  label: string; before?: number | null; after?: number | null; better?: "up" | "down" | null; fmt?: (v: number) => string; hint?: string;
}) {
  if (before == null || after == null) return null;
  const delta = after - before;
  const improved = better && Math.abs(delta) > 1e-6 ? (better === "up" ? delta > 0 : delta < 0) : null;
  return (
    <tr title={hint}>
      <td className="py-1 pr-2 text-muted-foreground">{label}</td>
      <td className="py-1 text-right tabular-nums">{fmt(before)}</td>
      <td className="px-1 text-muted-foreground"><ArrowRight className="size-3" /></td>
      <td className={cn("py-1 text-right font-medium tabular-nums", improved === true && "text-[oklch(0.45_0.1_150)]", improved === false && "text-warn")}>{fmt(after)}</td>
    </tr>
  );
}

export function ReviewPanel({ version }: { version: Version }) {
  const setFlags = useHumanizer((s) => s.setFlags);
  const [checking, setChecking] = useState(false);
  const text = currentText(version);
  const r = version.result;
  const stale = version.flagsFor !== text;
  const flags = version.flags;
  const real = flags.filter((f) => f.source !== "pipeline");
  const notes = flags.filter((f) => f.source === "pipeline");
  const repairedN = notes.filter((f) => f.type === "repaired").length;
  const keptN = notes.length - repairedN;

  const recheck = async () => {
    setChecking(true);
    try {
      const res = await humanizer.validate(version.original, text, r?.context.key_terms ?? []);
      setFlags(res.flags, text);
      toast.success(res.flags.length ? `${res.flags.length} item(s) to review` : "No problems found by the automated checks");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const before: Metrics | undefined = r?.profile_before.metrics;
  const after: Metrics | undefined = r?.metrics_after;
  const resolved = r ? r.profile_before.issues.filter((i) => !r.issues_after.some((a) => a.id === i.id)) : [];
  const remaining = r ? r.profile_before.issues.filter((i) => r.issues_after.some((a) => a.id === i.id)) : [];

  return (
    <aside className="space-y-4" aria-label="Review">
      <section className="hw-card p-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-foreground/70" />
          <h3 className="text-[14px] font-medium">Meaning check</h3>
          <Button size="xs" variant="ghost" className="ml-auto" onClick={recheck} disabled={checking}>
            {checking ? <Loader2 className="animate-spin" /> : <RefreshCw />} Re-check
          </Button>
        </div>
        {stale && (
          <p className="mt-2 rounded bg-warn-bg/70 px-2 py-1.5 text-[12px] leading-snug">
            You&apos;ve changed the text since this check ran. Re-check to compare the current version with your original (automated checks only).
          </p>
        )}
        {real.length === 0 ? (
          <p className="mt-2 flex gap-2 text-[13px] leading-snug text-muted-foreground">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[oklch(0.5_0.1_150)]" />
            <span>
              {r && !stale ? r.validation.statement : "No problems found by the automated checks. They catch common problems but can't prove the meaning is identical."}
            </span>
          </p>
        ) : (
          <>
            <p className="mt-2 text-[13px] text-muted-foreground">{real.length} possible change{real.length === 1 ? "" : "s"} in meaning. Click one to find it.</p>
            <ul className="mt-1 -mx-2 max-h-[340px] divide-y divide-dashed divide-foreground/10 overflow-y-auto">
              {real.map((f, i) => <FlagItem key={f.id ?? i} f={f} />)}
            </ul>
          </>
        )}
        {r?.validation.ai_note && !stale && <p className="mt-2 text-[12px] text-muted-foreground">{r.validation.ai_note}</p>}
        {notes.length > 0 && (
          <details className="mt-2 text-[12.5px]">
            <summary className="cursor-pointer text-muted-foreground">
              {[repairedN && `${repairedN} passage${repairedN === 1 ? "" : "s"} repaired after the check`, keptN && `${keptN} kept as written`].filter(Boolean).join(" · ")}
            </summary>
            <ul className="mt-1 -mx-2">{notes.map((f, i) => <FlagItem key={i} f={f} />)}</ul>
          </details>
        )}
      </section>

      {r && before && after && (
        <section className="hw-card p-4">
          <h3 className="text-[14px] font-medium">Writing analysis</h3>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">Measured on the text as first rewritten.</p>
          <table className="mt-2 w-full text-[12.5px]">
            <tbody>
              <MetricRow label="Avg. sentence length" before={before.avg_sentence_length} after={after.avg_sentence_length} fmt={(v) => `${v.toFixed(0)} words`} />
              <MetricRow label="Sentence variety" before={before.sentence_length_variation} after={after.sentence_length_variation} better="up"
                fmt={(v) => v.toFixed(2)} hint="Spread of sentence lengths relative to the average. Higher means a less monotonous rhythm." />
              <MetricRow label="Stock transitions" before={before.transition_rate} after={after.transition_rate} better="down" fmt={(v) => `${Math.round(v * 100)}%`}
                hint="Share of sentences that open with furthermore, moreover, additionally…" />
              <MetricRow label="Reading ease" before={before.flesch_reading_ease} after={after.flesch_reading_ease} better="up" fmt={(v) => v.toFixed(0)}
                hint="Flesch reading ease: higher is easier to read." />
              <MetricRow label="Formality" before={before.formality} after={after.formality} fmt={(v) => `${v.toFixed(0)}/100`} />
              <MetricRow label="Words" before={before.words} after={after.words} fmt={(v) => v.toLocaleString()} />
            </tbody>
          </table>
          {(resolved.length > 0 || remaining.length > 0) && (
            <ul className="mt-3 space-y-1 text-[12.5px]">
              {resolved.map((i) => (
                <li key={i.id} className="flex gap-1.5 text-muted-foreground"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-[oklch(0.5_0.1_150)]" /><span className="line-through decoration-foreground/30">{i.label}</span></li>
              ))}
              {remaining.map((i) => (
                <li key={i.id} className="flex gap-1.5"><span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-warn" />{i.label}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {r?.stage_report && <StageReportCard version={version} />}

      {r && (
        <section className="hw-card p-4 text-[12.5px]">
          <h3 className="text-[14px] font-medium">What the model understood</h3>
          <dl className="mt-2 space-y-1">
            {r.context.document_type && <div className="flex gap-2"><dt className="w-20 shrink-0 text-muted-foreground">Type</dt><dd>{r.context.document_type}</dd></div>}
            {r.context.audience && <div className="flex gap-2"><dt className="w-20 shrink-0 text-muted-foreground">Audience</dt><dd>{r.context.audience}</dd></div>}
            {r.context.tone && <div className="flex gap-2"><dt className="w-20 shrink-0 text-muted-foreground">Tone</dt><dd>{r.context.tone}</dd></div>}
            {r.context.purpose && <div className="flex gap-2"><dt className="w-20 shrink-0 text-muted-foreground">Purpose</dt><dd>{r.context.purpose}</dd></div>}
          </dl>
          {!!r.context.key_terms?.length && (
            <div className="mt-2">
              <p className="text-muted-foreground">Kept exactly</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {r.context.key_terms.slice(0, 16).map((t) => <span key={t} className="rounded bg-muted px-1.5 py-0.5 text-[11.5px]">{t}</span>)}
              </div>
            </div>
          )}
          <MeaningMap r={r} />
          <p className="mt-3 text-[11.5px] text-muted-foreground">
            {r.model} · {r.sections} section{r.sections === 1 ? "" : "s"} · {Math.round(r.duration_s)} s
            {r.context.source === "automatic" ? " · context from automatic analysis" : ""}
          </p>
        </section>
      )}
    </aside>
  );
}

function StageReportCard({ version }: { version: Version }) {
  const r = version.result!;
  const rep = r.stage_report;
  const u = r.usage;
  const blocksWith = (stage: string, status: string) => r.blocks.filter((b) => b.trail?.some((t) => t.stage === stage && t.status === status)).length;
  return (
    <section className="hw-card p-4 text-[12.5px]">
      <h3 className="text-[14px] font-medium">What each stage did</h3>
      <p className="mt-0.5 text-[11.5px] text-muted-foreground">
        Every stage checks each paragraph and only sends the ones with problems to the model. An edit is kept only if it passes the safety checks.
      </p>
      <ul className="mt-2 space-y-1.5">
        {STAGE_ROWS.map(({ key, label, what }) => {
          const s = rep[key];
          if (!s) return null;
          let line: string;
          if (key === "structure") line = `${s.applied ?? 0} of ${s.blocks ?? 0} paragraph(s) rewritten`;
          else if (key === "validation") {
            const bits = [`${s.changed ?? 0} changed paragraph(s) checked`];
            if (s.repair_attempted) bits.push(`${s.repaired ?? 0} of ${s.repair_attempted} repaired`);
            if (s.reverted) bits.push(`${s.reverted} kept as written`);
            line = bits.join(" · ");
          } else {
            const auto = key === "grammar" ? blocksWith("grammar", "auto") : 0;
            line = !s.flagged && !auto && !s.flow_fixed ? "Nothing needed fixing"
              : [s.flagged ? `${s.applied ?? 0} of ${s.flagged} flagged paragraph(s) improved` : "",
                s.rejected ? `${s.rejected} edit(s) rejected` : "", auto ? `${auto} rule-based fix(es)` : "",
                s.flow_fixed ? `${s.flow_fixed} opening(s) smoothed` : ""].filter(Boolean).join(" · ");
          }
          return (
            <li key={key} className="flex gap-2" title={what}>
              <span className="w-[86px] shrink-0 text-muted-foreground">{label}</span>
              <span className="min-w-0">
                {line}
                {!!s.examples?.length && key !== "structure" && key !== "validation" && (
                  <details className="text-[11.5px] text-muted-foreground">
                    <summary className="cursor-pointer">What it looked for</summary>
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-4">{s.examples.slice(0, 4).map((e, i) => <li key={i}>{e}</li>)}</ul>
                  </details>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {u && (
        <p className="mt-3 text-[11.5px] leading-snug text-muted-foreground" title={Object.entries(u.by_stage).map(([k, v]) => `${k}: ${v.calls} call(s), ${v.prompt_tokens + v.output_tokens} tokens`).join("\n")}>
          {u.calls} model call{u.calls === 1 ? "" : "s"} for the whole run · {(u.prompt_tokens + u.output_tokens).toLocaleString()} tokens
          ({u.output_tokens.toLocaleString()} written) · {Object.keys(u.models).join(", ")}
        </p>
      )}
    </section>
  );
}

function MeaningMap({ r }: { r: NonNullable<Version["result"]> }) {
  const map = r.context.semantic_map ?? {};
  const led = r.context.ledger ?? {};
  const ids = Object.keys(map).filter((k) => map[k].points.length);
  const quals = Object.values(led).reduce((n, l) => n + l.qualifiers.length + l.negations.length, 0);
  if (!ids.length && !quals) return null;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-muted-foreground">
        {ids.length ? `${ids.reduce((n, k) => n + map[k].points.length, 0)} point(s) mapped` : "Meaning ledger"}
        {quals ? ` · ${quals} qualifier(s) and negation(s) kept` : ""}
      </summary>
      <ol className="mt-1 space-y-1.5">
        {Object.keys(led).map((k) => (
          <li key={k} className="rounded bg-foreground/[0.03] px-2 py-1">
            {map[k]?.role && <p className="text-[11px] text-muted-foreground italic">{map[k].role}</p>}
            {map[k]?.points?.map((p, i) => <p key={i}>• {p}</p>)}
            {(led[k].qualifiers.length > 0 || led[k].negations.length > 0) && (
              <p className="text-[11px] text-muted-foreground">Kept: {[...led[k].qualifiers, ...led[k].negations].slice(0, 5).map((q) => `“${q}”`).join(", ")}</p>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
