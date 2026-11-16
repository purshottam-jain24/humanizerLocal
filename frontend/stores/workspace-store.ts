"use client";

import { create } from "zustand";
import { api } from "@/lib/api";
import type { DocumentStructure, Finding, RevisionHistory, Suggestion } from "@/lib/types";

interface WorkspaceState {
  docId: string | null;
  structure: DocumentStructure | null;
  suggestions: Suggestion[];
  findings: Finding[];
  history: RevisionHistory | null;
  selectedPid: string | null;
  selectedSentence: number | null;
  /** where the selection came from — the origin panel doesn't scroll itself */
  selectionSource: "nav" | "preview" | "editor" | null;
  revision: number;
  loading: boolean;
  error: string | null;
  init: (docId: string, initialPid?: string | null) => Promise<void>;
  refresh: (parts?: ("structure" | "suggestions" | "findings" | "history")[]) => Promise<void>;
  select: (pid: string | null, source: WorkspaceState["selectionSource"], sentence?: number | null) => void;
  upsertSuggestion: (s: Suggestion) => void;
}

let initToken: { docId: string } | null = null;
let initInflight = false;

export const useWorkspace = create<WorkspaceState>((set, get) => ({
  docId: null,
  structure: null,
  suggestions: [],
  findings: [],
  history: null,
  selectedPid: null,
  selectedSentence: null,
  selectionSource: null,
  revision: 0,
  loading: true,
  error: null,

  init: async (docId, initialPid) => {
    // De-duplicate concurrent loads of the same document (e.g. React's dev double-mount),
    // so a late duplicate response can't reset the selection.
    if (get().docId === docId && initInflight) return;
    const token = (initToken = { docId });
    initInflight = true;
    set({ docId, loading: true, error: null, structure: null, suggestions: [], findings: [], selectedPid: null });
    try {
      const [structure, suggestions, findings, history] = await Promise.all([
        api.structure(docId), api.suggestions(docId), api.findings(docId), api.revisions(docId),
      ]);
      if (token !== initToken) return;
      const first = structure.paragraphs.find((p) => p.part === "body" && p.word_count > 0 && p.kind !== "title");
      set({ structure, suggestions, findings, history, loading: false, revision: structure.document.revision_head, selectedPid: (initialPid && structure.paragraphs.some((p) => p.id === initialPid) ? initialPid : first?.id) ?? null, selectionSource: initialPid ? "editor" : "nav" });
    } catch (e) {
      if (token === initToken) set({ loading: false, error: (e as Error).message });
    } finally {
      if (token === initToken) initInflight = false;
    }
  },

  refresh: async (parts = ["structure", "suggestions", "findings", "history"]) => {
    const id = get().docId;
    if (!id) return;
    const [structure, suggestions, findings, history] = await Promise.all([
      parts.includes("structure") ? api.structure(id) : Promise.resolve(null),
      parts.includes("suggestions") ? api.suggestions(id) : Promise.resolve(null),
      parts.includes("findings") ? api.findings(id) : Promise.resolve(null),
      parts.includes("history") ? api.revisions(id) : Promise.resolve(null),
    ]);
    set((s) => ({
      structure: structure ?? s.structure,
      suggestions: suggestions ?? s.suggestions,
      findings: findings ?? s.findings,
      history: history ?? s.history,
      revision: structure ? structure.document.revision_head : s.revision,
    }));
  },

  select: (pid, source, sentence = null) => set({ selectedPid: pid, selectionSource: source, selectedSentence: sentence }),

  upsertSuggestion: (sug) =>
    set((s) => {
      const i = s.suggestions.findIndex((x) => x.id === sug.id);
      const next = [...s.suggestions];
      if (i >= 0) next[i] = sug;
      else next.push(sug);
      return { suggestions: next };
    }),
}));
