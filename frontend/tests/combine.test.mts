import { test } from "node:test";
import assert from "node:assert/strict";
import { combineBlocks, type CombineBlock } from "../lib/combine.ts";

const blocks: CombineBlock[] = [
  { id: 0, rewritable: false, original: "Title", prefix: "## ", sep: "\n\n" },
  { id: 1, rewritable: true, original: "First original.", prefix: "", sep: "\n\n" },
  { id: 2, rewritable: true, original: "Second original.", prefix: "- ", sep: "\n" },
];
const variants = [
  { 1: "First balanced.", 2: "Second balanced." },
  { 1: "First concise.", 2: "Second concise." },
] as Record<number, string>[];

test("picks per paragraph, keeps headings and markers exactly", () => {
  const out = combineBlocks(blocks, (v, id) => variants[v][id], (id) => (id === 1 ? { from: 1 } : { from: 0 }));
  assert.equal(out, "## Title\n\nFirst concise.\n\n- Second balanced.\n");
});

test("original and edited choices", () => {
  const out = combineBlocks(blocks, (v, id) => variants[v][id], (id) => (id === 1 ? { from: "original" } : { from: 1, edited: "Mine." }));
  assert.equal(out, "## Title\n\nFirst original.\n\n- Mine.\n");
});

test("missing variant text falls back to the original", () => {
  const out = combineBlocks(blocks, () => undefined, () => ({ from: 0 }));
  assert.equal(out, "## Title\n\nFirst original.\n\n- Second original.\n");
});
