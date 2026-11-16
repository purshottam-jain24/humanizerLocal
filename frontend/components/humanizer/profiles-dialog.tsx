"use client";

import { useRef, useState } from "react";
import { ArrowLeft, FileUp, Loader2, Lock, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { humanizer, type ProfileDraft, type StyleProfile } from "@/lib/humanizer";
import { timeAgo } from "@/lib/text";
import { countWords } from "./input-panel";

type View = { kind: "list" } | { kind: "create" } | { kind: "edit"; profile: StyleProfile };

interface Form {
  name: string;
  description: string;
  traits: string;
  expressions: string[];
  stats: Record<string, unknown>;
  summary: string[];
  sample_words: number;
}

export function ProfilesDialog({
  open, onOpenChange, profiles, onChanged, minWords, uploadAccept,
}: {
  open: boolean; onOpenChange: (o: boolean) => void; profiles: StyleProfile[]; onChanged: (selectId?: string | null) => void; minWords: number; uploadAccept: string;
}) {
  const [view, setView] = useState<View>({ kind: "list" });
  const [samples, setSamples] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reset = () => {
    abortRef.current?.abort();
    setView({ kind: "list" });
    setSamples("");
    setForm(null);
    setAiNote(null);
    setAnalyzing(false);
    setConfirmDelete(null);
  };

  const addFiles = async (files: FileList | null) => {
    for (const f of Array.from(files ?? [])) {
      try {
        const r = await humanizer.extract(f);
        setSamples((s) => (s ? s.trimEnd() + "\n\n" : "") + r.text);
      } catch (e) {
        toast.error(`${f.name}: ${(e as Error).message}`);
      }
    }
  };

  const analyze = async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setAnalyzing(true);
    try {
      const d: ProfileDraft = await humanizer.analyzeProfile(samples, ac.signal);
      setForm({ name: "", description: d.description, traits: d.traits.join("\n"), expressions: d.expressions, stats: d.stats, summary: d.summary, sample_words: d.sample_words });
      setAiNote(d.ai_used ? null : `The AI description isn't available (${d.ai_error ?? "model unavailable"}); the profile uses measured statistics only.`);
      setSamples(""); // samples are no longer needed and are not kept
    } catch (e) {
      if ((e as Error).name !== "AbortError") toast.error((e as Error).message);
    } finally {
      setAnalyzing(false);
    }
  };

  const save = async () => {
    if (!form) return;
    if (!form.name.trim()) return toast.error("Give the profile a name.");
    setSaving(true);
    const traits = form.traits.split("\n").map((t) => t.trim()).filter(Boolean);
    try {
      if (view.kind === "edit") {
        await humanizer.updateProfile(view.profile.id, { name: form.name, description: form.description, traits, expressions: form.expressions });
        toast.success("Profile updated");
        onChanged();
      } else {
        const p = await humanizer.createProfile({ name: form.name, description: form.description, traits, expressions: form.expressions, stats: form.stats, sample_words: form.sample_words });
        toast.success(`“${p.name}” saved and selected`);
        onChanged(p.id);
      }
      reset();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await humanizer.deleteProfile(id);
      toast.success("Profile deleted");
      onChanged(null);
      reset();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const edit = (p: StyleProfile) => {
    setView({ kind: "edit", profile: p });
    setForm({ name: p.name, description: p.description, traits: p.traits.join("\n"), expressions: p.expressions, stats: p.stats, summary: [], sample_words: p.sample_words });
  };

  const sampleWords = countWords(samples);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view.kind !== "list" && (
              <Button size="icon-xs" variant="ghost" aria-label="Back" onClick={reset}><ArrowLeft /></Button>
            )}
            {view.kind === "list" ? "Writing profiles" : view.kind === "create" ? "New writing profile" : "Edit profile"}
          </DialogTitle>
          <DialogDescription className="flex gap-1.5">
            <Lock className="mt-0.5 size-3 shrink-0" />
            Samples are analysed on this computer and then discarded. Only the profile you save is kept, and you can delete it any time.
          </DialogDescription>
        </DialogHeader>

        {view.kind === "list" && (
          <div className="space-y-3">
            {profiles.length === 0 ? (
              <p className="text-[13px] text-muted-foreground">
                A profile captures how you write (sentence rhythm, register, habits) from a few samples of your own writing, so rewrites sound like you.
              </p>
            ) : (
              <ul className="divide-y divide-dashed divide-foreground/10">
                {profiles.map((p) => (
                  <li key={p.id} className="flex items-start gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{p.name}</p>
                      <p className="line-clamp-2 text-[12.5px] text-muted-foreground">{p.description || "No description"}</p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">From {p.sample_words.toLocaleString()} words · updated {timeAgo(p.updated_at)}</p>
                    </div>
                    {confirmDelete === p.id ? (
                      <div className="flex items-center gap-1">
                        <Button size="xs" variant="destructive" onClick={() => remove(p.id)}>Delete</Button>
                        <Button size="xs" variant="ghost" onClick={() => setConfirmDelete(null)}>Keep</Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-0.5">
                        <Button size="icon-sm" variant="ghost" aria-label={`Edit ${p.name}`} onClick={() => edit(p)}><Pencil /></Button>
                        <Button size="icon-sm" variant="ghost" aria-label={`Delete ${p.name}`} onClick={() => setConfirmDelete(p.id)}><Trash2 /></Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <Button onClick={() => setView({ kind: "create" })}><Plus /> New profile from my writing</Button>
          </div>
        )}

        {view.kind === "create" && !form && (
          <div className="space-y-2">
            <Textarea
              value={samples}
              onChange={(e) => setSamples(e.target.value)}
              rows={9}
              aria-label="Writing samples"
              placeholder="Paste a few things you've written yourself: emails, essays, posts. More variety gives a better profile."
              className="min-h-0 font-serif text-[14px] leading-relaxed"
              disabled={analyzing}
            />
            <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <span className="tabular-nums">{sampleWords.toLocaleString()} words{sampleWords < minWords && ` (at least ${minWords})`}</span>
              <Button size="xs" variant="ghost" className="ml-auto" onClick={() => fileRef.current?.click()} disabled={analyzing}><FileUp /> Add files</Button>
              <input ref={fileRef} type="file" multiple accept={uploadAccept} className="hidden" onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </div>
            <div className="flex gap-2">
              <Button onClick={analyze} disabled={analyzing || sampleWords < minWords}>
                {analyzing ? <><Loader2 className="animate-spin" /> Analysing your style…</> : "Analyse my style"}
              </Button>
              {analyzing && <Button variant="ghost" onClick={() => abortRef.current?.abort()}>Cancel</Button>}
            </div>
          </div>
        )}

        {form && (
          <div className="space-y-3">
            {aiNote && <p className="rounded bg-warn-bg/70 px-2 py-1.5 text-[12px]">{aiNote}</p>}
            <label className="grid gap-1 text-[13px]">
              <span className="font-medium">Name</span>
              <Input value={form.name} maxLength={80} autoFocus placeholder="e.g. My blog voice" onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="grid gap-1 text-[13px]">
              <span className="font-medium">Description</span>
              <Textarea value={form.description} maxLength={1500} rows={3} className="min-h-0 text-[13px]" onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </label>
            <label className="grid gap-1 text-[13px]">
              <span className="font-medium">Style traits <span className="font-normal text-muted-foreground">(one per line)</span></span>
              <Textarea value={form.traits} rows={4} className="min-h-0 text-[13px]" onChange={(e) => setForm({ ...form, traits: e.target.value })} />
            </label>
            {form.expressions.length > 0 && (
              <div className="text-[13px]">
                <p className="font-medium">Expressions you use <span className="font-normal text-muted-foreground">(short phrases only; remove any you don&apos;t want)</span></p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {form.expressions.map((x) => (
                    <span key={x} className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[12px]">
                      {x}
                      <button type="button" aria-label={`Remove ${x}`} onClick={() => setForm({ ...form, expressions: form.expressions.filter((e) => e !== x) })}>
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {form.summary.length > 0 && (
              <div className="rounded-md bg-muted/50 p-2.5 text-[12px] text-muted-foreground">
                <p className="mb-1 font-medium text-foreground/80">Measured from {form.sample_words.toLocaleString()} words</p>
                <ul className="list-disc space-y-0.5 pl-4">{form.summary.map((l) => <li key={l}>{l}</li>)}</ul>
              </div>
            )}
            <div className="flex gap-2">
              <Button onClick={save} disabled={saving}>{saving && <Loader2 className="animate-spin" />} Save profile</Button>
              <Button variant="ghost" onClick={reset}>Cancel</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
