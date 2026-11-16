"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, ArrowLeft, Columns3, Lock, PencilLine, X } from "lucide-react";
import { PageShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { humanizer, type HumanizeOptions, type StyleProfile } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { useActiveVersion, useHumanizer } from "@/stores/humanizer-store";
import { CompareVariations } from "./compare-variations";
import { ConfigBar } from "./config-bar";
import { InputPanel } from "./input-panel";
import { OutputPanel } from "./output-panel";
import { ProcessingPanel } from "./processing-panel";
import { ProfilesDialog } from "./profiles-dialog";
import { ReviewPanel } from "./review-panel";

const EASE = [0.22, 1, 0.36, 1] as const;

export function HumanizerWorkspace() {
  const [options, setOptions] = useState<HumanizeOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<StyleProfile[]>([]);
  const [profilesOpen, setProfilesOpen] = useState(false);
  const { processing, versions, view, setView, setActive, removeVersion, start, input, settings, setSettings, startManual } = useHumanizer();
  const version = useActiveVersion();

  const loadProfiles = useCallback(async (selectId?: string | null) => {
    try {
      const list = await humanizer.profiles();
      setProfiles(list);
      const current = useHumanizer.getState().settings.profile_id;
      if (selectId !== undefined) setSettings({ profile_id: selectId });
      else if (current && !list.some((p) => p.id === current)) setSettings({ profile_id: null });
    } catch {
      /* the options error below explains an offline backend */
    }
  }, [setSettings]);

  useEffect(() => {
    let live = true;
    humanizer.options()
      .then((o) => live && setOptions(o))
      .catch((e: Error) => live && setOptionsError(e.message));
    humanizer.profiles().then((l) => live && setProfiles(l)).catch(() => {});
    return () => { live = false; };
  }, []);

  // Text handed over from a document ("Humanize it") runs as soon as the model is known to be ready.
  const autoStart = useHumanizer((s) => s.autoStart);
  useEffect(() => {
    if (!autoStart || (!options && !optionsError)) return;
    useHumanizer.getState().clearAutoStart();
    if (options?.provider.model_ready) start();
  }, [autoStart, options, optionsError, start]);

  // ⌘↵ / Ctrl+↵ runs the humanizer from anywhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        start();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [start]);

  const running = processing?.status === "running" || processing?.status === "cancelling";
  const provider = options?.provider;
  const canRun = !!provider?.model_ready && !running && input.length <= (options?.limits.max_chars ?? Infinity);
  const showReview = view === "review" && !!version && !processing;
  const groupVersions = version?.group ? versions.filter((v) => v.group === version.group) : [];
  const comparable = groupVersions.filter((v) => v.result && v.approach !== "combined").length >= 2;
  const showCompare = view === "compare" && comparable && !processing;

  return (
    <PageShell className="max-w-[1320px] px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-end gap-x-6 gap-y-2">
        <div>
          <p className="font-hand text-[19px] leading-none text-pencil -rotate-1">same meaning, better sentences</p>
          <h1 className="mt-1 font-display text-[38px] leading-none tracking-tight sm:text-[44px]">Humanizer</h1>
        </div>
        <p className="max-w-xl pb-1 text-[13.5px] leading-relaxed text-muted-foreground">
          Rewrites stiff, repetitive or generic writing into clear, natural prose for your audience. It keeps your facts, citations and quotations,
          checks the result for changes in meaning, and lets you accept or reject every edit.
        </p>
      </div>

      <ProviderNotice options={options} error={optionsError} speed={settings.speed ?? "fast"} />

      <div className="mb-4">
        <ConfigBar options={options} profiles={profiles} onManageProfiles={() => setProfilesOpen(true)} canRun={canRun} />
      </div>

      {versions.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[12px]">
          {(showReview || showCompare) && (
            <Button size="sm" variant="ghost" onClick={() => setView("compose")} className="mr-1"><ArrowLeft /> Edit original</Button>
          )}
          {showReview && comparable && (
            <Button size="sm" variant="outline" className="mr-1 bg-paper" onClick={() => setView("compare")}><Columns3 /> Compare &amp; combine</Button>
          )}
          <span className="text-muted-foreground">Versions</span>
          {versions.map((v) => (
            <span key={v.id} className={cn("group inline-flex items-center rounded-full border transition-colors",
              v.id === version?.id && showReview ? "border-foreground/30 bg-paper" : "border-transparent bg-foreground/[0.04] hover:bg-foreground/[0.07]")}>
              <button type="button" className="py-0.5 pr-1 pl-2.5" onClick={() => setActive(v.id)} disabled={running}>
                {v.label}
                <span className="ml-1 text-muted-foreground">{new Date(v.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              </button>
              <button type="button" aria-label={`Discard ${v.label}`} className="rounded-full p-1 text-muted-foreground opacity-0 group-hover:opacity-100 focus:opacity-100"
                onClick={() => removeVersion(v.id)}>
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <AnimatePresence mode="wait" initial={false}>
        {showCompare ? (
          <motion.div key={`compare-${version!.group}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.35, ease: EASE }}>
            <CompareVariations versions={groupVersions} onClose={() => setView("review")} />
          </motion.div>
        ) : showReview && version ? (
          <motion.div key={`review-${version.id}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.35, ease: EASE }} className="grid items-start gap-5 [&>*]:min-w-0 lg:grid-cols-[minmax(0,1fr)_330px]">
            <OutputPanel version={version} options={options} />
            <ReviewPanel version={version} />
          </motion.div>
        ) : (
          <motion.div key="compose" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.35, ease: EASE }} className="grid items-start gap-5 [&>*]:min-w-0 lg:grid-cols-2">
            <InputPanel options={options} disabled={running} canRun={canRun} />
            {processing ? (
              <ProcessingPanel />
            ) : (
              <EmptyOutput hasInput={!!input.trim()} onManual={startManual} hasVersions={versions.length > 0}
                onBack={() => version && setActive(version.id)} intensity={settings.intensity} />
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <ProfilesDialog open={profilesOpen} onOpenChange={setProfilesOpen} profiles={profiles} onChanged={loadProfiles}
        minWords={options?.limits.min_profile_words ?? 150} uploadAccept={(options?.upload.extensions ?? [".txt", ".md", ".docx", ".pdf"]).join(",")} />
    </PageShell>
  );
}

function ProviderNotice({ options, error, speed }: { options: HumanizeOptions | null; error: string | null; speed: "fast" | "thorough" }) {
  if (error)
    return (
      <p className="mb-4 flex items-center gap-2 rounded-md bg-del-bg px-3 py-2 text-[13px] text-del"><AlertTriangle className="size-4" />{error}</p>
    );
  if (!options) return <div className="mb-4 h-[34px]" />;
  const p = options.provider;
  if (!p.available || !p.model_ready)
    return (
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md bg-warn-bg px-3 py-2 text-[13px]">
        <AlertTriangle className="size-4 text-warn" />
        <span>
          {!p.available ? "The AI assistant (Ollama) isn't running, so rewriting is unavailable." : `The selected model${p.model ? ` “${p.model}”` : ""} isn't installed.`}
          {" "}You can still paste text and edit it by hand.
        </span>
        <Button size="xs" variant="outline" className="ml-auto bg-paper" render={<Link href="/settings" />}>Open Settings</Button>
      </div>
    );
  return (
    <p className={cn("mb-4 text-[12.5px] leading-relaxed", p.local ? "text-muted-foreground" : "rounded-md bg-warn-bg px-3 py-2 text-foreground")}>
      {p.local ? <Lock className="mr-1.5 inline size-3.5 -translate-y-px" /> : <AlertTriangle className="mr-1.5 inline size-3.5 -translate-y-px text-warn" />}
      {options.privacy} Model: <span className="font-medium text-foreground/80">{speed === "fast" ? options.speed.fast_model ?? `${p.model} (reasoning off)` : p.model}</span>.
      {speed === "fast" && !options.speed.fast_model && (
        <> For faster, cleaner results download <span className="font-medium text-foreground/80">{options.speed.recommended_fast_model}</span> in{" "}
          <Link href="/settings" className="underline">Settings</Link>.</>
      )}
    </p>
  );
}

function EmptyOutput({ hasInput, onManual, hasVersions, onBack, intensity }: {
  hasInput: boolean; onManual: () => void; hasVersions: boolean; onBack: () => void; intensity: string;
}) {
  return (
    <section aria-label="Result" className="relative flex min-h-[420px] flex-col items-center justify-center rounded-[3px] border border-dashed border-foreground/15 px-8 text-center lg:min-h-[560px]">
      <p className="font-display text-[26px] leading-tight text-foreground/60">Your improved text appears here</p>
      <p className="mt-2 max-w-sm text-[13px] leading-relaxed text-muted-foreground">
        Choose a mode and intensity ({intensity} now), then press <span className="font-medium text-foreground/80">Humanize</span> or ⌘↵.
        Nothing replaces your original; every run becomes a version you can compare.
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {hasVersions && <Button variant="outline" size="sm" className="bg-paper" onClick={onBack}>Back to the last result</Button>}
        <Button variant="ghost" size="sm" onClick={onManual} disabled={!hasInput}><PencilLine /> Edit sentence by sentence instead</Button>
      </div>
    </section>
  );
}
