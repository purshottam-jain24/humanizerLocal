import { PageTransition } from "@/components/motion/transitions";
import { Workspace } from "@/components/editor/workspace";

/** The full three-panel editor for power users. */
export default async function AdvancedPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PageTransition>
      <Workspace docId={id} />
    </PageTransition>
  );
}
