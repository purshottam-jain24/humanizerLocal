"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { AlertTriangle, ArrowRight, ExternalLink, Info, Quote, ShieldCheck } from "lucide-react";
import { TopBar } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { NativeSelect, Segmented } from "@/components/ui/segmented";
import { api, type AnalysisResponse } from "@/lib/api";
import { displayText, label, MODULE_LABELS } from "@/lib/text";
import type { DocumentStructure, Finding } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AiDetectionSection } from "@/components/detection/ai-detection-report";
import { cachedAiReport } from "@/components/detection/ai-detection";
import { BACK } from "@/components/motion/transitions";

type Section = "overview" | "ai_detection" | "writing" | "similarity" | "ai_patterns" | "readability" | "academic" | "formatting";
const SECTIONS: Section[] = ["overview", "ai_detection", "writing", "similarity", "ai_patterns", "readability", "academic", "formatting"];

export function QualityCenter({ docId, initialSection }: { docId: string; initialSection?: string }) {
  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [structure, setStructure] = useState<DocumentStructure | null>(null);
  const [section, setSection] = useState<Section>(SECTIONS.includes(initialSection as Section) ? (initialSection as Section) : "overview");
  const [error, setError] = useState<string | null>(null);
  // Arriving from the review screen, the AI section can draw from the report it already had.
  const [cachedAi] = useState(() => cachedAiReport(docId));
  const [switched, setSwitched] = useState(false);

  useEffect(() => {
    Promise.all([api.analysis(docId), api.findings(docId, { status: "", limit: "10000" }), api.structure(docId)])
      .then(([a, f, s]) => { setData(a); setFindings(f); setStructure(s); })
      .catch((e) => setError(e.message));
  }, [docId]);

  const pIndex = useMemo(() => {
    const m = new Map<string, number>();
    structure?.paragraphs.forEach((p, i) => m.set(p.id, i + 1));
    return m;
  }, [structure]);

  const open = findings.filter((f) => f.status === "open");
  const byModule = (m: string) => open.filter((f) => f.module === m);

  return (
    <div className="min-h-screen">
      <TopBar>
        <div className="flex min-w-0 items-center gap-3 overflow-hidden text-sm">
          <Link href="/" transitionTypes={BACK} className="hidden text-muted-foreground hover:text-foreground sm:inline">Documents</Link>
          <span className="hidden text-muted-foreground/50 sm:inline">/</span>
          <span className="truncate font-medium">{structure?.document.filename ?? "…"}</span>
          <span className="hidden text-muted-foreground/50 sm:inline">/</span>
          <span className="hidden sm:inline">Full report</span>
        </div>
        <div className="ml-4 shrink-0">
          <Button size="sm" render={<Link href={`/documents/${docId}`} transitionTypes={BACK} />}>Back to review <ArrowRight /></Button>
        </div>
      </TopBar>
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-4 py-6 sm:px-6 md:grid-cols-[200px_1fr] md:gap-8 md:py-8">
        <nav className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1 text-sm md:sticky md:top-20 md:mx-0 md:block md:h-fit md:space-y-0.5 md:overflow-visible md:px-0 md:pb-0" aria-label="Report sections">
          {SECTIONS.map((s) => (
            <button key={s} onClick={() => { setSwitched(true); setSection(s); }}
              className={cn("flex shrink-0 items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-left whitespace-nowrap transition-colors hover:bg-secondary md:w-full", section === s && "bg-secondary font-medium")}>
              {s === "overview" ? "Review overview" : MODULE_LABELS[s]}
              {s === "ai_detection" ? (
                data?.reports.ai_detection?.available && data.reports.ai_detection.ai_percent != null
                  ? <span className="font-mono text-xs text-brand">{data.reports.ai_detection.ai_percent}%</span> : null
              ) : s !== "overview" && <span className="font-mono text-xs text-muted-foreground">{byModule(s).length}</span>}
            </button>
          ))}
        </nav>
        <main className="min-w-0">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {section === "ai_detection" && (data || cachedAi) && (
            // One instance whether it starts from the cached report or the loaded one, so nothing replays.
            <motion.div key="ai_detection" initial={switched ? { opacity: 0, y: 6 } : false} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
              <AiDetectionSection docId={docId} initial={cachedAi ?? data?.reports.ai_detection ?? null} pIndex={pIndex} arrived={!!cachedAi && !switched} />
            </motion.div>
          )}
          {!data && !error && !(section === "ai_detection" && cachedAi) && <div className="h-64 animate-pulse rounded-xl bg-muted" />}
          {data && structure && section !== "ai_detection" && (
            <motion.div key={section} initial={switched ? { opacity: 0, y: 6 } : false} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
              {section === "overview" && <Overview data={data} structure={structure} findings={findings} />}
              {section === "writing" && <FindingList title="Writing quality" intro="Grammar, spelling, punctuation, wording and flow. Rule-based checks run offline; AI review findings (if enabled) are marked." items={byModule("writing")} pIndex={pIndex} docId={docId} />}
              {section === "similarity" && <Similarity data={data} items={byModule("similarity")} pIndex={pIndex} docId={docId} />}
              {section === "ai_patterns" && <AiPatterns data={data} items={byModule("ai_patterns")} pIndex={pIndex} docId={docId} />}
              {section === "readability" && <Readability data={data} items={byModule("readability")} pIndex={pIndex} docId={docId} />}
              {section === "academic" && <FindingList title="Academic completeness" intro="Citations, references, unsupported claims and undefined terms. Anything that would need a new source is marked for verification — HumanWrite never invents references or evidence." items={byModule("academic")} pIndex={pIndex} docId={docId} header={<AcademicSummary data={data} />} />}
              {section === "formatting" && <FindingList title="Formatting & structure" intro="Heading hierarchy, manual formatting and layout habits that affect navigation and consistency." items={byModule("formatting")} pIndex={pIndex} docId={docId} />}
            </motion.div>
          )}
        </main>
      </div>
    </div>
  );
}

function H({ children, sub }: { children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="font-display text-[34px] leading-tight tracking-tight">{children}</h1>
      {sub && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{sub}</p>}
    </div>
  );
}

function Overview({ data, structure, findings }: { data: AnalysisResponse; structure: DocumentStructure; findings: Finding[] }) {
  const sum = structure.summary;
  const count = (m: string, cats?: string[]) => findings.filter((f) => f.status === "open" && f.module === m && (!cats || cats.includes(f.category))).length;
  const sugg = (st: string) => data.suggestion_counts.filter((s) => s.status === st).reduce((a, b) => a + b.n, 0);
  const tiles: [string, number, string?][] = [
    ["Paragraphs", sum.paragraphs],
    ["Words", sum.words],
    ["Grammar & spelling", count("writing", ["grammar", "spelling", "punctuation"])],
    ["Clarity & style", count("writing", ["clarity", "awkward_wording", "wordiness", "redundancy", "filler", "complexity", "tone", "flow", "transition", "vague_statement"])],
    ["Repetition", count("writing", ["repetition", "terminology"])],
    ["Similarity matches", count("similarity")],
    ["Writing-pattern notes", count("ai_patterns")],
    ["Citation issues", count("academic")],
    ["Formatting warnings", count("formatting")],
    ["Accepted", sugg("accepted"), "text-add"],
    ["Rejected", sugg("rejected")],
    ["Pending review", sugg("pending"), "text-brand"],
  ];
  return (
    <>
      <H sub="Each area is reported separately. There is no combined “quality score”: similarity, AI detection and writing quality measure different things.">Review overview</H>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {tiles.map(([k, v, tone]) => (
          <div key={k} className="hw-card p-4">
            <p className={cn("font-mono text-2xl tabular-nums", tone)}>{v.toLocaleString()}</p>
            <p className="mt-1 text-xs text-muted-foreground">{k}</p>
          </div>
        ))}
      </div>
      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 text-sm">
        <Card title="Document structure">
          <ul className="grid grid-cols-2 gap-y-1 text-muted-foreground">
            <li>Pages: <b className="text-foreground">{sum.pages}</b> {sum.page_source === "estimated" && <span className="text-xs">(estimated)</span>}</li>
            <li>Headings: <b className="text-foreground">{sum.headings}</b></li>
            <li>Tables: <b className="text-foreground">{sum.tables}</b></li>
            <li>Images: <b className="text-foreground">{sum.images}</b></li>
            <li>Sections: <b className="text-foreground">{sum.sections}</b></li>
            <li>Footnotes: <b className="text-foreground">{sum.footnotes}</b></li>
            <li>Hyperlinks: <b className="text-foreground">{sum.hyperlinks}</b></li>
            <li>References: <b className="text-foreground">{sum.references}</b></li>
          </ul>
        </Card>
        <Card title="Notes">
          <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
            {(structure.warnings.length ? structure.warnings : ["No extraction warnings."]).map((w) => <li key={w}>{w}</li>)}
            {data.stages?.filter((s) => s.detail && (s.key === "paragraphs" || s.key === "ocr")).map((s) => <li key={s.key}>{s.label}: {s.detail}</li>)}
          </ul>
        </Card>
      </div>
    </>
  );
}

function Card({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("hw-card p-4", className)}>
      <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h2>
      {children}
    </section>
  );
}

function FindingList({ title, intro, items, pIndex, docId, header }: { title: string; intro: string; items: Finding[]; pIndex: Map<string, number>; docId: string; header?: React.ReactNode }) {
  const [cat, setCat] = useState("all");
  const [prio, setPrio] = useState<"all" | "high" | "medium" | "low">("all");
  const cats = [...new Set(items.map((f) => f.category))];
  const list = items.filter((f) => (cat === "all" || f.category === cat) && (prio === "all" || f.priority === prio));
  return (
    <>
      <H sub={intro}>{title}</H>
      {header}
      <div className="mb-3 flex items-center gap-2">
        <NativeSelect value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">
          <option value="all">All categories ({items.length})</option>
          {cats.map((c) => <option key={c} value={c}>{label(c)} ({items.filter((f) => f.category === c).length})</option>)}
        </NativeSelect>
        <Segmented ariaLabel="Priority" value={prio} onChange={setPrio} options={[{ value: "all", label: "All" }, { value: "high", label: "High" }, { value: "medium", label: "Medium" }, { value: "low", label: "Low" }]} />
      </div>
      {list.length === 0 ? <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">Nothing to report here.</p> : (
        <ul className="divide-y hw-card">
          {list.slice(0, 500).map((f) => <FindingRow key={f.id} f={f} pIndex={pIndex} docId={docId} />)}
        </ul>
      )}
    </>
  );
}

function FindingRow({ f, pIndex, docId }: { f: Finding; pIndex: Map<string, number>; docId: string }) {
  return (
    <li className="flex gap-3 px-4 py-3 text-sm">
      <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", f.priority === "high" ? "bg-del" : f.priority === "medium" ? "bg-warn" : "bg-muted-foreground/40")} />
      <div className="min-w-0 flex-1">
        <p>
          <span className="font-medium">{label(f.category)}</span>
          {f.source === "llm" && <span className="ml-1.5 rounded bg-brand-soft px-1 text-[10px] text-brand">AI review</span>}
          {f.source === "statistical" && <span className="ml-1.5 rounded bg-secondary px-1 text-[10px]">statistical</span>}
          {f.data.needs_verification && <span className="ml-1.5 rounded bg-warn-bg px-1 text-[10px] text-warn">verify</span>}
        </p>
        {f.passage && <p className="mt-0.5 truncate font-serif text-muted-foreground">“{displayText(f.passage)}”</p>}
        <p className="mt-0.5 text-foreground/80">{f.explanation}</p>
        {f.correction !== null && <p className="mt-0.5 text-xs text-muted-foreground">Suggested: <span className="font-serif text-add">{f.correction || "(remove)"}</span></p>}
      </div>
      {f.paragraph_id && (
        <Link href={`/documents/${docId}?p=${f.paragraph_id}`} className="shrink-0 self-center font-mono text-xs text-brand hover:underline">¶{pIndex.get(f.paragraph_id)}</Link>
      )}
    </li>
  );
}

function Similarity({ data, items, pIndex, docId }: { data: AnalysisResponse; items: Finding[]; pIndex: Map<string, number>; docId: string }) {
  const sim = data.reports.similarity;
  const groups: [string, string][] = [["exact", "Exact matches"], ["near_verbatim", "Near-verbatim matches"], ["similar_phrasing", "Similar phrasing"], ["semantic", "Semantic similarity"], ["internal", "Internal document duplication"], ["quoted", "Properly quoted passages"]];
  return (
    <>
      <H sub="Similarity is evidence to review, not proof of plagiarism. Matches may be properly cited, common phrases, or your own earlier work.">Similarity &amp; plagiarism</H>
      <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 text-sm">
        <Card title="What was checked">
          <ul className="space-y-1 text-muted-foreground">
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-add" />
              {sim?.local?.checked ? <>Local reference corpus: {sim.local.corpus?.sources} source(s), {sim.local.corpus?.words.toLocaleString()} words. Methods: {sim.local.methods?.join(", ")}.</> : sim?.local?.reason}
            </li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-add" />Internal duplication within this document.</li>
            <li className="flex gap-2"><Info className="mt-0.5 size-3.5 shrink-0" />
              {sim?.external?.checked ? <>External: {sim.external.providers?.join("; ")} — {sim.external.queries_sent} short queries sent.</> : sim?.external?.reason ?? "External sources not checked."}
            </li>
            {sim?.semantic_note && <li className="flex gap-2"><Info className="mt-0.5 size-3.5 shrink-0" />{sim.semantic_note}</li>}
          </ul>
        </Card>
        <Card title="Coverage limits">
          <p className="text-muted-foreground">
            {sim?.external?.coverage_note ?? "HumanWrite compares only against sources you imported (and, if enabled, public scholarly metadata). It has not checked the entire internet, and it does not include Turnitin, iThenticate or other commercial databases."}
          </p>
          <Link href="/corpus" className="mt-2 inline-flex items-center gap-1 text-xs text-brand hover:underline">Manage reference corpus <ArrowRight className="size-3" /></Link>
        </Card>
      </div>
      {groups.map(([cat, title]) => {
        const g = items.filter((f) => f.category === cat);
        if (!g.length) return null;
        return (
          <section key={cat} className="mb-6">
            <h2 className="mb-2 text-sm font-medium">{title} <span className="font-mono text-xs text-muted-foreground">{g.length}</span></h2>
            <ul className="space-y-3">
              {g.map((f) => (
                <li key={f.id} className="hw-card p-4 text-sm">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="mb-1 text-[10px] tracking-wider text-muted-foreground uppercase">Your text {f.paragraph_id && <Link className="font-mono text-brand normal-case" href={`/documents/${docId}?p=${f.paragraph_id}`}>¶{pIndex.get(f.paragraph_id)}</Link>}</p>
                      <p className="font-serif">{f.passage ? displayText(f.passage) : "—"}</p>
                    </div>
                    <div>
                      <p className="mb-1 text-[10px] tracking-wider text-muted-foreground uppercase">{cat === "internal" ? "Earlier in this document" : "Matching passage"}</p>
                      <p className="font-serif text-muted-foreground"><Quote className="mr-1 inline size-3" />{f.data.matching_passage}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
                    <span>{f.explanation}</span>
                    {f.data.source?.title && <span>Source: <b className="font-medium text-foreground">{f.data.source.title}</b>{f.data.source.kind ? ` (${f.data.source.kind})` : ""}</span>}
                    {f.data.source?.url && <a href={f.data.source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-brand hover:underline">Open source <ExternalLink className="size-3" /></a>}
                    {f.data.longest_run ? <span>Longest overlap: {f.data.longest_run} words</span> : null}
                    {f.data.citation_status && <span className={f.data.citation_status === "citation_nearby" ? "text-add" : "text-warn"}>{f.data.citation_status === "citation_nearby" ? "Citation detected nearby" : "No citation detected"}</span>}
                  </div>
                  {f.data.recommendations?.length ? (
                    <ul className="mt-2 list-disc pl-4 text-xs text-foreground/80">{f.data.recommendations.map((r) => <li key={r}>{r}</li>)}</ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {items.length === 0 && <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No similarity matches in the sources that were checked.</p>}
      <p className="mt-4 text-xs text-muted-foreground">HumanWrite does not rewrite matched passages to hide similarity. Cite, quote, or explain the idea in your own analysis.</p>
    </>
  );
}

function AiPatterns({ data, items, pIndex, docId }: { data: AnalysisResponse; items: Finding[]; pIndex: Map<string, number>; docId: string }) {
  const r = data.reports.ai_patterns;
  if (!r) return null;
  return (
    <>
      <H sub="Repetitive, predictable or formulaic habits worth a second look, whoever wrote the text. For an AI-generated estimate, see AI detection.">Writing patterns</H>
      <div className="mb-5 flex items-start gap-3 rounded-xl border bg-warn-bg/60 p-4 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
        <ul className="space-y-0.5 text-[oklch(0.4_0.08_70)]">{r.disclaimer.map((d) => <li key={d}>{d}</li>)}</ul>
      </div>
      <div className="mb-5 hw-card p-5">
        <p className="text-xs tracking-wide text-muted-foreground uppercase">Overall observation</p>
        <p className="mt-1 text-lg font-medium">{r.level}</p>
        <p className="text-xs text-muted-foreground">{r.words_analyzed.toLocaleString()} words analysed · method: {r.method} signals (no model used)</p>
      </div>
      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {r.signals.map((s) => (
          <div key={s.id} className={cn("hw-card p-4 text-sm", s.observed && "border-warn/50")}>
            <div className="flex items-center justify-between">
              <p className="font-medium">{s.label}</p>
              <span className={cn("rounded-full px-2 py-0.5 text-[10px]", s.observed ? "bg-warn-bg text-warn" : "bg-secondary text-muted-foreground")}>{s.observed ? "worth reviewing" : "typical"}</span>
            </div>
            <p className="mt-1 text-muted-foreground">{s.explanation}</p>
            <p className="mt-1 font-mono text-[11px] text-muted-foreground">{s.detail}</p>
          </div>
        ))}
      </div>
      <FindingList title="Specific observations" intro="Each observation describes the pattern itself, with a way to make the writing clearer, more specific or better supported." items={items} pIndex={pIndex} docId={docId} />
    </>
  );
}

function Readability({ data, items, pIndex, docId }: { data: AnalysisResponse; items: Finding[]; pIndex: Map<string, number>; docId: string }) {
  const o = data.reports.readability?.overall as Record<string, number | string> & { sentence_length_histogram?: { label: string; count: number }[] } | undefined;
  const hist = o?.sentence_length_histogram ?? [];
  const max = Math.max(1, ...hist.map((h) => h.count));
  return (
    <>
      <H sub="Standard readability formulas. They measure sentence and word length, not quality — specialist writing is expected to score as harder.">Readability</H>
      {o?.words ? (
        <div className="mb-6 grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="grid grid-cols-2 gap-3">
            {[["Flesch reading ease", o.flesch_reading_ease, String(o.band)], ["Flesch–Kincaid grade", o.flesch_kincaid_grade], ["Avg sentence", `${o.avg_sentence_length} words`], ["Avg paragraph", `${o.avg_paragraph_length} words`], ["Longest sentence", `${o.longest_sentence} words`], ["Passive constructions", o.passive_constructions]].map(([k, v, sub]) => (
              <div key={k as string} className="hw-card p-4">
                <p className="font-mono text-xl tabular-nums">{v as string}</p>
                <p className="mt-1 text-xs text-muted-foreground">{k as string}{sub ? ` · ${sub}` : ""}</p>
              </div>
            ))}
          </div>
          <Card title="Sentence length distribution">
            <div className="flex h-40 items-end gap-2">
              {hist.map((h) => (
                <div key={h.label} className="flex flex-1 flex-col items-center gap-1">
                  <span className="font-mono text-[10px] text-muted-foreground">{h.count}</span>
                  <motion.div initial={{ height: 0 }} animate={{ height: `${(h.count / max) * 100}%` }} className="w-full rounded-t bg-chart-1/70" />
                  <span className="text-[10px] text-muted-foreground">{h.label}</span>
                </div>
              ))}
            </div>
            <p className="mt-2 text-center text-[10px] text-muted-foreground">words per sentence</p>
          </Card>
        </div>
      ) : <p className="mb-6 text-sm text-muted-foreground">Not enough body text to measure.</p>}
      <FindingList title="Paragraph observations" intro="Long or very dense paragraphs." items={items} pIndex={pIndex} docId={docId} />
    </>
  );
}

function AcademicSummary({ data }: { data: AnalysisResponse }) {
  const a = data.reports.academic;
  if (!a) return null;
  return (
    <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4 text-sm">
      {[["In-text citations", a.in_text_citations], ["Reference entries", a.reference_entries], ["Footnote citations", a.footnote_citations], ["Unsupported claims", a.unsupported_claims]].map(([k, v]) => (
        <div key={k as string} className="hw-card p-4">
          <p className="font-mono text-xl tabular-nums">{String(v ?? 0)}</p>
          <p className="mt-1 text-xs text-muted-foreground">{k as string}</p>
        </div>
      ))}
      {a.citation_styles && Object.keys(a.citation_styles).length > 0 && (
        <p className="col-span-4 text-xs text-muted-foreground">Citation styles detected: {Object.entries(a.citation_styles).map(([k, v]) => `${k} (${v})`).join(", ")}</p>
      )}
    </div>
  );
}
