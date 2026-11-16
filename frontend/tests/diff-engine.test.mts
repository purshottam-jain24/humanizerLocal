// Run with: npm test   (Node ≥ 22.18 strips TypeScript types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { changeSignature, diffDocuments, rebuild, splitParagraphs, joinParagraphs } from "../lib/diff-engine.ts";

const ORIGINAL = `# Title

In today's world, technology plays a crucial role. Furthermore, it has many benefits.

Second paragraph stays the same.

Third paragraph will be removed entirely by the editor.

Last paragraph about results and 42 participants.
`;

const REVISED = `# Title

Technology matters, and it has many benefits.

Second paragraph stays the same.

Last paragraph about results and 42 participants.

A brand new closing paragraph appears here.
`;

test("splitParagraphs round-trips exactly", () => {
  for (const t of [ORIGINAL, REVISED, "", "one", "\n\nlead\n\n\n\ntrail\n\n", "a\n- b\n- c\n\nd"]) {
    assert.equal(joinParagraphs(splitParagraphs(t)), t);
  }
});

test("identical texts produce no changes", () => {
  const d = diffDocuments(ORIGINAL, ORIGINAL);
  assert.equal(d.changes.length, 0);
  assert.ok(d.rows.every((r) => r.kind === "equal"));
  assert.equal(rebuild(d, new Set()), ORIGINAL);
});

test("classifies modified, removed and added paragraphs", () => {
  const d = diffDocuments(ORIGINAL, REVISED);
  const kinds = d.rows.map((r) => r.kind);
  assert.deepEqual(kinds, ["equal", "modified", "equal", "removed", "equal", "added"]);
  assert.equal(d.stats.paragraphsChanged, 1);
  assert.equal(d.stats.removed, 1);
  assert.equal(d.stats.added, 1);
  assert.ok(d.changes.length >= 3);
});

test("accepting everything reproduces the revised text; rejecting everything reproduces the original", () => {
  const d = diffDocuments(ORIGINAL, REVISED);
  assert.equal(rebuild(d, new Set()), REVISED);
  const all = new Set(d.changes.map((c) => c.id));
  assert.equal(rebuild(d, all).trim(), ORIGINAL.trim());
});

test("rejecting a single word hunk only reverts that hunk", () => {
  const a = "The quick brown fox jumps over the lazy dog.";
  const b = "The fast brown fox leaps over the lazy dog.";
  const d = diffDocuments(a, b);
  assert.equal(d.changes.length, 2);
  const first = d.changes[0];
  assert.equal(first.removed.trim(), "quick");
  assert.equal(rebuild(d, new Set([first.id])), "The quick brown fox leaps over the lazy dog.");
});

test("sentence granularity groups whole sentences", () => {
  const a = "One stays. Two changes here. Three stays.";
  const b = "One stays. Two is different now. Three stays.";
  const d = diffDocuments(a, b, "sentence");
  assert.equal(d.changes.length, 1);
  assert.match(d.changes[0].removed, /Two changes here\./);
  assert.equal(rebuild(d, new Set([d.changes[0].id])), a);
});

test("detects reordered (moved) paragraphs and can move them back", () => {
  const a = "Alpha paragraph with several words.\n\nBeta paragraph with other words.\n\nGamma paragraph closes it.";
  const b = "Beta paragraph with other words.\n\nGamma paragraph closes it.\n\nAlpha paragraph with several words.";
  const d = diffDocuments(a, b);
  assert.equal(d.stats.moved, 1);
  const moved = d.changes.find((c) => c.type === "moved");
  assert.ok(moved);
  assert.equal(rebuild(d, new Set([moved!.id])), a);
});

test("change signatures are stable", () => {
  const d1 = diffDocuments("a b c", "a x c");
  const d2 = diffDocuments("a b c", "a x c");
  assert.equal(changeSignature(d1.changes[0]), changeSignature(d2.changes[0]));
});
