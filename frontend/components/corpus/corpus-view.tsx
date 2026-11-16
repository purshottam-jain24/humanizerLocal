"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, FolderInput, Loader2, Sparkles, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { PageShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { api, postSSE } from "@/lib/api";
import { useSystem } from "@/hooks/use-system";
import { timeAgo } from "@/lib/text";
import type { CorpusSource } from "@/lib/types";

export function CorpusView() {
  const [sources, setSources] = useState<CorpusSource[] | null>(null);
  const [stats, setStats] = useState<{ sources: number; chunks: number; words: number; embedded_chunks: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [folder, setFolder] = useState("");
  const [embedding, setEmbedding] = useState<{ done: number; total: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { system } = useSystem();

  const load = async () => {
    const r = await api.corpus();
    setSources(r.sources);
    setStats(r.stats);
  };
  // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
  useEffect(() => { load().catch((e) => toast.error(e.message)); }, []);

  const report = (r: { imported: CorpusSource[]; failed: { file: string; error: string }[] }) => {
    if (r.imported.length) toast.success(`Indexed ${r.imported.length} source(s)`);
    for (const f of r.failed.slice(0, 5)) toast.error(`${f.file}: ${f.error}`);
    load();
  };

  const embedModel = system?.settings.ollama.embed_model;

  return (
    <PageShell>
      <h1 className="font-display text-[44px] leading-none tracking-tight">Compare sources</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        Sources your documents are compared against for similarity. Files are copied into HumanWrite&apos;s local data folder and indexed with fingerprints (winnowing), TF-IDF and, optionally, local embeddings. Nothing is uploaded anywhere.
      </p>

      <div className="mt-6 grid gap-3 md:grid-cols-2">
        <div className="hw-card p-5">
          <p className="text-sm font-medium">Import files</p>
          <p className="text-xs text-muted-foreground">PDF (with a text layer), DOCX, TXT or Markdown.</p>
          <Button className="mt-3" disabled={busy} onClick={() => input.current?.click()}>
            {busy ? <Loader2 className="animate-spin" /> : <Upload />} Choose files
          </Button>
          <input ref={input} type="file" multiple accept=".pdf,.docx,.txt,.md" className="sr-only" onChange={async (e) => {
            const files = [...(e.target.files ?? [])];
            e.target.value = "";
            if (!files.length) return;
            setBusy(true);
            try { report(await api.corpusUpload(files)); } catch (err) { toast.error((err as Error).message); } finally { setBusy(false); }
          }} />
        </div>
        <div className="hw-card p-5">
          <p className="text-sm font-medium">Import a folder</p>
          <p className="text-xs text-muted-foreground">A folder on this Mac; supported files in sub-folders are included.</p>
          <div className="mt-3 flex gap-2">
            <Input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="~/Documents/Sources" className="h-8" />
            <Button disabled={busy || !folder.trim()} onClick={async () => {
              setBusy(true);
              try { report(await api.corpusFolder(folder.trim())); } catch (err) { toast.error((err as Error).message); } finally { setBusy(false); }
            }}><FolderInput /> Import</Button>
          </div>
        </div>
      </div>

      {stats && (
        <div className="mt-6 flex flex-wrap items-center gap-6 hw-card px-5 py-4 text-sm">
          <span><b className="font-mono">{stats.sources}</b> sources</span>
          <span><b className="font-mono">{stats.words.toLocaleString()}</b> words</span>
          <span><b className="font-mono">{stats.chunks}</b> passages</span>
          <span><b className="font-mono">{stats.embedded_chunks}</b> embedded</span>
          <div className="ml-auto flex items-center gap-3">
            {embedding && <div className="w-40"><Progress value={(embedding.done / Math.max(1, embedding.total)) * 100} /></div>}
            <Button size="sm" variant="outline" disabled={!embedModel || !!embedding || !stats.chunks} title={embedModel ? undefined : "Select an embedding model in Settings"}
              onClick={async () => {
                setEmbedding({ done: 0, total: 1 });
                try {
                  await postSSE("/api/corpus/embed", {}, (ev, d) => {
                    if (ev === "progress") setEmbedding(d);
                    if (ev === "done") toast.success(`Embedded ${d.embedded} passage(s) with ${d.model}`);
                    if (ev === "error") toast.error(d.error);
                  });
                } finally { setEmbedding(null); load(); }
              }}>
              <Sparkles /> {embedModel ? `Embed with ${embedModel}` : "No embedding model"}
            </Button>
          </div>
        </div>
      )}

      <ul className="mt-4 divide-y hw-card">
        {sources?.map((s) => (
          <li key={s.id} className="flex items-center gap-3 px-4 py-3 text-sm">
            <FileText className="size-4 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{s.title}</p>
              <p className="text-xs text-muted-foreground">{s.kind.toUpperCase()} · {s.word_count.toLocaleString()} words · {s.chunk_count} passages · added {timeAgo(s.added_at)}{s.embedded ? ` · embedded (${s.embed_model})` : ""}</p>
            </div>
            <Button size="icon-sm" variant="ghost" aria-label={`Remove ${s.title}`} onClick={async () => { await api.corpusDelete(s.id); load(); }}><Trash2 /></Button>
          </li>
        ))}
        {sources?.length === 0 && <li className="p-8 text-center text-sm text-muted-foreground">No sources yet. Similarity analysis will only check for internal duplication until you add some.</li>}
      </ul>
    </PageShell>
  );
}
