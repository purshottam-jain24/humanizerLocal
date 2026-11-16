import { ViewTransition } from "react";

/**
 * Navigation motion, built on React's <ViewTransition> + the browser View Transitions API.
 *
 * - Pages slide in the direction of travel: "nav-forward" (deeper into a document) and
 *   "nav-back" (towards My documents). Untyped navigations (browser back, top nav) crossfade.
 * - The document itself is one continuous object: the sheet you drop a file on, the page being
 *   scanned and the page in the review screen share the name `doc-<id>`, so the browser morphs
 *   one into the next instead of cutting between screens.
 * - The header never moves (see `.app-header` in globals.css).
 *
 * Browsers without the API simply navigate without animation. Reduced motion is respected in CSS.
 */

export const FORWARD = ["nav-forward"];
export const BACK = ["nav-back"];

export const docName = (id: string) => `doc-${id}`;
export const ringName = (id: string) => `ai-ring-${id}`;

export function PageTransition({ children }: { children: React.ReactNode }) {
  return (
    <ViewTransition
      enter={{ "nav-forward": "nav-forward", "nav-back": "nav-back", default: "none" }}
      exit={{ "nav-forward": "nav-forward", "nav-back": "nav-back", default: "none" }}
      default="none"
    >
      {children}
    </ViewTransition>
  );
}

/** An element that keeps its identity across pages and morphs between its positions. */
export function Shared({ name, children }: { name: string | undefined; children: React.ReactNode }) {
  if (!name) return <>{children}</>;
  return (
    <ViewTransition name={name} share="morph" default="none">
      {children}
    </ViewTransition>
  );
}
