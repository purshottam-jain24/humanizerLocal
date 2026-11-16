import { diffArrays, diffSentences, diffWordsWithSpace } from "diff";

/**
 * Document diff engine.
 *
 * 1. Paragraph alignment: LCS over paragraphs (jsdiff `diffArrays`, Myers' algorithm).
 * 2. Within each run of removed/added paragraphs, paragraphs are paired by word similarity
 *    (Dice coefficient) to find *modified* paragraphs; the rest are additions/removals.
 * 3. Unpaired removed/added paragraphs that match each other elsewhere are *moved* (reordered).
 * 4. Modified paragraphs get a word- or sentence-level diff (Myers again), grouped into hunks
 *    that can be accepted or rejected individually. Rejecting rebuilds the text.
 */

export type Granularity = "word" | "sentence";

export interface Hunk {
  id: string;
  removed: string;
  added: string;
}

export type Seg = { kind: "same"; text: string } | { kind: "change"; hunk: Hunk };

export type Row =
  | { kind: "equal"; key: string; a: string; b: string; aSep: string; bSep: string }
  | { kind: "modified"; key: string; a: string; b: string; aSep: string; bSep: string; segs: Seg[]; sentencesChanged: number }
  | { kind: "added"; key: string; b: string; bSep: string; movedFrom?: string }
  | { kind: "removed"; key: string; a: string; aSep: string; movedTo?: string };

export interface ChangeRef {
  id: string; // hunk id or row key
  rowKey: string;
  type: "modified" | "added" | "removed" | "moved";
  removed: string;
  added: string;
}

export interface DocDiff {
  rows: Row[];
  changes: ChangeRef[];
  stats: { paragraphsChanged: number; added: number; removed: number; moved: number; wordsAdded: number; wordsRemoved: number };
}

export interface Para {
  text: string;
  sep: string;
}

/** Split into paragraphs on blank lines, keeping the exact separators so text can be rebuilt. */
export function splitParagraphs(text: string): Para[] {
  const out: Para[] = [];
  const re = /\n[ \t]*\n\s*/g;
  let last = 0;
  let lead = "";
  const m0 = /^\s+/.exec(text);
  if (m0) {
    lead = m0[0];
    last = m0[0].length;
  }
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index < last) continue;
    out.push({ text: text.slice(last, m.index), sep: m[0] });
    last = m.index + m[0].length;
  }
  const tail = text.slice(last);
  const tm = /\s*$/.exec(tail);
  const tailText = tm ? tail.slice(0, tail.length - tm[0].length) : tail;
  if (tailText || !out.length) out.push({ text: tailText, sep: tm ? tm[0] : "" });
  else if (out.length) out[out.length - 1].sep += tail;
  if (lead && out.length) out[0] = { ...out[0], text: lead + out[0].text };
  return out;
}

export function joinParagraphs(ps: Para[]): string {
  return ps.map((p) => p.text + p.sep).join("");
}

const WORD = /[\p{L}\p{N}’'-]+/gu;
function words(s: string): string[] {
  return (s.toLowerCase().match(WORD) ?? []) as string[];
}

/** Dice similarity over word multisets (0–1). */
export function similarity(a: string, b: string): number {
  const wa = words(a);
  const wb = words(b);
  if (!wa.length && !wb.length) return 1;
  if (!wa.length || !wb.length) return 0;
  const counts = new Map<string, number>();
  for (const w of wa) counts.set(w, (counts.get(w) ?? 0) + 1);
  let common = 0;
  for (const w of wb) {
    const c = counts.get(w) ?? 0;
    if (c > 0) {
      common++;
      counts.set(w, c - 1);
    }
  }
  return (2 * common) / (wa.length + wb.length);
}

const PAIR_THRESHOLD = 0.3;
const MOVE_THRESHOLD = 0.85;

function inlineHunks(a: string, b: string, granularity: Granularity, keyPrefix: string): Seg[] {
  const parts = granularity === "sentence" ? diffSentences(a, b) : diffWordsWithSpace(a, b);
  const segs: Seg[] = [];
  let pendingRemoved = "";
  let pendingAdded = "";
  let k = 0;
  const flush = () => {
    if (pendingRemoved || pendingAdded) {
      segs.push({ kind: "change", hunk: { id: `${keyPrefix}h${k++}`, removed: pendingRemoved, added: pendingAdded } });
      pendingRemoved = "";
      pendingAdded = "";
    }
  };
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.added) pendingAdded += p.value;
    else if (p.removed) pendingRemoved += p.value;
    else {
      // A lone space between two changes belongs to the hunk, so "a b → c d" is one change, not two.
      const next = parts[i + 1];
      if ((pendingRemoved || pendingAdded) && next && (next.added || next.removed) && /^\s{1,2}$/.test(p.value)) {
        pendingRemoved += p.value;
        pendingAdded += p.value;
        continue;
      }
      flush();
      segs.push({ kind: "same", text: p.value });
    }
  }
  flush();
  return segs;
}

function sentencesOf(s: string): string[] {
  return (s.match(/[^.!?…]+(?:[.!?…]+["'”’)\]]*|$)/g) ?? []).map((x) => x.trim()).filter(Boolean);
}

export function diffDocuments(original: string, revised: string, granularity: Granularity = "word"): DocDiff {
  const A = splitParagraphs(original);
  const B = splitParagraphs(revised);
  const norm = (s: string) => s.trim();
  const ops = diffArrays(A.map((p) => norm(p.text)), B.map((p) => norm(p.text)));
  const rows: Row[] = [];
  let ai = 0;
  let bi = 0;
  let pendingA: number[] = [];
  let pendingB: number[] = [];
  let rowN = 0;
  const nextKey = () => `r${rowN++}`;

  const flushPending = () => {
    // Pair removed with added paragraphs in order, greedily by best similarity, preserving order.
    const usedB = new Set<number>();
    const pairs = new Map<number, number>();
    let minB = 0;
    for (const x of pendingA) {
      let best = -1;
      let bestScore = PAIR_THRESHOLD;
      for (let j = minB; j < pendingB.length; j++) {
        if (usedB.has(j)) continue;
        const s = similarity(A[x].text, B[pendingB[j]].text);
        if (s > bestScore) {
          bestScore = s;
          best = j;
        }
      }
      if (best >= 0) {
        pairs.set(x, best);
        usedB.add(best);
        minB = best + 1;
      }
    }
    // Emit in revised order, interleaving removals before the next pair.
    let ia = 0;
    for (let j = 0; j < pendingB.length; j++) {
      const pairedA = [...pairs.entries()].find(([, bj]) => bj === j)?.[0];
      if (pairedA !== undefined) {
        while (ia < pendingA.length && pendingA[ia] !== pairedA) {
          if (!pairs.has(pendingA[ia])) rows.push({ kind: "removed", key: nextKey(), a: A[pendingA[ia]].text, aSep: A[pendingA[ia]].sep });
          ia++;
        }
        ia++;
        const a = A[pairedA];
        const b = B[pendingB[j]];
        const key = nextKey();
        const segs = inlineHunks(a.text, b.text, granularity, key);
        const aSet = new Set(sentencesOf(a.text));
        const sentencesChanged = sentencesOf(b.text).filter((s) => !aSet.has(s)).length;
        rows.push({ kind: "modified", key, a: a.text, b: b.text, aSep: a.sep, bSep: b.sep, segs, sentencesChanged });
      } else {
        rows.push({ kind: "added", key: nextKey(), b: B[pendingB[j]].text, bSep: B[pendingB[j]].sep });
      }
    }
    while (ia < pendingA.length) {
      if (!pairs.has(pendingA[ia])) rows.push({ kind: "removed", key: nextKey(), a: A[pendingA[ia]].text, aSep: A[pendingA[ia]].sep });
      ia++;
    }
    pendingA = [];
    pendingB = [];
  };

  for (const op of ops) {
    const n = op.count ?? op.value.length;
    if (op.removed) {
      for (let i = 0; i < n; i++) pendingA.push(ai++);
    } else if (op.added) {
      for (let i = 0; i < n; i++) pendingB.push(bi++);
    } else {
      flushPending();
      for (let i = 0; i < n; i++) {
        rows.push({ kind: "equal", key: nextKey(), a: A[ai].text, b: B[bi].text, aSep: A[ai].sep, bSep: B[bi].sep });
        ai++;
        bi++;
      }
    }
  }
  flushPending();

  // Moved paragraphs: an addition that matches a removal elsewhere.
  const removed = rows.filter((r): r is Extract<Row, { kind: "removed" }> => r.kind === "removed");
  for (const r of rows) {
    if (r.kind !== "added") continue;
    const m = removed.find((x) => !x.movedTo && similarity(x.a, r.b) >= MOVE_THRESHOLD);
    if (m) {
      m.movedTo = r.key;
      r.movedFrom = m.key;
    }
  }

  const changes: ChangeRef[] = [];
  const stats = { paragraphsChanged: 0, added: 0, removed: 0, moved: 0, wordsAdded: 0, wordsRemoved: 0 };
  for (const r of rows) {
    if (r.kind === "modified") {
      stats.paragraphsChanged++;
      for (const s of r.segs) {
        if (s.kind === "change") {
          changes.push({ id: s.hunk.id, rowKey: r.key, type: "modified", removed: s.hunk.removed, added: s.hunk.added });
          stats.wordsAdded += words(s.hunk.added).length;
          stats.wordsRemoved += words(s.hunk.removed).length;
        }
      }
    } else if (r.kind === "added") {
      if (r.movedFrom) stats.moved++;
      else {
        stats.added++;
        stats.wordsAdded += words(r.b).length;
      }
      changes.push({ id: r.key, rowKey: r.key, type: r.movedFrom ? "moved" : "added", removed: "", added: r.b });
    } else if (r.kind === "removed") {
      if (!r.movedTo) {
        stats.removed++;
        stats.wordsRemoved += words(r.a).length;
        changes.push({ id: r.key, rowKey: r.key, type: "removed", removed: r.a, added: "" });
      }
    }
  }
  return { rows, changes, stats };
}

/**
 * Rebuild the revised text with some changes reverted to the original.
 * `reject` holds hunk ids (inside modified paragraphs) and row keys (added / removed / moved paragraphs).
 */
export function rebuild(diff: DocDiff, reject: Set<string>): string {
  const out: Para[] = [];
  const push = (p: Para) => {
    const last = out[out.length - 1];
    if (last && !last.sep.includes("\n")) out[out.length - 1] = { ...last, sep: "\n\n" };
    out.push(p);
  };
  for (const r of diff.rows) {
    if (r.kind === "equal") push({ text: r.b, sep: r.bSep });
    else if (r.kind === "modified") {
      const text = r.segs.map((s) => (s.kind === "same" ? s.text : reject.has(s.hunk.id) ? s.hunk.removed : s.hunk.added)).join("");
      if (text.trim()) push({ text, sep: r.bSep });
    } else if (r.kind === "added") {
      // Rejecting a move puts the paragraph back where it was (handled at the removed row).
      if (r.movedFrom && reject.has(r.key)) continue;
      if (!reject.has(r.key)) push({ text: r.b, sep: r.bSep });
    }
    if (r.kind === "removed") {
      const restore = r.movedTo ? reject.has(r.movedTo) : reject.has(r.key);
      if (restore) push({ text: r.a, sep: r.aSep });
    }
  }
  // Normalise the final separator to match the revised text's ending.
  if (out.length) {
    const lastRevised = [...diff.rows].reverse().find((r) => r.kind !== "removed") as Extract<Row, { bSep: string }> | undefined;
    if (lastRevised) out[out.length - 1] = { ...out[out.length - 1], sep: lastRevised.bSep };
  }
  return joinParagraphs(out);
}

/** Signature of a change, stable across rebuilds — used to remember accepted changes. */
export function changeSignature(c: { removed: string; added: string }): string {
  return `${c.removed}␞${c.added}`;
}
