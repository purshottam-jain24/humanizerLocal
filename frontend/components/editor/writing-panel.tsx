"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { History, Loader2, Pencil, RotateCcw, Sparkles, Square, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { NativeSelect, Segmented } from "@/components/ui/segmented";
import { api, postSSE } from "@/lib/api";
import { displayText, fromEditable, label, MODULE_LABELS, toEditable } from "@/lib/text";
import type { Finding, Paragraph, Suggestion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSystem } from "@/hooks/use-system";
import { useWorkspace } from "@/stores/workspace-store";
import { DiffView } from "@/components/suggestions/diff-view";
import { SuggestionCard } from "@/components/suggestions/suggestion-card";

const ACTIONS: { value: string; label: string; group: string }[] = [
  { value: "fix_grammar", label: "Fix grammar", group: "Correct" },
  { value: "fix_spelling", label: "Correct spelling", group: "Correct" },
  { value: "improve_punctuation", label: "Improve punctuation", group: "Correct" },
  { value: "improve_clarity", label: "Improve clarity", group: "Improve" },
  { value: "simplify", label: "Simplify wording", group: "Improve" },
  { value: "rephrase", label: "Rephrase", group: "Improve" },
  { value: "rewrite_paragraph", label: "Rewrite paragraph", group: "Improve" },
  { value: "academic_tone", label: "Improve academic tone", group: "Improve" },
  { value: "improve_transitions", label: "Improve transitions", group: "Improve" },
  { value: "remove_repetition", label: "Remove repetition", group: "Tighten" },
  { value: "concise", label: "Make more concise", group: "Tighten" },
  { value: "expand", label: "Expand explanation", group: "Develop" },
  { value: "readability", label: "Improve readability", group: "Develop" },
  { value: "terminology", label: "Terminology consistency", group: "Develop" },
  { value: "preserve_style", label: "Improve, keep my style", group: "Develop" },
  { value: "custom", label: "Custom instruction…", group: "Custom" },
];

interface StreamState {
  genId: string | null;
  text: string;
  status: "idle" | "waiting" | "streaming";
  abort?: AbortController;
  groupId?: string | null;
  thinking?: boolean;
}

export function WritingPanel() {
  const { structure, selectedPid, selectedSentence, suggestions, findings, docId, select, refresh, upsertSuggestion } = useWorkspace();
  const { system } = useSystem();
  const para = structure?.paragraphs.find((p) => p.id === selectedPid) ?? null;
  const [action, setAction] = useState("improve_clarity");
  const [mode, setMode] = useState<"light" | "balanced" | "extensive">("balanced");
  const [custom, setCustom] = useState("");
  const [stream, setStream] = useState<StreamState>({ genId: null, text: "", status: "idle" });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [compare, setCompare] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Sync the default intensity from settings, and reset local editing UI when the selection changes.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (system?.settings.writing.intensity) setMode(system.settings.writing.intensity);
  }, [system?.settings.writing.intensity]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEditing(false);
    setShowHistory(false);
  }, [selectedPid]);

  const paraSuggestions = useMemo(() => suggestions.filter((s) => s.paragraph_id === selectedPid), [suggestions, selectedPid]);
  const paraFindings = useMemo(() => findings.filter((f) => f.paragraph_id === selectedPid), [findings, selectedPid]);
  const groups = useMemo(() => {
    const m = new Map<string, Suggestion[]>();
    for (const s of paraSuggestions) m.set(s.group_id, [...(m.get(s.group_id) ?? []), s]);
    return m;
  }, [paraSuggestions]);
  const ordered = useMemo(() => {
    const rank: Record<string, number> = { pending: 0, incomplete: 1, stale: 2, accepted: 3, rejected: 4, reverted: 5, superseded: 6, failed: 7, generating: 8 };
    return [...paraSuggestions].filter((s) => s.status !== "generating").sort((a, b) => rank[a.status] - rank[b.status] || b.created_at - a.created_at);
  }, [paraSuggestions]);

  if (!structure) return null;
  if (!para) {
    return <div className="grid h-full place-items-center text-sm text-muted-foreground">Select a paragraph to begin.</div>;
  }

  const index = structure.paragraphs.filter((p) => p.part === para.part).findIndex((p) => p.id === para.id) + 1;
  const modelReady = !!system?.ollama.available && !!system.settings.ollama.model;
  const sentenceId = selectedSentence !== null ? `${para.id}.s${selectedSentence}` : null;
  const scope = selectedSentence !== null ? "sentence" : "paragraph";

  const generate = async (opts: { altOf?: Suggestion; findingId?: string; actionOverride?: string } = {}) => {
    if (!docId) return;
    const abort = new AbortController();
    const groupId = opts.altOf?.group_id ?? null;
    setStream({ genId: null, text: "", status: "waiting", abort, groupId });
    const styles = ["clearer", "concise", "minimal", "restructured"];
    const body = {
      paragraph_id: para.id,
      scope: opts.altOf ? (opts.altOf.scope === "sentence" ? "sentence" : "paragraph") : opts.findingId ? "paragraph" : scope,
      sentence_id: opts.altOf ? opts.altOf.sentence_id : sentenceId,
      action: opts.altOf?.action ?? opts.actionOverride ?? action,
      mode,
      custom_instruction: action === "custom" || custom ? custom || null : null,
      finding_id: opts.findingId ?? null,
      alt_style: opts.altOf ? styles[(groups.get(opts.altOf.group_id)?.length ?? 1) % styles.length] : null,
      group_id: groupId,
    };
    let raw = "";
    try {
      await postSSE(`/api/documents/${docId}/suggestions/generate`, body, (event, data) => {
        if (event === "started") setStream((s) => ({ ...s, genId: data.gen_id, status: "streaming" }));
        else if (event === "thinking") setStream((s) => ({ ...s, thinking: true }));
        else if (event === "token") {
          raw += data.text;
          setStream((s) => ({ ...s, text: raw }));
        } else if (event === "completed" || event === "cancelled") {
          upsertSuggestion(data.suggestion);
          if (event === "cancelled") toast("Generation stopped — partial output kept as an incomplete draft");
        } else if (event === "error") {
          if (data.suggestion) upsertSuggestion(data.suggestion);
          toast.error(data.message);
        }
      }, abort.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") toast.error((e as Error).message);
    } finally {
      setStream({ genId: null, text: "", status: "idle" });
      refresh(["suggestions", "structure"]);
    }
  };

  const stop = async () => {
    if (stream.genId) await api.cancelGeneration(stream.genId);
    else stream.abort?.abort();
  };

  const saveManual = async () => {
    if (!docId) return;
    try {
      await api.editParagraph(docId, para.id, fromEditable(draft));
      toast.success("Manual edit applied to the proposed document");
      setEditing(false);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* Paragraph header */}
      <div className="flex items-center gap-2 border-b px-5 py-2.5">
        <span className="font-mono text-xs text-muted-foreground">¶{index}</span>
        <span className="text-sm font-medium">{kindLabel(para)}</span>
        <span className="text-xs text-muted-foreground">
          {para.part !== "body" ? `${para.part} · ` : ""}{para.page ? `page ${para.page} · ` : ""}{para.word_count} words{para.style_name ? ` · ${para.style_name}` : ""}
        </span>
        {para.modified && <span className="rounded-full bg-add-bg px-2 py-0.5 text-[10px] font-medium text-add">Modified</span>}
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => setShowHistory((v) => !v)}><History /> History</Button>
          {para.modified && (
            <Button size="sm" variant="ghost" onClick={async () => { await api.restoreParagraph(docId!, para.id); toast("Restored original text"); refresh(); }}>
              <RotateCcw /> Restore original
            </Button>
          )}
          {!editing && (
            <Button size="sm" variant="outline" onClick={() => { setDraft(toEditable(para.text)); setEditing(true); }}>
              <Pencil /> Edit
            </Button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-5 px-5 py-5">
          {/* Paragraph text */}
          <section className="hw-card p-5">
            {editing ? (
              <div className="space-y-2">
                <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} className="min-h-40 font-serif text-[16px] leading-relaxed" autoFocus
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveManual(); if (e.key === "Escape") setEditing(false); }} />
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={saveManual}>Apply edit</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                  <span className="ml-auto text-xs text-muted-foreground">⌘↵ to apply · ⟦n⟧ markers are footnotes/images/fields and must stay</span>
                </div>
              </div>
            ) : (
              <>
                <ParagraphText para={para} findings={paraFindings} selectedSentence={selectedSentence} onSentence={(i) => select(para.id, "editor", i === selectedSentence ? null : i)} />
                {para.modified && (
                  <details className="mt-4 border-t pt-3 text-sm">
                    <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Changes from the original</summary>
                    <DiffView before={para.original_text} after={para.text} className="mt-2 text-[14px]" />
                  </details>
                )}
              </>
            )}
            {showHistory && <ParagraphHistory pid={para.id} />}
          </section>

          {/* Findings without a direct correction */}
          {paraFindings.length > 0 && (
            <section>
              <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Findings in this paragraph</h3>
              <ul className="space-y-1.5">
                {paraFindings.map((f) => (
                  <li key={f.id} className="flex items-start gap-2.5 rounded-lg border bg-card px-3 py-2 text-sm">
                    <PriorityDot p={f.priority} />
                    <div className="min-w-0 flex-1">
                      <p>
                        <span className="font-medium">{label(f.category)}</span>
                        <span className="text-muted-foreground"> · {MODULE_LABELS[f.module]}{f.source === "llm" ? " · AI review" : ""}</span>
                      </p>
                      {f.passage && <p className="truncate font-serif text-[13px] text-muted-foreground">“{displayText(f.passage)}”</p>}
                      <p className="text-[13px] text-foreground/80">{f.explanation}</p>
                      {f.module === "similarity" && f.data.source && (
                        <p className="mt-0.5 text-xs text-muted-foreground">Source: {f.data.source.title}{f.data.source.url && <> · <a href={f.data.source.url} target="_blank" rel="noreferrer" className="text-brand hover:underline">link</a></>}</p>
                      )}
                    </div>
                    {f.correction === null && f.module === "writing" && modelReady && (
                      <Button size="xs" variant="ghost" onClick={() => generate({ findingId: f.id, actionOverride: "fix_finding" })} disabled={stream.status !== "idle"}>
                        <Wand2 /> Suggest fix
                      </Button>
                    )}
                    <Button size="xs" variant="ghost" className="text-muted-foreground" onClick={async () => { await api.patchFinding(f.id, "dismissed"); refresh(["findings", "structure"]); }}>
                      Dismiss
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* AI generation */}
          <section className="hw-card">
            <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
              <Sparkles className="size-4 text-brand" />
              <span className="text-sm font-medium">Improve with local AI</span>
              <span className="text-xs text-muted-foreground">
                {scope === "sentence" ? `Sentence ${selectedSentence! + 1} selected` : "Whole paragraph"} · {system?.settings.ollama.model ?? "no model"}
              </span>
              <div className="ml-auto">
                <Segmented ariaLabel="Editing intensity" value={mode} onChange={setMode}
                  options={[{ value: "light", label: "Light", title: "Grammar, spelling, punctuation — minimal changes" }, { value: "balanced", label: "Balanced", title: "Clarity, repetition, transitions" }, { value: "extensive", label: "Extensive", title: "Restructure and reorganise, preserving meaning" }]} />
              </div>
            </div>
            <div className="space-y-3 p-4">
              <div className="flex gap-2">
                <NativeSelect value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action" className="flex-1">
                  {["Correct", "Improve", "Tighten", "Develop", "Custom"].map((g) => (
                    <optgroup key={g} label={g}>
                      {ACTIONS.filter((a) => a.group === g).map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                    </optgroup>
                  ))}
                </NativeSelect>
                {stream.status === "idle" ? (
                  <Button onClick={() => generate()} disabled={!modelReady || (action === "custom" && !custom.trim())}>
                    <Wand2 /> Generate
                  </Button>
                ) : (
                  <Button variant="outline" onClick={stop}><Square /> Stop</Button>
                )}
              </div>
              <Textarea value={custom} onChange={(e) => setCustom(e.target.value)} placeholder={action === "custom" ? "Describe what to change, e.g. “make the second sentence more specific”" : "Optional extra instruction"} className="min-h-12 text-sm" />
              {!modelReady && (
                <p className="text-xs text-muted-foreground">
                  {system?.ollama.available ? "Select a local model in Settings → Local AI to enable AI suggestions." : "Ollama is not running. Start it to enable AI suggestions; rule-based corrections below still work."}
                </p>
              )}
              <AnimatePresence>
                {stream.status !== "idle" && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                    <div className="rounded-lg border border-brand/25 bg-brand-soft/50 p-3">
                      <p className="mb-1.5 flex items-center gap-1.5 text-xs text-brand">
                        <Loader2 className="size-3 animate-spin" />
                        {stream.status === "waiting" ? "Loading model and preparing context…" : stream.text ? "Generating locally" : stream.thinking ? "The model is reasoning before it writes…" : "Generating locally"}
                      </p>
                      <p className={cn("min-h-6 whitespace-pre-wrap font-serif text-[15px] leading-relaxed", stream.status === "streaming" && "hw-caret")}>
                        {streamPreview(stream.text)}
                      </p>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </section>

          {/* Suggestions */}
          <section aria-label="Suggestions">
            <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Suggestions {ordered.length > 0 && <span className="normal-case">({ordered.filter((s) => s.status === "pending").length} pending)</span>}
            </h3>
            {ordered.length === 0 && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No suggestions for this paragraph yet.</p>}
            <div className="space-y-3">
              <AnimatePresence initial={false}>
                {ordered.map((s) => (
                  <SuggestionCard key={s.id} s={s} alternatives={groups.get(s.group_id)?.filter((x) => x.proposed_text).length ?? 1}
                    onAlternative={(x) => generate({ altOf: x })} onCompare={setCompare} />
                ))}
              </AnimatePresence>
            </div>
          </section>
        </div>
      </div>

      <Dialog open={!!compare} onOpenChange={(o) => !o && setCompare(null)}>
        <DialogContent className="sm:max-w-5xl">
          <DialogHeader><DialogTitle>Compare alternatives</DialogTitle></DialogHeader>
          {compare && <CompareAlternatives items={groups.get(compare) ?? []} onDone={() => setCompare(null)} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Render the visible part of a streamed <revision>…</revision> reply as it arrives. */
function streamPreview(raw: string) {
  const m = raw.match(/<revision>([\s\S]*?)(<\/revision>|$)/);
  const text = m ? m[1] : raw.replace(/^<[^>]*$/, "");
  return text.replace(/⟦\d+⟧/g, "◆");
}

function kindLabel(p: Paragraph) {
  if (p.kind === "heading") return `Heading ${p.heading_level}`;
  if (p.kind === "title") return "Title";
  if (p.kind === "list_item") return "List item";
  if (p.table) return "Table cell";
  return { paragraph: "Paragraph", caption: "Caption", toc: "Table of contents", empty: "Empty paragraph", object: "Object", heading: "Heading", title: "Title", list_item: "List item" }[p.kind];
}

function PriorityDot({ p }: { p: string }) {
  return <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", p === "high" ? "bg-del" : p === "medium" ? "bg-warn" : "bg-muted-foreground/40")} aria-label={`${p} priority`} />;
}

function ParagraphText({ para, findings, selectedSentence, onSentence }: { para: Paragraph; findings: Finding[]; selectedSentence: number | null; onSentence: (i: number) => void }) {
  // Finding highlights only apply when the text is the original the findings were computed on.
  const marks = para.modified ? [] : findings.filter((f) => f.start !== null && f.end !== null && f.end > f.start && f.end - f.start < para.text.length * 0.9);
  const text = para.text;
  const sentences = para.sentences.length ? para.sentences : [{ id: "s0", start: 0, end: text.length }];
  return (
    <div className={cn("font-serif text-[16px] leading-[1.75]", (para.kind === "heading" || para.kind === "title") && "text-xl font-semibold")}>
      {sentences.map((s, i) => {
        const seg = text.slice(s.start, s.end);
        const inner = highlight(seg, s.start, marks);
        return (
          <span key={s.id}>
            <span
              role="button"
              tabIndex={0}
              onClick={() => onSentence(i)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSentence(i))}
              className={cn("cursor-pointer rounded-sm transition-colors hover:bg-brand-soft/60", selectedSentence === i && "bg-brand-soft ring-1 ring-brand/30")}
              title="Select sentence for sentence-level AI actions"
            >
              {inner}
            </span>
            {text.slice(s.end, sentences[i + 1]?.start ?? s.end)}
          </span>
        );
      })}
    </div>
  );
}

function highlight(seg: string, offset: number, marks: Finding[]) {
  const local = marks
    .map((f) => ({ f, s: Math.max(0, f.start! - offset), e: Math.min(seg.length, f.end! - offset) }))
    .filter((m) => m.e > m.s)
    .sort((a, b) => a.s - b.s);
  const out: React.ReactNode[] = [];
  let cur = 0;
  for (const m of local) {
    if (m.s < cur) continue;
    if (m.s > cur) out.push(displayText(seg.slice(cur, m.s)));
    out.push(
      <mark key={m.f.id} title={`${label(m.f.category)}: ${m.f.explanation}`}
        className={cn("rounded-[2px] bg-transparent text-inherit underline decoration-2 underline-offset-[5px]",
          m.f.module === "similarity" ? "bg-warn-bg decoration-warn" : m.f.priority === "high" ? "decoration-del/70" : m.f.priority === "medium" ? "decoration-warn/80" : "decoration-brand/40")}>
        {displayText(seg.slice(m.s, m.e))}
      </mark>,
    );
    cur = m.e;
  }
  if (cur < seg.length) out.push(displayText(seg.slice(cur)));
  return out;
}

function CompareAlternatives({ items, onDone }: { items: Suggestion[]; onDone: () => void }) {
  const docId = useWorkspace((s) => s.docId)!;
  const refresh = useWorkspace((s) => s.refresh);
  const usable = items.filter((s) => s.proposed_text);
  return (
    <div className="grid max-h-[70vh] gap-3 overflow-y-auto" style={{ gridTemplateColumns: `repeat(${Math.min(3, usable.length)}, minmax(0,1fr))` }}>
      {usable.map((s, i) => (
        <div key={s.id} className="flex flex-col rounded-lg border p-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">Alternative {i + 1} · {s.status}</p>
          <DiffView before={s.original_text} after={s.proposed_text} className="flex-1 text-[14px]" />
          {s.meaning && <p className={cn("mt-2 text-xs", s.meaning.level === "likely_preserved" ? "text-add" : "text-warn")}>{s.meaning.label}</p>}
          {s.status === "pending" && (
            <Button size="sm" className="mt-3" onClick={async () => {
              try { await api.accept(docId, s.id); toast.success("Alternative applied"); await refresh(); onDone(); }
              catch (e) { toast.error((e as Error).message); }
            }}>Use this version</Button>
          )}
        </div>
      ))}
    </div>
  );
}

function ParagraphHistory({ pid }: { pid: string }) {
  const docId = useWorkspace((s) => s.docId)!;
  const revision = useWorkspace((s) => s.revision);
  const [ops, setOps] = useState<Awaited<ReturnType<typeof api.paragraphHistory>> | null>(null);
  useEffect(() => {
    let live = true;
    api.paragraphHistory(docId, pid).then((o) => live && setOps(o));
    return () => { live = false; };
  }, [docId, pid, revision]);
  if (!ops) return null;
  return (
    <div className="mt-4 border-t pt-3">
      <h4 className="mb-2 text-xs font-medium text-muted-foreground">Revision history for this paragraph</h4>
      {ops.length === 0 ? <p className="text-xs text-muted-foreground">No changes yet — this is the original text.</p> : (
        <ol className="space-y-2">
          {ops.map((o) => (
            <li key={o.seq} className={cn("text-xs", o.state !== "applied" && "opacity-50")}>
              <span className="font-mono">#{o.seq}</span> {o.op_type === "manual" ? "Manual edit" : o.op_type === "restore" ? "Restored original" : o.note ?? o.op_type}
              <span className="text-muted-foreground"> · {new Date(o.created_at * 1000).toLocaleString()} · {o.state}</span>
              {o.before_text !== null && o.after_text !== null && <DiffView before={o.before_text} after={o.after_text} className="mt-1 text-[13px]" />}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
