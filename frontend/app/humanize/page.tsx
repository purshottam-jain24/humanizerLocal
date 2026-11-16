import type { Metadata } from "next";
import { PageTransition } from "@/components/motion/transitions";
import { HumanizerWorkspace } from "@/components/humanizer/workspace";

export const metadata: Metadata = { title: "Humanizer · HumanWrite" };

export default function HumanizePage() {
  return (
    <PageTransition>
      <HumanizerWorkspace />
    </PageTransition>
  );
}
