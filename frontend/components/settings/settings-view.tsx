"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Cpu, Download, Gauge, Loader2, PlugZap, ShieldCheck, Trash2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { PageShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { NativeSelect, Segmented } from "@/components/ui/segmented";
import { api, postSSE } from "@/lib/api";
import { useSystem } from "@/hooks/use-system";
import { formatBytes } from "@/lib/text";
import type { Settings } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DetectorSetupDialog, InstallProgress, useDetector } from "@/components/detection/ai-detection";
import { ScanSearch } from "lucide-react";

type Patch = Partial<{ [K in keyof Settings]: Partial<Settings[K]> }>;

export function SettingsView() {
  const { system, error, refresh } = useSystem(8000);
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editable copy once system info arrives
    if (system && !settings) setSettings(system.settings);
  }, [system, settings]);

  const save = async (patch: Patch) => {
    try {
      const s = await api.updateSettings(patch);
      setSettings(s);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <PageShell className="max-w-4xl">
      <h1 className="font-display text-[44px] leading-none tracking-tight">Settings</h1>
      <p className="mt-3 text-muted-foreground">Everything runs on your computer. The AI assistant is optional and private.</p>
      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
      {system && settings && (
        <div className="mt-8 space-y-8">
          <LocalAI settings={settings} save={save} />
          <AiDetectionSettings settings={settings} save={save} />
          <PrivacySummary on={settings.privacy.external_sources} />
          <details className="group hw-card">
            <summary className="flex cursor-pointer list-none items-center justify-between px-6 py-4 text-base font-medium">
              Advanced settings
              <span className="text-sm font-normal text-muted-foreground group-open:hidden">Writing style, checks, privacy and storage</span>
            </summary>
            <div className="space-y-8 border-t p-6">
          <Section title="Writing preferences" icon={<Gauge className="size-4" />}>
            <Field label="Default editing intensity" hint="Light: grammar and spelling only · Balanced: clarity and flow · Extensive: restructuring, meaning preserved">
              <Segmented ariaLabel="Intensity" value={settings.writing.intensity} onChange={(v) => save({ writing: { intensity: v } })}
                options={[{ value: "light", label: "Light" }, { value: "balanced", label: "Balanced" }, { value: "extensive", label: "Extensive" }]} />
            </Field>
            <Field label="Preferred tone">
              <NativeSelect value={settings.writing.tone} onChange={(e) => save({ writing: { tone: e.target.value } })}>
                <option value="academic">Academic</option>
                <option value="formal">Formal</option>
                <option value="neutral">Neutral</option>
                <option value="conversational">Conversational</option>
              </NativeSelect>
            </Field>
            <Field label="Language variety">
              <NativeSelect value={settings.writing.language} onChange={(e) => save({ writing: { language: e.target.value } })}>
                <option value="en-US">English (US)</option>
                <option value="en-GB">English (UK)</option>
                <option value="en-AU">English (Australia)</option>
                <option value="en-IN">English (India)</option>
              </NativeSelect>
            </Field>
            <Field label="Standing instructions" hint="Applied to every AI suggestion, e.g. “Keep British spelling. Prefer active voice.”">
              <Textarea defaultValue={settings.writing.custom_instructions} onBlur={(e) => e.target.value !== settings.writing.custom_instructions && save({ writing: { custom_instructions: e.target.value } })} className="min-h-16" />
            </Field>
          </Section>

          <Section title="Analysis" icon={<Cpu className="size-4" />}>
            <Toggle label="AI paragraph review during processing" hint="Uses the selected local model to look for issues rule-based checks miss. Slower; limited to the first N paragraphs." checked={settings.analysis.llm_review} onChange={(v) => save({ analysis: { llm_review: v } })} />
            <Field label="AI review limit (paragraphs)">
              <Input type="number" min={0} max={2000} defaultValue={settings.analysis.llm_review_max_paragraphs} className="w-28"
                onBlur={(e) => save({ analysis: { llm_review_max_paragraphs: Number(e.target.value) } })} />
            </Field>
            <Toggle label="Dictionary spell check" hint="Words used repeatedly in the document are treated as deliberate terms." checked={settings.analysis.spellcheck} onChange={(v) => save({ analysis: { spellcheck: v } })} />
            <Toggle label="Similarity analysis" hint="Against your local reference corpus and within the document." checked={settings.analysis.similarity} onChange={(v) => save({ analysis: { similarity: v } })} />
            <Toggle label="Semantic similarity" hint="Uses the local embedding model. Similar meaning is not evidence of copying." checked={settings.analysis.semantic_similarity} onChange={(v) => save({ analysis: { semantic_similarity: v } })} />
            <Toggle label="OCR embedded images" hint={system.ocr_available ? "Reads text inside images with Apple's on-device Vision framework. Normal Word text is never OCR'd." : "macOS Vision framework not available."}
              checked={settings.analysis.ocr} disabled={!system.ocr_available} onChange={(v) => save({ analysis: { ocr: v } })} />
            <Toggle label="Scanning animation" hint="Respects your system's reduce-motion setting." checked={settings.document.scan_animation} onChange={(v) => save({ document: { scan_animation: v } })} />
          </Section>

          <Privacy settings={settings} save={save} providers={system.external_providers} storage={system.storage} />
            </div>
          </details>
        </div>
      )}
    </PageShell>
  );
}

function LocalAI({ settings, save }: { settings: Settings; save: (p: Patch) => Promise<void> }) {
  const { system, refresh } = useSystem(8000);
  const [pullTarget, setPullTarget] = useState<string | null>(null);
  const [pulling, setPulling] = useState<{ model: string; status: string; pct: number | null } | null>(null);
  const [custom, setCustom] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<Awaited<ReturnType<typeof api.testModel>> | null>(null);
  if (!system) return null;
  const o = system.ollama;
  const installed = new Set(o.models.map((m) => m.name));
  const has = (n: string) => installed.has(n) || installed.has(`${n}:latest`) || [...installed].some((i) => i.startsWith(n + ":"));
  const writingModels = o.models.filter((m) => !m.is_embedding);
  const embedModels = o.models.filter((m) => m.is_embedding);

  const pull = async (model: string) => {
    setPulling({ model, status: "starting", pct: null });
    try {
      await postSSE("/api/ollama/pull", { model, confirm: true }, (event, data) => {
        if (event === "progress") setPulling({ model, status: data.status, pct: data.total ? Math.round((data.completed ?? 0) / data.total * 100) : null });
        if (event === "error") throw new Error(data.error);
        if (event === "done") toast.success(`${model} downloaded`);
      });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPulling(null);
      refresh();
    }
  };

  return (
    <Section title="AI assistant" icon={<PlugZap className="size-4" />}>
      <div className={cn("flex items-start gap-3 rounded-lg p-3 text-sm", o.available ? "bg-add-bg/60" : "bg-warn-bg")}>
        {o.available ? <CheckCircle2 className="mt-0.5 size-4 text-add" /> : <XCircle className="mt-0.5 size-4 text-warn" />}
        <div className="flex-1">
          <p className="font-medium">
            {!o.available ? "The AI assistant is off" : settings.ollama.model ? `Ready — using ${settings.ollama.model}` : "Almost ready — download a model below"}
          </p>
          <p className="text-xs text-muted-foreground">{o.available ? "Runs privately on this computer through Ollama. Nothing is sent online." : (
            <>To turn it on, install the free Ollama app from <a className="underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">ollama.com</a>, open it, then come back here. Spelling, grammar and style checks work without it.</>
          )}</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => refresh()}>Recheck</Button>
      </div>

      {o.available && (
        <>
          <Field label="Writing model" hint="Used for suggestions, rewrites and AI review.">
            <div className="flex items-center gap-2">
              <NativeSelect value={settings.ollama.model ?? ""} onChange={(e) => { save({ ollama: { model: e.target.value || null } }); setTestResult(null); }} className="min-w-56">
                <option value="">— none —</option>
                {writingModels.map((m) => <option key={m.name} value={m.name}>{m.name} {m.parameter_size ? `(${m.parameter_size}${m.quantization ? `, ${m.quantization}` : ""})` : ""}</option>)}
              </NativeSelect>
              <Button size="sm" variant="outline" disabled={!settings.ollama.model || testing} onClick={async () => {
                setTesting(true);
                setTestResult(await api.testModel(settings.ollama.model!));
                setTesting(false);
                refresh();
              }}>{testing ? <Loader2 className="animate-spin" /> : null} Test model</Button>
            </div>
            {testResult && (
              <p className={cn("mt-1.5 text-xs", testResult.ok ? "text-add" : "text-destructive")}>
                {testResult.ok ? `Replied “${testResult.reply}” · first token ${testResult.first_token_s?.toFixed(2)}s · ${testResult.tokens_per_s ? `${testResult.tokens_per_s.toFixed(1)} tokens/s` : ""} · total ${testResult.total_s?.toFixed(2)}s (first run includes loading)` : testResult.error}
              </p>
            )}
          </Field>
          <details className="group">
            <summary className="cursor-pointer list-none text-sm text-muted-foreground hover:text-foreground">More AI options ›</summary>
            <div className="mt-4 space-y-5">
          <Field label="Embedding model" hint="Used only for semantic similarity against your reference corpus.">
            <NativeSelect value={settings.ollama.embed_model ?? ""} onChange={(e) => save({ ollama: { embed_model: e.target.value || null } })} className="min-w-56">
              <option value="">— none —</option>
              {embedModels.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </NativeSelect>
          </Field>
          <Field label="Temperature" hint="Lower is more conservative.">
            <div className="flex items-center gap-2">
              <input type="range" min={0} max={1} step={0.05} defaultValue={settings.ollama.temperature} onMouseUp={(e) => save({ ollama: { temperature: Number((e.target as HTMLInputElement).value) } })} className="w-40 accent-[var(--brand)]" />
              <span className="font-mono text-xs">{settings.ollama.temperature}</span>
            </div>
          </Field>
          <Field label="Context window / keep-alive">
            <div className="flex gap-2">
              <NativeSelect value={settings.ollama.num_ctx} onChange={(e) => save({ ollama: { num_ctx: Number(e.target.value) } })}>
                {[2048, 4096, 8192].map((n) => <option key={n} value={n}>{n} tokens</option>)}
              </NativeSelect>
              <NativeSelect value={settings.ollama.keep_alive} onChange={(e) => save({ ollama: { keep_alive: e.target.value } })}>
                <option value="0">Unload immediately</option>
                <option value="5m">Keep loaded 5 min</option>
                <option value="30m">Keep loaded 30 min</option>
              </NativeSelect>
              {o.loaded.map((l) => <Button key={l.name} size="sm" variant="ghost" onClick={async () => { await api.unloadModel(l.name); refresh(); }}>Unload {l.name}</Button>)}
            </div>
          </Field>

            </div>
          </details>
          <div>
            <p className="mb-2 text-sm font-medium">Models</p>
            <ul className="divide-y rounded-lg border">
              {system.recommended_models.map((m) => (
                <li key={m.name} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                  <div className="min-w-0 flex-1">
                    <p><span className="font-mono">{m.name}</span> <span className="text-xs text-muted-foreground">· {m.role} · ~{m.size_gb} GB download · ~{m.ram_gb} GB RAM</span></p>
                    <p className="text-xs text-muted-foreground">{m.notes}</p>
                  </div>
                  {has(m.name) ? (
                    <span className="flex items-center gap-1 text-xs text-add"><CheckCircle2 className="size-3.5" /> Installed</span>
                  ) : pulling?.model === m.name ? (
                    <div className="w-40 text-xs">
                      <Progress value={pulling.pct ?? 0} />
                      <p className="mt-1 truncate text-muted-foreground">{pulling.status}{pulling.pct !== null ? ` · ${pulling.pct}%` : ""}</p>
                    </div>
                  ) : (
                    <Button size="sm" variant="outline" disabled={!!pulling} onClick={() => setPullTarget(m.name)}><Download /> Download</Button>
                  )}
                </li>
              ))}
            </ul>
            <div className="mt-2 flex gap-2">
              <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Other model tag, e.g. llama3.2:3b" className="h-8 max-w-64 text-sm" />
              <Button size="sm" variant="outline" disabled={!custom.trim() || !!pulling} onClick={() => setPullTarget(custom.trim())}>Download</Button>
            </div>
            {o.models.length > 0 && (
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-xs text-muted-foreground">Installed models ({o.models.length})</summary>
                <ul className="mt-2 divide-y rounded-lg border">
                  {o.models.map((m) => (
                    <li key={m.name} className="flex items-center gap-3 px-3 py-2">
                      <span className="flex-1 font-mono text-xs">{m.name}</span>
                      <span className="text-xs text-muted-foreground">{formatBytes(m.size_bytes)}</span>
                      <Button size="icon-xs" variant="ghost" aria-label={`Delete ${m.name}`} onClick={async () => {
                        if (!confirm(`Delete ${m.name} from Ollama? It can be downloaded again later.`)) return;
                        await api.deleteModel(m.name);
                        refresh();
                      }}><Trash2 /></Button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </>
      )}

      <AlertDialog open={!!pullTarget} onOpenChange={(v) => !v && setPullTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Download {pullTarget}?</AlertDialogTitle>
            <AlertDialogDescription>
              Ollama will download the model weights from the Ollama registry{system.recommended_models.find((m) => m.name === pullTarget) ? ` (about ${system.recommended_models.find((m) => m.name === pullTarget)!.size_gb} GB)` : ""}. This is the only time HumanWrite triggers a network download; your documents are not sent anywhere.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => { const t = pullTarget!; setPullTarget(null); pull(t); }}>Download</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

function Privacy({ settings, save, providers, storage }: { settings: Settings; save: (p: Patch) => Promise<void>; providers: Record<string, string>; storage: { data_dir: string; bytes: number; documents: number } }) {
  const [confirmClear, setConfirmClear] = useState(false);
  return (
    <Section title="Privacy" icon={<ShieldCheck className="size-4" />}>
      <div className="rounded-lg bg-secondary/60 p-3 text-sm">
        <p className="font-medium">What leaves this device</p>
        <p className="mt-1 text-muted-foreground">
          {settings.privacy.external_sources
            ? "When external source discovery is on, up to 12 short sentences per document are sent as search queries to the providers selected below during similarity analysis. Nothing else is sent."
            : "Nothing. Documents, analysis, suggestions, revisions and AI prompts stay on this Mac. Model downloads happen only when you click Download."}
        </p>
      </div>
      <Toggle label="External source discovery" hint="Off by default. Compares distinctive sentences against public scholarly metadata (titles and abstracts). Does not bypass paywalls and is not a whole-internet check."
        checked={settings.privacy.external_sources} onChange={(v) => save({ privacy: { external_sources: v } })} />
      {settings.privacy.external_sources && (
        <div className="space-y-1.5 pl-1">
          {Object.entries(providers).map(([k, v]) => (
            <label key={k} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="accent-[var(--brand)]" checked={settings.privacy.external_providers.includes(k)}
                onChange={(e) => save({ privacy: { external_providers: e.target.checked ? [...settings.privacy.external_providers, k] : settings.privacy.external_providers.filter((p) => p !== k) } })} />
              {v}
            </label>
          ))}
        </div>
      )}
      <Field label="Local storage">
        <p className="text-sm text-muted-foreground"><span className="font-mono text-xs">{storage.data_dir}</span> · {formatBytes(storage.bytes)} · {storage.documents} document(s)</p>
      </Field>
      <div>
        <Button variant="destructive" onClick={() => setConfirmClear(true)}><Trash2 /> Clear document history</Button>
      </div>
      <AlertDialog open={confirmClear} onOpenChange={setConfirmClear}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete all document data?</AlertDialogTitle>
            <AlertDialogDescription>Removes every stored original, analysis, suggestion, revision and export from HumanWrite&apos;s data folder. Your reference corpus and settings are kept.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={async () => { const r = await api.clearHistory(); toast.success(`Deleted ${r.deleted} document(s)`); }}>Delete everything</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="hw-card p-6">
      <h2 className="mb-5 flex items-center gap-2 text-base font-semibold">{icon}{title}</h2>
      <div className="space-y-5">{children}</div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[220px_1fr] items-start gap-4">
      <div>
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Toggle({ label, hint, checked, onChange, disabled }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-6">
      <div>
        <p className={cn("text-sm font-medium", disabled && "opacity-50")}>{label}</p>
        {hint && <p className="mt-0.5 max-w-xl text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={(v) => onChange(!!v)} disabled={disabled} aria-label={label} />
    </div>
  );
}

function AiDetectionSettings({ settings, save }: { settings: Settings; save: (p: Patch) => Promise<void> }) {
  const { status, progress, refresh } = useDetector();
  const [setup, setSetup] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  useEffect(() => { refresh(); }, [refresh]);
  return (
    <Section title="AI detection" icon={<ScanSearch className="size-4" />}>
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        Shows how much of a document looks AI-generated, passage by passage. Runs on your computer using open detection models.
      </p>
      {!status ? (
        <div className="h-10 animate-pulse rounded-lg bg-muted" />
      ) : status.ready ? (
        <div className="space-y-4">
          <p className="flex items-center gap-2 text-sm"><CheckCircle2 className="size-4 text-add" /> Ready · {status.model} · uses {status.disk_mb} MB of disk space</p>
          {status.upgrade_available && (progress ? <InstallProgress /> : (
            <div className="flex items-center justify-between gap-4 rounded-xl bg-warn-bg/60 p-4">
              <p className="text-sm">
                <span className="font-medium">Upgrade available: a second detection model.</span>{" "}
                <span className="text-muted-foreground">Much better at technical answers and notes, and fewer false alarms. One-time download of about {status.upgrade_mb} MB.</span>
              </p>
              <Button size="sm" onClick={() => setSetup(true)}><Download /> Upgrade</Button>
            </div>
          ))}
          <Toggle label="Check every new document automatically" hint="Adds a few seconds to each scan. You can always run it by hand from the review screen."
            checked={settings.analysis.ai_detection} onChange={(v) => save({ analysis: { ai_detection: v } })} />
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setConfirmRemove(true)}><Trash2 /> Remove detector model</Button>
        </div>
      ) : progress ? (
        <InstallProgress />
      ) : (
        <div className="flex items-center justify-between gap-4 rounded-xl bg-secondary/60 p-4">
          <p className="text-sm">Not set up. One-time download of about {status.download_mb || 2250} MB{!status.libraries_installed ? " plus libraries" : ""}.</p>
          <Button size="sm" onClick={() => setSetup(true)}><Download /> Set up</Button>
        </div>
      )}
      <DetectorSetupDialog open={setup} onOpenChange={setSetup} />
      <AlertDialog open={confirmRemove} onOpenChange={setConfirmRemove}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the AI detector?</AlertDialogTitle>
            <AlertDialogDescription>Deletes the downloaded detector model and frees about {status?.disk_mb ?? 0} MB. Existing results stay in your reports. You can set it up again at any time.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={async () => { await api.removeDetector(); await refresh(); toast.success("Detector removed"); }}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

function PrivacySummary({ on }: { on: boolean }) {
  return (
    <section className="flex items-start gap-3 hw-card p-6">
      <ShieldCheck className="mt-0.5 size-5 text-add" />
      <div>
        <p className="font-medium">{on ? "Mostly private" : "Fully private"}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {on
            ? "Your documents stay on this computer. Because online source search is on, a few short sentences are sent to OpenAlex/Crossref to look for matching publications."
            : "Your documents, suggestions and edits never leave this computer."}
        </p>
      </div>
    </section>
  );
}
