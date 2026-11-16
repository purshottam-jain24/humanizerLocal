/**
 * Combine variations of one humanizer run paragraph by paragraph.
 *
 * Every variation rewrites the same blocks, so block ids, prefixes ("## ", "- ") and separators are shared;
 * a combination is rebuilt exactly from them. Blocks that were not rewritable (headings, quotations, code)
 * always keep their original text.
 */
export interface CombineBlock { id: number; rewritable: boolean; original: string; prefix: string; sep: string }
export type Choice = { from: number | "original"; edited?: string };

export function combineBlocks(
  blocks: CombineBlock[],
  variantText: (variant: number, blockId: number) => string | undefined,
  choice: (blockId: number) => Choice,
): string {
  return blocks.map((b) => {
    if (!b.rewritable) return b.prefix + b.original + b.sep;
    const c = choice(b.id);
    const text = c.edited ?? (c.from === "original" ? b.original : variantText(c.from, b.id) ?? b.original);
    return b.prefix + text + b.sep;
  }).join("");
}
